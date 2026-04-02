"""Unit tests for health monitor module."""
import pytest


class TestHealthMonitor:
    """Test cases for HealthMonitor class."""

    def test_health_monitor_initialization(self):
        """Test health monitor initialization."""
        from network.health_monitor import HealthMonitor
        
        monitor = HealthMonitor(coordinator_node_id="coordinator-001")
        
        # Verify basic properties exist
        assert hasattr(monitor, 'coordinator_node_id')


class TestHealthStatus:
    """Test cases for HealthStatus data class."""

    def test_health_status_creation(self):
        """Test creating a HealthStatus instance."""
        from network.health_monitor import HealthStatus
        
        status = HealthStatus(
            node_id="node-001",
            is_healthy=True,
            last_check_time=1712000000.0,
            cpu_usage_percent=45.2,
            gpu_memory_used_mb=8192,
            ping_latency_ms=12,
            error_message=None
        )
        
        assert status.node_id == "node-001"
        assert status.is_healthy is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
