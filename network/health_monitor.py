"""
Health Monitor Module for Exo Windows Porting

This module provides health checking and monitoring capabilities for P2P nodes.

Author: Exo Windows Porting Team
License: MIT
"""

import asyncio
from typing import Dict, List, Optional, Callable, Awaitable
from dataclasses import dataclass
from datetime import datetime
import socket


@dataclass
class HealthStatus:
    """节点健康状态"""
    
    node_id: str
    is_healthy: bool
    last_check_time: float
    
    # 延迟指标 (ms)
    ping_latency_ms: Optional[float] = None
    
    # 负载信息
    cpu_usage_percent: Optional[float] = None
    gpu_memory_used_mb: Optional[int] = None
    
    # 错误信息
    error_message: Optional[str] = None


class HealthMonitor:
    """
    P2P 集群健康检查监控器。
    
    功能：
    - 定期发送心跳请求
    - 自动标记离线节点
    - 触发故障转移机制
    
    Usage:
        monitor = HealthMonitor(coordinator_node_id="exo-coordinator-001")
        
        # Start monitoring loop
        await monitor.start()
        
        # Check specific node health
        status = await monitor.check_node_health("exo-node-001")
        
        # Stop monitoring
        await monitor.stop()
    """
    
    DEFAULT_CHECK_INTERVAL = 30  # seconds
    
    def __init__(self, coordinator_node_id: str):
        self.coordinator_node_id = coordinator_node_id
        
        # Node health tracking
        self.node_status: Dict[str, HealthStatus] = {}
        
        # Monitoring configuration
        self.check_interval = self.DEFAULT_CHECK_INTERVAL
        self.offline_threshold = 90  # seconds without heartbeat
        
        # Event loop control
        self.running = False
        self.monitor_task: Optional[asyncio.Task] = None
        
        # Callbacks
        self.on_node_healthy: Optional[Callable[[str], Awaitable[None]]] = None
        self.on_node_unhealthy: Optional[Callable[[str, str], Awaitable[None]]] = None
        self.on_node_offline: Optional[Callable[[str], Awaitable[None]]] = None
    
    async def start(self) -> None:
        """Start the monitoring loop"""
        
        if self.running:
            print("⚠️ Monitor already running")
            return
        
        self.running = True
        
        # Start background monitoring task
        self.monitor_task = asyncio.create_task(self._monitor_loop())
        
        print(f"🔍 Health monitor started (interval: {self.check_interval}s)")
    
    async def stop(self) -> None:
        """Stop the monitoring loop"""
        
        if not self.running:
            return
        
        self.running = False
        
        if self.monitor_task:
            self.monitor_task.cancel()
            try:
                await self.monitor_task
            except asyncio.CancelledError:
                pass
        
        print("✅ Health monitor stopped")
    
    async def _monitor_loop(self) -> None:
        """Background monitoring loop"""
        
        import time
        
        while self.running:
            try:
                # Check all known nodes
                for node_id in list(self.node_status.keys()):
                    await self._check_node(node_id)
                
                # Wait before next check cycle
                await asyncio.sleep(self.check_interval)
            
            except Exception as e:
                print(f"❌ Monitor loop error: {e}")
                await asyncio.sleep(5)  # Back off on error
    
    async def _check_node(self, node_id: str) -> None:
        """Check health of a specific node
        
        Args:
            node_id: ID of the node to check
            
        Returns:
            HealthStatus object with current status
            
        Notes:
            Triggers callbacks when node state changes (healthy ↔ unhealthy)
        """
        
        try:
            import time as time_module
            
            start_time = time_module.time()
            
            # Send ping request (simplified implementation)
            latency = await self._send_ping(node_id)
            
            elapsed_ms = (time_module.time() - start_time) * 1000
            
            # Update status if healthy
            old_status = self.node_status.get(node_id)
            
            # Detect state transition: unhealthy → healthy
            if old_status and not old_status.is_healthy:
                print(f"✅ Node {node_id} recovered")
                
                if self.on_node_healthy:
                    try:
                        await self.on_node_healthy(node_id)
                    except Exception as callback_error:
                        print(f"❌ Error in on_node_healthy callback for {node_id}: {callback_error}")
            
            # Detect state transition: healthy → unhealthy (first time check)
            elif old_status is None and not self.node_status.get(node_id, HealthStatus(
                node_id=node_id, is_healthy=True, last_check_time=0
            )).is_healthy:
                print(f"⚠️ Node {node_id} first seen as unhealthy")
                
                if self.on_node_unhealthy:
                    try:
                        await self.on_node_unhealthy(node_id, "First check failed")
                    except Exception as callback_error:
                        print(f"❌ Error in on_node_unhealthy callback for {node_id}: {callback_error}")
            
            # Create or update status
            new_status = HealthStatus(
                node_id=node_id,
                is_healthy=True,  # Will be updated below if check fails
                last_check_time=time_module.time(),
                ping_latency_ms=latency or elapsed_ms
            )
            
            self.node_status[node_id] = new_status
            
        except Exception as e:
            import traceback
            
            print(f"❌ Node {node_id} health check failed: {e}")
            traceback.print_exc()
            
            # Update status to unhealthy
            old_status = self.node_status.get(node_id)
            
            # Detect state transition: healthy → unhealthy
            if old_status and old_status.is_healthy:
                print(f"⚠️ Node {node_id} now unhealthy")
                
                if self.on_node_unhealthy:
                    try:
                        await self.on_node_unhealthy(node_id, str(e))
                    except Exception as callback_error:
                        print(f"❌ Error in on_node_unhealthy callback for {node_id}: {callback_error}")
            
            # Create unhealthy status (or update existing)
            self.node_status[node_id] = HealthStatus(
                node_id=node_id,
                is_healthy=False,
                last_check_time=time_module.time(),
                error_message=str(e)
            )
    
    async def _send_ping(self, node_id: str) -> Optional[float]:
        """Send ping to a node and measure latency"""
        
        # This is a simplified implementation
        # In production, you would use actual network communication
        
        try:
            import random
            
            # Simulate network latency (10-50ms for local network)
            simulated_latency = random.uniform(10.0, 50.0)
            
            return simulated_latency
            
        except Exception as e:
            print(f"❌ Ping failed to {node_id}: {e}")
            raise
    
    def get_offline_nodes(self) -> List[str]:
        """Get list of offline nodes"""
        
        import time
        
        now = time.time()
        offline_nodes = []
        
        for node_id, status in self.node_status.items():
            if not status.is_healthy:
                offline_nodes.append(node_id)
            
            elif now - status.last_check_time > self.offline_threshold:
                offline_nodes.append(node_id)
        
        return offline_nodes
    
    def get_healthy_nodes(self) -> List[str]:
        """Get list of healthy nodes"""
        
        import time
        
        now = time.time()
        healthy_nodes = []
        
        for node_id, status in self.node_status.items():
            if status.is_healthy and (now - status.last_check_time <= self.offline_threshold):
                healthy_nodes.append(node_id)
        
        return healthy_nodes
    
    def get_node_health(self, node_id: str) -> Optional[HealthStatus]:
        """Get health status of a specific node"""
        
        return self.node_status.get(node_id)


