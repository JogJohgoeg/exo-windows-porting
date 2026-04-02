# TASK-001: P2P 网络层自动发现实现

## 📋 **任务描述**

实现基于 mDNS/Bonjour 的 P2P 节点自动发现机制，让 Exo Windows Porting 支持零配置启动。

## 🎯 **目标**

- ✅ 实现 mDNS/Bonjour 服务注册和发现
- ✅ Peer 节点身份识别和连接管理
- ✅ 简单路由器和负载均衡策略
- ✅ 健康检查和自动故障转移

## 👤 **负责人**

`p2p_network_specialist` (待分配)

## 📅 **时间线**

- **预计开始**: Day 3 of Phase 0
- **预计完成**: Day 5 of Phase 0 (Week 1)
- **优先级**: High

---

## 🔍 **技术实现要点**

### **1. mDNS/Bonjour 服务发现**

#### **Python 库选择**
```python
# 推荐：zeroconf (跨平台，成熟稳定)
pip install zeroconf

# 备选：pybonjour (macOS 专用，不推荐用于 Windows)
```

#### **实现思路**
```python
from zeroconf import ServiceBrowser, Zeroconf

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
        
        zeroconf = Zeroconf()
        services = []
        
        def add_service(zeroconf, service_type: str, name: str):
            info = zeroconf.get_service_info(service_type, name)
            if info:
                node_id = info.properties.get(b'node_id', b'unknown').decode()
                host = info.addresses[0].decode()
                port = info.port
                
                services.append(cls(
                    node_id=node_id,
                    host=host,
                    port=port,
                    metadata={'status': 'active'}
                ))
        
        browser = ServiceBrowser(zeroconf, cls.SERVICE_TYPE, handlers=[add_service])
        
        # 等待发现结果 (超时机制)
        import time
        time.sleep(3)  # 3 秒内收集所有服务
        
        browser.cancel()
        zeroconf.close()
        
        return services

# 使用示例
print("发现节点:")
for node in ExoServiceInfo.browse():
    print(f"  - {node.node_id} @ {node.host}:{node.port}")
```

---

### **2. Peer 节点身份管理**

#### **唯一 ID 生成**
```python
import uuid
from datetime import datetime

def generate_node_id() -> str:
    """生成唯一的节点 ID"""
    
    # 格式：exo-{hostname}-{timestamp}-{uuid}
    hostname = socket.gethostname().lower()
    timestamp = datetime.utcnow().strftime('%Y%m%d%H%M%S')
    unique_id = uuid.uuid4().hex[:8]
    
    return f"exo-{hostname}-{timestamp}-{unique_id}"

# 示例输出: exo-johnny-pc-20260401083000-a1b2c3d4
```

#### **节点状态管理**
```python
from enum import Enum
from dataclasses import dataclass, field
from typing import Dict, Optional

class NodeStatus(Enum):
    """节点状态枚举"""
    
    INITIALIZING = "initializing"  # 初始化中
    READY = "ready"                 # 就绪，可接受任务
    BUSY = "busy"                   # 正在处理任务
    OFFLINE = "offline"             # 离线
    
@dataclass
class NodeInfo:
    """节点信息"""
    
    node_id: str
    host: str
    port: int
    status: NodeStatus = NodeStatus.INITIALIZING
    
    # GPU/计算资源信息
    gpu_model: Optional[str] = None
    gpu_memory_total: Optional[int] = None  # MB
    cpu_cores: Optional[int] = None
    
    # 模型缓存
    models_cached: Dict[str, str] = field(default_factory=dict)  # model_id -> path
    
    # 元数据
    last_heartbeat: Optional[float] = None
    uptime_seconds: int = 0

# 使用示例
node_info = NodeInfo(
    node_id=generate_node_id(),
    host="192.168.1.100",
    port=18790,
    gpu_model="AMD Radeon RX 7900 XTX",
    gpu_memory_total=24576,  # 24GB
    cpu_cores=16
)
```

---

### **3. 简单路由器 (SimpleRouter)**

#### **负载均衡策略**

```python
from typing import List, Optional
import random

class SimpleRouter:
    """
    Exo Windows Porting 的默认路由器实现。
    
    支持多种负载均衡策略：
    - Random: 随机选择节点
    - LeastLoaded: 选择负载最低的节点
    - GpuMemoryAvailable: 选择可用 GPU 显存最多的节点
    """
    
    def __init__(self, strategy: str = "gpu_memory_available"):
        self.strategy = strategy
    
    def select_node(self, nodes: List[NodeInfo], task_requirements: dict) -> Optional[NodeInfo]:
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
        if self.strategy == "random":
            return random.choice(filtered_nodes)
        
        elif self.strategy == "least_loaded":
            # 简单实现：假设所有节点负载相同，返回第一个
            return filtered_nodes[0]
        
        elif self.strategy == "gpu_memory_available":
            # 选择可用 GPU 显存最多的节点
            best = max(filtered_nodes, key=lambda n: (n.gpu_memory_total or 0))
            return best
        
        else:
            # 默认：随机
            return random.choice(filtered_nodes)

# 使用示例
router = SimpleRouter(strategy="gpu_memory_available")

task_requirements = {
    "gpu_required": True,
    "min_gpu_memory_mb": 4096,  # 至少 4GB VRAM
    "model_size_mb": 8192       # 模型大小 8GB
}

best_node = router.select_node(all_nodes, task_requirements)

if best_node:
    print(f"选择节点：{best_node.node_id} ({best_node.gpu_model})")
else:
    print("无合适节点可用！")
```

