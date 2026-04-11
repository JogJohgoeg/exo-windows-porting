"""
ROCm GPU Backend for Exo Windows Porting.

This module provides AMD ROCm GPU acceleration support for llama-cpp-python.

Author: Exo Windows Porting Team
License: MIT
"""

from typing import Optional, Dict, List, Any
import os


class LLamaRocmBackend:
    """llama.cpp ROCm backend wrapper."""
    
    def __init__(self, model_path: str, device_id: int = 0, n_ctx: int = 8192):
        self.model_path = model_path
        self.device_id = device_id
        self.n_ctx = n_ctx
        
        # Initialize llama-cpp-python with ROCm support.
        # ROCm is selected at install time via the ROCm-specific wheel;
        # there is no runtime use_rocm=True flag in llama-cpp-python.
        # Passing it would raise a TypeError: unexpected keyword argument.
        try:
            from llama_cpp import Llama
            
            self.llm = Llama(
                model_path=model_path,
                n_gpu_layers=-1,  # All layers to GPU (-1 = all)
                n_ctx=n_ctx,
                verbose=False
            )
            
            print(f"✅ ROCm backend initialized for device {device_id}")
            
        except ImportError as e:
            raise ImportError(
                "llama-cpp-python not installed with ROCm support. "
                "Install with:\n"
                f"pip install llama-cpp-python --index-url https://abetlen.github.io/llama-cpp-python/whl/rocm6.2"
            ) from e
    
    async def generate(self, prompt: str, max_tokens: int = 512) -> str:
        """Generate text using ROCm-accelerated llama.cpp."""
        
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


# Factory function for creating ROCm backend instances
def create_rocm_backend(model_path: str, device_id: int = 0, n_ctx: int = 8192):
    """
    Create a ROCm-accelerated llama.cpp backend.
    
    Args:
        model_path: Path to GGUF model file
        device_id: ROCm device ID (default: 0)
        n_ctx: Context window size (default: 8192)
        
    Returns:
        LLamaRocmBackend instance
        
    Raises:
        ImportError: If llama-cpp-python not installed with ROCm support
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
