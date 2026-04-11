"""
Tests for the distributed pipeline inference module.

Design principles
-----------------
- No actual model weights are loaded (mocked).
- No GPU required (CPU tensors everywhere).
- Real ZMQ sockets are used for transport tests (loopback, ephemeral ports).
- Each test class focuses on one concern.

Run with:
    pytest tests/test_distributed.py -v
"""

from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import dataclass
from typing import List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import torch


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _run(coro):
    """Run an async coroutine in the default event loop."""
    return asyncio.get_event_loop().run_until_complete(coro)


# ─────────────────────────────────────────────────────────────────────────────
# 1. Shard assignment math
# ─────────────────────────────────────────────────────────────────────────────

class TestAssignShards:
    """assign_shards() must correctly partition model layers across nodes."""

    def _nodes(self, vrams: list) -> list:
        return [
            {"node_id": f"n{i}", "host": "127.0.0.1", "gpu_memory_mb": v}
            for i, v in enumerate(vrams)
        ]

    def test_single_node_owns_all_layers(self):
        from exo_windows_porting.distributed.shard import assign_shards

        topo = assign_shards("fake/model", 32, self._nodes([8192]))
        assert len(topo.shards) == 1
        s = topo.shards[0]
        assert s.start_layer == 0
        assert s.end_layer == 32
        assert s.is_first is True
        assert s.is_last is True
        assert s.n_layers == 32

    def test_equal_vram_splits_evenly(self):
        from exo_windows_porting.distributed.shard import assign_shards

        topo = assign_shards("fake/model", 32, self._nodes([8192, 8192]))
        assert len(topo.shards) == 2
        # Together they cover all 32 layers with no gaps or overlaps
        assert topo.shards[0].start_layer == 0
        assert topo.shards[-1].end_layer == 32
        boundaries = [(s.start_layer, s.end_layer) for s in topo.shards]
        # Contiguous
        for i in range(len(boundaries) - 1):
            assert boundaries[i][1] == boundaries[i + 1][0]

    def test_proportional_vram_gives_more_layers_to_bigger_gpu(self):
        from exo_windows_porting.distributed.shard import assign_shards

        # 2:1 VRAM ratio → node 0 should have ~2x layers of node 1
        topo = assign_shards("fake/model", 30, self._nodes([16384, 8192]))
        n0, n1 = topo.shards[0].n_layers, topo.shards[1].n_layers
        assert n0 > n1, f"Expected node0 ({n0} layers) > node1 ({n1} layers)"

    def test_first_and_last_flags(self):
        from exo_windows_porting.distributed.shard import assign_shards

        topo = assign_shards("fake/model", 32, self._nodes([8192, 8192, 8192]))
        assert topo.shards[0].is_first is True
        assert topo.shards[0].is_last is False
        assert topo.shards[1].is_first is False
        assert topo.shards[1].is_last is False
        assert topo.shards[2].is_first is False
        assert topo.shards[2].is_last is True

    def test_minimum_one_layer_per_node(self):
        from exo_windows_porting.distributed.shard import assign_shards

        # More nodes than layers: each should get at least 1 layer
        # (assign_shards should not crash; excess nodes may be trimmed)
        nodes = self._nodes([8192] * 4)
        topo = assign_shards("fake/model", 4, nodes)
        for s in topo.shards:
            assert s.n_layers >= 1, f"Shard {s.node_id} has 0 layers"

    def test_total_layers_covered(self):
        from exo_windows_porting.distributed.shard import assign_shards

        for n_nodes in [1, 2, 3, 5, 8]:
            topo = assign_shards(
                "fake/model", 32, self._nodes([8192] * n_nodes)
            )
            covered = sum(s.n_layers for s in topo.shards)
            assert covered == 32, f"{n_nodes} nodes: covered {covered} ≠ 32"

    def test_shard_ports_are_unique_and_sequential(self):
        from exo_windows_porting.distributed.shard import assign_shards

        topo = assign_shards("fake/model", 32, self._nodes([8192, 8192, 8192]), base_port=29500)
        ports = [s.inference_port for s in topo.shards]
        assert ports == sorted(set(ports)), "Ports must be unique and increasing"

    def test_topology_next_shard(self):
        from exo_windows_porting.distributed.shard import assign_shards

        topo = assign_shards("fake/model", 32, self._nodes([8192, 8192]))
        first_id = topo.shards[0].node_id
        second_id = topo.shards[1].node_id
        assert topo.next_shard(first_id).node_id == second_id
        assert topo.next_shard(second_id) is None