# Health check utility functions
async def ping_node(host: str, port: int, timeout: float = 5.0) -> bool:
    """
    Check if a node is reachable via TCP ping.
    
    Args:
        host: Node hostname or IP address
        port: Node port number
        timeout: Connection timeout in seconds
        
    Returns:
        True if node is reachable, False otherwise
    """
    
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        
        result = sock.connect_ex((host, port))
        sock.close()
        
        return result == 0
        
    except Exception as e:
        print(f"❌ Ping failed to {host}:{port}: {e}")
        return False


async def get_node_info(host: str, port: int) -> Optional[Dict]:
    """
    Get detailed information about a node.
    
    Args:
        host: Node hostname or IP address
        port: Node port number
        
    Returns:
        Dictionary with node information or None if unavailable
    """
    
    try:
        # This is a simplified implementation
        # In production, you would make an actual API call to the node
        
        return {
            "node_id": f"exo-node-{hash(host) % 1000}",
            "host": host,
            "port": port,
            "status": "healthy",
            "gpu_model": "Unknown",
            "gpu_memory_total": 8192,
            "cpu_cores": 8,
            "current_load": 0.5
        }
        
    except Exception as e:
        print(f"❌ Failed to get node info from {host}:{port}: {e}")
        return None


# Main entry point for testing
if __name__ == "__main__":
    async def main():
        # Create health monitor
        monitor = HealthMonitor(coordinator_node_id="exo-coordinator-001")
        
        try:
            # Register some nodes (for testing)
            monitor.node_status["node-1"] = HealthStatus(
                node_id="node-1",
                is_healthy=True,
                last_check_time=time.time(),
                ping_latency_ms=25.0
            )
            
            monitor.node_status["node-2"] = HealthStatus(
                node_id="node-2", 
                is_healthy=False,
                last_check_time=time.time() - 100,  # Offline for 100s
                error_message="Connection timeout"
            )
            
            # Start monitoring
            await monitor.start()
            
            print("🔍 Monitoring started...")
            
            # Wait a bit to see results
            await asyncio.sleep(65)  # ~2 check cycles
            
        except KeyboardInterrupt:
            print("\n⚠️ Stopping health monitor...")
        finally:
            await monitor.stop()
        
        # Print final status
        print("\n📊 Final Health Status:")
        for node_id, status in monitor.node_status.items():
            if status.is_healthy:
                print(f"   ✅ {node_id}: Healthy (latency: {status.ping_latency_ms:.1f}ms)")
            else:
                print(f"   ❌ {node_id}: Unhealthy ({status.error_message})")


if __name__ == "__main__":
    import time
    asyncio.run(main())
