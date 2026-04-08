"""
ZMQ-based tensor transport for pipeline-parallel inference.

Socket pattern: PUSH/PULL (one-directional streaming)
  - Each worker BINDs a PULL socket on its inference_port.
  - The previous node in the pipeline CONNECTs a PUSH socket to it.

Wire format (all fields big-endian):
  [4B] magic = 0xE7E0_DA7A
  [4B] request_id_len
  [N B] request_id (UTF-8)
  [1B] flags  (bit 0 = FINISHED — no tensor follows; rest reserved)

  If NOT finished:
    [1B] dtype_char  ('f'=float32, 'h'=float16, 'l'=int64, 'i'=int32)
    [4B] ndim
    [ndim * 8B] shape  (int64 per dim)
    [data bytes]

The FINISHED flag is used to signal that a request is complete so each
worker in the pipeline can evict its KV cache entry.  The signal cascades:
  coordinator → worker-1 → worker-2 → … → last-worker → coordinator
using the same ZMQ channels as normal activations.
"""

from __future__ import annotations

import logging
import struct
from dataclasses import dataclass
from typing import Optional, Tuple

import numpy as np
import torch
import zmq
import zmq.asyncio

logger = logging.getLogger(__name__)

_MAGIC = 0xE7E0DA7A
_FLAG_FINISHED = 0x01

_DTYPE_MAP: dict = {
    "f": torch.float32,
    "h": torch.float16,
    "l": torch.int64,
    "i": torch.int32,
}
_TORCH_TO_CHAR: dict = {v: k for k, v in _DTYPE_MAP.items()}


@dataclass
class TensorMessage:
    """A message passing through the pipeline."""
    request_id: str
    tensor: Optional[torch.Tensor]   # None when finished=True
    finished: bool = False           # True = evict KV cache and forward signal


# ──────────────────────────────────────────────────────────────────────────────
# Serialization
# ──────────────────────────────────────────────────────────────────────────────

def _serialize(
    request_id: str,
    tensor: Optional[torch.Tensor],
    finished: bool = False,
) -> bytes:
    """Serialize a TensorMessage to bytes."""
    rid = request_id.encode()
    flags = _FLAG_FINISHED if finished else 0x00

    header = struct.pack("!II", _MAGIC, len(rid)) + rid + struct.pack("!B", flags)

    if finished or tensor is None:
        return header

    dtype_char = _TORCH_TO_CHAR.get(tensor.dtype)
    if dtype_char is None:
        tensor = tensor.to(torch.float16)
        dtype_char = "h"

    arr: np.ndarray = tensor.detach().cpu().numpy()
    ndim = arr.ndim
    header += struct.pack("!cI", dtype_char.encode(), ndim)
    header += struct.pack(f"!{ndim}q", *arr.shape)
    return header + arr.tobytes()


def _deserialize(data: bytes) -> TensorMessage:
    """Deserialize bytes to a TensorMessage."""
    offset = 0

    magic, rid_len = struct.unpack_from("!II", data, offset)
    offset += 8
    if magic != _MAGIC:
        raise ValueError(f"Bad magic: expected {_MAGIC:#010x}, got {magic:#010x}")

    request_id = data[offset: offset + rid_len].decode()
    offset += rid_len

    (flags,) = struct.unpack_from("!B", data, offset)
    offset += 1

    if flags & _FLAG_FINISHED:
        return TensorMessage(request_id=request_id, tensor=None, finished=True)

    dtype_char, ndim = struct.unpack_from("!cI", data, offset)
    offset += 5
    dtype_char = dtype_char.decode()

    shape = struct.unpack_from(f"!{ndim}q", data, offset)
    offset += 8 * ndim

    torch_dtype = _DTYPE_MAP.get(dtype_char, torch.float16)
    np_dtype = {
        "f": np.float32, "h": np.float16,
        "l": np.int64,   "i": np.int32,
    }[dtype_char]

    arr = np.frombuffer(data[offset:], dtype=np_dtype).reshape(shape)
    tensor = torch.from_numpy(arr.copy()).to(torch_dtype)
    return TensorMessage(request_id=request_id, tensor=tensor, finished=False)


