"""
ZMQ-based tensor transport for pipeline-parallel inference.

Socket pattern: PUSH/PULL (one-directional streaming)
  - Each worker BINDs a PULL socket on its inference_port.
  - The previous node in the pipeline CONNECTs a PUSH socket to it.
  - This keeps things simple and avoids head-of-line blocking.

For the coordinator to receive final logits from the last worker, the last
worker also BINDs a second PUSH socket (results_port = inference_port + 1000)
that the coordinator PULLs from.

Wire format (all fields big-endian):
  [4B] magic = 0xE7E0_DA7A   (sanity check)
  [4B] request_id_len
  [N B] request_id (UTF-8)
  [1B] dtype_char  ('f'=float32, 'h'=float16, 'l'=int64, 'i'=int32)
  [4B] ndim
  [ndim * 8B] shape  (int64 per dim)
  [data bytes]
"""

from __future__ import annotations

import logging
import struct
from typing import Optional, Tuple

import numpy as np
import torch
import zmq
import zmq.asyncio

logger = logging.getLogger(__name__)

_MAGIC = 0xE7E0DA7A
_DTYPE_MAP: dict = {
    "f": torch.float32,
    "h": torch.float16,
    "l": torch.int64,
    "i": torch.int32,
}
_TORCH_TO_CHAR: dict = {v: k for k, v in _DTYPE_MAP.items()}
_NP_TO_CHAR: dict = {
    np.float32: "f",
    np.float16: "h",
    np.int64: "l",
    np.int32: "i",
}


def _serialize(request_id: str, tensor: torch.Tensor) -> bytes:
    """Serialize (request_id, tensor) → bytes."""
    rid = request_id.encode()
    dtype_char = _TORCH_TO_CHAR.get(tensor.dtype)
    if dtype_char is None:
        # Fall back to float16 for unsupported dtypes
        tensor = tensor.to(torch.float16)
        dtype_char = "h"

    arr: np.ndarray = tensor.detach().cpu().numpy()
    ndim = arr.ndim
    shape = arr.shape

    header = struct.pack("!II", _MAGIC, len(rid))
    header += rid
    header += struct.pack("!cI", dtype_char.encode(), ndim)
    header += struct.pack(f"!{ndim}q", *shape)

    return header + arr.tobytes()


def _deserialize(data: bytes) -> Tuple[str, torch.Tensor]:
    """Deserialize bytes → (request_id, tensor)."""
    offset = 0

    magic, rid_len = struct.unpack_from("!II", data, offset)
    offset += 8
    if magic != _MAGIC:
        raise ValueError(f"Bad magic: expected {_MAGIC:#010x}, got {magic:#010x}")

    request_id = data[offset: offset + rid_len].decode()
    offset += rid_len

    dtype_char, ndim = struct.unpack_from("!cI", data, offset)
    offset += 5  # 1 char + 4 uint
    dtype_char = dtype_char.decode()

    shape = struct.unpack_from(f"!{ndim}q", data, offset)
    offset += 8 * ndim

    torch_dtype = _DTYPE_MAP.get(dtype_char, torch.float16)
    np_dtype = {
        "f": np.float32, "h": np.float16,
        "l": np.int64, "i": np.int32,
    }[dtype_char]

    arr = np.frombuffer(data[offset:], dtype=np_dtype).reshape(shape)
    tensor = torch.from_numpy(arr.copy()).to(torch_dtype)
    return request_id, tensor


class ActivationSender:
    """
    Sends tensors to the next node in the pipeline.

    Usage (async):
        sender = ActivationSender("tcp://192.168.1.11:29501")
        await sender.send("req-001", hidden_states)
        sender.close()
    """

    def __init__(self, address: str):
        """
        Args:
            address: Full ZMQ address of the next worker, e.g. "tcp://host:port".
        """
        self._ctx = zmq.asyncio.Context.instance()
        self._sock = self._ctx.socket(zmq.PUSH)
        self._sock.connect(address)
        logger.debug("ActivationSender connected to %s", address)

    async def send(self, request_id: str, tensor: torch.Tensor) -> None:
        """Send a tensor asynchronously."""
        payload = _serialize(request_id, tensor)
        await self._sock.send(payload, copy=False)
        logger.debug("Sent tensor %s shape=%s to %s", request_id, tuple(tensor.shape), self._sock)

    def close(self) -> None:
        self._sock.close(linger=0)


class ActivationReceiver:
    """
    Receives tensors from the previous node in the pipeline.

    Usage (async):
        receiver = ActivationReceiver(port=29501)
        request_id, tensor = await receiver.recv()
        receiver.close()
    """

    def __init__(self, port: int, host: str = "*"):
        """
        Args:
            port: Port to bind on.
            host: Bind address (default "*" = all interfaces).
        """
        self._ctx = zmq.asyncio.Context.instance()
        self._sock = self._ctx.socket(zmq.PULL)
        self._sock.bind(f"tcp://{host}:{port}")
        logger.info("ActivationReceiver bound on port %d", port)

    async def recv(self) -> Tuple[str, torch.Tensor]:
        """Receive a tensor asynchronously. Blocks until data arrives."""
        data = await self._sock.recv(copy=False)
        request_id, tensor = _deserialize(bytes(data))
        logger.debug("Received tensor %s shape=%s", request_id, tuple(tensor.shape))
        return request_id, tensor

    def close(self) -> None:
        self._sock.close(linger=0)


class LogitsSender:
    """
    Last worker → coordinator: sends logits back.
    Uses a separate PUSH socket on results_port = inference_port + 1000.
    """

    def __init__(self, coordinator_host: str, results_port: int):
        self._ctx = zmq.asyncio.Context.instance()
        self._sock = self._ctx.socket(zmq.PUSH)
        self._sock.connect(f"tcp://{coordinator_host}:{results_port}")
        logger.debug("LogitsSender connected to %s:%d", coordinator_host, results_port)

    async def send(self, request_id: str, logits: torch.Tensor) -> None:
        payload = _serialize(request_id, logits)
        await self._sock.send(payload, copy=False)

    def close(self) -> None:
        self._sock.close(linger=0)


class LogitsReceiver:
    """
    Coordinator side: receives logits from the last worker.
    """

    def __init__(self, results_port: int):
        self._ctx = zmq.asyncio.Context.instance()
        self._sock = self._ctx.socket(zmq.PULL)
        self._sock.bind(f"tcp://*:{results_port}")
        logger.info("LogitsReceiver bound on port %d", results_port)

    async def recv(self) -> Tuple[str, torch.Tensor]:
        data = await self._sock.recv(copy=False)
        return _deserialize(bytes(data))

    def close(self) -> None:
        self._sock.close(linger=0)
