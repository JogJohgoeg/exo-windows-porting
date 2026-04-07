"""
P2P Network Discovery Module for Exo Windows Porting

This module provides mDNS/Bonjour based peer discovery and connection management.

Author: Exo Windows Porting Team
License: MIT
"""

import logging
import os
import socket
import uuid
from typing import List, Dict, Optional, Callable, Awaitable, Tuple
from dataclasses import dataclass, field, asdict
from datetime import datetime
from threading import Event
import asyncio

logger = logging.getLogger(__name__)

# Import zeroconf for mDNS/Bonjour support
try:
    from zeroconf import ServiceBrowser, Zeroconf, ServiceInfo
except ImportError:
    raise ImportError(
        "zeroconf not installed. Install with:\npip install zeroconf"
    )


@dataclass
class NodeInfo:
    """节点信息"""
    
    node_id: str
    host: str
    port: int
    
    # GPU/计算资源信息
    gpu_model: Optional[str] = None
    gpu_memory_total: Optional[int] = None  # MB
    cpu_cores: Optional[int] = None
    
    # 模型缓存
    models_cached: Dict[str, str] = field(default_factory=dict)  # model_id -> path
    
    # 元数据
    last_heartbeat: Optional[float] = None
    uptime_seconds: int = 0
    status: str = "initializing"  # initializing, ready, busy, offline


@dataclass 
class PeerNodeInfo(NodeInfo):
    """扩展的 peer 节点信息，包含连接状态"""
    
    connection_established: bool = False
    last_seen: Optional[float] = None
    ping_latency_ms: Optional[float] = None


class ExoServiceInfo:
    """Exo P2P 服务信息"""
    
    SERVICE_TYPE = "_exo._tcp.local."
    SERVICE_NAME_PREFIX = "exo-node"
    
    def __init__(self, node_id: str, host: str, port: int, metadata: dict):
        self.node_id = node_id
        self.host = host
        self.port = port
        self.metadata = metadata  # GPU 信息、模型列表等
    
    @classmethod
    def browse(cls) -> List['ExoServiceInfo']:
        """浏览局域网内的 Exo 节点"""
        
        services = []
        zeroconf = Zeroconf()
        
        try:
            def add_service(zeroconf, service_type: str, name: str):
                info = zeroconf.get_service_info(service_type, name)
                if info:
                    properties = {}
                    for key, value in info.properties.items():
                        if isinstance(value, bytes):
                            try:
                                properties[key.decode()] = value.decode()
                            except UnicodeDecodeError:
                                properties[key] = value
                    
                    node_id = properties.get('node_id', 'unknown')
                    host = info.addresses[0].decode() if info.addresses else '127.0.0.1'
                    port = info.port
                    
                    services.append(cls(
                        node_id=node_id,
                        host=host,
                        port=port,
                        metadata={'status': 'active', **properties}
                    ))
            
            browser = ServiceBrowser(zeroconf, cls.SERVICE_TYPE, handlers=[add_service])
            
            # 等待发现结果 (超时机制)
            import time
            start_time = time.time()
            timeout = 5.0  # 5 秒内收集所有服务
            
            while time.time() - start_time < timeout:
                time.sleep(0.1)
            
            browser.cancel()
            zeroconf.close()
            
        except Exception as e:
            logger.error("mDNS discovery failed: %s", e)
        
        return services