# ──────────────────────────────────────────────────────────────────────────────
# Sockets
# ──────────────────────────────────────────────────────────────────────────────

class ActivationSender:
    """
    Sends tensors (or finished signals) to the next node in the pipeline.

    Usage:
        sender = ActivationSender("tcp://192.168.1.11:29501")
        await sender.send("req-001", hidden_states)
        await sender.send_finished("req-001")   # evict KV cache downstream
        sender.close()
    """

    def __init__(self, address: str):
        self._ctx = zmq.asyncio.Context.instance()
        self._sock = self._ctx.socket(zmq.PUSH)
        self._sock.connect(address)
        logger.debug("ActivationSender connected to %s", address)

    async def send(self, request_id: str, tensor: torch.Tensor) -> None:
        payload = _serialize(request_id, tensor, finished=False)
        await self._sock.send(payload)

    async def send_finished(self, request_id: str) -> None:
        """Send a finished/evict signal with no tensor payload."""
        payload = _serialize(request_id, None, finished=True)
        await self._sock.send(payload)
        logger.debug("Sent FINISHED signal for %s", request_id)

    def close(self) -> None:
        self._sock.close(linger=0)


class ActivationReceiver:
    """
    Receives tensors (or finished signals) from the previous node.

    Usage:
        receiver = ActivationReceiver(port=29501)
        msg = await receiver.recv(timeout_ms=30_000)
        if msg.finished:
            evict_cache(msg.request_id)
        else:
            process(msg.tensor)
        receiver.close()
    """

    def __init__(self, port: int, host: str = "*"):
        self._ctx = zmq.asyncio.Context.instance()
        self._sock = self._ctx.socket(zmq.PULL)
        self._sock.bind(f"tcp://{host}:{port}")
        logger.info("ActivationReceiver bound on port %d", port)

    async def recv(self, timeout_ms: int = 30_000) -> TensorMessage:
        """
        Receive a message.  Raises TimeoutError if nothing arrives within
        timeout_ms milliseconds (0 = wait forever).
        """
        if timeout_ms > 0:
            ready = await self._sock.poll(timeout_ms, zmq.POLLIN)
            if not ready:
                raise TimeoutError(
                    f"ActivationReceiver: no message within {timeout_ms} ms"
                )
        data: bytes = await self._sock.recv()
        return _deserialize(data)

    def close(self) -> None:
        self._sock.close(linger=0)


class LogitsSender:
    """Last worker → coordinator: sends logits (or finished signal) back."""

    def __init__(self, coordinator_host: str, results_port: int):
        self._ctx = zmq.asyncio.Context.instance()
        self._sock = self._ctx.socket(zmq.PUSH)
        self._sock.connect(f"tcp://{coordinator_host}:{results_port}")
        logger.debug("LogitsSender connected to %s:%d", coordinator_host, results_port)

    async def send(self, request_id: str, logits: torch.Tensor) -> None:
        payload = _serialize(request_id, logits, finished=False)
        await self._sock.send(payload)

    async def send_finished(self, request_id: str) -> None:
        payload = _serialize(request_id, None, finished=True)
        await self._sock.send(payload)

    def close(self) -> None:
        self._sock.close(linger=0)


class LogitsReceiver:
    """Coordinator side: receives logits (or finished signal) from the last worker."""

    def __init__(self, results_port: int):
        self._ctx = zmq.asyncio.Context.instance()
        self._sock = self._ctx.socket(zmq.PULL)
        self._sock.bind(f"tcp://*:{results_port}")
        logger.info("LogitsReceiver bound on port %d", results_port)

    async def recv(self, timeout_ms: int = 30_000) -> TensorMessage:
        if timeout_ms > 0:
            ready = await self._sock.poll(timeout_ms, zmq.POLLIN)
            if not ready:
                raise TimeoutError(
                    f"LogitsReceiver: no message within {timeout_ms} ms"
                )
        data: bytes = await self._sock.recv()
        return _deserialize(data)

    def close(self) -> None:
        self._sock.close(linger=0)
