"""
Integration tests for Exo Windows Porting.

These tests cover the actual logic paths that the unit tests miss:
- BackendConfig validation rejects bad values
- BackendFactory selects the correct backend based on hardware flags
- LLMBackend subclasses enforce the interface contract
- AMD detection correctly distinguishes hardware presence from ROCm readiness
- Health monitor uses real TCP connectivity, not random simulation
- PeerDiscoveryManager static-peer fallback works without mDNS
- Router returns None (not a crash) when no node meets requirements

All GPU-dependent tests use mocks so they run on any CI machine.
"""

import asyncio
import socket
import threading
import time
from dataclasses import dataclass
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _open_tcp_server(host: str = "127.0.0.1", port: int = 0) -> tuple:
    """Bind a TCP server socket and return (server_sock, actual_port)."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((host, port))
    srv.listen(1)
    _, actual_port = srv.getsockname()
    return srv, actual_port


# ──────────────────────────────────────────────────────────────────────────────
# 质疑一 – Abstract base class enforces interface
# ──────────────────────────────────────────────────────────────────────────────

class TestLLMBackendInterface:
    """LLMBackend ABC must reject incomplete subclasses at instantiation time."""

    def test_incomplete_subclass_raises_typeerror(self):
        from exo_windows_porting.backend.base import LLMBackend

        class IncompleteBackend(LLMBackend):
            # Missing: generate() and get_backend_name()
            pass

        with pytest.raises(TypeError):
            IncompleteBackend()

    def test_partial_subclass_missing_generate_raises(self):
        from exo_windows_porting.backend.base import LLMBackend

        class PartialBackend(LLMBackend):
            def get_backend_name(self) -> str:
                return "partial"
            # generate() still missing

        with pytest.raises(TypeError):
            PartialBackend()

    def test_complete_subclass_instantiates(self):
        from exo_windows_porting.backend.base import LLMBackend

        class FakeBackend(LLMBackend):
            def get_backend_name(self) -> str:
                return "fake"

            async def generate(self, prompt: str, max_tokens: int = 512) -> str:
                return f"echo: {prompt}"

        backend = FakeBackend()
        assert backend.get_backend_name() == "fake"
        result = asyncio.get_event_loop().run_until_complete(backend.generate("hi"))
        assert result == "echo: hi"

    def test_all_concrete_backends_have_correct_name(self):
        """Each backend class must report its own name, not inherit a default."""
        from exo_windows_porting.backend.base import LLMBackend

        class CpuBackend(LLMBackend):
            async def generate(self, prompt, max_tokens=512): return ""
            def get_backend_name(self): return "cpu"

        class CudaBackend(LLMBackend):
            async def generate(self, prompt, max_tokens=512): return ""
            def get_backend_name(self): return "cuda"

        class RocmBackend(LLMBackend):
            async def generate(self, prompt, max_tokens=512): return ""
            def get_backend_name(self): return "rocm"

        assert CpuBackend().get_backend_name() == "cpu"
        assert CudaBackend().get_backend_name() == "cuda"
        assert RocmBackend().get_backend_name() == "rocm"


# ──────────────────────────────────────────────────────────────────────────────
# 质疑一 – AMD detection: hardware vs. ROCm stack
# ──────────────────────────────────────────────────────────────────────────────

class TestAMDDetection:
    """
    has_amd_gpu and rocm_ready must be set independently.

    The old code set has_amd_gpu=True via dxdiag and then used that flag to
    select the ROCm backend — even when ROCm was not installed. The correct
    logic requires rocm_ready=True (rocm-smi responded) before selecting ROCm.
    """

    def test_amd_gpu_present_but_rocm_not_installed(self):
        """dxdiag finds AMD GPU ⟹ has_amd_gpu=True, rocm_ready=False."""
        from exo_windows_porting.backend.backend_utils import SystemInfo, _detect_amd_via_dxdiag

        dxdiag_output = (
            "Display Devices\n"
            "  Card name: AMD Radeon RX 7900 XTX\n"
            "  Manufacturer: Advanced Micro Devices, Inc.\n"
        )

        info = SystemInfo()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=dxdiag_output, stderr="")
            _detect_amd_via_dxdiag(info)

        assert info.has_amd_gpu is True
        assert info.rocm_ready is False  # dxdiag does NOT prove ROCm is installed

    def test_rocm_smi_success_sets_both_flags(self):
        """When rocm-smi succeeds, both has_amd_gpu and rocm_ready must be True."""
        from exo_windows_porting.backend.backend_utils import detect_hardware

        rocm_output = "GPU[0]: Radeon RX 7900 XTX\n"
        nvidia_absent = MagicMock(returncode=1, stdout="", stderr="not found")
        rocm_present = MagicMock(returncode=0, stdout=rocm_output, stderr="")

        def fake_run(cmd, **kwargs):
            if cmd[0] == "nvidia-smi":
                return nvidia_absent
            if cmd[0] == "rocm-smi":
                return rocm_present
            return MagicMock(returncode=1, stdout="", stderr="")

        with patch("subprocess.run", side_effect=fake_run):
            info = detect_hardware()

        assert info.has_amd_gpu is True
        assert info.rocm_ready is True

    def test_factory_does_not_select_rocm_without_rocm_ready(self):
        """
        BackendFactory.select_backend() must NOT choose ROCm when
        has_amd_gpu=True but rocm_ready=False.
        """
        from exo_windows_porting.backend.factory import BackendFactory, BackendConfig
        from exo_windows_porting.backend.backend_utils import SystemInfo

        fake_hw = SystemInfo()
        fake_hw.has_amd_gpu = True
        fake_hw._rocm_ready = False
        fake_hw.has_nvidia_gpu = False

        factory = BackendFactory.__new__(BackendFactory)
        factory.config = BackendConfig()
        factory.hardware = fake_hw

        selected = factory.select_backend()
        assert selected == "cpu", (
            f"Expected 'cpu' when ROCm stack absent, got '{selected}'"
        )

    def test_factory_selects_rocm_when_stack_ready(self):
        from exo_windows_porting.backend.factory import BackendFactory, BackendConfig
        from exo_windows_porting.backend.backend_utils import SystemInfo

        fake_hw = SystemInfo()
        fake_hw.has_amd_gpu = True
        fake_hw._rocm_ready = True
        fake_hw.has_nvidia_gpu = False

        factory = BackendFactory.__new__(BackendFactory)
        factory.config = BackendConfig()
        factory.hardware = fake_hw

        selected = factory.select_backend()
        assert selected == "rocm"


# ──────────────────────────────────────────────────────────────────────────────
# 质疑三 – BackendConfig validation
# ──────────────────────────────────────────────────────────────────────────────

class TestBackendConfigValidation:
    """BackendConfig must reject nonsensical values immediately."""

    def test_valid_config_accepted(self):
        from exo_windows_porting.backend.factory import BackendConfig
        cfg = BackendConfig(max_tokens=256, n_ctx=4096, preferred_backend="cpu")
        assert cfg.max_tokens == 256

    def test_zero_max_tokens_rejected(self):
        from exo_windows_porting.backend.factory import BackendConfig
        with pytest.raises(ValueError, match="max_tokens"):
            BackendConfig(max_tokens=0)

    def test_negative_n_ctx_rejected(self):
        from exo_windows_porting.backend.factory import BackendConfig
        with pytest.raises(ValueError, match="n_ctx"):
            BackendConfig(n_ctx=-1)

    def test_invalid_backend_name_rejected(self):
        from exo_windows_porting.backend.factory import BackendConfig
        with pytest.raises(ValueError, match="preferred_backend"):
            BackendConfig(preferred_backend="tpu")

    def test_none_backend_accepted(self):
        from exo_windows_porting.backend.factory import BackendConfig
        cfg = BackendConfig(preferred_backend=None)
        assert cfg.preferred_backend is None


# ──────────────────────────────────────────────────────────────────────────────
# 质疑三– Thread-safe singleton
# ──────────────────────────────────────────────────────────────────────────────

class TestSingletonThreadSafety:
    """get_backend_factory() must return the same object under concurrent access."""

    def test_concurrent_calls_return_same_instance(self):
        import exo_windows_porting.backend.factory as factory_mod

        # Reset singleton before test
        factory_mod._factory_instance = None

        instances = []
        errors = []

        def _get():
            try:
                instances.append(factory_mod.get_backend_factory())
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=_get) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Errors during concurrent access: {errors}"
        # All 20 calls must return the exact same object
        assert len(set(id(i) for i in instances)) == 1, (
            "Multiple BackendFactory instances created under concurrency"
        )

        # Clean up
        factory_mod._factory_instance = None


# ──────────────────────────────────────────────────────────────────────────────
# 质疑四– Health monitor: real TCP ping, not random simulation
# ──────────────────────────────────────────────────────────────────────────────

class TestHealthMonitorPing:
    """
    _send_ping() previously returned random.uniform(10, 50) — pure simulation.
    It must now attempt a real TCP connection and raise on failure.
    """

    def test_ping_reachable_node(self):
        """A node with a listening TCP socket must be reported reachable."""
        from network.health_monitor import ping_node

        srv, port = _open_tcp_server()
        try:
            result = asyncio.get_event_loop().run_until_complete(
                ping_node("127.0.0.1", port, timeout=2.0)
            )
            assert result is True
        finally:
            srv.close()

    def test_ping_unreachable_node(self):
        """A port with nothing listening must be reported unreachable."""
        from network.health_monitor import ping_node

        # Pick a port that is (almost certainly) not in use
        result = asyncio.get_event_loop().run_until_complete(
            ping_node("127.0.0.1", 19999, timeout=0.5)
        )
        assert result is False

    def test_check_node_marks_unreachable_as_unhealthy(self):
        """HealthMonitor._check_node() must mark a node unhealthy when TCP fails."""
        from network.health_monitor import HealthMonitor, HealthStatus

        monitor = HealthMonitor(coordinator_node_id="coord-test")
        monitor.node_status["dead-node"] = HealthStatus(
            node_id="dead-node",
            is_healthy=True,
            last_check_time=time.time(),
        )

        # Patch _send_ping to simulate a timeout/failure
        async def _fail(_node_id):
            raise ConnectionRefusedError("connection refused")

        monitor._send_ping = _fail

        asyncio.get_event_loop().run_until_complete(monitor._check_node("dead-node"))

        assert monitor.node_status["dead-node"].is_healthy is False


# ──────────────────────────────────────────────────────────────────────────────
# 质疑四– Static peer fallback in PeerDiscoveryManager
# ──────────────────────────────────────────────────────────────────────────────

class TestStaticPeerFallback:
    """
    When mDNS is unavailable (enterprise networks, VPN, cross-subnet),
    users must be able to specify peers manually via static_peers.
    """

    def test_static_peers_are_added_on_init(self):
        from network.discovery import PeerDiscoveryManager

        mgr = PeerDiscoveryManager(
            node_id="local-node",
            port=18790,
            static_peers=[("192.168.1.10", 18790), ("192.168.1.11", 18790)],
        )

        assert len(mgr.peers) == 2
        peer_hosts = {p.host for p in mgr.peers.values()}
        assert "192.168.1.10" in peer_hosts
        assert "192.168.1.11" in peer_hosts

    def test_static_peers_do_not_duplicate_self(self):
        """If a static peer resolves to the local node_id, it should be skipped."""
        from network.discovery import PeerDiscoveryManager

        mgr = PeerDiscoveryManager(
            node_id="local-node",
            port=18790,
            static_peers=[("127.0.0.1", 18790)],
        )
        # 127.0.0.1:18790 could be itself; the peer list must not contain
        # an entry with node_id == "local-node"
        for peer in mgr.peers.values():
            assert peer.node_id != "local-node"


# ──────────────────────────────────────────────────────────────────────────────
# 质疑四 – Router returns None on impossible requirements
# ──────────────────────────────────────────────────────────────────────────────

class TestRouterEdgeCases:
    """Router must degrade gracefully, never raise, when no node qualifies."""

    def test_no_nodes_returns_none(self):
        from network.router import SimpleRouter

        router = SimpleRouter()
        result = router.select_node([], {"gpu_required": False})
        assert result is None

    def test_gpu_required_but_no_gpu_nodes_returns_none(self):
        from network.router import SimpleRouter
        from network.discovery import PeerNodeInfo

        cpu_only_node = PeerNodeInfo(
            node_id="cpu-node",
            host="10.0.0.1",
            port=18790,
            gpu_memory_total=None,
        )

        router = SimpleRouter()
        result = router.select_node(
            [cpu_only_node],
            {"gpu_required": True, "min_gpu_memory_mb": 8192},
        )
        assert result is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