class PeerDiscoveryManager:
    """
    P2P 节点自动发现管理器。
    
    功能：
    - mDNS/Bonjour 服务注册和广播
    - 局域网内其他 Exo 节点的自动发现
    - Peer 节点身份识别和连接管理
    - 心跳检测和故障转移
    
    Usage:
        discovery = PeerDiscoveryManager(node_id="exo-001", port=18790)
        await discovery.start()
        
        # 发现其他节点
        peers = discovery.discover_peers(timeout=5.0)
        
        # 停止服务
        await discovery.stop()
    """
    
    def __init__(
        self,
        node_id: str,
        port: int,
        host: str = "127.0.0.1",
        static_peers: Optional[List[Tuple[str, int]]] = None,
    ):
        """
        Args:
            node_id:      Unique identifier for this node.
            port:         Port this node listens on.
            host:         Bind address for this node.
            static_peers: Optional list of (host, port) tuples for peers that
                          should be registered immediately without mDNS.
                          Use this when mDNS is unavailable (VPN, cross-subnet,
                          corporate firewall blocking multicast).
        """
        self.node_id = node_id
        self.port = port
        self.host = host

        # mDNS 相关
        self.zeroconf: Optional[Zeroconf] = None
        self.service_browser: Optional[ServiceBrowser] = None

        # Peer 节点列表
        self.peers: Dict[str, PeerNodeInfo] = {}

        # 事件循环和线程控制
        self.running = False
        self.stop_event = Event()

        # 回调函数
        self.on_peer_discovered: Optional[Callable[[PeerNodeInfo], Awaitable[None]]] = None
        self.on_peer_lost: Optional[Callable[[str], Awaitable[None]]] = None

        logger.debug("PeerDiscoveryManager initialised for node %s", node_id)

        # Pre-register static peers so the cluster works without mDNS.
        if static_peers:
            self._register_static_peers(static_peers)

    def _register_static_peers(self, static_peers: List[Tuple[str, int]]) -> None:
        """
        Add manually configured peers to the peer table.

        Each peer gets a synthetic node_id derived from host:port.  Self-entries
        (matching this node's own host+port) are silently skipped.
        """
        for peer_host, peer_port in static_peers:
            if peer_host == self.host and peer_port == self.port:
                logger.debug("Skipping self-referencing static peer %s:%d", peer_host, peer_port)
                continue

            synthetic_id = f"static-{peer_host}-{peer_port}"
            if synthetic_id in self.peers:
                continue

            peer = PeerNodeInfo(
                node_id=synthetic_id,
                host=peer_host,
                port=peer_port,
                last_seen=datetime.now().astimezone().timestamp(),
                status="static",
            )
            self.peers[synthetic_id] = peer
            logger.info("Static peer registered: %s:%d (id=%s)", peer_host, peer_port, synthetic_id)
    
    async def start(self) -> None:
        """启动 mDNS 服务注册和发现"""
        
        if self.running:
            logger.warning("Discovery manager already running")
            return
        
        self.running = True
        self.stop_event.clear()
        
        try:
            # 初始化 zeroconf
            self.zeroconf = Zeroconf()
            
            # 注册服务
            service_name = f"{self.node_id}.{ExoServiceInfo.SERVICE_NAME_PREFIX}"
            service_type = ExoServiceInfo.SERVICE_TYPE
            
            # 准备服务信息
            properties = {
                b'node_id': self.node_id.encode(),
                b'port': str(self.port).encode(),
                b'host': self.host.encode(),
                b'version': b'0.1.0-alpha',
                b'timestamp': datetime.now().astimezone().isoformat().encode()  # Fixed: Use timezone-aware datetime
            }
            
            # 注册 mDNS 服务
            service_info = ServiceInfo(
                service_type,
                service_name,
                addresses=[socket.inet_aton(self.host)],
                port=self.port,
                properties=properties,
                server=f"{self.node_id}.local."
            )
            
            self.zeroconf.register_service(service_info)
            logger.info("Registered mDNS service: %s", service_name)
            
            # 启动服务发现浏览器
            def on_service_state_change(zeroconf, service_type: str, name: str, state):
                if state == 0:  # ServiceStateAdded
                    self._on_peer_discovered(name)
                elif state == 1:  # ServiceStateRemoved
                    self._on_peer_lost(name)
            
            self.service_browser = ServiceBrowser(
                self.zeroconf, 
                service_type, 
                handlers=[on_service_state_change]
            )
            
            logger.info("Starting mDNS peer discovery for %s", service_type)
            
        except Exception as e:
            logger.error("Failed to start discovery manager: %s", e)
            raise
    
    def _on_peer_discovered(self, name: str) -> None:
        """当发现新节点时调用"""
        
        try:
            info = self.zeroconf.get_service_info(ExoServiceInfo.SERVICE_TYPE, name)
            if not info:
                return
            
            # 解析服务信息
            properties = {}
            for key, value in info.properties.items():
                if isinstance(value, bytes):
                    try:
                        properties[key.decode()] = value.decode()
                    except UnicodeDecodeError:
                        properties[key] = value
            
            node_id = properties.get('node_id', 'unknown')
            
            # 避免重复添加自己
            if node_id == self.node_id:
                return
            
            peer_info = PeerNodeInfo(
                node_id=node_id,
                host=info.addresses[0].decode() if info.addresses else '127.0.0.1',
                port=info.port,
                last_seen=datetime.now().astimezone().timestamp(),  # Fixed: Use timezone-aware datetime
                status="discovered"
            )
            
            self.peers[node_id] = peer_info
            
            logger.info("Discovered new peer: %s @ %s:%d", node_id, peer_info.host, peer_info.port)
            
        except Exception as e:
            logger.error("Error processing discovered service: %s", e)
    
    def _on_peer_lost(self, name: str) -> None:
        """当节点消失时调用"""
        
        try:
            # 从 peers 列表中移除
            for node_id in list(self.peers.keys()):
                if name.startswith(f"{node_id}."):
                    del self.peers[node_id]
                    logger.warning("Peer lost: %s", node_id)
                    
                    # 触发回调 - 使用安全的异步调用方式
                    if self.on_peer_lost:
                        try:
                            loop = asyncio.get_running_loop()
                            # 在运行中的事件循环中调度任务
                            asyncio.create_task(self.on_peer_lost(node_id))
                        except RuntimeError:
                            # 没有运行中的事件循环，使用 create_task 或同步处理
                            import threading
                            threading.Thread(
                                target=lambda: self._run_callback_async(node_id),
                                daemon=True
                            ).start()
                        
        except Exception as e:
            logger.error("Error processing lost service: %s", e)
    
    async def _run_callback_async(self, node_id: str) -> None:
        """在后台线程中安全运行异步回调"""
        
        try:
            await self.on_peer_lost(node_id)
        except Exception as e:
            logger.error("Error in peer lost callback for %s: %s", node_id, e)
    
    async def discover_peers(self, timeout: float = 5.0) -> List[PeerNodeInfo]:
        """
        手动发现局域网内的其他 Exo 节点。
        
        Args:
            timeout: 发现超时时间 (秒)
            
        Returns:
            PeerNodeInfo 列表，每个元素代表一个发现的 peer 节点
        """
        
        discovered = []
        
        try:
            services = ExoServiceInfo.browse()
            
            for service in services:
                if service.node_id == self.node_id:
                    continue  # 跳过自己
                
                peer_info = PeerNodeInfo(
                    node_id=service.node_id,
                    host=service.host,
                    port=service.port,
                    last_seen=datetime.now().astimezone().timestamp(),  # Fixed: Use timezone-aware datetime
                    status="discovered"
                )
                
                discovered.append(peer_info)
                self.peers[service.node_id] = peer_info
                
            logger.info("Discovered %d peer(s)", len(discovered))
            
        except Exception as e:
            logger.error("Manual mDNS discovery failed: %s", e)
        
        return discovered
    
    async def stop(self) -> None:
        """停止 mDNS 服务注册和发现"""
        
        if not self.running:
            return
        
        self.running = False
        self.stop_event.set()
        
        try:
            if self.service_browser:
                self.service_browser.cancel()
                
            if self.zeroconf:
                self.zeroconf.close()
            
            logger.info("Discovery manager stopped")
            
        except Exception as e:
            logger.error("Error stopping discovery manager: %s", e)


