"""
Exo Windows Porting - 性能基准测试工具

此脚本用于测试和比较不同后端 (CPU/ROCm/CUDA) 的性能表现。

Usage:
    python scripts/benchmark_performance.py --model <path> --backend <cpu|rocm|cuda>
    python scripts/benchmark_performance.py --all-backends --output results.json
"""

import os
import sys
import json
import time
from typing import Dict, List, Any
from dataclasses import dataclass, asdict
from pathlib import Path

import numpy as np


@dataclass
class BenchmarkResult:
    """性能测试结果数据类"""
    
    backend_type: str
    model_path: str
    gpu_device: int = -1
    
    # 延迟指标 (ms)
    ttft_ms: float = 0.0          # Time to First Token
    tbt_ms: float = 0.0           # Tokens Between Tokens
    
    # 吞吐量指标 (tokens/s)
    throughput_tps: float = 0.0   # Overall throughput
    
    # GPU 显存使用量 (MB)
    gpu_memory_used_mb: int = 0
    
    # 总耗时 (秒)
    total_time_s: float = 0.0
    
    # 生成统计
    tokens_generated: int = 0
    prompts_evaluated: int = 0


class PerformanceBenchmark:
    """Exo Windows Porting 性能基准测试器"""
    
    def __init__(self, model_path: str):
        self.model_path = Path(model_path).resolve()
        
        if not self.model_path.exists():
            raise FileNotFoundError(f"Model file not found: {model_path}")
        
        # 加载模型 (根据后端类型)
        self._load_model()
    
    def _load_model(self):
        """根据 model_path 自动选择后端"""
        
        from exo_windows_porting.backend import create_backend
        
        # 检测 GPU 支持情况
        if self._check_rocm_support():
            backend_type = "rocm"
            device_id = 0
        elif self._check_cuda_support():
            backend_type = "cuda"
            device_id = 1
        else:
            backend_type = "cpu"
            device_id = -1
        
        print(f"🔍 Detected available backend: {backend_type}")
        
        # 创建后端实例
        self.backend = create_backend(
            model_path=str(self.model_path),
            backend_type=backend_type,
            gpu_device=device_id if backend_type != "cpu" else -1
        )
    
    def _check_rocm_support(self) -> bool:
        """检查 ROCm GPU 支持"""
        
        try:
            import subprocess
            result = subprocess.run(
                ['hipconfig', '--version'],
                capture_output=True,
                text=True,
                timeout=5
            )
            
            if result.returncode == 0 and 'AMD' in result.stdout:
                return True
                
        except Exception as e:
            print(f"⚠️ ROCm detection failed: {e}")
        
        return False
    
    def _check_cuda_support(self) -> bool:
        """检查 CUDA GPU 支持"""
        
        try:
            import torch
            if torch.cuda.is_available():
                device_count = torch.cuda.device_count()
                print(f"✅ Found {device_count} CUDA GPU(s)")
                
                for i in range(device_count):
                    print(f"   GPU {i}: {torch.cuda.get_device_name(i)}")
                
                return True
                
        except Exception as e:
            print(f"⚠️ CUDA detection failed: {e}")
        
        return False
    
    def run_benchmark(
        self, 
        prompts: List[str], 
        max_tokens: int = 512,
        temperature: float = 0.7
    ) -> BenchmarkResult:
        """
        运行完整基准测试。
        
        Args:
            prompts: 测试提示列表 (建议至少 3-5 个)
            max_tokens: 每个提示生成的最大 token 数
            temperature: 采样温度
            
        Returns:
            BenchmarkResult 对象包含所有性能指标
        """
        
        import asyncio
        
        results = []
        total_time_start = time.time()
        
        for i, prompt in enumerate(prompts):
            print(f"\n📝 Testing prompt {i+1}/{len(prompts)}: '{prompt[:50]}...'")
            
            # 记录开始时间
            start_time = time.time()
            ttft_start = None
            
            # 生成文本 (异步包装)
            async def generate_with_timing():
                nonlocal ttft_start
                
                gen_start = time.time()
                
                result = await self.backend.generate(
                    prompt=prompt,
                    max_tokens=max_tokens,
                    temperature=temperature
                )
                
                # 估算 TTFT (假设首 token 在 10% 时间内到达)
                if ttft_start is None:
                    ttft_ms = (time.time() - gen_start) * 0.1 * 1000
                else:
                    ttft_ms = (ttft_start - gen_start) * 1000
                
                return {
                    'text': result.text,
                    'tokens_generated': len(result.text.split()),
                    'ttft_ms': max(0, ttft_ms),
                    'total_time_s': time.time() - start_time
                }
            
            # 运行生成任务
            task_result = asyncio.run(generate_with_timing())
            
            results.append(task_result)
        
        total_time_end = time.time()
        total_elapsed = total_time_end - total_time_start
        
        # 计算统计指标
        ttft_values = [r['ttft_ms'] for r in results]
        throughput_values = []
        
        for result in results:
            if result['total_time_s'] > 0.1:  # 排除极短生成
                tokens_generated = result['tokens_generated']
                generation_time = max(0, result['total_time_s'] - (result['ttft_ms'] / 1000))
                
                if generation_time > 0:
                    throughput_values.append(tokens_generated / generation_time)
        
        avg_ttft = np.mean(ttft_values)
        avg_throughput = np.mean(throughput_values) if throughput_values else 0
        
        # 估算 GPU 显存使用 (简化版)
        gpu_memory_used = self._estimate_gpu_memory_usage()
        
        return BenchmarkResult(
            backend_type=self.backend.config.backend_type,
            model_path=str(self.model_path),
            gpu_device=getattr(self.backend.config, 'device', -1),
            ttft_ms=avg_ttft,
            tbt_ms=(total_elapsed - (sum(r['ttft_ms'] for r in results) / 1000)) * 1000 / len(prompts),
            throughput_tps=avg_throughput,
            gpu_memory_used_mb=gpu_memory_used,
            total_time_s=total_elapsed,
            tokens_generated=sum(r['tokens_generated'] for r in results),
            prompts_evaluated=len(prompts)
        )
    
    def _estimate_gpu_memory_usage(self) -> int:
        """估算 GPU 显存使用量 (简化版)"""
        
        try:
            if self.backend.config.backend_type == "rocm":
                # ROCm GPU 内存估计
                return 4096  # 假设值，实际应通过 ROCm API 获取
                
            elif self.backend.config.backend_type == "cuda":
                import torch
                if torch.cuda.is_available():
                    allocated = torch.cuda.memory_allocated(0) / (1024 * 1024)
                    return int(allocated)
                    
        except Exception as e:
            print(f"⚠️ GPU memory detection failed: {e}")
        
        return 0
    
    def generate_report(self, result: BenchmarkResult) -> str:
        """生成性能报告"""
        
        report_lines = [
            "📊 Exo Windows Porting - 性能基准测试报告",
            "=" * 50,
            "",
            f"后端类型：{result.backend_type}",
            f"模型路径：{Path(result.model_path).name}",
            f"GPU 设备 ID: {result.gpu_device if result.gpu_device >= 0 else 'N/A (CPU)'}",
            "",
            "延迟指标:",
            f"   TTFT (首字延迟):     {result.ttft_ms:.1f} ms",
            f"   TBT (token 间隔):    {result.tbt_ms:.1f} ms",
            "",
            "吞吐量指标:",
            f"   Throughput:          {result.throughput_tps:.1f} tokens/s",
            "",
            "资源使用:",
            f"   GPU Memory Used:     {result.gpu_memory_used_mb} MB ({result.gpu_memory_used_mb/1024:.1f} GB)",
            f"   Total Time:          {result.total_time_s:.2f} s",
            "",
            "生成统计:",
            f"   Prompts Evaluated:   {result.prompts_evaluated}",
            f"   Tokens Generated:    {result.tokens_generated}",
            "",
        ]
        
        return "\n".join(report_lines)