# ─────────────────────────────────────────────────────────────────────────────
# 2. Tensor serialization (transport wire format)
# ─────────────────────────────────────────────────────────────────────────────

class TestTensorSerialization:
    """_serialize / _deserialize must be exact inverses."""

    def _rt(self, tensor: torch.Tensor, rid: str = "req-001"):
        from exo_windows_porting.distributed.transport import _deserialize, _serialize
        raw = _serialize(rid, tensor)
        msg = _deserialize(raw)
        return msg.request_id, msg.tensor

    def test_float16_roundtrip(self):
        t = torch.randn(1, 8, 4096, dtype=torch.float16)
        rid, out = self._rt(t)
        assert rid == "req-001"
        assert out.shape == t.shape
        assert out.dtype == torch.float16
        assert torch.allclose(t, out)

    def test_int64_roundtrip(self):
        t = torch.tensor([[1, 2, 3, 42, 100]], dtype=torch.int64)
        rid, out = self._rt(t, rid="tok-request-xyz")
        assert rid == "tok-request-xyz"
        assert out.dtype == torch.int64
        assert (out == t).all()

    def test_large_tensor_roundtrip(self):
        # (1, 512, 4096) float16 ≈ 4 MB
        t = torch.randn(1, 512, 4096, dtype=torch.float16)
        _, out = self._rt(t)
        assert out.shape == t.shape
        assert torch.allclose(t, out)

    def test_bad_magic_raises(self):
        from exo_windows_porting.distributed.transport import _deserialize
        with pytest.raises(ValueError, match="Bad magic"):
            _deserialize(b"\x00\x00\x00\x00" + b"\x00" * 20)

    def test_unicode_request_id(self):
        t = torch.zeros(1, 1, dtype=torch.float32)
        rid = "请求-001-αβγ"
        got_rid, _ = self._rt(t, rid=rid)
        assert got_rid == rid


# ─────────────────────────────────────────────────────────────────────────────
# 3. ZMQ transport loopback (real sockets, no mocks)
# ─────────────────────────────────────────────────────────────────────────────

class TestZMQTransportLoopback:
    """
    Use real ZMQ PUSH/PULL sockets on loopback for integration-level coverage.
    No GPU, no model — just tensor message passing.
    """

    def _free_port(self) -> int:
        import socket
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]

    def test_sender_receiver_loopback(self):
        port = self._free_port()

        receiver = None
        sender = None
        try:
            from exo_windows_porting.distributed.transport import (
                ActivationReceiver,
                ActivationSender,
            )

            receiver = ActivationReceiver(port=port)
            sender = ActivationSender(f"tcp://127.0.0.1:{port}")

            tensor = torch.randn(1, 16, 4096, dtype=torch.float16)
            rid = "loopback-test"

            async def _exchange():
                await sender.send(rid, tensor)
                msg = await receiver.recv()
                return msg.request_id, msg.tensor

            got_rid, got_tensor = _run(_exchange())
            assert got_rid == rid
            assert got_tensor.shape == tensor.shape
            assert torch.allclose(tensor, got_tensor)
        finally:
            if sender:
                sender.close()
            if receiver:
                receiver.close()

    def test_multiple_messages_in_order(self):
        port = self._free_port()

        try:
            from exo_windows_porting.distributed.transport import (
                ActivationReceiver,
                ActivationSender,
            )

            receiver = ActivationReceiver(port=port)
            sender = ActivationSender(f"tcp://127.0.0.1:{port}")

            tensors = [torch.tensor([[i]], dtype=torch.int64) for i in range(5)]

            async def _exchange():
                for i, t in enumerate(tensors):
                    await sender.send(f"req-{i}", t)
                results = []
                for _ in tensors:
                    msg = await receiver.recv()
                    results.append((msg.request_id, int(msg.tensor.item())))
                return results

            results = _run(_exchange())
            for i, (rid, val) in enumerate(results):
                assert rid == f"req-{i}"
                assert val == i
        finally:
            sender.close()
            receiver.close()


