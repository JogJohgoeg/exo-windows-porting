"""
Pipeline worker node.

A worker:
  1. Loads its assigned model shard (ShardedModel).
  2. BINDs a ZMQ PULL socket → receives activations from the previous node.
  3. Runs the forward pass on its layers.
  4. Sends hidden states (or logits) to the next node via PUSH.

For the last worker, logits are sent to the coordinator's LogitsReceiver.

Worker lifecycle
----------------
    worker = PipelineWorker(shard, model_id, coordinator_host, coordinator_results_port)
    await worker.start()      # load model, bind sockets, warm up
    await worker.run()        # event loop — blocks until stop() called
    await worker.stop()
"""

from __future__ import annotations

import asyncio
import logging
from typing import Dict, List, Optional, Tuple

import torch

from .shard import ModelShard
from .sharded_model import KVCache, ShardedModel
from .transport import (
    ActivationReceiver,
    ActivationSender,
    LogitsSender,
)

logger = logging.getLogger(__name__)

# request_id → per-layer KV cache (kept between decode steps)
_KVStore = Dict[str, List[Optional[KVCache]]]


class PipelineWorker:
    """
    Runs one stage of the distributed inference pipeline.

    Args:
        shard:                  This worker's ModelShard descriptor.
        model_id:               HuggingFace model ID or local path.
        next_worker_host:       Host of the next pipeline stage.
                                Ignored if this is the last shard.
        coordinator_host:       Host of the coordinator — used by the last shard
                                to send logits back.
        coordinator_results_port: Port the coordinator listens on for logits.
        device:                 Torch device string ("cuda", "cuda:1", "cpu", …).
    """

    def __init__(
        self,
        shard: ModelShard,
        model_id: str,
        *,
        next_worker_host: Optional[str] = None,
        coordinator_host: str = "127.0.0.1",
        coordinator_results_port: int = 29600,
        device: str = "cuda",
    ):
        self.shard = shard
        self.model_id = model_id
        self.next_worker_host = next_worker_host
        self.coordinator_host = coordinator_host
        self.coordinator_results_port = coordinator_results_port
        self.device = device

        self._model: Optional[ShardedModel] = None
        self._receiver: Optional[ActivationReceiver] = None
        self._sender: Optional[ActivationSender] = None
        self._logits_sender: Optional[LogitsSender] = None

        # KV cache store: {request_id: [kv_per_layer, ...]}
        self._kv_store: _KVStore = {}
        self._running = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Load model shard and bind network sockets."""
        logger.info("Starting worker for shard %s on device=%s", self.shard, self.device)

        # Load shard (CPU-intensive — run in executor to not block event loop)
        loop = asyncio.get_event_loop()
        self._model = await loop.run_in_executor(
            None, lambda: ShardedModel(self.shard, self.model_id, self.device)
        )

        # Bind incoming activation socket
        self._receiver = ActivationReceiver(self.shard.inference_port)

        # Connect outgoing socket
        if not self.shard.is_last:
            assert self.next_worker_host is not None, "next_worker_host required for non-last shards"
            next_shard_idx = None
            for i, _ in enumerate([self.shard]):
                next_shard_idx = self.shard.inference_port + 1
                break
            # The next worker's inference_port is shard.inference_port+1 by convention
            # (set by assign_shards which increments base_port sequentially)
            # We receive the actual address from the coordinator at start() time.
            # For simplicity: coordinator passes next_worker_host at construction.
            # We infer port as inference_port + 1; this matches assign_shards layout.
            next_port = self.shard.inference_port + 1
            self._sender = ActivationSender(
                f"tcp://{self.next_worker_host}:{next_port}"
            )
        else:
            self._logits_sender = LogitsSender(
                self.coordinator_host, self.coordinator_results_port
            )

        # Optional: warmup to trigger CUDA JIT
        loop.run_in_executor(None, self._model.warmup)

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
        """
        Receive activation → forward → send output.
        Loops until stop() is called.
        """
        logger.info("Worker %s entering run loop", self.shard.node_id)
        while self._running:
            try:
                await self._process_one()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.exception("Worker %s error: %s", self.shard.node_id, exc)

    # ------------------------------------------------------------------
    # Single step
    # ------------------------------------------------------------------

    async def _process_one(self) -> None:
        """Receive one activation, run forward, send output."""
        request_id, activation = await self._receiver.recv()

        with torch.no_grad():
            output, new_kv = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self._forward(request_id, activation),
            )

        # Update KV cache for this request
        if new_kv is not None:
            self._kv_store[request_id] = new_kv

        # Send output to next stage
        if self.shard.is_last:
            await self._logits_sender.send(request_id, output)
        else:
            await self._sender.send(request_id, output)

    def _forward(
        self, request_id: str, activation: torch.Tensor
    ) -> Tuple[torch.Tensor, Optional[List]]:
        """Synchronous forward pass (runs in executor thread)."""
        past_kv = self._kv_store.get(request_id)

        if self.shard.is_first:
            # activation is token IDs (int64) for first shard
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

    def clear_kv_cache(self, request_id: str) -> None:
        """Release KV cache for a finished request."""
        self._kv_store.pop(request_id, None)
