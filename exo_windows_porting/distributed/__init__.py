"""
Distributed pipeline inference for Exo Windows Porting.

Key exports
-----------
DistributedPipelineEngine
    Drop-in LLMBackend that runs inference across multiple nodes via
    pipeline parallelism.  Supports both multi-machine clusters and
    single-machine multi-GPU setups.

assign_shards
    Divide a model's transformer layers across nodes proportional to
    their available VRAM.

ClusterTopology / ModelShard
    Data classes describing the pipeline layout.

PipelineWorker
    Run a single pipeline stage (typically one per process/machine).

ShardCoordinator
    Orchestrate generation: tokenize, feed tokens to pipeline, sample
    output tokens from logits.

Quick start (single machine, 2 GPUs)
--------------------------------------
    from exo_windows_porting.distributed import DistributedPipelineEngine
    import asyncio

    engine = DistributedPipelineEngine.local(
        model_id="meta-llama/Llama-2-7b-hf",
        n_local_shards=2,
        devices=["cuda:0", "cuda:1"],
    )

    async def main():
        await engine.start()
        text = await engine.generate("What is the capital of France?")
        print(text)
        await engine.stop()

    asyncio.run(main())

Quick start (2-node cluster)
-----------------------------
    # On each worker machine, run:
    #   python -m exo_windows_porting.distributed.worker_cli \\
    #       --model meta-llama/Llama-2-7b-hf \\
    #       --node-id node-a --port 29500 \\
    #       --coordinator 192.168.1.1

    # On the coordinator machine:
    from exo_windows_porting.distributed import DistributedPipelineEngine

    engine = DistributedPipelineEngine.from_nodes(
        model_id="meta-llama/Llama-2-7b-hf",
        nodes=[
            {"node_id": "node-a", "host": "192.168.1.10", "gpu_memory_mb": 24576},
            {"node_id": "node-b", "host": "192.168.1.11", "gpu_memory_mb": 12288},
        ],
    )
"""

from .adaptive_scheduler import AdaptiveScheduler
from .constraint_solver import ConstraintSolver, SolverConfig, solve_topology
from .coordinator import ShardCoordinator
from .hypergraph import HypergraphTopology, HyperNode, HyperEdge, build_hypergraph_topology
from .pipeline import DistributedPipelineEngine
from .shard import ClusterTopology, ModelShard, assign_shards
from .worker import PipelineWorker

__all__ = [
    # Core engine
    "DistributedPipelineEngine",
    "ShardCoordinator",
    "PipelineWorker",
    # Classic topology
    "ClusterTopology",
    "ModelShard",
    "assign_shards",
    # Hypergraph topology
    "HypergraphTopology",
    "HyperNode",
    "HyperEdge",
    "build_hypergraph_topology",
    # Constraint solver
    "ConstraintSolver",
    "SolverConfig",
    "solve_topology",
    # Adaptive scheduler
    "AdaptiveScheduler",
]