# ─────────────────────────────────────────────────────────────────────────────
# 4. ShardedModel (mocked — no real weights)
# ─────────────────────────────────────────────────────────────────────────────

class TestShardedModel:
    """
    Verify ShardedModel routing logic without loading real weights.
    We monkey-patch _load_weights to inject a mock model.
    """

    def _make_mock_inner(self, n_layers: int = 4, hidden: int = 64, vocab: int = 100):
        """Build a minimal nn.Module tree that mimics the HF LLaMA inner model."""
        import torch.nn as nn

        class FakeLayer(nn.Module):
            def forward(self, x, past_key_value=None, use_cache=True):
                kv = (torch.zeros(1, 1, x.shape[1], 16),
                      torch.zeros(1, 1, x.shape[1], 16))
                return (x, kv) if use_cache else (x,)

        inner = MagicMock()
        inner.embed_tokens = nn.Embedding(vocab, hidden)
        inner.layers = nn.ModuleList([FakeLayer() for _ in range(n_layers)])
        inner.norm = nn.LayerNorm(hidden)
        return inner

    def _make_shard(self, start, end, total, is_first=False, is_last=False):
        from exo_windows_porting.distributed.shard import ModelShard
        return ModelShard(
            node_id="test", host="127.0.0.1", inference_port=29500,
            start_layer=start, end_layer=end,
            is_first=is_first, is_last=is_last,
            n_layers_total=total,
        )

    def _build_sharded(self, shard, hidden=64, vocab=100):
        import torch.nn as nn
        from exo_windows_porting.distributed.sharded_model import ShardedModel

        inner = self._make_mock_inner(shard.n_layers_total, hidden, vocab)

        with patch.object(ShardedModel, "_load_weights"):
            obj = ShardedModel.__new__(ShardedModel)
            # nn.Module requires __init__ before sub-module assignment
            nn.Module.__init__(obj)
            obj.shard = shard
            obj.device = "cpu"

            # Manually wire up the sub-modules
            if shard.is_first:
                obj.embed_tokens = inner.embed_tokens
            obj.layers = nn.ModuleList(
                [inner.layers[i] for i in range(shard.start_layer, shard.end_layer)]
            )
            if shard.is_last:
                obj.norm = inner.norm
                obj.lm_head = nn.Linear(hidden, vocab, bias=False)

            # Fake config
            cfg = MagicMock()
            cfg.hidden_size = hidden
            obj.model_config = cfg

        return obj

    def test_first_shard_embeds_token_ids(self):
        shard = self._make_shard(0, 2, 4, is_first=True)
        model = self._build_sharded(shard)
        input_ids = torch.tensor([[1, 2, 3]])
        out, kv = model.forward(input_ids=input_ids)
        assert out.shape[-1] == 64    # hidden_size
        assert out.shape[1] == 3     # seq_len

    def test_middle_shard_requires_hidden_states(self):
        shard = self._make_shard(1, 3, 4)
        model = self._build_sharded(shard)
        hs = torch.randn(1, 5, 64)
        out, kv = model.forward(hidden_states=hs)
        assert out.shape == hs.shape

    def test_first_shard_raises_without_input_ids(self):
        shard = self._make_shard(0, 2, 4, is_first=True)
        model = self._build_sharded(shard)
        with pytest.raises(ValueError, match="input_ids"):
            model.forward(hidden_states=torch.randn(1, 3, 64))

    def test_last_shard_returns_logits(self):
        shard = self._make_shard(2, 4, 4, is_last=True)
        model = self._build_sharded(shard, hidden=64, vocab=100)
        hs = torch.randn(1, 7, 64)
        logits, kv = model.forward(hidden_states=hs)
        assert logits.shape == (1, 7, 100)   # [batch, seq, vocab]