class SimpleRouter:
    """
    简单路由器 - Exo Windows Porting 的默认负载均衡器。
    
    支持多种负载均衡策略：
    - Random: 随机选择节点
    - LeastLoaded: 选择负载最低的节点 (简化版)
    - GpuMemoryAvailable: 选择可用 GPU 显存最多的节点
    
    Usage:
        router = SimpleRouter(strategy="gpu_memory_available")
        best_node = router.select_node(nodes, task_requirements)
    """
    
    def __init__(self, strategy: str = "gpu_memory_available"):
        self.strategy = strategy
    
    def select_node(self, nodes: List[PeerNodeInfo], task_requirements: dict) -> Optional[PeerNodeInfo]:
        """
        根据任务需求选择最佳节点。
        
        Args:
            nodes: 可用节点列表
            task_requirements: {
                "gpu_required": bool,
                "min_gpu_memory_mb": int,
                "model_size_mb": int
            }
            
        Returns:
            最佳节点或 None (无合适节点)
        """
        
        if not nodes:
            return None
        
        # 过滤满足基本需求的节点
        filtered_nodes = []
        for node in nodes:
            if task_requirements.get("gpu_required"):
                if node.gpu_memory_total and \
                   node.gpu_memory_total >= task_requirements["min_gpu_memory_mb"]:
                    filtered_nodes.append(node)
            else:
                filtered_nodes.append(node)
        
        if not filtered_nodes:
            return None
        
        # 根据策略选择
        import random
        
        if self.strategy == "random":
            return random.choice(filtered_nodes)
        
        elif self.strategy == "least_loaded":
            # 简化版：假设所有节点负载相同，返回第一个
            return filtered_nodes[0]
        
        elif self.strategy == "gpu_memory_available":
            # 选择可用 GPU 显存最多的节点
            best = max(filtered_nodes, key=lambda n: (n.gpu_memory_total or 0))
            return best
        
        else:
            # 默认：随机
            return random.choice(filtered_nodes)


