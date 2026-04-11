"""
Tests for the FastAPI dashboard server endpoints.

Uses FastAPI's synchronous TestClient (backed by httpx) — no running event
loop required from the test side.  Async lifespan is handled internally.
"""
from __future__ import annotations

import os
import pytest
from fastapi.testclient import TestClient

import exo_windows_porting.dashboard.server as _srv
from exo_windows_porting.dashboard.server import app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_server_state():
    """Reset global server state between tests."""
    _srv._dist_engine = None
    _srv._dist_scheduler = None
    _srv._dist_hypergraph = None
    _srv._model_cache.clear()
    _srv._inference_queue.clear()
    _srv._cluster_status.total_nodes = 0
    _srv._cluster_status.active_nodes = 0
    _srv._cluster_status.total_gpu_memory_gb = 0.0
    _srv._EXO_API_KEY = None   # auth disabled by default
    yield
    # teardown: same reset
    _srv._dist_engine = None
    _srv._dist_scheduler = None
    _srv._dist_hypergraph = None
    _srv._model_cache.clear()
    _srv._EXO_API_KEY = None


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def auth_client():
    """Client fixture where EXO_API_KEY is set to 'test-key'."""
    _srv._EXO_API_KEY = "test-key"
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# Public endpoints (no auth)
# ---------------------------------------------------------------------------