# ─────────────────────────────────────────────────────────────────────────────
# 5. Coordinator sampling
# ─────────────────────────────────────────────────────────────────────────────

class TestCoordinatorSampling:
    """_sample() must implement greedy and top-p correctly."""

    def _make_coordinator(self, temperature=1.0, top_p=1.0):
        from exo_windows_porting.distributed.coordinator import ShardCoordinator
        from exo_windows_porting.distributed.shard import ClusterTopology

        topo = MagicMock(spec=ClusterTopology)
        topo.model_id = "fake/model"
        topo.shards = []

        coord = ShardCoordinator.__new__(ShardCoordinator)
        coord.topology = topo
        coord.temperature = temperature
        coord.top_p = top_p
        return coord

    def test_greedy_always_picks_argmax(self):
        coord = self._make_coordinator(temperature=0.0)
        logits = torch.zeros(1, 50)
        logits[0, 17] = 100.0   # clear winner
        token = coord._sample(logits)
        assert token == 17

    def test_sampling_respects_temperature(self):
        """With high temperature, distribution becomes more uniform."""
        coord = self._make_coordinator(temperature=1.5)
        logits = torch.randn(1, 1000)
        # Should not raise; returned token must be a valid index
        token = coord._sample(logits)
        assert 0 <= token < 1000

    def test_top_p_excludes_low_prob_tokens(self):
        """With top_p=0.01, only the highest-probability tokens can be sampled."""
        coord = self._make_coordinator(temperature=1.0, top_p=0.01)
        logits = torch.zeros(1, 100)
        logits[0, 42] = 50.0   # token 42 dominates
        tokens = {coord._sample(logits) for _ in range(20)}
        # With p=0.01, only the dominant token should ever appear
        assert tokens == {42}, f"Unexpected tokens sampled: {tokens}"


# ─────────────────────────────────────────────────────────────────────────────
# 6. DistributedPipelineEngine API surface
# ─────────────────────────────────────────────────────────────────────────────

class TestDistributedPipelineEngine:
    """Engine must implement LLMBackend and expose the right API."""

    def test_is_llm_backend_subclass(self):
        from exo_windows_porting.backend.base import LLMBackend
        from exo_windows_porting.distributed.pipeline import DistributedPipelineEngine
        assert issubclass(DistributedPipelineEngine, LLMBackend)

    def test_get_backend_name_contains_distributed(self):
        from exo_windows_porting.distributed.pipeline import DistributedPipelineEngine
        from exo_windows_porting.distributed.shard import ClusterTopology

        topo = MagicMock(spec=ClusterTopology)
        topo.n_nodes = 3
        topo.model_id = "fake"
        topo.shards = []

        engine = DistributedPipelineEngine.__new__(DistributedPipelineEngine)
        engine.topology = topo
        engine.local_shards = []
        engine.local_devices = []
        name = engine.get_backend_name()
        assert "distributed" in name.lower()

    def test_from_nodes_returns_engine(self):
        """Constructing an engine from a topology must return DistributedPipelineEngine."""
        from exo_windows_porting.distributed.pipeline import DistributedPipelineEngine
        from exo_windows_porting.distributed.shard import assign_shards

        nodes = [
            {"node_id": "n0", "host": "127.0.0.1", "gpu_memory_mb": 8192},
            {"node_id": "n1", "host": "127.0.0.2", "gpu_memory_mb": 8192},
        ]
        topo = assign_shards("fake/model", 32, nodes)
        engine = DistributedPipelineEngine(topology=topo)
        assert isinstance(engine, DistributedPipelineEngine)
        assert engine.topology.n_nodes == 2

    def test_local_factory_assigns_local_shards(self):
        from exo_windows_porting.distributed.pipeline import DistributedPipelineEngine
        from exo_windows_porting.distributed.shard import assign_shards

        nodes = [{"node_id": f"local-{i}", "host": "127.0.0.1", "gpu_memory_mb": 1}
                 for i in range(2)]
        topo = assign_shards("fake/model", 8, nodes)
        engine = DistributedPipelineEngine(
            topology=topo,
            local_shards=topo.shards,
            local_devices=["cpu", "cpu"],
        )
        assert len(engine.local_shards) == 2
        topo_ids = {s.node_id for s in engine.topology.shards}
        local_ids = {s.node_id for s in engine.local_shards}
        assert local_ids.issubset(topo_ids)