# Factory function for creating discovery manager instances
def create_discovery_manager(
    node_id: str = None,
    port: int = 18790,
    host: str = "127.0.0.1"
) -> PeerDiscoveryManager:
    """
    Factory function to create a discovery manager instance.
    
    Args:
        node_id: Node ID (auto-generated if not provided)
        port: Port number for the service
        host: Host address for the service
        
    Returns:
        PeerDiscoveryManager instance
    """
    
    import uuid
    
    if not node_id:
        # Generate unique node ID
        hostname = socket.gethostname().lower()
        timestamp = datetime.utcnow().strftime('%Y%m%d%H%M%S')
        unique_id = str(uuid.uuid4()).hex[:8]
        node_id = f"exo-{hostname}-{timestamp}-{unique_id}"
    
    return PeerDiscoveryManager(node_id=node_id, port=port, host=host)


# Main entry point for testing
if __name__ == "__main__":
    async def main():
        # Create discovery manager
        discovery = create_discovery_manager(
            node_id="exo-test-node",
            port=18790,
            host="127.0.0.1"
        )
        
        try:
            # Start discovery
            await discovery.start()
            
            print("🔍 Waiting for peers...")
            
            # Discover peers manually
            peers = await discovery.discover_peers(timeout=5.0)
            
            if peers:
                print(f"✅ Found {len(peers)} peer(s):")
                for peer in peers:
                    print(f"   - {peer.node_id} @ {peer.host}:{peer.port}")
            else:
                print("ℹ️ No peers discovered yet.")
            
            # Wait a bit longer to see if any new peers appear
            import time
            await asyncio.sleep(10)
            
        except KeyboardInterrupt:
            print("\n⚠️ Stopping discovery manager...")
        finally:
            await discovery.stop()
    
    # Run main function
    asyncio.run(main())
