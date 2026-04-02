"""Unit tests for P2P discovery module - Based on actual implementation."""
import pytest


class TestNodeInfo:
    """Test cases for NodeInfo data class."""

    def test_node_info_creation(self):
        """Test creating a NodeInfo instance."""
        from network.discovery import NodeInfo
        
        node = NodeInfo(
            node_id="node-001",
            host="192.168.1.100",
            port=8080,
            gpu_model="RX 7900 XTX",
            gpu_memory_total=32000,
            cpu_cores=16
        )
        
        assert node.node_id == "node-001"
        assert node.host == "192.168.1.100"
        assert node.port == 8080
        assert node.gpu_model == "RX 7900 XTX"

    def test_node_info_default_values(self):
        """Test NodeInfo with default values."""
        from network.discovery import NodeInfo
        
        node = NodeInfo(
            node_id="node-002",
            host="10.0.0.5",
            port=9000
        )
        
        assert node.gpu_model is None
        assert node.status == "initializing"


class TestPeerDiscoveryManager:
    """Test cases for PeerDiscoveryManager class."""

    def test_initialization(self):
        """Test that discovery manager initializes correctly."""
        from network.discovery import PeerDiscoveryManager
        
        manager = PeerDiscoveryManager(
            node_id="test-node-001",
            port=8080,
            host="127.0.0.1"
        )
        
        assert manager.node_id == "test-node-001"
        assert manager.port == 8080


class TestLoadBalancer:
    """Test cases for LoadBalancer class."""

    def test_load_balancer_creation(self):
        """Test creating a load balancer."""
        from network.router import LoadBalancer
        
        lb = LoadBalancer()
        
        assert lb is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