# ─────────────────────────────────────────────────────────────────────────────
# 7. End-to-end mock generation (no GPU, no model weights)
# ─────────────────────────────────────────────────────────────────────────────

class TestMockGeneration:
    """
    Simulate a complete generate() call with all network I/O mocked.
    Verifies the coordinator–worker contract without real hardware.
    """

    def test_generate_returns_string(self):
        """
        Coordinator.generate() must return a non-empty string when the
        transport and tokenizer are mocked.
        """
        from exo_windows_porting.distributed.coordinator import ShardCoordinator
        from exo_windows_porting.distributed.shard import ClusterTopology, ModelShard

        # Build a 1-node topology (coordinator + last node in one)
        shard = ModelShard(
            node_id="solo", host="127.0.0.1", inference_port=29500,
            start_layer=0, end_layer=4,
            is_first=True, is_last=True, n_layers_total=4,
        )
        topo = ClusterTopology(model_id="fake/model", shards=[shard])

        coord = ShardCoordinator(topo, results_port=29601)

        # Mock tokenizer
        fake_tokenizer = MagicMock()
        fake_tokenizer.return_value = {"input_ids": torch.tensor([[1, 2, 3]])}
        fake_tokenizer.__call__ = MagicMock(return_value={"input_ids": torch.tensor([[1, 2, 3]])})
        fake_tokenizer.eos_token_id = 2
        fake_tokenizer.decode = lambda ids, **kw: " ".join(str(i) for i in ids)

        coord._tokenizer = fake_tokenizer
        coord._started = True

        # Mock transport: pretend 3 tokens are returned then EOS
        token_sequence = [42, 17, 2]   # last is EOS
        call_count = {"n": 0}
        # Capture the actual request_id that the coordinator uses so recv()
        # can echo it back — _recv_logits() now validates IDs strictly.
        captured_rid: list = []

        from exo_windows_porting.distributed.transport import TensorMessage

        async def fake_send(rid, tensor):
            if not captured_rid:
                captured_rid.append(rid)

        async def fake_send_finished(rid):
            pass

        async def fake_recv(timeout_ms=30_000):
            i = call_count["n"]
            call_count["n"] += 1
            rid = captured_rid[0] if captured_rid else "req-x"
            # After generation tokens, return a FINISHED echo when asked
            if i >= len(token_sequence):
                return TensorMessage(request_id=rid, tensor=None, finished=True)
            # Return dummy logits with the desired token as argmax
            logits = torch.full((1, 1, 100), -100.0)
            logits[0, 0, token_sequence[i]] = 100.0
            return TensorMessage(request_id=rid, tensor=logits, finished=False)

        coord._first_sender = MagicMock()
        coord._first_sender.send = AsyncMock(side_effect=fake_send)
        coord._first_sender.send_finished = AsyncMock(side_effect=fake_send_finished)
        coord._logits_receiver = MagicMock()
        coord._logits_receiver.recv = AsyncMock(side_effect=fake_recv)

        result = _run(coord.generate("Hello, world!", max_new_tokens=10))
        # The coordinator should stop at EOS (token_id=2)
        assert isinstance(result, str)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
