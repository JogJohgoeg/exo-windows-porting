"""
CPU Backend for Exo Windows Porting.

This module provides CPU-only inference using llama-cpp-python.

Author: Exo Windows Porting Team
License: MIT
"""

from typing import Optional, Dict, List, Any
import time


class LLamaCpuBackend:
    """llama.cpp CPU backend."""
    
    def __init__(self, model_path: str, n_ctx: int = 8192, verbose: bool = False):
        self.model_path = model_path
        self.n_ctx = n_ctx
        self.verbose = verbose
        
        # Initialize llama-cpp-python for CPU inference
        try:
            from llama_cpp import Llama
            
            self.llm = Llama(
                model_path=model_path,
                use_mmap=True,  # Memory mapping for faster loading
                use_lock=True,  # Thread-safe
                n_threads=4,  # Number of CPU threads to use
                n_ctx=n_ctx,
                verbose=False
            )
            
            if verbose:
                print(f"✅ CPU backend initialized from {model_path}")
                
        except ImportError as e:
            raise ImportError(
                "llama-cpp-python not installed. Install with:\npip install llama-cpp-python"
            ) from e
    
    async def generate(self, prompt: str, max_tokens: int = 512) -> str:
        """Generate text using CPU-accelerated llama.cpp."""
        
        start_time = time.time()
        
        result = self.llm(
            prompt=prompt,
            max_tokens=max_tokens,
            stop=None,
            echo=False
        )
        
        elapsed_ms = (time.time() - start_time) * 1000
        
        if self.verbose:
            print(f"⏱️ CPU generation completed in {elapsed_ms:.2f}ms")
        
        return result['choices'][0]['text']


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
