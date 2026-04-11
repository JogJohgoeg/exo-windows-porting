"""
Adaptive scheduler for runtime shard rebalancing.

Monitors per-shard metrics (latency, KV cache hit rate, token throughput)
using a rolling window and triggers shard reassignment when load imbalance
exceeds a configurable threshold.

Integration with DistributedPipelineEngine
------------------------------------------
    from exo_windows_porting.distributed import DistributedPipelineEngine
    from exo_windows_porting.distributed.adaptive_scheduler import AdaptiveScheduler
    from exo_windows_porting.distributed.constraint_solver import ConstraintSolver

    topo = ...  # HypergraphTopology, already solved
    engine = DistributedPipelineEngine.from_hypergraph(topo)
    scheduler = AdaptiveScheduler(topo)
    engine.set_scheduler(scheduler)

    # The engine calls scheduler.record_latency() after each generation,
    # and scheduler.should_rebalance() + apply_hypergraph_topology() as needed.

Author: Exo Windows Porting Team
License: MIT
"""
from __future__ import annotations

import copy
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, Optional

from .constraint_solver import ConstraintSolver
from .hypergraph import HypergraphTopology

logger = logging.getLogger(__name__)

_WINDOW_SIZE = 100          # rolling window samples per shard
_DEFAULT_IMBALANCE = 0.35   # trigger if max/min latency ratio > 1.35
_DEFAULT_INTERVAL = 50      # check every N recorded samples
_DEFAULT_COOLDOWN = 60.0    # minimum seconds between rebalances


# ---------------------------------------------------------------------------
# Per-shard metrics
# ---------------------------------------------------------------------------

@dataclass
class ShardMetrics:
    """Rolling metrics for one shard node."""

    node_id: str
    latency_samples: Deque[float] = field(
        default_factory=lambda: deque(maxlen=_WINDOW_SIZE)
    )
    kv_hits: int = 0
    kv_misses: int = 0
    tokens_processed: int = 0
    last_updated: float = field(default_factory=time.monotonic)

    @property
    def avg_latency_ms(self) -> float:
        if not self.latency_samples:
            return 0.0
        return sum(self.latency_samples) / len(self.latency_samples)

    @property
    def p95_latency_ms(self) -> float:
        """95th-percentile latency from the rolling window."""
        if not self.latency_samples:
            return 0.0
        sorted_samples = sorted(self.latency_samples)
        idx = max(0, int(len(sorted_samples) * 0.95) - 1)
        return sorted_samples[idx]

    @property
    def kv_hit_rate(self) -> float:
        total = self.kv_hits + self.kv_misses
        return self.kv_hits / total if total > 0 else 0.0

    @property
    def throughput_tok_s(self) -> float:
        age = time.monotonic() - self.last_updated + 1e-6
        return self.tokens_processed / age

    def to_dict(self) -> dict:
        return {
            "avg_latency_ms": round(self.avg_latency_ms, 2),
            "p95_latency_ms": round(self.p95_latency_ms, 2),
            "kv_hit_rate": round(self.kv_hit_rate, 3),
            "tokens_processed": self.tokens_processed,
        }


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------

