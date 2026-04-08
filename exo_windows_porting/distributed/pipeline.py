"""
High-level distributed inference engine.

DistributedPipelineEngine is a LLMBackend subclass that manages the full
pipeline: shard assignment, worker startup, coordinator generation loop,
and clean teardown.

Two usage modes
---------------
Coordinator mode (this machine orchestrates but may not hold a shard):
    engine = DistributedPipelineEngine.from_nodes(
        model_id="meta-llama/Llama-2-7b-hf",
        nodes=[
            {"node_id": "node-a", "host": "192.168.1.10", "gpu_memory_mb": 24576},
            {"node_id": "node-b", "host": "192.168.1.11", "gpu_memory_mb": 12288},
        ],
    )
    await engine.start()
    text = await engine.generate("Hello!", max_tokens=128)
    await engine.stop()

Single-machine mode (run all shards locally for testing):
    engine = DistributedPipelineEngine.local(
        model_id="meta-llama/Llama-2-7b-hf",
        n_local_shards=2,
        devices=["cuda:0", "cuda:1"],
    )
"""

from __future__ import annotations

import asyncio
import logging
from typing import List, Optional

from ..backend.base import LLMBackend
from .coordinator import ShardCoordinator
from .shard import ClusterTopology, ModelShard, assign_shards
from .worker import PipelineWorker

logger = logging.getLogger(__name__)


class DistributedPipelineEngine(LLMBackend):
    """
    LLMBackend implementation that runs inference across a pipeline of nodes.

    Inheriting from LLMBackend means it can be dropped into any existing code
    that currently uses LLamaCpuBackend / LLamaCudaBackend / LLamaRocmBackend.
    """

    def __init__(
        self,
        topology: ClusterTopology,
        *,
        local_shards: Optional[List[ModelShard]] = None,
        local_devices: Optional[List[str]] = None,
        coordinator_host: str = "127.0.0.1",
        results_port: int = 29600,
        temperature: float = 1.0,
        top_p: float = 0.9,
    ):
        """
        Args:
            topology:         Full cluster topology (from assign_shards).
            local_shards:     Subset of topology.shards that THIS process runs.
                              If None, this process is coordinator-only.
            local_devices:    Torch device for each local shard (same length as
                              local_shards). Defaults to ["cuda"] * n.
            coordinator_host: IP of this machine (where coordinator listens).
            results_port:     Port this coordinator binds for receiving logits.
            temperature:      Sampling temperature.
            top_p:            Nucleus sampling p.
        """
        self.topology = topology
        self.local_shards = local_shards or []
        self.local_devices = local_devices or ["cuda"] * len(self.local_shards)
        self.coordinator_host = coordinator_host
        self.results_port = results_port

        self._workers: List[PipelineWorker] = []
        self._worker_tasks: List[asyncio.Task] = []
        self._coordinator = ShardCoordinator(
            topology,
            results_port=results_port,
            temperature=temperature,
            top_p=top_p,
        )
        self._started = False

    # ------------------------------------------------------------------
    # Factory helpers
    # ------------------------------------------------------------------

    @classmethod
    def from_nodes(
        cls,
        model_id: str,
        nodes: List[dict],
        *,
        results_port: int = 29600,
        temperature: float = 1.0,
        top_p: float = 0.9,
    ) -> "DistributedPipelineEngine":
        """
        Create an engine for a multi-machine cluster.

        The coordinator (this process) does NOT hold any shards — it just
        orchestrates. Workers run as separate processes on each node.

        Example:
            nodes = [
                {"node_id": "n1", "host": "10.0.0.1", "gpu_memory_mb": 24576},
                {"node_id": "n2", "host": "10.0.0.2", "gpu_memory_mb": 24576},
            ]
            engine = DistributedPipelineEngine.from_nodes(
                "meta-llama/Llama-2-7b-hf", nodes
            )
        """
        # Probe first node to find total layers
        from transformers import AutoConfig
        config = AutoConfig.from_pretrained(model_id)
        n_layers = config.num_hidden_layers

        topology = assign_shards(model_id, n_layers, nodes)
        logger.info("Topology: %s", topology)

        return cls(
            topology=topology,
            results_port=results_port,
            temperature=temperature,
            top_p=top_p,
        )

    @classmethod
    def local(
        cls,
        model_id: str,
        n_local_shards: int = 2,
        devices: Optional[List[str]] = None,
        *,
        base_port: int = 29500,
        results_port: int = 29600,
        temperature: float = 1.0,
        top_p: float = 0.9,
    ) -> "DistributedPipelineEngine":
        """
        Run all shards in-process on one machine (for testing / single-node use).

        Example:
            engine = DistributedPipelineEngine.local(
                "meta-llama/Llama-2-7b-hf",
                n_local_shards=2,
                devices=["cuda:0", "cuda:1"],
            )
        """
        if devices is None:
            import torch
            n_gpus = torch.cuda.device_count()
            if n_gpus >= n_local_shards:
                devices = [f"cuda:{i}" for i in range(n_local_shards)]
            else:
                devices = ["cuda" if torch.cuda.is_available() else "cpu"] * n_local_shards

        from transformers import AutoConfig
        config = AutoConfig.from_pretrained(model_id)
        n_layers = config.num_hidden_layers

        nodes = [
            {
                "node_id": f"local-{i}",
                "host": "127.0.0.1",
                "gpu_memory_mb": 1,        # uniform weights → even split
            }
            for i in range(n_local_shards)
        ]
        topology = assign_shards(model_id, n_layers, nodes, base_port=base_port)

        return cls(
            topology=topology,
            local_shards=topology.shards,
            local_devices=devices,
            coordinator_host="127.0.0.1",
            results_port=results_port,
            temperature=temperature,
            top_p=top_p,
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        if self._started:
            return

        # Start any local workers
        for shard, device in zip(self.local_shards, self.local_devices):
            # For local mode: next worker is also local; derive host/port.
            next_shard = self.topology.next_shard(shard.node_id)
            next_host = next_shard.host if next_shard else None

            worker = PipelineWorker(
                shard=shard,
                model_id=self.topology.model_id,
                next_worker_host=next_host,
                coordinator_host=self.coordinator_host,
                coordinator_results_port=self.results_port,
                device=device,
            )
            await worker.start()
            self._workers.append(worker)

            # Run the worker event loop in a background asyncio task
            task = asyncio.create_task(
                worker.run(), name=f"worker-{shard.node_id}"
            )
            self._worker_tasks.append(task)

        # Start the coordinator
        await self._coordinator.start()
        self._started = True
        logger.info("DistributedPipelineEngine started (%d local shard(s))", len(self._workers))

    async def stop(self) -> None:
        for task in self._worker_tasks:
            task.cancel()
        await asyncio.gather(*self._worker_tasks, return_exceptions=True)
        for worker in self._workers:
            await worker.stop()
        await self._coordinator.stop()
        self._started = False

    # ------------------------------------------------------------------
    # LLMBackend interface
    # ------------------------------------------------------------------

    def get_backend_name(self) -> str:
        return f"distributed-pipeline/{self.topology.n_nodes}-nodes"

    async def generate(self, prompt: str, max_tokens: int = 512) -> str:
        if not self._started:
            await self.start()

        return await self._coordinator.generate(prompt, max_new_tokens=max_tokens)

    async def generate_stream(self, prompt: str, max_tokens: int = 512):
        """Yield token strings as they are generated."""
        if not self._started:
            await self.start()

        async for token in self._coordinator.generate_stream(prompt, max_new_tokens=max_tokens):
            yield token
