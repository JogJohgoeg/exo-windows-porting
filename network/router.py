"""
Simple Router Module for Exo Windows Porting

This module provides load balancing and task routing capabilities.

Author: Exo Windows Porting Team
License: MIT
"""

from typing import List, Dict, Optional, Callable
from dataclasses import dataclass
import random


@dataclass
class TaskRequest:
    """任务请求信息"""
    
    task_id: str
    model_path: str
    prompt: str
    
    # GPU 需求
    gpu_required: bool = False
    min_gpu_memory_mb: int = 4096  # 最小显存需求
    max_gpu_memory_mb: Optional[int] = None  # 最大允许显存使用量
    
    # 性能要求
    latency_sensitive: bool = False  # 是否对延迟敏感
    throughput_priority: bool = False  # 是否优先考虑吞吐量


@dataclass 
class RoutingResult:
    """路由结果"""
    
    selected_node_id: str
    routing_strategy: str
    confidence_score: float  # 0.0-1.0, 表示选择的置信度
    
    # 可选：备选节点列表 (用于故障转移)
    backup_nodes: List[str] = None


class SimpleRouter:
    """
    Exo Windows Porting 的默认路由器实现。
    
    支持多种负载均衡策略：
    - Random: 随机选择节点
    - LeastLoaded: 选择负载最低的节点 (简化版)
    - GpuMemoryAvailable: 选择可用 GPU 显存最多的节点
    
    Usage:
        router = SimpleRouter(strategy="gpu_memory_available")
        result = router.route(task_request, available_nodes)
        
        if result.selected_node_id:
            print(f"Selected node: {result.selected_node_id}")
        else:
            print("No suitable nodes found!")
    """
    
    STRATEGIES = ["random", "least_loaded", "gpu_memory_available"]
    
    def __init__(self, strategy: str = "gpu_memory_available"):
        """
        Initialize the router.
        
        Args:
            strategy: Load balancing strategy (default: gpu_memory_available)
            
        Raises:
            ValueError: If invalid strategy is provided
        """
        
        if strategy not in self.STRATEGIES:
            raise ValueError(
                f"Invalid strategy '{strategy}'. "
                f"Must be one of: {self.STRATEGIES}"
            )
        
        self.strategy = strategy
    
    def route(self, task_request: TaskRequest, nodes: List[Dict]) -> RoutingResult:
        """
        Route a task request to the best available node.
        
        Args:
            task_request: The task request to route
            nodes: List of available nodes with their capabilities
            
        Returns:
            RoutingResult containing the selected node and metadata
            
        Example node format:
            {
                "node_id": "exo-node-001",
                "status": "ready",  # ready, busy, offline
                "gpu_model": "AMD Radeon RX 7900 XTX",
                "gpu_memory_total": 24576,  # MB
                "cpu_cores": 16,
                "current_load": 0.3,  # 0.0-1.0
                "models_cached": ["model-1.gguf", "model-2.gguf"]
            }
        """
        
        if not nodes:
            return RoutingResult(
                selected_node_id=None,
                routing_strategy=self.strategy,
                confidence_score=0.0,
                backup_nodes=[]
            )
        
        # Filter nodes based on task requirements
        filtered = self._filter_nodes(nodes, task_request)
        
        if not filtered:
            return RoutingResult(
                selected_node_id=None,
                routing_strategy=self.strategy,
                confidence_score=0.0,
                backup_nodes=[n["node_id"] for n in nodes]  # Return all as backups
            )
        
        # Select best node based on strategy
        selected = self._select_best_node(filtered, task_request)
        
        return RoutingResult(
            selected_node_id=selected["node_id"],
            routing_strategy=self.strategy,
            confidence_score=self._calculate_confidence(selected, filtered),
            backup_nodes=[n["node_id"] for n in filtered if n["node_id"] != selected["node_id"]]
        )
    
    def _filter_nodes(self, nodes: List[Dict], task_request: TaskRequest) -> List[Dict]:
        """Filter nodes based on task requirements"""
        
        filtered = []
        
        for node in nodes:
            # Skip offline or busy nodes (unless we have no choice)
            if node.get("status") == "offline":
                continue
            
            if task_request.latency_sensitive and node.get("current_load", 0) > 0.8:
                continue
            
            # Check GPU requirements
            if task_request.gpu_required:
                gpu_memory = node.get("gpu_memory_total", 0)
                
                if not gpu_memory or gpu_memory < task_request.min_gpu_memory_mb:
                    continue
                
                if task_request.max_gpu_memory_mb and \
                   gpu_memory > task_request.max_gpu_memory_mb:
                    # Skip if GPU memory exceeds max allowed (cost optimization)
                    continue
            
            filtered.append(node)
        
        return filtered
    
    def _select_best_node(self, nodes: List[Dict], task_request: TaskRequest) -> Dict:
        """Select the best node based on current strategy
        
        Args:
            nodes: List of candidate nodes (must not be empty)
            task_request: The task request being routed
            
        Returns:
            Best matching node from the list
            
        Raises:
            ValueError: If nodes list is empty
        """
        
        if not nodes:
            raise ValueError("Cannot select best node from empty list")
        
        if self.strategy == "random":
            return random.choice(nodes)
        
        elif self.strategy == "least_loaded":
            # Select node with lowest load
            sorted_nodes = sorted(
                nodes, 
                key=lambda n: n.get("current_load", 0)
            )
            return sorted_nodes[0] if sorted_nodes else nodes[0]
        
        elif self.strategy == "gpu_memory_available":
            # Select node with most available GPU memory
            sorted_nodes = sorted(
                nodes, 
                key=lambda n: n.get("gpu_memory_total", 0), 
                reverse=True
            )
            return sorted_nodes[0] if sorted_nodes else nodes[0]
        
        else:
            # Fallback to random with safety check
            if not nodes:
                raise ValueError("No nodes available for selection")
            return random.choice(nodes)
    
    def _calculate_confidence(self, selected: Dict, all_candidates: List[Dict]) -> float:
        """Calculate confidence score for the selection"""
        
        if len(all_candidates) == 1:
            return 1.0
        
        # Calculate how much better the selected node is compared to others
        selected_value = self._get_selection_value(selected)
        other_values = [self._get_selection_value(node) for node in all_candidates]
        
        max_other = max(other_values)
        min_other = min(other_values)
        
        if max_other == min_other:
            return 0.5  # All nodes are equal
        
        # Confidence based on how much better the selected node is
        confidence = (selected_value - min_other) / (max_other - min_other + 1e-6)
        return min(1.0, max(0.0, confidence))
    
    def _get_selection_value(self, node: Dict) -> float:
        """Get a numeric value for the node based on current strategy
        
        Args:
            node: Node information dictionary
            
        Returns:
            Numeric value representing node suitability
        """
        
        if self.strategy == "least_loaded":
            # Higher is better (inverse of load)
            return 1.0 - node.get("current_load", 0)
        
        elif self.strategy == "gpu_memory_available":
            # Higher is better (more GPU memory)
            return float(node.get("gpu_memory_total", 0))
        
        else:  # random
            # For random strategy, use a deterministic value to avoid confidence calculation issues
            # Use node_id hash for consistency across calls
            import hashlib
            node_id = node.get("node_id", "")
            hash_value = int(hashlib.md5(node_id.encode()).hexdigest(), 16)
            return (hash_value % 1000) / 1000.0  # Return value between 0 and 1

    def select_node(self, nodes, requirements: dict):
        """
        Simplified routing interface used by the discovery/health stack.

        Args:
            nodes: List of node dicts or PeerNodeInfo objects.
            requirements: Dict with optional keys ``gpu_required`` (bool) and
                          ``min_gpu_memory_mb`` (int).

        Returns:
            The selected node (same type as input elements) or ``None`` if no
            node satisfies the requirements.
        """
        if not nodes:
            return None

        # Normalise PeerNodeInfo objects to dicts so _filter_nodes can work.
        def _to_dict(n):
            if isinstance(n, dict):
                return n
            try:
                from dataclasses import asdict as _asdict
                return _asdict(n)
            except Exception:
                return vars(n)

        node_dicts = [_to_dict(n) for n in nodes]

        task = TaskRequest(
            task_id="__select_node__",
            model_path="",
            prompt="",
            gpu_required=requirements.get("gpu_required", False),
            min_gpu_memory_mb=requirements.get("min_gpu_memory_mb", 0),
        )

        result = self.route(task, node_dicts)
        if result.selected_node_id is None:
            return None

        # Return the original node object (not the normalised dict copy).
        for original, d in zip(nodes, node_dicts):
            if d.get("node_id") == result.selected_node_id:
                return original
        return None