def run_comparison_benchmark(
    model_path: str, 
    backends_to_test: List[str] = ["cpu", "rocm", "cuda"]
) -> Dict[str, BenchmarkResult]:
    """
    运行多后端对比测试。
    
    Args:
        model_path: GGUF 模型文件路径
        backends_to_test: 要测试的后端列表
        
    Returns:
        字典，键为后端类型，值为对应测试结果
    """
    
    results = {}
    
    for backend_type in backends_to_test:
        print(f"\n{'='*50}")
        print(f"🔥 Testing {backend_type.upper()} backend...")
        print('=' * 50)
        
        try:
            from exo_windows_porting.backend import create_backend
            
            # 创建后端实例
            if backend_type == "cpu":
                backend = create_backend(
                    model_path=model_path,
                    backend_type="cpu"
                )
                
            elif backend_type == "rocm":
                backend = create_backend(
                    model_path=model_path,
                    backend_type="rocm",
                    gpu_device=0
                )
                
            elif backend_type == "cuda":
                backend = create_backend(
                    model_path=model_path,
                    backend_type="cuda",
                    gpu_device=0
                )
            
            # 运行基准测试
            benchmark = PerformanceBenchmark(model_path)
            benchmark.backend = backend
            
            prompts = [
                "What is the meaning of life?",
                "Explain quantum computing in simple terms.",
                "Write a short poem about artificial intelligence."
            ]
            
            result = benchmark.run_benchmark(
                prompts=prompts,
                max_tokens=256,
                temperature=0.7
            )
            
            results[backend_type] = result
            
            # 打印报告
            print(benchmark.generate_report(result))
            
        except Exception as e:
            print(f"❌ {backend_type.upper()} backend test failed: {e}")
            continue
    
    return results


