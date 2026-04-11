"""
CUDA GPU Backend for Exo Windows Porting.

This module provides NVIDIA CUDA GPU acceleration support for llama-cpp-python.

Author: Exo Windows Porting Team
License: MIT
"""

from typing import Optional, Dict, List, Any
import os


class LLamaCudaBackend:
    """llama.cpp CUDA backend wrapper."""
    
    def __init__(self, model_path: str, device_id: int = 0, n_ctx: int = 8192):
        self.model_path = model_path
        self.device_id = device_id
        self.n_ctx = n_ctx
        
        # Initialize llama-cpp-python with CUDA support
        try:
            from llama_cpp import Llama
            
            self.llm = Llama(
                model_path=model_path,
                n_gpu_layers=-1,  # All layers to GPU (-1 = all)
                n_ctx=n_ctx,
                verbose=False
            )
            
            print(f"✅ CUDA backend initialized for device {device_id}")
            
        except ImportError as e:
            raise ImportError(
                "llama-cpp-python not installed with CUDA support. "
                "Install with:\n"
                f"pip install llama-cpp-python --index-url https://abetlen.github.io/llama-cpp-python/whl/cu121"
            ) from e
    
    async def generate(self, prompt: str, max_tokens: int = 512) -> str:
        """Generate text using CUDA-accelerated llama.cpp."""
        
        import time
        
        start_time = time.time()
        
        result = self.llm(
            prompt=prompt,
            max_tokens=max_tokens,
            stop=None,
            echo=False
        )
        
        elapsed_ms = (time.time() - start_time) * 1000
        
        return result['choices'][0]['text']


# Factory function for creating CUDA backend instances
def create_cuda_backend(model_path: str, device_id: int = 0, n_ctx: int = 8192):
    """
    Create a CUDA-accelerated llama.cpp backend.
    
    Args:
        model_path: Path to GGUF model file
        device_id: CUDA device ID (default: 0)
        n_ctx: Context window size (default: 8192)
        
    Returns:
        LLamaCudaBackend instance
        
    Raises:
        ImportError: If llama-cpp-python not installed with CUDA support
    """
    
    return LLamaCudaBackend(model_path=model_path, device_id=device_id, n_ctx=n_ctx)


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