class LoadBalancer:
    """
    Advanced load balancer with health monitoring and automatic failover.
    
    Features:
    - Health checks for all nodes
    - Automatic failover when nodes become unavailable
    - Dynamic re-routing based on real-time metrics
    
    Usage:
        lb = LoadBalancer(health_check_interval=30)
        
        # Add nodes
        await lb.add_node("node-1", {"gpu_memory_total": 24576})
        await lb.add_node("node-2", {"gpu_memory_total": 8192})
        
        # Route a task
        result = await lb.route(task_request)
        
        # Remove node (if failed or decommissioned)
        await lb.remove_node("node-3")
    """
    
    def __init__(self, router: Optional[SimpleRouter] = None):
        self.router = router or SimpleRouter()
        self.nodes: Dict[str, Dict] = {}
        
        # Health check configuration
        self.health_check_interval = 30  # seconds
        self.healthy_threshold = 3  # consecutive successful checks before marking healthy
        self.unhealthy_threshold = 2  # consecutive failed checks before marking unhealthy
        
        # Node health status
        self.node_health: Dict[str, int] = {}  # node_id -> consecutive successes
        self.node_failures: Dict[str, int] = {}  # node_id -> consecutive failures
    
    async def add_node(self, node_id: str, capabilities: Dict) -> None:
        """Add a new node to the load balancer"""
        
        if node_id in self.nodes:
            print(f"⚠️ Node {node_id} already exists")
            return
        
        self.nodes[node_id] = {**capabilities, "status": "healthy"}
        self.node_health[node_id] = 0
        self.node_failures[node_id] = 0
        
        print(f"✅ Added node: {node_id}")
    
    async def remove_node(self, node_id: str) -> None:
        """Remove a node from the load balancer
        
        Args:
            node_id: ID of the node to remove
            
        Returns:
            None
            
        Notes:
            Silently ignores if node doesn't exist (no-op)
        """
        
        if node_id not in self.nodes:
            print(f"⚠️ Node {node_id} does not exist, skipping removal")
            return
        
        # Clean up health tracking
        self.node_health.pop(node_id, None)
        self.node_failures.pop(node_id, None)
        
        del self.nodes[node_id]
        
        print(f"❌ Removed node: {node_id}")
    
    async def update_node_status(self, node_id: str, status: str) -> None:
        """Update a node's health status"""
        
        if node_id not in self.nodes:
            return
        
        self.nodes[node_id]["status"] = status
    
    async def route(self, task_request: TaskRequest) -> RoutingResult:
        """Route a task request through the load balancer"""
        
        # Get healthy nodes only
        healthy_nodes = [
            node for node in self.nodes.values() 
            if node.get("status") == "healthy"
        ]
        
        return self.router.route(task_request, healthy_nodes)


