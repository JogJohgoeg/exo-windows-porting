"""
ROCm GPU Backend for Exo Windows Porting.

This module provides AMD ROCm GPU acceleration support for llama-cpp-python.

IMPORTANT: ROCm support is activated by installing the ROCm-compiled wheel of
llama-cpp-python, NOT by passing a runtime flag. The `use_rocm` parameter does
not exist in llama_cpp.Llama and was removed here.

    pip install llama-cpp-python \
        --index-url https://abetlen.github.io/llama-cpp-python/whl/rocm6.2

Author: Exo Windows Porting Team
License: MIT
"""

import logging
import os
import time

from .base import LLMBackend

logger = logging.getLogger(__name__)


class LLamaRocmBackend(LLMBackend):
    """llama.cpp ROCm backend wrapper."""

    def __init__(self, model_path: str, device_id: int = 0, n_ctx: int = 8192):
        self.model_path = model_path
        self.device_id = device_id
        self.n_ctx = n_ctx

        # ROCm is selected by the GPU-targeted wheel; set the device via env var.
        os.environ.setdefault("HIP_VISIBLE_DEVICES", str(device_id))

        try:
            from llama_cpp import Llama

            self.llm = Llama(
                model_path=model_path,
                n_gpu_layers=-1,  # offload all layers to GPU
                n_ctx=n_ctx,
                verbose=False,
            )

            logger.info("ROCm backend initialized (device=%d, model=%s)", device_id, model_path)

        except ImportError as e:
            raise ImportError(
                "llama-cpp-python is not installed with ROCm support.\n"
                "Install the ROCm wheel first:\n"
                "  pip install llama-cpp-python "
                "--index-url https://abetlen.github.io/llama-cpp-python/whl/rocm6.2"
            ) from e

    # ------------------------------------------------------------------
    # LLMBackend interface
    # ------------------------------------------------------------------

    def get_backend_name(self) -> str:
        return "rocm"

    async def generate(self, prompt: str, max_tokens: int = 512) -> str:
        """Generate text using the ROCm-compiled llama.cpp."""
        start = time.monotonic()

        result = self.llm(
            prompt=prompt,
            max_tokens=max_tokens,
            stop=None,
            echo=False,
        )

        elapsed_ms = (time.monotonic() - start) * 1000
        logger.debug("ROCm generation finished in %.1f ms", elapsed_ms)

        return result["choices"][0]["text"]


# Factory function for creating ROCm backend instances
def create_rocm_backend(model_path: str, device_id: int = 0, n_ctx: int = 8192) -> LLamaRocmBackend:
    """
    Create a ROCm-accelerated llama.cpp backend.

    Args:
        model_path: Path to GGUF model file
        device_id: HIP/ROCm device index (default: 0)
        n_ctx: Context window size (default: 8192)

    Returns:
        LLamaRocmBackend instance

    Raises:
        ImportError: If llama-cpp-python is not the ROCm-compiled build
    """
    return LLamaRocmBackend(model_path=model_path, device_id=device_id, n_ctx=n_ctx)


# Main entry point for testing
if __name__ == "__main__":
    import asyncio
    
    async def main():
        # Initialize ROCm backend
        backend = create_rocm_backend(
            model_path="models/Qwen2.5-7B-Instruct.Q4_K_M.gguf",
            device_id=0
        )
        
        # Test generation
        result = await backend.generate("What is the meaning of life?", max_tokens=128)
        print(f"Generated: {result}")
    
    asyncio.run(main())