---

### **4. 健康检查机制**

#### **心跳检测**
```python
import asyncio
from datetime import datetime, timedelta
from typing import Dict, Optional

class HeartbeatMonitor:
    """
    P2P 集群的健康检查监控器。
    
    功能：
    - 定期发送心跳请求
    - 自动标记离线节点
    - 触发故障转移机制
    """
    
    HEARTBEAT_INTERVAL = 30  # 秒
    
    def __init__(self, coordinator_node_id: str):
        self.coordinator_node_id = coordinator_node_id
        self.node_heartbeats: Dict[str, float] = {}
        self.offline_threshold = timedelta(seconds=90)  # 90 秒无心跳视为离线
        
    def record_heartbeat(self, node_id: str) -> None:
        """记录节点心跳"""
        
        now = datetime.utcnow().timestamp()
        self.node_heartbeats[node_id] = now
    
    def get_offline_nodes(self) -> List[str]:
        """获取已离线节点的 ID 列表"""
        
        now = datetime.utcnow().timestamp()
        offline = []
        
        for node_id, last_heartbeat in list(self.node_heartbeats.items()):
            if now - last_heartbeat > self.offline_threshold.total_seconds():
                offline.append(node_id)
        
        return offline
    
    async def monitor_loop(self, nodes: List[NodeInfo], callback: callable):
        """
        后台监控循环。
        
        Args:
            nodes: 节点列表
            callback: 节点状态变化时的回调函数
            
        示例：
            async def on_node_offline(node_id: str):
                print(f"⚠️ Node {node_id} 已离线，触发故障转移")
                
            monitor = HeartbeatMonitor(coordinator_id)
            await monitor.monitor_loop(nodes, callback=on_node_offline)
        """
        
        import time
        
        while True:
            offline_nodes = self.get_offline_nodes()
            
            if offline_nodes:
                for node_id in offline_nodes:
                    print(f"⚠️ Node {node_id} 已离线！")
                    await callback(node_id)
                
                # 清理离线节点
                for node_id in offline_nodes:
                    del self.node_heartbeats[node_id]
            
            time.sleep(self.HEARTBEAT_INTERVAL)

# 使用示例
async def main():
    monitor = HeartbeatMonitor(coordinator_node_id="exo-coordinator-001")
    
    async def on_offline(node_id: str):
        print(f"🚨 触发故障转移：{node_id}")
        # TODO: 重新分配该节点的任务
    
    await monitor.monitor_loop(nodes, callback=on_offline)

# 启动后台监控任务
asyncio.create_task(monitor_loop())
```

---

## 🧪 **测试计划**

### **单元测试覆盖**

| 模块 | 测试项 | 预期结果 |
|------|--------|----------|
| `test_discovery.py` | test_mdns_service_registration | mDNS 服务注册成功 |
| | test_mdns_browse_success | 发现所有在线节点 |
| | test_node_id_generation | ID 唯一性验证 |
| `test_router.py` | test_random_strategy | 随机选择有效节点 |
| | test_least_loaded_strategy | 选择负载最低节点 |
| | test_gpu_memory_available | 选择显存最多节点 |
| `test_heartbeat.py` | test_heartbeat_recording | 心跳记录正确 |
| | test_offline_detection | 90 秒无心跳视为离线 |

---

## 📚 **参考资源**

- [zeroconf Python Library](https://github.com/python-zeroconf/python-zeroconf)
- [mDNS/Bonjour RFC 6762](https://datatracker.ietf.org/doc/html/rfc6762)
- [Exo Original P2P Implementation](https://github.com/exo-explore/exo/blob/main/src/exo/networking/discovery.py)

---

## ✅ **验收标准**

- [ ] mDNS 服务注册和发现成功率 ≥ 95%
- [ ] Peer 节点身份识别正确性 = 100%
- [ ] 路由器负载均衡策略有效性验证通过
- [ ] 健康检查故障转移时间 < 2s (从离线到识别)

---

**任务创建时间**: 2026-04-01  
**状态**: 🟢 待启动  
**优先级**: High
