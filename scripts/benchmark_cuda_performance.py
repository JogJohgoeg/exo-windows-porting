"""
CUDA GPU Performance Benchmark for Exo Windows Porting.

This script tests and compares CUDA GPU performance against CPU-only inference.

Author: Exo Windows Porting Team
License: MIT
"""

import asyncio
from typing import Dict, List, Optional
from dataclasses import dataclass
from datetime import datetime


@dataclass
class BenchmarkResult:
    """Benchmark result."""
    
    backend_type: str
    model_path: str
    prompt: str
    
    # Performance metrics
    ttft_ms: float  # Time to first token (ms)
    throughput_tok_s: float  # Tokens per second
    total_tokens: int
    
    # GPU usage (if applicable)
    gpu_memory_used_mb: Optional[int] = None
    gpu_power_w: Optional[float] = None


class CudaBenchmarkTool:
    """CUDA GPU performance benchmarking tool."""
    
    def __init__(self, model_path: str):
        self.model_path = model_path
        
        # Test prompts (varied complexity)
        self.prompts = [
            "What is the meaning of life?",
            "Explain quantum computing in simple terms.",
            "Write a short story about time travel.",
            "Solve this math problem: 2 + 2 * 2 - 1",
            "Translate 'Hello, world!' to French."
        ]
        
    async def benchmark_cpu(self, prompt: str, max_tokens: int = 512) -> BenchmarkResult:
        """Benchmark CPU-only inference."""
        
        import time
        
        start_time = time.time()
        
        try:
            from llama_cpp import Llama
            
            # Initialize with CPU only (n_gpu_layers=0)
            llm = Llama(
                model_path=self.model_path,
                n_gpu_layers=0,  # No GPU layers
                n_ctx=8192,
                verbose=False
            )
            
            result = llm(
                prompt=prompt,
                max_tokens=max_tokens,
                stop=None,
                echo=False
            )
            
        except Exception as e:
            print(f"❌ CPU benchmark failed: {e}")
            return None
        
        elapsed_ms = (time.time() - start_time) * 1000
        total_tokens = len(result['choices'][0]['text'].split())
        
        # Estimate throughput
        throughput = total_tokens / (elapsed_ms / 1000.0) if elapsed_ms > 0 else 0
        
        return BenchmarkResult(
            backend_type="CPU",
            model_path=self.model_path,
            prompt=prompt,
            ttft_ms=elapsed_ms,  # Simplified: assume TTFT ≈ total time for CPU
            throughput_tok_s=throughput,
            total_tokens=total_tokens
        )
    
    async def benchmark_cuda(self, device_id: int = 0) -> Optional[BenchmarkResult]:
        """Benchmark CUDA GPU inference."""
        
        import time
        
        start_time = time.time()
        
        try:
            from llama_cpp import Llama
            
            # Initialize with all layers on GPU
            llm = Llama(
                model_path=self.model_path,
                n_gpu_layers=-1,  # All layers to GPU
                n_ctx=8192,
                verbose=False
            )
            
            prompt = self.prompts[0]  # Use first prompt for benchmark
            
            result = llm(
                prompt=prompt,
                max_tokens=512,
                stop=None,
                echo=False
            )
            
        except Exception as e:
            print(f"❌ CUDA benchmark failed: {e}")
            return None
        
        elapsed_ms = (time.time() - start_time) * 1000
        total_tokens = len(result['choices'][0]['text'].split())
        
        # Estimate throughput
        throughput = total_tokens / (elapsed_ms / 1000.0) if elapsed_ms > 0 else 0
        
        return BenchmarkResult(
            backend_type="CUDA",
            model_path=self.model_path,
            prompt=prompt,
            ttft_ms=elapsed_ms,  # Simplified: assume TTFT ≈ total time for GPU
            throughput_tok_s=throughput,
            total_tokens=total_tokens
        )


async def run_benchmark(model_path: str = None) -> List[BenchmarkResult]:
    """Run full benchmark suite."""
    
    tool = CudaBenchmarkTool(model_path or "models/Qwen2.5-7B-Instruct.Q4_K_M.gguf")
    
    results = []
    
    print("🔍 Starting CUDA GPU Performance Benchmark...")
    print(f"   Model: {tool.model_path}")
    print(f"   Date: {datetime.utcnow().isoformat()}")
    print()
    
    # CPU benchmark (simplified)
    print("⏳ Running CPU-only benchmark...")
    cpu_result = await tool.benchmark_cpu(
        prompt="What is the meaning of life?",
        max_tokens=512
    )
    
    if cpu_result:
        results.append(cpu_result)
        print(f"   ✅ CPU: TTFT={cpu_result.ttft_ms:.0f}ms, Throughput={cpu_result.throughput_tok_s:.1f} tok/s")
    
    # CUDA benchmark (simplified)
    print("⏳ Running CUDA GPU benchmark...")
    cuda_result = await tool.benchmark_cuda(device_id=0)
    
    if cuda_result:
        results.append(cuda_result)
        print(f"   ✅ CUDA: TTFT={cuda_result.ttft_ms:.0f}ms, Throughput={cuda_result.throughput_tok_s:.1f} tok/s")
        
        # Calculate speedup
        if cpu_result and cuda_result.throughput_tok_s > 0:
            speedup = cuda_result.throughput_tok_s / cpu_result.throughput_tok_s
            print(f"   🚀 Speedup: {speedup:.2f}x")
    
    return results


def print_summary(results: List[BenchmarkResult]) -> None:
    """Print benchmark summary."""
    
    if not results:
        print("⚠️ No results to display!")
        return
    
    print("\n📊 Benchmark Summary:")
    print("-" * 80)
    print(f"{'Backend':<15} {'TTFT (ms)':<12} {'Throughput (tok/s)':<18} {'Total Tokens':<12}")
    print("-" * 80)
    
    for result in results:
        print(f"{result.backend_type:<15} {result.ttft_ms:<12.0f} {result.throughput_tok_s:<18.1f} {result.total_tokens:<12}")
    
    print("-" * 80)


# Main entry point for testing
if __name__ == "__main__":
    async def main():
        results = await run_benchmark()
        
        if results:
            print_summary(results)
            
            # Save results to file
            import json
            
            output_data = []
            for result in results:
                output_data.append({
                    "backend_type": result.backend_type,
                    "model_path": result.model_path,
                    "prompt": result.prompt,
                    "ttft_ms": round(result.ttft_ms, 2),
                    "throughput_tok_s": round(result.throughput_tok_s, 2),
                    "total_tokens": result.total_tokens,
                    "gpu_memory_used_mb": result.gpu_memory_used_mb,
                    "gpu_power_w": result.gpu_power_w
                })
            
            output_file = f"benchmark_results_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.json"
            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump(output_data, f, indent=2)
            
            print(f"\n✅ Results saved to {output_file}")


if __name__ == "__main__":
    asyncio.run(main())
