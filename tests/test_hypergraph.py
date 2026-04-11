"""
Tests for hypergraph topology, constraint solver, and adaptive scheduler.

All tests run without GPU or transformers — pure Python logic only.
"""
from __future__ import annotations

import time
import pytest

from exo_windows_porting.distributed.hypergraph import (
    HyperEdge,
    HyperNode,
    HypergraphTopology,
    build_hypergraph_topology,
)
from exo_windows_porting.distributed.constraint_solver import (
    ConstraintSolver,
    SolverConfig,
    solve_topology,
)
from exo_windows_porting.distributed.adaptive_scheduler import (
    AdaptiveScheduler,
    ShardMetrics,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _two_node_topo(
    vram0: int = 8192,
    vram1: int = 8192,
    n_layers: int = 32,
    bw0: float = 16.0,
    bw1: float = 16.0,
    nvlink: bool = False,
) -> HypergraphTopology:
    return build_hypergraph_topology(
        "test-model",
        n_layers,
        [
            {"node_id": "n0", "host": "127.0.0.1", "gpu_memory_mb": vram0,
             "bandwidth_gbps": bw0, "nvlink": nvlink},
            {"node_id": "n1", "host": "127.0.0.1", "gpu_memory_mb": vram1,
             "bandwidth_gbps": bw1},
        ],
    )


def _three_node_topo(n_layers: int = 32) -> HypergraphTopology:
    return build_hypergraph_topology(
        "test-model",
        n_layers,
        [
            {"node_id": "n0", "host": "127.0.0.1", "gpu_memory_mb": 16384},
            {"node_id": "n1", "host": "127.0.0.1", "gpu_memory_mb": 8192},
            {"node_id": "n2", "host": "127.0.0.1", "gpu_memory_mb": 8192},
        ],
    )


# ---------------------------------------------------------------------------
# HyperNode
# ---------------------------------------------------------------------------

class TestHyperNode:
    def test_effective_bandwidth_pcix(self):
        node = HyperNode("n0", "127.0.0.1", 29500, gpu_memory_mb=8192, bandwidth_gbps=16.0)
        assert node.effective_bandwidth_gbps == 16.0

    def test_effective_bandwidth_nvlink(self):
        node = HyperNode(
            "n0", "127.0.0.1", 29500, gpu_memory_mb=8192,
            bandwidth_gbps=16.0, nvlink=True,
        )
        # NVLink multiplier is 5×
        assert node.effective_bandwidth_gbps == 80.0


# ---------------------------------------------------------------------------
# HyperEdge
# ---------------------------------------------------------------------------

class TestHyperEdge:
    def test_pipeline_edge_bandwidth_is_min(self):
        src = HyperNode("s", "127.0.0.1", 29500, gpu_memory_mb=8192, bandwidth_gbps=32.0)
        dst = HyperNode("d", "127.0.0.1", 29501, gpu_memory_mb=8192, bandwidth_gbps=16.0)
        edge = HyperEdge.pipeline_edge(src, dst)
        assert edge.bandwidth_gbps == 16.0

    def test_pipeline_edge_nvlink_latency(self):
        src = HyperNode("s", "127.0.0.1", 29500, gpu_memory_mb=8192, nvlink=True)
        dst = HyperNode("d", "127.0.0.1", 29501, gpu_memory_mb=8192, nvlink=True)
        edge = HyperEdge.pipeline_edge(src, dst)
        assert edge.latency_us == 10.0

    def test_pipeline_edge_pcix_latency(self):
        src = HyperNode("s", "127.0.0.1", 29500, gpu_memory_mb=8192)
        dst = HyperNode("d", "127.0.0.1", 29501, gpu_memory_mb=8192)
        edge = HyperEdge.pipeline_edge(src, dst)
        assert edge.latency_us == 50.0

    def test_transfer_latency_increases_with_size(self):
        edge = HyperEdge("e", ["s"], ["d"], bandwidth_gbps=16.0, latency_us=50.0)
        small = edge.transfer_latency_ms(1_000)
        large = edge.transfer_latency_ms(1_000_000)
        assert large > small


# ---------------------------------------------------------------------------
# HypergraphTopology
# ---------------------------------------------------------------------------

class TestHypergraphTopology:
    def test_build_creates_nodes_and_edges(self):
        topo = _two_node_topo()
        assert len(topo.nodes) == 2
        assert len(topo.edges) == 1   # one pipeline edge between the two nodes

    def test_three_nodes_two_edges(self):
        topo = _three_node_topo()
        assert len(topo.edges) == 2

    def test_edge_between_found(self):
        topo = _two_node_topo()
        edge = topo.edge_between("n0", "n1")
        assert edge is not None

    def test_edge_between_not_found_reverse(self):
        topo = _two_node_topo()
        # Edges are directed
        assert topo.edge_between("n1", "n0") is None

    def test_get_pipeline_order_requires_shard_map(self):
        topo = _two_node_topo()
        # shard_map empty → pipeline order is empty
        assert topo.get_pipeline_order() == []

    def test_get_pipeline_order_after_solve(self):
        topo = _two_node_topo()
        ConstraintSolver().solve(topo)
        order = topo.get_pipeline_order()
        assert [n.node_id for n in order] == ["n0", "n1"]

    def test_bottleneck_edge_none_when_no_edges(self):
        topo = HypergraphTopology("m", 32)
        assert topo.bottleneck_edge(8192) is None

    def test_repr_contains_model_id(self):
        topo = _two_node_topo()
        ConstraintSolver().solve(topo)
        assert "test-model" in repr(topo)


# ---------------------------------------------------------------------------
# ConstraintSolver
# ---------------------------------------------------------------------------

class TestConstraintSolver:
    def test_layers_sum_to_total(self):
        topo = _two_node_topo(n_layers=32)
        ConstraintSolver().solve(topo)
        total = sum(e - s for s, e in topo.shard_map.values())
        assert total == 32

    def test_contiguous_ranges(self):
        topo = _three_node_topo(n_layers=32)
        ConstraintSolver().solve(topo)
        ordered = topo.get_pipeline_order()
        prev_end = 0
        for node in ordered:
            s, e = topo.shard_map[node.node_id]
            assert s == prev_end, f"Gap before {node.node_id}: expected {prev_end}, got {s}"
            prev_end = e
        assert prev_end == 32

    def test_vram_constraint_respected(self):
        # 200 MB/layer, 2 layers max per node for 400 MB VRAM with 10% overhead
        topo = _two_node_topo(vram0=500, vram1=500, n_layers=4)
        cfg = SolverConfig(layer_memory_mb=200, overhead_fraction=0.1)
        ConstraintSolver(cfg).solve(topo)
        for nid, (s, e) in topo.shard_map.items():
            layers = e - s
            node = topo.nodes[nid]
            usable = node.gpu_memory_mb * (1 - cfg.overhead_fraction)
            assert layers * cfg.layer_memory_mb <= usable + cfg.layer_memory_mb, (
                f"{nid} assigned {layers} layers but usable={usable:.0f} MB"
            )

    def test_raises_on_insufficient_vram(self):
        # 1 layer per 200 MB, but nodes only have 100 MB each
        topo = _two_node_topo(vram0=100, vram1=100, n_layers=10)
        cfg = SolverConfig(layer_memory_mb=200, min_layers_per_shard=1)
        with pytest.raises(ValueError, match="Insufficient VRAM"):
            ConstraintSolver(cfg).solve(topo)

    def test_raises_on_no_nodes(self):
        topo = HypergraphTopology("m", 32)
        with pytest.raises(ValueError, match="no nodes"):
            ConstraintSolver().solve(topo)

    def test_bandwidth_weight_biases_larger_node(self):
        # n0 has 10× the bandwidth of n1 and same VRAM — should get more layers
        topo = _two_node_topo(vram0=8192, vram1=8192, n_layers=32,
                               bw0=160.0, bw1=16.0)
        cfg = SolverConfig(layer_memory_mb=100, bandwidth_weight=1.0)
        ConstraintSolver(cfg).solve(topo)
        s0, e0 = topo.shard_map["n0"]
        s1, e1 = topo.shard_map["n1"]
        assert (e0 - s0) >= (e1 - s1), "High-bandwidth node should get >= layers"

    def test_min_layers_per_shard(self):
        topo = _three_node_topo(n_layers=32)
        cfg = SolverConfig(layer_memory_mb=50, min_layers_per_shard=3)
        ConstraintSolver(cfg).solve(topo)
        for s, e in topo.shard_map.values():
            assert (e - s) >= 3

    def test_single_node(self):
        topo = build_hypergraph_topology("m", 32, [
            {"node_id": "only", "host": "127.0.0.1", "gpu_memory_mb": 32768}
        ])
        ConstraintSolver().solve(topo)
        assert topo.shard_map["only"] == (0, 32)

    def test_estimate_latency_keys(self):
        topo = _two_node_topo(n_layers=32)
        solver = ConstraintSolver()
        solver.solve(topo)
        lats = solver.estimate_latency_ms(topo)
        assert set(lats.keys()) == {"n0", "n1"}
        for v in lats.values():
            assert v > 0

    def test_imbalance_ratio_balanced(self):
        solver = ConstraintSolver()
        ratio = solver.imbalance_ratio({"n0": 10.0, "n1": 10.0})
        assert ratio == pytest.approx(1.0)

    def test_imbalance_ratio_unbalanced(self):
        solver = ConstraintSolver()
        ratio = solver.imbalance_ratio({"n0": 20.0, "n1": 5.0})
        assert ratio == pytest.approx(4.0, rel=0.01)

    def test_solve_topology_convenience(self):
        topo = solve_topology(
            "my-model", 32,
            [
                {"node_id": "a", "host": "127.0.0.1", "gpu_memory_mb": 8192},
                {"node_id": "b", "host": "127.0.0.1", "gpu_memory_mb": 8192},
            ],
            layer_memory_mb=100,
        )
        assert topo.shard_map, "shard_map should be populated by solve_topology"
        total = sum(e - s for s, e in topo.shard_map.values())
        assert total == 32


# ---------------------------------------------------------------------------
# get_linear_topology (backward compat)
# ---------------------------------------------------------------------------

class TestGetLinearTopology:
    def test_shards_equal_nodes(self):
        topo = _three_node_topo(n_layers=32)
        ConstraintSolver(SolverConfig(layer_memory_mb=100)).solve(topo)
        linear = topo.get_linear_topology()
        assert len(linear.shards) == 3

    def test_linear_layers_sum(self):
        topo = _two_node_topo(n_layers=32)
        ConstraintSolver(SolverConfig(layer_memory_mb=100)).solve(topo)
        linear = topo.get_linear_topology()
        total = sum(s.end_layer - s.start_layer for s in linear.shards)
        assert total == 32

    def test_linear_pipeline_order_matches_hypergraph(self):
        topo = _three_node_topo(n_layers=32)
        ConstraintSolver(SolverConfig(layer_memory_mb=100)).solve(topo)
        linear = topo.get_linear_topology()
        hyper_order = [n.node_id for n in topo.get_pipeline_order()]
        linear_order = [s.node_id for s in linear.shards]
        assert linear_order == hyper_order


# ---------------------------------------------------------------------------
# AdaptiveScheduler
# ---------------------------------------------------------------------------

class TestAdaptiveScheduler:
    def _make_scheduler(self, imbalance=0.35, interval=10, cooldown=0.0) -> AdaptiveScheduler:
        topo = _two_node_topo(n_layers=32)
        ConstraintSolver(SolverConfig(layer_memory_mb=100)).solve(topo)
        return AdaptiveScheduler(
            topo,
            imbalance_threshold=imbalance,
            check_interval=interval,
            cooldown_s=cooldown,
        )

    def test_no_rebalance_without_samples(self):
        sched = self._make_scheduler()
        assert not sched.should_rebalance()

    def test_no_rebalance_when_balanced(self):
        sched = self._make_scheduler(interval=5)
        for _ in range(6):
            sched.record_latency("n0", 10.0)
            sched.record_latency("n1", 10.0)
        assert not sched.should_rebalance()

    def test_rebalance_triggered_on_imbalance(self):
        sched = self._make_scheduler(interval=5, cooldown=0.0)
        # n0 is 5× slower than n1 → well above 35 % threshold
        for _ in range(6):
            sched.record_latency("n0", 50.0)
            sched.record_latency("n1", 10.0)
        assert sched.should_rebalance()

    def test_cooldown_prevents_immediate_second_rebalance(self):
        sched = self._make_scheduler(interval=5, cooldown=60.0)
        for _ in range(6):
            sched.record_latency("n0", 50.0)
            sched.record_latency("n1", 10.0)
        sched.should_rebalance()              # first check triggers
        sched._last_rebalance = time.monotonic()  # simulate just rebalanced
        for _ in range(6):
            sched.record_latency("n0", 50.0)
            sched.record_latency("n1", 10.0)
        assert not sched.should_rebalance()   # cooldown still active

    def test_propose_rebalance_returns_valid_topology(self):
        sched = self._make_scheduler(interval=5, cooldown=0.0)
        for _ in range(6):
            sched.record_latency("n0", 80.0)   # n0 is very slow
            sched.record_latency("n1", 10.0)
        sched.should_rebalance()               # consume samples
        new_topo = sched.propose_rebalance()

        total = sum(e - s for s, e in new_topo.shard_map.values())
        assert total == 32, "All layers must still be assigned"

    def test_propose_rebalance_slow_node_gets_fewer_layers(self):
        sched = self._make_scheduler(interval=5, cooldown=0.0)
        original_n0_layers = (
            sched.topology.shard_map["n0"][1] - sched.topology.shard_map["n0"][0]
        )
        # Make n0 very slow relative to n1
        for _ in range(6):
            sched.record_latency("n0", 200.0)
            sched.record_latency("n1", 10.0)
        sched.should_rebalance()
        new_topo = sched.propose_rebalance()

        new_n0_layers = new_topo.shard_map["n0"][1] - new_topo.shard_map["n0"][0]
        assert new_n0_layers <= original_n0_layers, (
            "Slow node n0 should get <= layers after rebalance"
        )

    def test_rebalance_count_increments(self):
        sched = self._make_scheduler(interval=5, cooldown=0.0)
        assert sched.rebalance_count == 0
        for _ in range(6):
            sched.record_latency("n0", 80.0)
            sched.record_latency("n1", 10.0)
        sched.should_rebalance()
        sched.propose_rebalance()
        assert sched.rebalance_count == 1

    def test_record_total_latency_distributes_proportionally(self):
        sched = self._make_scheduler()
        sched.record_total_latency(100.0)
        # n0 has 16 layers, n1 has 16 layers → each gets 50 ms
        for nid in ["n0", "n1"]:
            m = sched._metrics[nid]
            assert len(m.latency_samples) == 1
            assert m.latency_samples[0] == pytest.approx(50.0, rel=0.05)

    def test_record_kv_hit_rate(self):
        sched = self._make_scheduler()
        sched.record_kv("n0", hit=True)
        sched.record_kv("n0", hit=True)
        sched.record_kv("n0", hit=False)
        m = sched._metrics["n0"]
        assert m.kv_hit_rate == pytest.approx(2 / 3, rel=0.01)

    def test_metrics_summary_keys(self):
        sched = self._make_scheduler()
        summary = sched.metrics_summary()
        assert set(summary.keys()) == {"n0", "n1"}
        for v in summary.values():
            assert "avg_latency_ms" in v
            assert "kv_hit_rate" in v

    def test_shard_metrics_p95(self):
        m = ShardMetrics(node_id="n0")
        for i in range(100):
            m.latency_samples.append(float(i))  # 0..99
        # p95 should be around 94–95
        assert m.p95_latency_ms >= 93.0


# ---------------------------------------------------------------------------
# Integration: build → solve → schedule → propose
# ---------------------------------------------------------------------------

class TestTopologyPersistence:
    def test_to_dict_roundtrip(self):
        topo = _two_node_topo(n_layers=32)
        ConstraintSolver(SolverConfig(layer_memory_mb=100)).solve(topo)
        d = topo.to_dict()
        restored = HypergraphTopology.from_dict(d)
        assert restored.model_id == topo.model_id
        assert restored.n_layers == topo.n_layers
        assert restored.shard_map == topo.shard_map
        assert set(restored.nodes.keys()) == set(topo.nodes.keys())
        assert set(restored.edges.keys()) == set(topo.edges.keys())

    def test_save_load_roundtrip(self, tmp_path):
        topo = _three_node_topo(n_layers=32)
        ConstraintSolver(SolverConfig(layer_memory_mb=100)).solve(topo)
        path = str(tmp_path / "topo.json")
        topo.save(path)
        loaded = HypergraphTopology.load(path)
        assert loaded.shard_map == topo.shard_map
        assert loaded.model_id == topo.model_id

    def test_dict_preserves_nvlink(self):
        topo = _two_node_topo(nvlink=True)
        d = topo.to_dict()
        restored = HypergraphTopology.from_dict(d)
        assert restored.nodes["n0"].nvlink is True

    def test_dict_preserves_bandwidth(self):
        topo = build_hypergraph_topology("m", 32, [
            {"node_id": "a", "host": "127.0.0.1", "gpu_memory_mb": 8192,
             "bandwidth_gbps": 400.0},
            {"node_id": "b", "host": "127.0.0.1", "gpu_memory_mb": 8192},
        ])
        d = topo.to_dict()
        restored = HypergraphTopology.from_dict(d)
        assert restored.nodes["a"].bandwidth_gbps == 400.0


class TestEndToEnd:
    def test_full_pipeline(self):
        """Build topology, solve, run scheduler, propose rebalance."""
        topo = build_hypergraph_topology(
            "e2e-model",
            32,
            [
                {"node_id": "fast", "host": "127.0.0.1", "gpu_memory_mb": 16384,
                 "bandwidth_gbps": 200, "nvlink": True},
                {"node_id": "slow", "host": "127.0.0.1", "gpu_memory_mb": 8192,
                 "bandwidth_gbps": 16},
            ],
        )
        solver = ConstraintSolver(SolverConfig(layer_memory_mb=100))
        solver.solve(topo)

        # Fast node (NVLink, more VRAM) should get more layers
        fast_layers = topo.shard_map["fast"][1] - topo.shard_map["fast"][0]
        slow_layers = topo.shard_map["slow"][1] - topo.shard_map["slow"][0]
        assert fast_layers >= slow_layers

        # Simulate "slow" node being much slower in practice
        sched = AdaptiveScheduler(topo, check_interval=5, cooldown_s=0.0)
        for _ in range(6):
            sched.record_latency("fast", 5.0)
            sched.record_latency("slow", 50.0)
        assert sched.should_rebalance()

        new_topo = sched.propose_rebalance()
        assert sum(e - s for s, e in new_topo.shard_map.values()) == 32

        # get_linear_topology backward compat
        linear = new_topo.get_linear_topology()
        assert len(linear.shards) == 2
