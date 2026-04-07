"""
CPU Backend for Exo Windows Porting.

This module provides CPU-only inference using llama-cpp-python.

Author: Exo Windows Porting Team
License: MIT
"""

import logging
import os
import time

from .base import LLMBackend

logger = logging.getLogger(__name__)

# Respect LLAMA_N_THREADS env var so users can tune without code changes.
_DEFAULT_CPU_THREADS = int(os.environ.get("LLAMA_N_THREADS", "4"))


class LLamaCpuBackend(LLMBackend):
    """llama.cpp CPU backend."""

    def __init__(self, model_path: str, n_ctx: int = 8192, verbose: bool = False):
        self.model_path = model_path
        self.n_ctx = n_ctx
        self.verbose = verbose

        try:
            from llama_cpp import Llama

            self.llm = Llama(
                model_path=model_path,
                use_mmap=True,
                n_threads=_DEFAULT_CPU_THREADS,
                n_ctx=n_ctx,
                verbose=False,
            )

            logger.info("CPU backend initialized (threads=%d, model=%s)", _DEFAULT_CPU_THREADS, model_path)

        except ImportError as e:
            raise ImportError(
                "llama-cpp-python not installed. Install with:\n  pip install llama-cpp-python"
            ) from e

    # ------------------------------------------------------------------
    # LLMBackend interface
    # ------------------------------------------------------------------

    def get_backend_name(self) -> str:
        return "cpu"

    async def generate(self, prompt: str, max_tokens: int = 512) -> str:
        """Generate text using CPU inference."""
        start = time.monotonic()

        result = self.llm(
            prompt=prompt,
            max_tokens=max_tokens,
            stop=None,
            echo=False,
        )

        elapsed_ms = (time.monotonic() - start) * 1000
        logger.debug("CPU generation finished in %.1f ms", elapsed_ms)

        return result["choices"][0]["text"]


# Factory function for creating CPU backend instances
def create_cpu_backend(model_path: str, n_ctx: int = 8192, verbose: bool = False):
    """
    Create a CPU-only llama.cpp backend.
    
    Args:
        model_path: Path to GGUF model file
        n_ctx: Context window size (default: 8192)
        verbose: Enable verbose output
        
    Returns:
        LLamaCpuBackend instance
    """
    
    return LLamaCpuBackend(model_path=model_path, n_ctx=n_ctx, verbose=verbose)


# Main entry point for testing
if __name__ == "__main__":
    import asyncio
    
    async def main():
        # Initialize CPU backend
        backend = create_cpu_backend(
            model_path="models/Qwen2.5-7B-Instruct.Q4_K_M.gguf",
            n_ctx=8192,
            verbose=True
        )
        
        # Test generation
        result = await backend.generate("What is the meaning of life?", max_tokens=128)
        print(f"Generated: {result}")
    
    asyncio.run(main())
