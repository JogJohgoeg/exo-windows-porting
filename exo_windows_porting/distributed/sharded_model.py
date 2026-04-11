"""
Load and execute a slice of a LLaMA-style transformer model.

Memory-efficient loading strategy
-----------------------------------
We avoid loading the full model weights into GPU memory by:
  1. Creating the model architecture (no weights) with `from_config`.
  2. Loading the full checkpoint to CPU with `low_cpu_mem_usage=True`
     (this streams weights lazily via `safetensors` or `pickle`).
  3. Moving only the layers we own to the target device.
  4. Deleting all other layers immediately to free CPU RAM.

Supported architectures
-----------------------
All LLaMA-derivative models (LLaMA-2/3, Mistral, Qwen-2, DeepSeek-V2, etc.)
that follow the standard HuggingFace naming:
  model.embed_tokens
  model.layers[i]
  model.norm
  lm_head
"""

from __future__ import annotations

import gc
import logging
from typing import List, Optional, Tuple

import torch
import torch.nn as nn

from .shard import ModelShard

logger = logging.getLogger(__name__)

# Type alias
KVCache = List[Tuple[torch.Tensor, torch.Tensor]]   # per layer: (k, v)


class ShardedModel(nn.Module):
    """
    Holds only the transformer layers (and optionally embedding / lm_head)
    belonging to one pipeline stage.

    Forward pass
    ------------
    First shard:
        input_ids  → embed_tokens → layers[start:end] → hidden_states

    Middle shard:
        hidden_states → layers[start:end] → hidden_states

    Last shard:
        hidden_states → layers[start:end] → norm → lm_head → logits

    KV cache is a list (one entry per layer) of (key, value) tensors.
    Pass `past_key_values=None` for prefill; pass the list for decode steps.
    """

    def __init__(self, shard: ModelShard, model_id: str, device: str = "cuda"):
        super().__init__()

        self.shard = shard
        self.device = device

        logger.info(
            "Loading %s (layers %d..%d, device=%s)…",
            model_id, shard.start_layer, shard.end_layer, device,
        )

        self._load_weights(model_id, shard, device)
        logger.info("Shard loaded: %s", shard)

    # ------------------------------------------------------------------
    # Weight loading
    # ------------------------------------------------------------------

    def _load_weights(self, model_id: str, shard: ModelShard, device: str) -> None:
        from transformers import AutoModelForCausalLM, AutoConfig

        config = AutoConfig.from_pretrained(model_id)
        self.model_config = config

        # ── Load full model to CPU (lazy / memory-mapped) ────────────
        full = AutoModelForCausalLM.from_pretrained(
            model_id,
            torch_dtype=torch.float16,
            low_cpu_mem_usage=True,
        )

        inner = full.model   # LlamaModel / MistralModel / Qwen2Model …

        # ── Extract and move our parts ────────────────────────────────
        if shard.is_first:
            self.embed_tokens = inner.embed_tokens.to(device)

        # Extract only our layers
        self.layers = nn.ModuleList(
            [inner.layers[i].to(device) for i in range(shard.start_layer, shard.end_layer)]
        )

        if shard.is_last:
            self.norm = inner.norm.to(device)
            self.lm_head = full.lm_head.to(device)

        # ── Free everything else ──────────────────────────────────────
        del full
        gc.collect()
        if device.startswith("cuda"):
            torch.cuda.empty_cache()

    # ------------------------------------------------------------------
    # Forward pass
    # ------------------------------------------------------------------

    def forward(
        self,
        input_ids: Optional[torch.Tensor] = None,
        hidden_states: Optional[torch.Tensor] = None,
        past_key_values: Optional[KVCache] = None,
        use_cache: bool = True,
    ) -> Tuple[torch.Tensor, Optional[KVCache]]:
        """
        Args:
            input_ids:        Long tensor [batch, seq_len] — required for first shard.
            hidden_states:    Float tensor [batch, seq_len, d_model] — required for
                              non-first shards.
            past_key_values:  Cached (k, v) tensors per layer from previous decode steps.
            use_cache:        Whether to return updated KV cache.

        Returns:
            (output, new_past_key_values)
            - For last shard: output is logits [batch, seq_len, vocab_size].
            - For other shards: output is hidden_states [batch, seq_len, d_model].
            - new_past_key_values is None when use_cache=False.
        """
        if self.shard.is_first:
            if input_ids is None:
                raise ValueError("input_ids required for first shard")
            hidden_states = self.embed_tokens(input_ids.to(self.device))
        else:
            if hidden_states is None:
                raise ValueError("hidden_states required for non-first shards")
            hidden_states = hidden_states.to(self.device)

        new_kvs: Optional[KVCache] = [] if use_cache else None

        for i, layer in enumerate(self.layers):
            past_kv = (
                past_key_values[i]
                if past_key_values is not None and i < len(past_key_values)
                else None
            )
            layer_out = layer(
                hidden_states,
                past_key_value=past_kv,
                use_cache=use_cache,
            )
            # HF transformers returns (hidden_states,) or (hidden_states, kv)
            hidden_states = layer_out[0]
            if use_cache and len(layer_out) > 1:
                new_kvs.append(layer_out[1])
            elif use_cache:
                new_kvs.append(None)

        if self.shard.is_last:
            hidden_states = self.norm(hidden_states)
            logits = self.lm_head(hidden_states)
            return logits, new_kvs

        return hidden_states, new_kvs

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @property
    def dtype(self) -> torch.dtype:
        return next(self.parameters()).dtype

    def warmup(self, seq_len: int = 8) -> None:
        """Run a tiny forward pass to trigger CUDA JIT compilation."""
        with torch.no_grad():
            if self.shard.is_first:
                dummy_ids = torch.zeros(1, seq_len, dtype=torch.long, device=self.device)
                self.forward(input_ids=dummy_ids)
            else:
                d = self.model_config.hidden_size
                dummy_hs = torch.zeros(1, seq_len, d, dtype=torch.float16, device=self.device)
                self.forward(hidden_states=dummy_hs)
        logger.debug("Warmup complete for shard %s", self.shard)
