"""
Hardware-aware constraint solver for hypergraph shard assignment.

Problem
-------
Given:
  - n_layers:           total transformer layers in the model
  - nodes:              list of HyperNode with VRAM and bandwidth attributes
  - layer_memory_mb:    VRAM footprint of one transformer layer
  - activation_bytes:   size of the hidden-state tensor passed between shards

Find:
  - Contiguous layer ranges [start_i, end_i) for each node_i
  Such that:
    1. sum(end_i - start_i) == n_layers                   (all layers covered)
    2. (end_i - start_i) * layer_memory_mb ≤ node_i.gpu_memory_mb * (1 - overhead)
                                                           (VRAM fits)
    3. end_i - start_i ≥ min_layers_per_shard             (minimum granularity)

Objective:
  Minimise max(shard_compute_time_i) + weighted * sum(transfer_latency_ij)

  Since we do not have real compute benchmarks at assignment time, we use
  n_layers_i / effective_bandwidth_i as a proxy for per-node cost.

Algorithm
---------
Greedy weighted allocation:
  weight_i = capacity_i × (1 + w × bw_i / max_bw)
  alloc_i  = round(n_layers × weight_i / Σweight_j)

  Then clamp to [min_layers, capacity_i] and adjust total to n_layers exactly.

This is O(n_nodes) and produces near-optimal results for the typical case
of 2-8 nodes with heterogeneous VRAM.

Author: Exo Windows Porting Team
License: MIT
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .hypergraph import HypergraphTopology, HyperNode

logger = logging.getLogger(__name__)

_DEFAULT_LAYER_MEMORY_MB = 200    # ~200 MB per layer for a 7B fp16 model
_DEFAULT_ACTIVATION_BYTES = 8 * 1024   # 8 KB hidden state per token (seq=1, d=4096)


@dataclass
class SolverConfig:
    """Tunable parameters for the constraint solver."""
    layer_memory_mb: int = _DEFAULT_LAYER_MEMORY_MB
    activation_bytes: int = _DEFAULT_ACTIVATION_BYTES
    min_layers_per_shard: int = 1
    overhead_fraction: float = 0.10   # reserve 10 % VRAM for activations / KV cache
    bandwidth_weight: float = 0.30    # how much inter-node BW biases allocation


class ConstraintSolver:
    """
    Assigns transformer layers to nodes under VRAM and bandwidth constraints.

    Usage::

        from exo_windows_porting.distributed.hypergraph import build_hypergraph_topology
        from exo_windows_porting.distributed.constraint_solver import ConstraintSolver

        topo = build_hypergraph_topology("my-model", 32, nodes)
        solver = ConstraintSolver()
        solver.solve(topo)            # fills topo.shard_map in-place
        linear = topo.get_linear_topology()
    """

    def __init__(self, config: Optional[SolverConfig] = None):
        self.config = config or SolverConfig()

    # ------------------------------------------------------------------
    # Primary API
    # ------------------------------------------------------------------

    def solve(
        self,
        topology: HypergraphTopology,
        layer_memory_mb: Optional[int] = None,
    ) -> HypergraphTopology:
        """
        Assign layers to nodes and update topology.shard_map in-place.

        Args:
            topology:        HypergraphTopology to solve (modified in-place).
            layer_memory_mb: Override per-layer VRAM usage (optional).

        Returns:
            The same topology with shard_map populated.

        Raises:
            ValueError: If total VRAM is insufficient for the model,
                        or if topology has no nodes.
        """
        cfg = self.config
        lmb = layer_memory_mb if layer_memory_mb is not None else cfg.layer_memory_mb

        nodes = list(topology.nodes.values())
        n_nodes = len(nodes)
        n_layers = topology.n_layers

        if n_nodes == 0:
            raise ValueError("Cannot solve: topology has no nodes")

        # ── 1. Compute per-node layer capacities ──────────────────────────
        capacities: List[int] = []
        for node in nodes:
            usable_vram = node.gpu_memory_mb * (1.0 - cfg.overhead_fraction)
            cap = max(int(usable_vram / lmb), cfg.min_layers_per_shard)
            capacities.append(cap)

        total_capacity = sum(capacities)
        if total_capacity < n_layers:
            detail = ", ".join(
                f"{nd.node_id}={nd.gpu_memory_mb}MB→{cap}L"
                for nd, cap in zip(nodes, capacities)
            )
            raise ValueError(
                f"Insufficient VRAM: cluster can hold {total_capacity} layers "
                f"({detail}) but model requires {n_layers} layers "
                f"(layer_memory_mb={lmb})."
            )

        # ── 2. Bandwidth-weighted allocation ─────────────────────────────
        bw_values = [nd.effective_bandwidth_gbps for nd in nodes]
        max_bw = max(bw_values) or 1.0

        weights = [
            cap * (1.0 + cfg.bandwidth_weight * bw / max_bw)
            for cap, bw in zip(capacities, bw_values)
        ]
        total_weight = sum(weights) or 1.0

        raw_allocs = [n_layers * w / total_weight for w in weights]
        allocs = [max(cfg.min_layers_per_shard, round(r)) for r in raw_allocs]
        allocs = [min(a, c) for a, c in zip(allocs, capacities)]

        # ── 3. Fix total to exactly n_layers ─────────────────────────────
        allocs = self._adjust_total(allocs, n_layers, capacities, cfg.min_layers_per_shard)

        # ── 4. Build contiguous ranges ────────────────────────────────────
        topology.shard_map.clear()
        cursor = 0
        for node, n_alloc in zip(nodes, allocs):
            topology.shard_map[node.node_id] = (cursor, cursor + n_alloc)
            cursor += n_alloc

        logger.info(
            "ConstraintSolver: %d layers → %d nodes | map=%s",
            n_layers,
            n_nodes,
            {nid: f"[{s},{e})" for nid, (s, e) in topology.shard_map.items()},
        )
        return topology

    # ------------------------------------------------------------------
    # Latency estimation
    # ------------------------------------------------------------------

    def estimate_latency_ms(
        self,
        topology: HypergraphTopology,
        tokens_per_request: int = 1,
        compute_ms_per_layer: float = 0.5,
    ) -> Dict[str, float]:
        """
        Estimate per-shard latency (compute + activation transfer) in ms.

        This is a static estimate based on hardware specs, not measured values.
        AdaptiveScheduler uses live measurements to refine this.

        Args:
            topology:             Solved HypergraphTopology.
            tokens_per_request:   Batch size for the activation tensor.
            compute_ms_per_layer: Estimated compute time per layer per token.

        Returns:
            Dict mapping node_id → estimated ms per decode step.
        """
        result: Dict[str, float] = {}
        ordered = topology.get_pipeline_order()

        for i, node in enumerate(ordered):
            nid = node.node_id
            start, end = topology.shard_map[nid]
            n_layers = end - start

            compute_ms = n_layers * compute_ms_per_layer * tokens_per_request

            transfer_ms = 0.0
            if i < len(ordered) - 1:
                next_node = ordered[i + 1]
                edge = topology.edge_between(nid, next_node.node_id)
                if edge:
                    transfer_ms = edge.transfer_latency_ms(
                        self.config.activation_bytes * tokens_per_request
                    )

            result[nid] = round(compute_ms + transfer_ms, 3)

        return result

    def imbalance_ratio(self, latencies: Dict[str, float]) -> float:
        """
        Return max/min latency ratio.

        1.0 = perfectly balanced; >1 = imbalanced (higher is worse).
        """
        if len(latencies) < 2:
            return 1.0
        vals = list(latencies.values())
        mn = min(vals)
        if mn <= 0:
            return float("inf")
        return max(vals) / mn

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _adjust_total(
        allocs: List[int],
        target: int,
        capacities: List[int],
        min_layers: int,
    ) -> List[int]:
        """Adjust allocs so they sum to exactly target."""
        allocs = list(allocs)
        diff = target - sum(allocs)

        if diff > 0:
            # Add layers one at a time to nodes with spare capacity
            # Prioritise nodes with the most spare capacity
            for _ in range(diff):
                spare = [(capacities[i] - allocs[i], i) for i in range(len(allocs))]
                spare.sort(reverse=True)
                for _, idx in spare:
                    if allocs[idx] < capacities[idx]:
                        allocs[idx] += 1
                        break

        elif diff < 0:
            # Remove layers one at a time from the largest shards
            for _ in range(-diff):
                idx = max(
                    range(len(allocs)),
                    key=lambda i: allocs[i] if allocs[i] > min_layers else -1,
                )
                if allocs[idx] > min_layers:
                    allocs[idx] -= 1

        return allocs


# ---------------------------------------------------------------------------
# Convenience function
# ---------------------------------------------------------------------------

def solve_topology(
    model_id: str,
    n_layers: int,
    nodes: List[dict],
    base_port: int = 29500,
    layer_memory_mb: int = _DEFAULT_LAYER_MEMORY_MB,
) -> HypergraphTopology:
    """
    One-shot: build a HypergraphTopology and solve the shard assignment.

    Args:
        model_id:         HuggingFace model ID (stored in topology metadata).
        n_layers:         Total transformer layers.
        nodes:            List of node dicts (see build_hypergraph_topology).
        base_port:        Base port for ZMQ sockets.
        layer_memory_mb:  VRAM per layer (override default 200 MB).

    Returns:
        Solved HypergraphTopology with shard_map populated.
    """
    from .hypergraph import build_hypergraph_topology

    topo = build_hypergraph_topology(model_id, n_layers, nodes, base_port=base_port)
    ConstraintSolver().solve(topo, layer_memory_mb=layer_memory_mb)
    return topo
