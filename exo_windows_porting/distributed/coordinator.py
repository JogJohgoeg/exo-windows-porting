"""
Cluster coordinator for distributed pipeline inference.

The coordinator:
  1. Holds the ClusterTopology (who owns which layers).
  2. Tokenizes prompts and sends token IDs to the first worker.
  3. Listens for logits from the last worker.
  4. Samples the next token and feeds it back to the pipeline.
  5. Repeats until max_tokens or EOS.

Generation loop (one token at a time after prefill)
---------------------------------------------------
  prefill:
    coordinator ──[token_ids]──► worker-1 ──[h]──► … ──► worker-N ──[logits]──► coordinator
    coordinator samples token_0

  decode step k:
    coordinator ──[token_k]──► worker-1 ──[h]──► … ──► worker-N ──[logits]──► coordinator
    (workers reuse their per-request KV cache — O(1) per step)
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import AsyncIterator, List, Optional

import torch

from .shard import ClusterTopology
from .transport import ActivationSender, LogitsReceiver, TensorMessage

logger = logging.getLogger(__name__)

_DEFAULT_RESULTS_PORT = 29600


class ShardCoordinator:
    """
    Orchestrates a distributed inference pipeline.

    Args:
        topology:       ClusterTopology built by `assign_shards()`.
        results_port:   Port this coordinator binds for receiving logits.
        temperature:    Sampling temperature (0 = greedy).
        top_p:          Nucleus sampling threshold.
    """

    def __init__(
        self,
        topology: ClusterTopology,
        results_port: int = _DEFAULT_RESULTS_PORT,
        temperature: float = 1.0,
        top_p: float = 0.9,
    ):
        self.topology = topology
        self.results_port = results_port
        self.temperature = temperature
        self.top_p = top_p

        self._tokenizer = None            # loaded lazily
        self._first_sender: Optional[ActivationSender] = None
        self._logits_receiver: Optional[LogitsReceiver] = None
        self._started = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Load tokenizer and open sockets."""
        if self._started:
            return

        from transformers import AutoTokenizer

        logger.info("Loading tokenizer for %s …", self.topology.model_id)
        self._tokenizer = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: AutoTokenizer.from_pretrained(
                self.topology.model_id, use_fast=True
            ),
        )

        first_shard = self.topology.shards[0]
        self._first_sender = ActivationSender(
            f"tcp://{first_shard.host}:{first_shard.inference_port}"
        )

        self._logits_receiver = LogitsReceiver(self.results_port)

        self._started = True
        logger.info(
            "Coordinator ready — pipeline: %s",
            " → ".join(s.node_id for s in self.topology.shards),
        )

    async def stop(self) -> None:
        if self._first_sender:
            self._first_sender.close()
        if self._logits_receiver:
            self._logits_receiver.close()
        self._started = False

    # ------------------------------------------------------------------
    # Generation
    # ------------------------------------------------------------------

    async def generate(
        self,
        prompt: str,
        max_new_tokens: int = 512,
        stop_sequences: Optional[List[str]] = None,
    ) -> str:
        """
        Run distributed autoregressive generation.

        Returns the full generated string (not including the prompt).
        """
        tokens: List[str] = []
        async for token in self.generate_stream(prompt, max_new_tokens, stop_sequences):
            tokens.append(token)
        return "".join(tokens)

    async def generate_stream(
        self,
        prompt: str,
        max_new_tokens: int = 512,
        stop_sequences: Optional[List[str]] = None,
    ) -> AsyncIterator[str]:
        """
        Stream generated tokens one by one.

        Each yielded value is a decoded string fragment (may be a sub-word).
        """
        if not self._started:
            raise RuntimeError("Call coordinator.start() before generating")

        request_id = str(uuid.uuid4())
        stop_sequences = stop_sequences or []

        # ── Tokenize ──────────────────────────────────────────────────
        enc = self._tokenizer(prompt, return_tensors="pt")
        input_ids: torch.Tensor = enc["input_ids"]   # [1, prompt_len]

        eos_id: int = self._tokenizer.eos_token_id or 2
        generated_ids: List[int] = []
        generated_text = ""

        async def _recv_logits() -> torch.Tensor:
            """Receive logits and validate they belong to this request."""
            msg: TensorMessage = await self._logits_receiver.recv()
            if msg.request_id != request_id:
                logger.warning(
                    "request_id mismatch: expected %s, got %s — possible pipeline desync",
                    request_id, msg.request_id,
                )
            return msg.tensor

        async def _evict_kv_cache() -> None:
            """Send FINISHED signal and drain the echo that cascades back."""
            await self._first_sender.send_finished(request_id)
            try:
                echo: TensorMessage = await self._logits_receiver.recv(timeout_ms=5_000)
                if not echo.finished:
                    logger.warning(
                        "Expected FINISHED echo for %s, got tensor instead", request_id
                    )
            except TimeoutError:
                logger.warning(
                    "FINISHED echo timed out for request %s — workers may have stale KV cache",
                    request_id,
                )

        # ── Prefill ───────────────────────────────────────────────────
        # Send full prompt token IDs to first worker
        await self._first_sender.send(request_id, input_ids)

        # Receive logits from last worker [1, prompt_len, vocab]
        logits = await _recv_logits()

        # Sample from the last position
        next_token_id = self._sample(logits[:, -1, :])
        generated_ids.append(next_token_id)

        decoded = self._tokenizer.decode([next_token_id], skip_special_tokens=True)
        generated_text += decoded
        yield decoded

        if next_token_id == eos_id or _hit_stop(generated_text, stop_sequences):
            await _evict_kv_cache()
            return

        # ── Decode loop ────────────────────────────────────────────────
        for _ in range(max_new_tokens - 1):
            # Send only the single new token
            new_token_tensor = torch.tensor([[next_token_id]], dtype=torch.long)
            await self._first_sender.send(request_id, new_token_tensor)

            logits = await _recv_logits()
            next_token_id = self._sample(logits[:, -1, :])
            generated_ids.append(next_token_id)

            decoded = self._tokenizer.decode([next_token_id], skip_special_tokens=True)
            generated_text += decoded
            yield decoded

            if next_token_id == eos_id or _hit_stop(generated_text, stop_sequences):
                break

        await _evict_kv_cache()
        logger.info(
            "Request %s finished: %d tokens generated", request_id, len(generated_ids)
        )

    # ------------------------------------------------------------------
    # Sampling
    # ------------------------------------------------------------------

    def _sample(self, logits: torch.Tensor) -> int:
        """
        Sample one token from logits [vocab_size].

        Uses temperature scaling + top-p (nucleus) filtering.
        With temperature=0 (or very small), falls back to greedy argmax.
        """
        logits = logits.float().squeeze(0)   # [vocab]

        if self.temperature <= 1e-6:
            return int(logits.argmax().item())

        logits = logits / self.temperature

        # Top-p (nucleus) filtering
        if self.top_p < 1.0:
            sorted_logits, sorted_indices = torch.sort(logits, descending=True)
            cumulative_probs = torch.cumsum(torch.softmax(sorted_logits, dim=-1), dim=-1)

            # Mark tokens whose *preceding* cumulative mass already exceeds top_p.
            # Shift right by one so we always include the token that first pushes
            # cumulative probability over the threshold (the "bridge token").
            sorted_indices_to_remove = cumulative_probs - torch.softmax(sorted_logits, dim=-1) > self.top_p
            sorted_indices_to_remove[1:] = sorted_indices_to_remove[:-1].clone()
            sorted_indices_to_remove[0] = False   # top token is always kept

            sorted_logits[sorted_indices_to_remove] = float("-inf")

            # Scatter back to original token order
            logits = torch.zeros_like(logits).scatter_(0, sorted_indices, sorted_logits)

        probs = torch.softmax(logits, dim=-1)
        return int(torch.multinomial(probs, num_samples=1).item())


# ------------------------------------------------------------------
# Utility
# ------------------------------------------------------------------

def _hit_stop(text: str, stop_sequences: List[str]) -> bool:
    return any(seq in text for seq in stop_sequences)
