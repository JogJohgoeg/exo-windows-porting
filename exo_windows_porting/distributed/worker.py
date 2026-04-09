"""
Pipeline worker node.

A worker:
  1. Loads its assigned model shard (ShardedModel).
  2. BINDs a ZMQ PULL socket → receives activations from the previous node.
  3. Runs the forward pass on its layers.
  4. Sends hidden states (or logits) to the next node via PUSH.
  5. On a FINISHED signal: evicts KV cache and forwards the signal downstream.

Worker lifecycle
----------------
    worker = PipelineWorker(
        shard, model_id,
        next_worker_host="192.168.1.11",
        next_worker_port=29501,          # explicit: no +1 guessing
        coordinator_host="192.168.1.1",
    )
    await worker.start()
    await worker.run()      # blocks until stop() called
    await worker.stop()
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Dict, List, Optional

import torch

from .shard import ModelShard
from .sharded_model import KVCache, ShardedModel
from .transport import (
    ActivationReceiver,
    ActivationSender,
    LogitsSender,
    TensorMessage,
)

logger = logging.getLogger(__name__)

_KVStore = Dict[str, Optional[List]]


class PipelineWorker:
    """
    Runs one stage of the distributed inference pipeline.

    Args:
        shard:                    This worker's ModelShard descriptor.
        model_id:                 HuggingFace model ID or local path.
        next_worker_host:         Host of the next pipeline stage.
                                  Required for non-last shards.
        next_worker_port:         ZMQ port of the next stage's PULL socket.
                                  Required for non-last shards.
                                  (No more guessing via +1.)
        coordinator_host:         Host of the coordinator — used by the last
                                  shard to send logits back.
        coordinator_results_port: Port the coordinator listens on for logits.
        device:                   Torch device string.
        recv_timeout_ms:          How long to wait for an upstream message
                                  before raising TimeoutError (0 = forever).
    """

    def __init__(
        self,
        shard: ModelShard,
        model_id: str,
        *,
        next_worker_host: Optional[str] = None,
        next_worker_port: Optional[int] = None,
        coordinator_host: str = "127.0.0.1",
        coordinator_results_port: int = 29600,
        device: str = "cuda",
        recv_timeout_ms: int = 30_000,
    ):
        self.shard = shard
        self.model_id = model_id
        self.next_worker_host = next_worker_host
        self.next_worker_port = next_worker_port
        self.coordinator_host = coordinator_host
        self.coordinator_results_port = coordinator_results_port
        self.device = device
        self.recv_timeout_ms = recv_timeout_ms

        self._model: Optional[ShardedModel] = None
        self._receiver: Optional[ActivationReceiver] = None
        self._sender: Optional[ActivationSender] = None
        self._logits_sender: Optional[LogitsSender] = None

        self._kv_store: _KVStore = {}
        self._kv_timestamps: Dict[str, float] = {}   # request_id → last-access wall time
        self._kv_ttl_seconds: float = 300.0           # evict after 5 min of inactivity
        self._last_ttl_check: float = 0.0
        self._running = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Load model shard and bind network sockets."""
        logger.info("Starting worker %s (device=%s)", self.shard, self.device)

        loop = asyncio.get_running_loop()

        # Load model in thread so we don't block the event loop
        self._model = await loop.run_in_executor(
            None, lambda: ShardedModel(self.shard, self.model_id, self.device)
        )

        # Bind incoming activation socket
        self._receiver = ActivationReceiver(self.shard.inference_port)

        # Connect outgoing socket (explicit port — no guessing)
        if not self.shard.is_last:
            if self.next_worker_host is None or self.next_worker_port is None:
                raise ValueError(
                    f"next_worker_host and next_worker_port are required "
                    f"for non-last shard {self.shard.node_id}"
                )
            self._sender = ActivationSender(
                f"tcp://{self.next_worker_host}:{self.next_worker_port}"
            )
        else:
            self._logits_sender = LogitsSender(
                self.coordinator_host, self.coordinator_results_port
            )

        # Warm up GPU (JIT kernels) — properly awaited
        await loop.run_in_executor(None, self._model.warmup)

        self._running = True
        logger.info("Worker %s ready", self.shard.node_id)

    async def stop(self) -> None:
        self._running = False
        if self._receiver:
            self._receiver.close()
        if self._sender:
            self._sender.close()
        if self._logits_sender:
            self._logits_sender.close()
        logger.info("Worker %s stopped", self.shard.node_id)

    # ------------------------------------------------------------------
    # Main event loop
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Receive → forward → send.  Loops until stop() is called."""
        logger.info("Worker %s entering run loop", self.shard.node_id)
        self._last_ttl_check = time.monotonic()
        while self._running:
            try:
                await self._process_one()
            except asyncio.CancelledError:
                break
            except TimeoutError as exc:
                # Log but keep running; upstream may be temporarily slow
                logger.warning("Worker %s recv timeout: %s", self.shard.node_id, exc)
            except Exception as exc:
                logger.exception("Worker %s unexpected error: %s", self.shard.node_id, exc)
                # Brief back-off before retrying, so we don't spin-loop on a
                # persistent failure (e.g. broken socket on the wrong port).
                await asyncio.sleep(0.5)

            # Periodically evict KV cache entries for zombie / hung requests.
            now = time.monotonic()
            if now - self._last_ttl_check >= 60.0:
                self._evict_stale_kv()
                self._last_ttl_check = now

    # ------------------------------------------------------------------
    # Single step
    # ------------------------------------------------------------------

    async def _process_one(self) -> None:
        """Receive one message, act on it, send output."""
        msg: TensorMessage = await self._receiver.recv(
            timeout_ms=self.recv_timeout_ms
        )

        if msg.finished:
            # ── Evict KV cache and propagate the signal downstream ──────
            self.clear_kv_cache(msg.request_id)
            if not self.shard.is_last:
                await self._sender.send_finished(msg.request_id)
            else:
                await self._logits_sender.send_finished(msg.request_id)
            logger.debug(
                "Worker %s: evicted KV cache for %s",
                self.shard.node_id, msg.request_id,
            )
            return

        # ── Normal inference ─────────────────────────────────────────
        request_id = msg.request_id
        activation = msg.tensor

        output, new_kv = await asyncio.get_running_loop().run_in_executor(
            None, lambda: self._forward(request_id, activation)
        )

        # Persist KV cache for next decode step
        if new_kv is not None:
            self._kv_store[request_id] = new_kv

        if self.shard.is_last:
            await self._logits_sender.send(request_id, output)
        else:
            await self._sender.send(request_id, output)

    # ------------------------------------------------------------------
    # Forward pass (runs in executor thread)
    # ------------------------------------------------------------------

    def _forward(self, request_id: str, activation: torch.Tensor):
        past_kv = self._kv_store.get(request_id)
        self._kv_timestamps[request_id] = time.monotonic()   # refresh on every use

        if self.shard.is_first:
            output, new_kv = self._model(
                input_ids=activation.long(),
                past_key_values=past_kv,
            )
        else:
            output, new_kv = self._model(
                hidden_states=activation,
                past_key_values=past_kv,
            )

        return output, new_kv

    # ------------------------------------------------------------------
    # Cache management
    # ------------------------------------------------------------------

    def clear_kv_cache(self, request_id: str) -> None:
        """Release KV tensors for a finished request and free VRAM."""
        self._kv_timestamps.pop(request_id, None)
        if request_id in self._kv_store:
            del self._kv_store[request_id]
            if self.device.startswith("cuda"):
                torch.cuda.empty_cache()
            logger.debug(
                "Worker %s: KV cache evicted for %s", self.shard.node_id, request_id
            )

    def _evict_stale_kv(self) -> None:
        """Evict KV cache entries that have not been accessed within the TTL.

        Protects against coordinator crashes that leave workers holding stale
        KV tensors indefinitely.
        """
        cutoff = time.monotonic() - self._kv_ttl_seconds
        stale = [rid for rid, ts in self._kv_timestamps.items() if ts < cutoff]
        for rid in stale:
            logger.warning(
                "Worker %s: evicting zombie KV cache for %s (idle > %.0fs)",
                self.shard.node_id, rid, self._kv_ttl_seconds,
            )
            self.clear_kv_cache(rid)
