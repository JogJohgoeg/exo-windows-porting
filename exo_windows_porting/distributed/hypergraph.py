"""
Hypergraph-based cluster topology for distributed inference.

Replaces the linear ClusterTopology with a proper hypergraph where:
  - HyperNodes carry explicit hardware attributes (VRAM, bandwidth, NVLink)
  - HyperEdges model inter-node connections with bandwidth/latency metadata
  - Topology supports non-linear pipelines and future redundant paths

A linear pipeline is a degenerate hypergraph (path graph), so the existing
PipelineWorker/ShardCoordinator code is unchanged — this layer sits above
them and feeds a ClusterTopology through get_linear_topology().

Author: Exo Windows Porting Team
License: MIT
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Hardware nodes
# ---------------------------------------------------------------------------

@dataclass
class HyperNode:
    """A compute node with explicit hardware attributes."""

    node_id: str
    host: str
    port: int

    # Hardware constraints
    gpu_memory_mb: int           # available VRAM
    bandwidth_gbps: float = 16.0 # inter-node link (PCIe 3.0 x16 default)
    nvlink: bool = False         # NVLink peer-to-peer available
    cpu_cores: int = 8

    # Runtime metrics — updated by AdaptiveScheduler
    current_load: float = 0.0    # 0.0–1.0

    @property
    def effective_bandwidth_gbps(self) -> float:
        """NVLink is ~5× faster than PCIe for GPU-to-GPU transfers."""
        return self.bandwidth_gbps * (5.0 if self.nvlink else 1.0)


# ---------------------------------------------------------------------------
# Inter-node edges
# ---------------------------------------------------------------------------

@dataclass
class HyperEdge:
    """
    A directed connection between one or more source and target nodes.

    For the common pipeline case: one source, one target.
    The multi-source / multi-target design is reserved for future
    broadcast/gather patterns (e.g. tensor-parallel attention).
    """

    edge_id: str
    source_ids: List[str]
    target_ids: List[str]
    bandwidth_gbps: float
    latency_us: float = 50.0   # baseline wire latency (µs)

    @classmethod
    def pipeline_edge(cls, src: HyperNode, dst: HyperNode) -> "HyperEdge":
        """Create a point-to-point pipeline edge between two nodes."""
        bw = min(src.effective_bandwidth_gbps, dst.effective_bandwidth_gbps)
        lat = 10.0 if (src.nvlink and dst.nvlink) else 50.0
        return cls(
            edge_id=f"{src.node_id}->{dst.node_id}",
            source_ids=[src.node_id],
            target_ids=[dst.node_id],
            bandwidth_gbps=bw,
            latency_us=lat,
        )

    def transfer_latency_ms(self, tensor_bytes: int) -> float:
        """Estimate transfer time for a tensor of given byte size."""
        # bytes / (bytes-per-ms) + wire latency
        bw_bytes_per_ms = self.bandwidth_gbps * 1e9 / 8 / 1_000
        return tensor_bytes / bw_bytes_per_ms + self.latency_us / 1_000


# ---------------------------------------------------------------------------
# Topology
# ---------------------------------------------------------------------------

@dataclass
class HypergraphTopology:
    """
    Full cluster topology represented as a hypergraph.

    shard_map maps node_id → (layer_start, layer_end_exclusive).
    The ConstraintSolver fills this in; consumers call get_linear_topology()
    to produce a ClusterTopology compatible with existing pipeline code.
    """

    model_id: str
    n_layers: int

    nodes: Dict[str, HyperNode] = field(default_factory=dict)
    edges: Dict[str, HyperEdge] = field(default_factory=dict)

    # Populated by ConstraintSolver.solve()
    shard_map: Dict[str, Tuple[int, int]] = field(default_factory=dict)

    # ---------------------------------------------------------------------------

    def add_node(self, node: HyperNode) -> None:
        self.nodes[node.node_id] = node

    def add_edge(self, edge: HyperEdge) -> None:
        self.edges[edge.edge_id] = edge

    def get_pipeline_order(self) -> List[HyperNode]:
        """Return nodes sorted by their layer_start (pipeline order)."""
        assigned = [
            (start, self.nodes[nid])
            for nid, (start, _) in self.shard_map.items()
            if nid in self.nodes
        ]
        return [node for _, node in sorted(assigned, key=lambda x: x[0])]

    def edge_between(self, src_id: str, dst_id: str) -> Optional[HyperEdge]:
        """Return the edge from src to dst, or None."""
        for edge in self.edges.values():
            if src_id in edge.source_ids and dst_id in edge.target_ids:
                return edge
        return None

    def bottleneck_edge(self, tensor_bytes: int) -> Optional[HyperEdge]:
        """Return the edge with the highest transfer latency for the given tensor size."""
        if not self.edges:
            return None
        return max(
            self.edges.values(),
            key=lambda e: e.transfer_latency_ms(tensor_bytes),
        )

    def get_linear_topology(self):
        """
        Convert to a ClusterTopology for use with existing pipeline code.

        Requires shard_map to be populated (call ConstraintSolver.solve first).
        """
        from .shard import ClusterTopology, ModelShard

        ordered = self.get_pipeline_order()
        n = len(ordered)
        shards = [
            ModelShard(
                node_id=node.node_id,
                host=node.host,
                inference_port=node.port,
                start_layer=self.shard_map[node.node_id][0],
                end_layer=self.shard_map[node.node_id][1],
                is_first=(i == 0),
                is_last=(i == n - 1),
                n_layers_total=self.n_layers,
            )
            for i, node in enumerate(ordered)
        ]
        return ClusterTopology(model_id=self.model_id, shards=shards)

    # ------------------------------------------------------------------
    # Serialisation / persistence
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        """Serialise topology to a JSON-compatible dict."""
        return {
            "model_id": self.model_id,
            "n_layers": self.n_layers,
            "nodes": {
                nid: {
                    "node_id": n.node_id,
                    "host": n.host,
                    "port": n.port,
                    "gpu_memory_mb": n.gpu_memory_mb,
                    "bandwidth_gbps": n.bandwidth_gbps,
                    "nvlink": n.nvlink,
                    "cpu_cores": n.cpu_cores,
                    "current_load": n.current_load,
                }
                for nid, n in self.nodes.items()
            },
            "edges": {
                eid: {
                    "edge_id": e.edge_id,
                    "source_ids": e.source_ids,
                    "target_ids": e.target_ids,
                    "bandwidth_gbps": e.bandwidth_gbps,
                    "latency_us": e.latency_us,
                }
                for eid, e in self.edges.items()
            },
            "shard_map": {nid: list(rng) for nid, rng in self.shard_map.items()},
        }

    @classmethod
    def from_dict(cls, data: dict) -> "HypergraphTopology":
        """Deserialise topology from a dict (e.g. loaded from JSON)."""
        topo = cls(model_id=data["model_id"], n_layers=data["n_layers"])
        for nd in data["nodes"].values():
            topo.nodes[nd["node_id"]] = HyperNode(
                node_id=nd["node_id"],
                host=nd["host"],
                port=nd["port"],
                gpu_memory_mb=nd["gpu_memory_mb"],
                bandwidth_gbps=nd.get("bandwidth_gbps", 16.0),
                nvlink=nd.get("nvlink", False),
                cpu_cores=nd.get("cpu_cores", 8),
                current_load=nd.get("current_load", 0.0),
            )
        for ed in data["edges"].values():
            topo.edges[ed["edge_id"]] = HyperEdge(
                edge_id=ed["edge_id"],
                source_ids=ed["source_ids"],
                target_ids=ed["target_ids"],
                bandwidth_gbps=ed["bandwidth_gbps"],
                latency_us=ed.get("latency_us", 50.0),
            )
        topo.shard_map = {nid: tuple(rng) for nid, rng in data["shard_map"].items()}
        return topo

    def save(self, path: str) -> None:
        """Save solved topology to a JSON file."""
        import json
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2)
        logger.info("Topology saved to %s", path)

    @classmethod
    def load(cls, path: str) -> "HypergraphTopology":
        """Load a solved topology from a JSON file."""
        import json
        with open(path) as f:
            return cls.from_dict(json.load(f))

    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        lines = [f"HypergraphTopology(model={self.model_id}, layers={self.n_layers})"]
        for nid, (s, e) in sorted(self.shard_map.items(), key=lambda x: x[1][0]):
            node = self.nodes.get(nid)
            bw = f"{node.bandwidth_gbps:.0f}Gbps" if node else "?"
            nvl = " [NVLink]" if (node and node.nvlink) else ""
            lines.append(f"  {nid}: layers [{s},{e}){nvl} @ {bw}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------

def build_hypergraph_topology(
    model_id: str,
    n_layers: int,
    nodes: List[dict],
    base_port: int = 29500,
) -> HypergraphTopology:
    """
    Build a HypergraphTopology from a list of node configuration dicts.

    Required keys per node: node_id, host, gpu_memory_mb
    Optional keys:          port, bandwidth_gbps, nvlink, cpu_cores

    shard_map is NOT populated here — call ConstraintSolver.solve() next.

    Example::

        topo = build_hypergraph_topology(
            "meta-llama/Llama-2-7b-hf",
            32,
            [
                {"node_id": "n0", "host": "10.0.0.1", "gpu_memory_mb": 24576,
                 "bandwidth_gbps": 400, "nvlink": True},
                {"node_id": "n1", "host": "10.0.0.2", "gpu_memory_mb": 12288},
            ],
        )
        ConstraintSolver().solve(topo)
        linear = topo.get_linear_topology()
    """
    topo = HypergraphTopology(model_id=model_id, n_layers=n_layers)

    for i, nd in enumerate(nodes):
        port = nd.get("port", base_port + i)
        node = HyperNode(
            node_id=nd["node_id"],
            host=nd["host"],
            port=port,
            gpu_memory_mb=nd["gpu_memory_mb"],
            bandwidth_gbps=nd.get("bandwidth_gbps", 16.0),
            nvlink=nd.get("nvlink", False),
            cpu_cores=nd.get("cpu_cores", 8),
        )
        topo.add_node(node)

    # Add pipeline edges between consecutive nodes
    node_list = list(topo.nodes.values())
    for i in range(len(node_list) - 1):
        edge = HyperEdge.pipeline_edge(node_list[i], node_list[i + 1])
        topo.add_edge(edge)

    logger.debug(
        "Built hypergraph: model=%s, layers=%d, nodes=%d, edges=%d",
        model_id, n_layers, len(topo.nodes), len(topo.edges),
    )
    return topo
