"""
Abstract base class for LLM backends.

All backends (CPU, ROCm, CUDA) must inherit from LLMBackend and implement
the required interface. This enforces the contract at class definition time
rather than at runtime.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass
class GenerateResult:
    """Structured result from a generate() call."""
    text: str
    tokens_generated: int
    elapsed_ms: float
    backend_name: str


class LLMBackend(ABC):
    """
    Abstract interface that every LLM backend must implement.

    Subclasses are responsible for:
    - Loading a GGUF model at construction time
    - Implementing async generate() for text completion
    - Reporting their backend name via get_backend_name()

    Failure to implement any abstract method raises TypeError at
    instantiation time, not at the first call site.
    """

    @abstractmethod
    async def generate(self, prompt: str, max_tokens: int = 512) -> str:
        """
        Generate text from a prompt.

        Args:
            prompt: Input text prompt
            max_tokens: Maximum number of tokens to generate

        Returns:
            Generated text string (without the prompt)

        Raises:
            RuntimeError: If the underlying model fails to generate
        """
        ...

    @abstractmethod
    def get_backend_name(self) -> str:
        """Return the identifier string for this backend (e.g. 'cpu', 'cuda', 'rocm')."""
        ...

    def __repr__(self) -> str:
        model = getattr(self, "model_path", "unknown")
        return f"{self.__class__.__name__}(model={model!r}, backend={self.get_backend_name()!r})"
