"""
Model shard descriptors for distributed pipeline inference.

A "shard" is the slice of a transformer model assigned to one node:
  - First shard:  token embedding  + its transformer layers
  - Middle shards: only transformer layers
  - Last shard:   its transformer layers + layer-norm + language-model head

All other nodes in a pipeline pass activations (hidden states) forward via ZMQ.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class ModelShard:
    """
    Describes the portion of a transformer model owned by one worker node.

    Attributes:
        node_id:       Unique identifier of the node that owns this shard.
        host:          IP / hostname of the worker.
        inference_port: ZMQ port the worker listens on for incoming activations.
        start_layer:   First transformer layer index (inclusive).
        end_layer:     Last transformer layer index (exclusive).
        is_first:      True iff this shard holds the token embedding table.
        is_last:       True iff this shard holds the final norm + lm_head.
        n_layers_total: Total number of transformer layers in the full model
                        (needed by workers to sanity-check shard boundaries).
    """

    node_id: str
    host: str
    inference_port: int

    start_layer: int
    end_layer: int

    is_first: bool
    is_last: bool
    n_layers_total: int

    @property
    def n_layers(self) -> int:
        """Number of transformer layers in this shard."""
        return self.end_layer - self.start_layer

    def __repr__(self) -> str:
        role = []
        if self.is_first:
            role.append("first")
        if self.is_last:
            role.append("last")
        role_str = "+".join(role) if role else "middle"
        return (
            f"ModelShard(node={self.node_id!r}, "
            f"layers={self.start_layer}..{self.end_layer}, "
            f"role={role_str})"
        )


@dataclass
class ClusterTopology:
    """
    The ordered pipeline of shards that makes up a distributed inference cluster.

    `shards` is ordered from first to last: shards[0] receives raw token IDs,
    each subsequent shard receives hidden states from the previous one, and
    shards[-1] returns logits to the coordinator.
    """

    model_id: str           # HuggingFace model ID or local path
    shards: List[ModelShard] = field(default_factory=list)

    @property
    def n_nodes(self) -> int:
        return len(self.shards)

    @property
    def n_layers_total(self) -> int:
        """Total transformer layers covered by all shards."""
        if not self.shards:
            return 0
        return max(s.end_layer for s in self.shards)

    def shard_for_node(self, node_id: str) -> Optional[ModelShard]:
        for s in self.shards:
            if s.node_id == node_id:
                return s
        return None

    def next_shard(self, node_id: str) -> Optional[ModelShard]:
        """Return the shard that comes after node_id in the pipeline."""
        for i, s in enumerate(self.shards):
            if s.node_id == node_id and i + 1 < len(self.shards):
                return self.shards[i + 1]
        return None

    def __repr__(self) -> str:
        pipeline = " → ".join(s.node_id for s in self.shards)
        return f"ClusterTopology(model={self.model_id!r}, pipeline=[{pipeline}])"


def assign_shards(
    model_id: str,
    n_layers_total: int,
    nodes: List[dict],
    base_port: int = 29500,
) -> ClusterTopology:
    """
    Assign transformer layers to nodes proportional to their available VRAM.

    Args:
        model_id:       HuggingFace model ID or local path.
        n_layers_total: Total number of transformer layers in the model.
        nodes:          List of dicts with keys: node_id, host, gpu_memory_mb.
                        Nodes without gpu_memory_mb get a weight of 1.
        base_port:      First inference port; subsequent nodes get base_port+1, etc.

    Returns:
        ClusterTopology with ModelShard objects in pipeline order.

    Example:
        nodes = [
            {"node_id": "node-a", "host": "192.168.1.10", "gpu_memory_mb": 24576},
            {"node_id": "node-b", "host": "192.168.1.11", "gpu_memory_mb": 12288},
        ]
        topo = assign_shards("meta-llama/Llama-2-7b-hf", 32, nodes)
        # node-a gets layers 0..21 (2/3 of 32), node-b gets 21..32 (1/3)
    """
    if not nodes:
        raise ValueError("At least one node is required")

    weights = [max(1, n.get("gpu_memory_mb", 1)) for n in nodes]
    total_w = sum(weights)

    shards: List[ModelShard] = []
    cursor = 0
    for i, (node, w) in enumerate(zip(nodes, weights)):
        is_last_node = i == len(nodes) - 1

        if is_last_node:
            end = n_layers_total
        else:
            # Proportional allocation, but at least 1 layer per node
            end = cursor + max(1, round(n_layers_total * w / total_w))
            end = min(end, n_layers_total - (len(nodes) - i - 1))  # reserve 1 layer per remaining node

        shards.append(ModelShard(
            node_id=node["node_id"],
            host=node["host"],
            inference_port=base_port + i,
            start_layer=cursor,
            end_layer=end,
            is_first=(i == 0),
            is_last=is_last_node,
            n_layers_total=n_layers_total,
        ))
        cursor = end

    return ClusterTopology(model_id=model_id, shards=shards)
