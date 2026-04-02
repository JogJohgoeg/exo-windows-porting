"""
Exo Windows Porting - P2P Network Module

This package provides peer-to-peer networking capabilities for distributed LLM inference.

Components:
- discovery: mDNS/Bonjour based peer discovery and connection management
- router: Load balancing and task routing with multiple strategies
- health_monitor: Health checking and monitoring for cluster nodes

Author: Exo Windows Porting Team
License: MIT
"""

from .discovery import (
    PeerDiscoveryManager,
    ExoServiceInfo,
    NodeInfo,
    PeerNodeInfo,
    create_discovery_manager
)

from .router import (
    SimpleRouter,
    LoadBalancer,
    TaskRequest,
    RoutingResult,
    create_router
)

from .health_monitor import (
    HealthMonitor,
    HealthStatus,
    ping_node,
    get_node_info
)

__all__ = [
    # Discovery
    "PeerDiscoveryManager",
    "ExoServiceInfo", 
    "NodeInfo",
    "PeerNodeInfo",
    "create_discovery_manager",
    
    # Router
    "SimpleRouter",
    "LoadBalancer",
    "TaskRequest",
    "RoutingResult",
    "create_router",
    
    # Health Monitor
    "HealthMonitor",
    "HealthStatus",
    "ping_node",
    "get_node_info"
]

__version__ = "0.1.0-alpha"
__author__ = "Exo Windows Porting Team"