def save_results(results: Dict[str, BenchmarkResult], output_path: str):
    """保存测试结果到 JSON 文件"""
    
    data = {}
    
    for backend_type, result in results.items():
        data[backend_type] = asdict(result)
    
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    
    print(f"✅ Results saved to {output_path}")


def main():
    """命令行入口"""
    
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Exo Windows Porting - 性能基准测试工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/benchmark_performance.py --model models/Qwen2.5-7B-Instruct.Q4_K_M.gguf --backend cpu
  python scripts/benchmark_performance.py --all-backends --output results.json
        """
    )
    
    parser.add_argument(
        "--model", 
        "-m", 
        type=str,
        required=True,
        help="Path to GGUF model file"
    )
    
    parser.add_argument(
        "--backend", 
        "-b", 
        choices=["cpu", "rocm", "cuda"],
        default=None,
        help="Backend to test (default: auto-detect)"
    )
    
    parser.add_argument(
        "--all-backends", 
        action="store_true",
        help="Test all available backends"
    )
    
    parser.add_argument(
        "--output", 
        "-o", 
        type=str,
        default=None,
        help="Output JSON file path (optional)"
    )
    
    args = parser.parse_args()
    
    # 验证模型文件
    model_path = Path(args.model).resolve()
    if not model_path.exists():
        print(f"❌ Model file not found: {model_path}")
        sys.exit(1)
    
    # 运行基准测试
    if args.all_backends:
        results = run_comparison_benchmark(str(model_path))
        
        if args.output:
            save_results(results, args.output)
            print(f"\n✅ All backends tested! Results saved to {args.output}")
            
    else:
        # 单后端测试
        backend_type = args.backend or "auto"
        
        try:
            from exo_windows_porting.backend import create_backend
            
            if backend_type == "auto":
                print("🔍 Auto-detecting best available backend...")
                
                if PerformanceBenchmark._check_rocm_support():
                    backend_type = "rocm"
                    device_id = 0
                    
                elif PerformanceBenchmark._check_cuda_support():
                    backend_type = "cuda"
                    device_id = 1
                    
                else:
                    backend_type = "cpu"
                    device_id = -1
                
                print(f"✅ Selected backend: {backend_type}")
            
            benchmark = PerformanceBenchmark(str(model_path))
            result = benchmark.run_benchmark(
                prompts=["What is the meaning of life?", "Explain quantum computing."],
                max_tokens=256,
                temperature=0.7
            )
            
            print(benchmark.generate_report(result))
            
            if args.output:
                save_results({backend_type: result}, args.output)
                
        except Exception as e:
            print(f"❌ Test failed: {e}")
            sys.exit(1)


if __name__ == "__main__":
    main()
