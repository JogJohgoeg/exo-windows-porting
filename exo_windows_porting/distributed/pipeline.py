"""
High-level distributed inference engine.

DistributedPipelineEngine is a LLMBackend subclass that manages the full
pipeline: shard assignment, worker startup, coordinator generation loop,
and clean teardown.

Usage modes
-----------
Hypergraph mode (recommended — hardware-aware shard assignment):
    from exo_windows_porting.distributed import DistributedPipelineEngine
    from exo_windows_porting.distributed.constraint_solver import solve_topology
    from exo_windows_porting.distributed.adaptive_scheduler import AdaptiveScheduler

    topo = solve_topology(
        "meta-llama/Llama-2-7b-hf",
        n_layers=32,
        nodes=[
            {"node_id": "n0", "host": "10.0.0.1", "gpu_memory_mb": 24576,
             "bandwidth_gbps": 400, "nvlink": True},
            {"node_id": "n1", "host": "10.0.0.2", "gpu_memory_mb": 12288},
        ],
    )
    engine = DistributedPipelineEngine.from_hypergraph(topo)
    engine.set_scheduler(AdaptiveScheduler(topo))   # optional: auto-rebalance

    await engine.start()
    text = await engine.generate("Hello!", max_tokens=128)
    await engine.stop()

Classic mode (backwards-compatible):
    engine = DistributedPipelineEngine.from_nodes(
        model_id="meta-llama/Llama-2-7b-hf",
        nodes=[...],
    )
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, List, Optional

from ..backend.base import LLMBackend
from .coordinator import ShardCoordinator
from .shard import ClusterTopology, ModelShard, assign_shards
from .transport import close_zmq_context
from .worker import PipelineWorker

if TYPE_CHECKING:
    from .adaptive_scheduler import AdaptiveScheduler
    from .hypergraph import HypergraphTopology

logger = logging.getLogger(__name__)


class DistributedPipelineEngine(LLMBackend):
    """
    LLMBackend implementation that runs inference across a pipeline of nodes.

    Supports three creation paths:
      - from_hypergraph()  — hardware-aware shard assignment via ConstraintSolver
      - from_nodes()       — classic VRAM-proportional assignment
      - local()            — single-machine testing mode

    Optional AdaptiveScheduler integration
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    Call ``engine.set_scheduler(scheduler)`` to enable runtime load monitoring.
    After every ``scheduler.check_interval`` generate() calls the engine will
    invoke ``scheduler.should_rebalance()``; if True it calls
    ``apply_hypergraph_topology()`` which gracefully restarts workers with the
    new shard assignment.
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
        self.topology = topology
        self.local_shards = local_shards or []
        self.local_devices = local_devices or ["cuda"] * len(self.local_shards)
        self.coordinator_host = coordinator_host
        self.results_port = results_port
        self._temperature = temperature
        self._top_p = top_p

        self._workers: List[PipelineWorker] = []
        self._worker_tasks: List[asyncio.Task] = []
        self._coordinator = ShardCoordinator(
            topology,
            results_port=results_port,
            temperature=temperature,
            top_p=top_p,
        )
        self._started = False

        # Hypergraph / adaptive scheduler (optional)
        self._hypergraph: Optional["HypergraphTopology"] = None
        self._scheduler: Optional["AdaptiveScheduler"] = None

    # ------------------------------------------------------------------
    # Factory helpers
    # ------------------------------------------------------------------

    @classmethod
    def from_hypergraph(
        cls,
        hypergraph: "HypergraphTopology",
        *,
        local_mode: bool = False,
        local_devices: Optional[List[str]] = None,
        results_port: int = 29600,
        temperature: float = 1.0,
        top_p: float = 0.9,
    ) -> "DistributedPipelineEngine":
        """
        Create an engine from a solved HypergraphTopology.

        The topology's shard_map must already be populated
        (call ConstraintSolver.solve() or solve_topology() first).

        Args:
            hypergraph:     Solved HypergraphTopology.
            local_mode:     If True, all shards run in this process
                            (single-machine testing).
            local_devices:  Device per shard in local mode.
            results_port:   ZMQ port for logits results.
            temperature:    Sampling temperature.
            top_p:          Nucleus sampling p.
        """
        if not hypergraph.shard_map:
            raise ValueError(
                "HypergraphTopology.shard_map is empty — call "
                "ConstraintSolver.solve() before from_hypergraph()."
            )

        linear = hypergraph.get_linear_topology()

        if local_mode:
            devices = local_devices or ["cpu"] * len(linear.shards)
            engine = cls(
                topology=linear,
                local_shards=linear.shards,
                local_devices=devices,
                coordinator_host="127.0.0.1",
                results_port=results_port,
                temperature=temperature,
                top_p=top_p,
            )
        else:
            engine = cls(
                topology=linear,
                results_port=results_port,
                temperature=temperature,
                top_p=top_p,
            )

        engine._hypergraph = hypergraph
        logger.info("Engine created from hypergraph:\n%s", hypergraph)
        return engine

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
        Create an engine for a multi-machine cluster (classic mode).

        Workers run as separate processes on each node.
        """
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
        """Run all shards in-process on one machine (for testing)."""
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
            {"node_id": f"local-{i}", "host": "127.0.0.1", "gpu_memory_mb": 1}
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
    # Adaptive scheduler
    # ------------------------------------------------------------------

    def set_scheduler(self, scheduler: "AdaptiveScheduler") -> None:
        """Attach an AdaptiveScheduler to this engine."""
        self._scheduler = scheduler
        logger.debug("AdaptiveScheduler attached to engine")

    async def apply_hypergraph_topology(
        self,
        new_topo: "HypergraphTopology",
    ) -> None:
        """
        Gracefully apply a new shard topology proposed by the scheduler.

        Steps:
          1. Finish in-flight requests (drain_timeout=5 s).
          2. Stop all workers and the coordinator.
          3. Rebuild worker list from new topology.
          4. Restart.

        Args:
            new_topo: Solved HypergraphTopology from AdaptiveScheduler.propose_rebalance().
        """
        logger.info("Applying new topology from AdaptiveScheduler...")
        was_local = len(self.local_shards) > 0

        await self.stop(drain_timeout=5.0)

        # Rebuild topology and coordinator
        linear = new_topo.get_linear_topology()
        self.topology = linear
        self._hypergraph = new_topo
        self._coordinator = ShardCoordinator(
            linear,
            results_port=self.results_port,
            temperature=self._temperature,
            top_p=self._top_p,
        )

        # Rebuild local_shards if running in local mode
        if was_local:
            self.local_shards = linear.shards
            # Keep same device assignment; pad/truncate if shard count changed
            n = len(self.local_shards)
            if len(self.local_devices) < n:
                self.local_devices.extend(["cpu"] * (n - len(self.local_devices)))
            else:
                self.local_devices = self.local_devices[:n]

        # Reset worker lists (stop() already cleared tasks)
        self._workers = []
        self._worker_tasks = []

        await self.start()
        logger.info("Topology rebalance complete — new layout:\n%s", new_topo)

    async def _maybe_rebalance(self, elapsed_ms: float) -> None:
        """
        Record timing with the scheduler and trigger rebalance if needed.

        Called after every generate() / generate_stream() call.
        """
        if self._scheduler is None:
            return

        self._scheduler.record_total_latency(elapsed_ms)

        if self._scheduler.should_rebalance():
            new_topo = self._scheduler.propose_rebalance()
            await self.apply_hypergraph_topology(new_topo)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        if self._started:
            return

        for shard, device in zip(self.local_shards, self.local_devices):
            next_shard = self.topology.next_shard(shard.node_id)
            next_host = next_shard.host if next_shard else None

            worker = PipelineWorker(
                shard=shard,
                model_id=self.topology.model_id,
                next_worker_host=next_host,
                next_worker_port=next_shard.inference_port if next_shard else None,
                coordinator_host=self.coordinator_host,
                coordinator_results_port=self.results_port,
                device=device,
            )
            await worker.start()
            self._workers.append(worker)

            task = asyncio.create_task(
                worker.run(), name=f"worker-{shard.node_id}"
            )
            task.add_done_callback(
                lambda t, nid=shard.node_id: self._on_worker_done(t, nid)
            )
            self._worker_tasks.append(task)

        await self._coordinator.start()
        self._started = True
        logger.info(
            "DistributedPipelineEngine started (%d local shard(s))",
            len(self._workers),
        )

    def _on_worker_done(self, task: asyncio.Task, node_id: str) -> None:
        if task.cancelled():
            logger.debug("Worker %s task cancelled", node_id)
            return
        exc = task.exception()
        if exc is not None:
            logger.error(
                "Worker %s crashed — pipeline may be stalled: %s",
                node_id, exc, exc_info=exc,
            )

    async def stop(self, drain_timeout: float = 5.0) -> None:
        """Stop all local workers and the coordinator."""
        for worker in self._workers:
            worker._running = False

        if drain_timeout > 0 and self._worker_tasks:
            _, pending = await asyncio.wait(
                self._worker_tasks,
                timeout=drain_timeout,
                return_when=asyncio.ALL_COMPLETED,
            )
            if pending:
                logger.warning(
                    "%d worker task(s) did not finish within %.1fs — cancelling",
                    len(pending), drain_timeout,
                )

        for task in self._worker_tasks:
            task.cancel()
        await asyncio.gather(*self._worker_tasks, return_exceptions=True)

        for worker in self._workers:
            await worker.stop()
        await self._coordinator.stop()

        close_zmq_context()
        self._started = False

    # ------------------------------------------------------------------
    # LLMBackend interface
    # ------------------------------------------------------------------

    def get_backend_name(self) -> str:
        return f"distributed-pipeline/{self.topology.n_nodes}-nodes"

    async def generate(self, prompt: str, max_tokens: int = 512) -> str:
        if not self._started:
            await self.start()

        t0 = time.monotonic()
        result = await self._coordinator.generate(prompt, max_new_tokens=max_tokens)
        await self._maybe_rebalance((time.monotonic() - t0) * 1000)
        return result

    async def generate_stream(self, prompt: str, max_tokens: int = 512):
        """Yield token strings as they are generated."""
        if not self._started:
            await self.start()

        t0 = time.monotonic()
        async for token in self._coordinator.generate_stream(
            prompt, max_new_tokens=max_tokens
        ):
            yield token
        await self._maybe_rebalance((time.monotonic() - t0) * 1000)