class AdaptiveScheduler:
    """
    Observes per-shard metrics and proposes topology rebalancing.

    Decision logic
    ~~~~~~~~~~~~~~
    ``should_rebalance()`` returns True when ALL of:
      1. At least ``check_interval`` latency samples have been collected
         since the last check.
      2. The cooldown period has elapsed since the last rebalance.
      3. max_avg_latency / min_avg_latency > (1 + imbalance_threshold).

    ``propose_rebalance()`` re-runs the ConstraintSolver with bandwidth
    penalties applied to slow nodes (so they receive fewer layers next time),
    and returns a new HypergraphTopology with updated shard_map.

    Applying the topology (restarting workers) is the caller's responsibility.
    """

    def __init__(
        self,
        topology: HypergraphTopology,
        solver: Optional[ConstraintSolver] = None,
        imbalance_threshold: float = _DEFAULT_IMBALANCE,
        check_interval: int = _DEFAULT_INTERVAL,
        cooldown_s: float = _DEFAULT_COOLDOWN,
    ):
        self.topology = topology
        self.solver = solver or ConstraintSolver()
        self.imbalance_threshold = imbalance_threshold
        self.check_interval = check_interval
        self.cooldown_s = cooldown_s

        self._metrics: Dict[str, ShardMetrics] = {
            nid: ShardMetrics(node_id=nid) for nid in topology.nodes
        }
        self._samples_since_check: int = 0
        self._last_rebalance: float = 0.0
        self._rebalance_count: int = 0

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def record_latency(self, node_id: str, latency_ms: float) -> None:
        """Record one latency sample for a shard."""
        m = self._get_or_create(node_id)
        m.latency_samples.append(latency_ms)
        m.tokens_processed += 1
        m.last_updated = time.monotonic()
        self._samples_since_check += 1

    def record_kv(self, node_id: str, *, hit: bool) -> None:
        """Record a KV cache hit or miss for a shard."""
        m = self._get_or_create(node_id)
        if hit:
            m.kv_hits += 1
        else:
            m.kv_misses += 1

    def record_total_latency(self, total_ms: float) -> None:
        """
        Record a whole-pipeline latency when per-shard breakdown is unavailable.

        Distributes the time proportionally across shards by their current
        layer counts, which is a reasonable proxy for compute time.
        """
        ordered = self.topology.get_pipeline_order()
        if not ordered:
            return

        layer_counts = []
        for node in ordered:
            start, end = self.topology.shard_map.get(node.node_id, (0, 0))
            layer_counts.append(max(end - start, 1))

        total_layers = sum(layer_counts)
        for node, lc in zip(ordered, layer_counts):
            fraction = lc / total_layers
            self.record_latency(node.node_id, total_ms * fraction)

    # ------------------------------------------------------------------
    # Decision
    # ------------------------------------------------------------------

    def should_rebalance(self) -> bool:
        """
        Return True if a rebalance should be triggered now.

        Checks sample count, cooldown, and imbalance ratio.
        Resets the sample counter on each call (whether or not rebalance
        is triggered) to prevent repeated triggers on the same data.
        """
        if self._samples_since_check < self.check_interval:
            return False

        self._samples_since_check = 0   # reset counter regardless of outcome

        if time.monotonic() - self._last_rebalance < self.cooldown_s:
            return False

        latencies = {
            nid: m.avg_latency_ms
            for nid, m in self._metrics.items()
            if m.latency_samples
        }
        if len(latencies) < 2:
            return False

        mn = min(latencies.values())
        mx = max(latencies.values())
        ratio = mx / (mn + 1e-9)

        if ratio > 1.0 + self.imbalance_threshold:
            slowest = max(latencies, key=latencies.__getitem__)
            fastest = min(latencies, key=latencies.__getitem__)
            logger.info(
                "Load imbalance detected (ratio=%.2f×): "
                "slowest=%s (%.1f ms avg), fastest=%s (%.1f ms avg) — rebalancing",
                ratio,
                slowest, latencies[slowest],
                fastest, latencies[fastest],
            )
            return True

        return False

    def propose_rebalance(self) -> HypergraphTopology:
        """
        Return a new HypergraphTopology with an updated shard assignment.

        Algorithm:
          1. Deep-copy the current topology.
          2. Penalise slow nodes by reducing their effective_bandwidth_gbps
             (the ConstraintSolver uses bandwidth as a proxy for speed).
          3. Re-run the solver on the copy.

        The caller (DistributedPipelineEngine.apply_hypergraph_topology)
        is responsible for restarting workers with the new assignment.
        """
        new_topo = copy.deepcopy(self.topology)

        # Gather current average latencies
        latencies = {
            nid: m.avg_latency_ms
            for nid, m in self._metrics.items()
            if m.latency_samples
        }

        if latencies:
            max_lat = max(latencies.values()) + 1e-9
            for nid, node in new_topo.nodes.items():
                lat = latencies.get(nid, max_lat)
                # Penalise bandwidth proportionally to relative slowness.
                # A node at max latency loses 40 % of its BW score;
                # a node at min latency is unaffected.
                penalty = 0.40 * (lat / max_lat)
                node.bandwidth_gbps = max(node.bandwidth_gbps * (1.0 - penalty), 1.0)

        self.solver.solve(new_topo)

        self._last_rebalance = time.monotonic()
        self._rebalance_count += 1

        logger.info(
            "Rebalance #%d: new shard map = %s",
            self._rebalance_count,
            {nid: f"[{s},{e})" for nid, (s, e) in new_topo.shard_map.items()},
        )
        return new_topo

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def metrics_summary(self) -> Dict[str, dict]:
        """Return a per-shard snapshot of current metrics."""
        return {nid: m.to_dict() for nid, m in self._metrics.items()}

    @property
    def rebalance_count(self) -> int:
        return self._rebalance_count

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _get_or_create(self, node_id: str) -> ShardMetrics:
        if node_id not in self._metrics:
            self._metrics[node_id] = ShardMetrics(node_id=node_id)
        return self._metrics[node_id]