# Factory function for creating router instances
def create_router(strategy: str = "gpu_memory_available") -> SimpleRouter:
    """
    Factory function to create a router instance.
    
    Args:
        strategy: Load balancing strategy
        
    Returns:
        SimpleRouter instance
    """
    
    return SimpleRouter(strategy=strategy)


# Main entry point for testing
if __name__ == "__main__":
    # Test the router
    print("Testing SimpleRouter...")
    
    router = create_router(strategy="gpu_memory_available")
    
    # Sample nodes
    nodes = [
        {
            "node_id": "exo-node-001",
            "status": "ready",
            "gpu_model": "AMD Radeon RX 7900 XTX",
            "gpu_memory_total": 24576,  # MB (24GB)
            "cpu_cores": 16,
            "current_load": 0.3,
            "models_cached": ["model-1.gguf"]
        },
        {
            "node_id": "exo-node-002",
            "status": "ready", 
            "gpu_model": "NVIDIA RTX 4090",
            "gpu_memory_total": 8192,  # MB (8GB)
            "cpu_cores": 8,
            "current_load": 0.5,
            "models_cached": ["model-1.gguf", "model-2.gguf"]
        },
        {
            "node_id": "exo-node-003", 
            "status": "busy",
            "gpu_model": "AMD Radeon RX 6950 XT",
            "gpu_memory_total": 16384,  # MB (16GB)
            "cpu_cores": 12,
            "current_load": 0.9,
            "models_cached": []
        }
    ]
    
    # Test routing with GPU requirement
    task = TaskRequest(
        task_id="task-001",
        model_path="/path/to/model.gguf",
        prompt="What is the meaning of life?",
        gpu_required=True,
        min_gpu_memory_mb=8192  # Need at least 8GB VRAM
    )
    
    result = router.route(task, nodes)
    
    print(f"\n📊 Routing Result:")
    print(f"   Selected Node: {result.selected_node_id}")
    print(f"   Strategy: {result.routing_strategy}")
    print(f"   Confidence: {result.confidence_score:.2f}")
    print(f"   Backup Nodes: {result.backup_nodes}")
    
    # Test without GPU requirement
    task.cpu_only = True
    result_no_gpu = router.route(task, nodes)
    
    print(f"\n📊 Routing Result (CPU-only):")
    print(f"   Selected Node: {result_no_gpu.selected_node_id}")