class TestPublicEndpoints:
    def test_root_returns_200(self, client):
        r = client.get("/")
        assert r.status_code == 200
        assert r.json()["status"] == "healthy"

    def test_root_contains_dashboard_link(self, client):
        r = client.get("/")
        assert "/ui" in r.json().get("dashboard", "")

    def test_health_returns_ok(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"
        assert "timestamp" in data
        assert "uptime_seconds" in data

    def test_health_uptime_non_negative(self, client):
        r = client.get("/health")
        assert r.json()["uptime_seconds"] >= 0


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

class TestAuthentication:
    def test_no_auth_required_when_key_not_set(self, client):
        r = client.get("/v1/cluster/status")
        assert r.status_code == 200

    def test_401_when_key_required_and_missing(self, auth_client):
        r = auth_client.get("/v1/cluster/status")
        assert r.status_code == 401

    def test_401_when_wrong_key(self, auth_client):
        r = auth_client.get(
            "/v1/cluster/status",
            headers={"X-API-Key": "wrong-key"},
        )
        assert r.status_code == 401

    def test_200_when_correct_key(self, auth_client):
        r = auth_client.get(
            "/v1/cluster/status",
            headers={"X-API-Key": "test-key"},
        )
        assert r.status_code == 200

    def test_health_always_public(self, auth_client):
        """Health endpoint is public even when auth is enabled."""
        r = auth_client.get("/health")
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# Cluster status
# ---------------------------------------------------------------------------

class TestClusterStatus:
    def test_returns_cluster_status_shape(self, client):
        r = client.get("/v1/cluster/status")
        assert r.status_code == 200
        data = r.json()
        assert "total_nodes" in data
        assert "active_nodes" in data
        assert "total_gpu_memory_gb" in data
        assert "uptime_seconds" in data

    def test_live_data_from_hypergraph(self, client):
        """When a hypergraph is injected, cluster status reflects it."""
        from exo_windows_porting.distributed.hypergraph import build_hypergraph_topology
        from exo_windows_porting.distributed.constraint_solver import ConstraintSolver, SolverConfig

        topo = build_hypergraph_topology("m", 32, [
            {"node_id": "n0", "host": "127.0.0.1", "gpu_memory_mb": 8192},
            {"node_id": "n1", "host": "127.0.0.1", "gpu_memory_mb": 8192},
        ])
        ConstraintSolver(SolverConfig(layer_memory_mb=100)).solve(topo)
        _srv._dist_hypergraph = topo

        r = client.get("/v1/cluster/status")
        assert r.status_code == 200
        data = r.json()
        assert data["total_nodes"] == 2
        assert data["active_nodes"] == 2
        assert data["total_gpu_memory_gb"] == pytest.approx(16.0, rel=0.01)


# ---------------------------------------------------------------------------
# Backend health
# ---------------------------------------------------------------------------

class TestBackends:
    def test_backends_endpoint_returns_expected_keys(self, client):
        r = client.get("/v1/health/backends")
        assert r.status_code == 200
        data = r.json()
        assert "hardware" in data
        assert "available_backends" in data
        assert "selected_backend" in data

    def test_cpu_always_available(self, client):
        r = client.get("/v1/health/backends")
        avail = r.json()["available_backends"]
        assert avail.get("cpu") is True


# ---------------------------------------------------------------------------
# Model cache
# ---------------------------------------------------------------------------

class TestModels:
    def test_empty_model_list(self, client):
        r = client.get("/v1/models")
        assert r.status_code == 200
        assert r.json() == []

    def test_register_model_file_not_found(self, client):
        r = client.post(
            "/v1/models/upload",
            params={"model_path": "/nonexistent/model.gguf", "size_mb": 100},
        )
        assert r.status_code == 404

    def test_get_unknown_model_404(self, client):
        r = client.get("/v1/models/nonexistent/info")
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Distributed topology endpoints
# ---------------------------------------------------------------------------

class TestDistributedTopology:
    def test_topology_inactive_when_no_engine(self, client):
        r = client.get("/v1/distributed/topology")
        assert r.status_code == 200
        data = r.json()
        assert data["active"] is False
        assert data["topology"] is None

    def test_topology_active_when_hypergraph_injected(self, client):
        from exo_windows_porting.distributed.hypergraph import build_hypergraph_topology
        from exo_windows_porting.distributed.constraint_solver import ConstraintSolver, SolverConfig

        topo = build_hypergraph_topology("test-model", 16, [
            {"node_id": "a", "host": "127.0.0.1", "gpu_memory_mb": 4096},
            {"node_id": "b", "host": "127.0.0.1", "gpu_memory_mb": 4096},
        ])
        ConstraintSolver(SolverConfig(layer_memory_mb=50)).solve(topo)
        _srv._dist_hypergraph = topo

        r = client.get("/v1/distributed/topology")
        assert r.status_code == 200
        data = r.json()
        assert data["active"] is True
        t = data["topology"]
        assert t["model_id"] == "test-model"
        assert t["n_layers"] == 16
        assert set(t["nodes"].keys()) == {"a", "b"}
        assert set(t["shard_map"].keys()) == {"a", "b"}
        total = sum(v[1] - v[0] for v in t["shard_map"].values())
        assert total == 16

    def test_topology_shard_map_contiguous(self, client):
        from exo_windows_porting.distributed.hypergraph import build_hypergraph_topology
        from exo_windows_porting.distributed.constraint_solver import ConstraintSolver, SolverConfig

        topo = build_hypergraph_topology("m", 24, [
            {"node_id": "x", "host": "127.0.0.1", "gpu_memory_mb": 8192},
            {"node_id": "y", "host": "127.0.0.1", "gpu_memory_mb": 4096},
            {"node_id": "z", "host": "127.0.0.1", "gpu_memory_mb": 4096},
        ])
        ConstraintSolver(SolverConfig(layer_memory_mb=50)).solve(topo)
        _srv._dist_hypergraph = topo

        r = client.get("/v1/distributed/topology")
        shard_map = r.json()["topology"]["shard_map"]
        # Sort by start and check contiguity
        ranges = sorted(shard_map.values(), key=lambda v: v[0])
        for i in range(len(ranges) - 1):
            assert ranges[i][1] == ranges[i + 1][0]


# ---------------------------------------------------------------------------
# Distributed scheduler metrics
# ---------------------------------------------------------------------------

class TestSchedulerMetrics:
    def test_inactive_when_no_scheduler(self, client):
        r = client.get("/v1/distributed/scheduler/metrics")
        assert r.status_code == 200
        data = r.json()
        assert data["active"] is False
        assert data["metrics"] == {}
        assert data["rebalance_count"] == 0

    def test_active_with_injected_scheduler(self, client):
        from exo_windows_porting.distributed.hypergraph import build_hypergraph_topology
        from exo_windows_porting.distributed.constraint_solver import ConstraintSolver, SolverConfig
        from exo_windows_porting.distributed.adaptive_scheduler import AdaptiveScheduler

        topo = build_hypergraph_topology("m", 16, [
            {"node_id": "p", "host": "127.0.0.1", "gpu_memory_mb": 4096},
            {"node_id": "q", "host": "127.0.0.1", "gpu_memory_mb": 4096},
        ])
        ConstraintSolver(SolverConfig(layer_memory_mb=50)).solve(topo)

        sched = AdaptiveScheduler(topo, check_interval=5, cooldown_s=0.0)
        for _ in range(3):
            sched.record_latency("p", 20.0)
            sched.record_latency("q", 10.0)

        _srv._dist_hypergraph = topo
        _srv._dist_scheduler = sched

        r = client.get("/v1/distributed/scheduler/metrics")
        assert r.status_code == 200
        data = r.json()
        assert data["active"] is True
        assert "p" in data["metrics"]
        assert "q" in data["metrics"]
        assert data["metrics"]["p"]["avg_latency_ms"] == pytest.approx(20.0)
        assert data["rebalance_count"] == 0


# ---------------------------------------------------------------------------
# Distributed setup endpoint (unit-level — no engine start)
# ---------------------------------------------------------------------------

class TestDistributedSetup:
    def test_setup_insufficient_vram_returns_400(self, client):
        """Constraint solver raises ValueError → endpoint returns 400."""
        payload = {
            "model_id": "test-model",
            "n_layers": 100,
            "nodes": [
                {"node_id": "a", "host": "127.0.0.1", "gpu_memory_mb": 100, "port": 29500},
            ],
            "layer_memory_mb": 500,   # 100 MB VRAM, 500 MB/layer → impossible
            "local_mode": False,
            "enable_scheduler": False,
        }
        r = client.post("/v1/distributed/setup", json=payload)
        assert r.status_code == 400
        assert "Insufficient" in r.json()["detail"]

    def test_setup_valid_topology_coordinator_only(self, client):
        """local_mode=False: engine is created but start() is NOT called."""
        payload = {
            "model_id": "test-model",
            "n_layers": 16,
            "nodes": [
                {"node_id": "n0", "host": "127.0.0.1", "gpu_memory_mb": 8192, "port": 29500},
                {"node_id": "n1", "host": "127.0.0.1", "gpu_memory_mb": 8192, "port": 29501},
            ],
            "layer_memory_mb": 100,
            "local_mode": False,
            "enable_scheduler": True,
            "imbalance_threshold": 0.30,
        }
        r = client.post("/v1/distributed/setup", json=payload)
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"
        assert data["scheduler_enabled"] is True
        t = data["topology"]
        assert t["n_layers"] == 16
        total_layers = sum(v[1] - v[0] for v in t["shard_map"].values())
        assert total_layers == 16

    def test_setup_populates_global_state(self, client):
        payload = {
            "model_id": "my-model",
            "n_layers": 8,
            "nodes": [
                {"node_id": "only", "host": "127.0.0.1", "gpu_memory_mb": 16384, "port": 29500},
            ],
            "layer_memory_mb": 100,
            "local_mode": False,
            "enable_scheduler": True,
        }
        client.post("/v1/distributed/setup", json=payload)
        assert _srv._dist_engine is not None
        assert _srv._dist_scheduler is not None
        assert _srv._dist_hypergraph is not None

    def test_stop_clears_global_state(self, client):
        # Setup first
        payload = {
            "model_id": "m",
            "n_layers": 8,
            "nodes": [{"node_id": "x", "host": "127.0.0.1", "gpu_memory_mb": 8192, "port": 29500}],
            "layer_memory_mb": 100,
            "local_mode": False,
            "enable_scheduler": False,
        }
        client.post("/v1/distributed/setup", json=payload)
        assert _srv._dist_engine is not None

        r = client.post("/v1/distributed/stop")
        assert r.status_code == 200
        assert r.json()["status"] == "stopped"
        assert _srv._dist_engine is None
        assert _srv._dist_hypergraph is None

    def test_stop_when_not_running(self, client):
        r = client.post("/v1/distributed/stop")
        assert r.status_code == 200
        assert r.json()["status"] == "not_running"

    def test_second_setup_replaces_first(self, client):
        def _setup(model_id):
            return client.post("/v1/distributed/setup", json={
                "model_id": model_id, "n_layers": 8,
                "nodes": [{"node_id": "a", "host": "127.0.0.1",
                            "gpu_memory_mb": 8192, "port": 29500}],
                "layer_memory_mb": 100, "local_mode": False,
                "enable_scheduler": False,
            })

        _setup("model-A")
        engine_a = _srv._dist_engine

        _setup("model-B")
        engine_b = _srv._dist_engine

        assert engine_b is not engine_a
        assert _srv._dist_hypergraph.model_id == "model-B"
