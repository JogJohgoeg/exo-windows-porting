"""
CUDA GPU Backend for Exo Windows Porting.

This module provides NVIDIA CUDA GPU acceleration support for llama-cpp-python.

Author: Exo Windows Porting Team
License: MIT
"""

import logging
import os
import time

from .base import LLMBackend

logger = logging.getLogger(__name__)


class LLamaCudaBackend(LLMBackend):
    """llama.cpp CUDA backend wrapper."""

    def __init__(self, model_path: str, device_id: int = 0, n_ctx: int = 8192):
        self.model_path = model_path
        self.device_id = device_id
        self.n_ctx = n_ctx

        os.environ.setdefault("CUDA_VISIBLE_DEVICES", str(device_id))

        try:
            from llama_cpp import Llama

            self.llm = Llama(
                model_path=model_path,
                n_gpu_layers=-1,
                n_ctx=n_ctx,
                verbose=False,
            )

            logger.info("CUDA backend initialized (device=%d, model=%s)", device_id, model_path)

        except ImportError as e:
            raise ImportError(
                "llama-cpp-python is not installed with CUDA support.\n"
                "Install the CUDA wheel:\n"
                "  pip install llama-cpp-python "
                "--index-url https://abetlen.github.io/llama-cpp-python/whl/cu121"
            ) from e

    # ------------------------------------------------------------------
    # LLMBackend interface
    # ------------------------------------------------------------------

    def get_backend_name(self) -> str:
        return "cuda"

    async def generate(self, prompt: str, max_tokens: int = 512) -> str:
        """Generate text using CUDA-accelerated llama.cpp."""
        start = time.monotonic()

        result = self.llm(
            prompt=prompt,
            max_tokens=max_tokens,
            stop=None,
            echo=False,
        )

        elapsed_ms = (time.monotonic() - start) * 1000
        logger.debug("CUDA generation finished in %.1f ms", elapsed_ms)

        return result["choices"][0]["text"]


# Factory function for creating CUDA backend instances
def create_cuda_backend(model_path: str, device_id: int = 0):
    """
    Create a CUDA-accelerated llama.cpp backend.
    
    Args:
        model_path: Path to GGUF model file
        device_id: CUDA device ID (default: 0)
        
    Returns:
        LLamaCudaBackend instance
        
    Raises:
        ImportError: If llama-cpp-python not installed with CUDA support
    """
    
    return LLamaCudaBackend(model_path=model_path, device_id=device_id)


# Main entry point for testing
if __name__ == "__main__":
    import asyncio
    
    async def main():
        # Initialize CUDA backend
        backend = create_cuda_backend(
            model_path="models/Qwen2.5-7B-Instruct.Q4_K_M.gguf",
            device_id=0
        )
        
        # Test generation
        result = await backend.generate("What is the meaning of life?", max_tokens=128)
        print(f"Generated: {result}")
    
    asyncio.run(main())
