# TASK-002: AMD ROCm Windows 集成与优化

## 📋 **任务描述**

深入研究 llama.cpp ROCm Windows 支持方案，实现完整的 GPU 加速推理后端。

## 🎯 **目标**

- ✅ 完成 ROCm for Windows 环境自动配置
- ✅ llama-cpp-python ROCm 支持集成
- ✅ GPU 加载、推理性能基准测试
- ✅ 故障排查手册编写

## 👤 **负责人**

`llama_rocm_expert` (待启动)

## 📅 **时间线**

- **预计开始**: Day 6 of Phase 0
- **预计完成**: Day 12 of Phase 0 (Week 2)
- **优先级**: Critical

---

## 🔍 **技术实现要点**

### **1. ROCm for Windows 环境分析**

#### **官方支持现状**

| GPU 型号 | ROCm 6.x | ROCm 7.x | Python 3.12+ |
|---------|----------|----------|--------------|
| RX 7900 XTX | ✅ | ✅ | ⚠️ |
| RX 7900 XT | ✅ | ✅ | ⚠️ |
| PRO W7900 | ✅ | ✅ | ✅ |
| RX 7700 XT | ✅ | ✅ | ✅ |

> 💡 **关键发现**: ROCm for Windows 技术预览版 (6.1.x) 和稳定版 (7.2.x) 均支持上述 GPU。

#### **环境要求**
```powershell
# 系统要求
Windows 11 22H2+      # 必须
Python 3.10-3.12     # Python 3.13 可能不兼容

# 必需组件
Visual Studio Build Tools 2022
MSVC v143            # C++ 编译器
Windows SDK 10.0.22621+
```

---

### **2. llama-cpp-python ROCm 支持方案**

#### **方案 A: 预编译 Wheel (推荐)**

```powershell
# NVIDIA CUDA 预编译 wheel
pip install "llama-cpp-python>=0.2.87" --index-url https://abetlen.github.io/llama-cpp-python/cu121

# AMD ROCm 预编译 wheel (如果存在)
pip install "llama-cpp-python>=0.2.87" --index-url https://abetlen.github.io/llama-cpp-python/rocm61
```

#### **方案 B: 源码编译 (备选)**

```powershell
# 安装 ROCm SDK
set HIP_PATH=C:\Program Files\AMD\ROCm\7.2.0
set HSA_OVERRIDE_GFX_VERSION=11.0.0  # RX 7900 XTX

# 编译 llama.cpp
cd llama.cpp
cmake -B build -DCMAKE_BUILD_TYPE=Release -DGGML_HIPBLAS=ON
cmake --build build --config Release

# Python 绑定
pip install llama-cpp-python==0.2.87 \
    --global-option=build_ext \
    --global-option="--library-paths=C:\Program Files\AMD\ROCm\7.2.0\lib"
```

---

### **3. GPU 加载与推理实现**

#### **llama_rocm.py 核心代码**

```python
import os
from llama_cpp import Llama

class ROCmBackend:
    """AMD ROCm GPU 加速后端"""
    
    def __init__(self, model_path: str, device_id: int = 0):
        self.model_path = model_path
        self.device_id = device_id
        
        # 设置 ROCm 环境变量
        os.environ['HIP_VISIBLE_DEVICES'] = str(device_id)
        
        # 初始化 llama.cpp
        self.llama = Llama(
            model_path=model_path,
            n_gpu_layers=-1,  # 所有层都加载到 GPU
            use_rocm=True,
            verbose=True
        )
    
    def generate(self, prompt: str, max_tokens: int = 512):
        """生成文本"""
        
        result = self.llama(
            prompt=prompt,
            max_tokens=max_tokens,
            temperature=0.7,
            top_p=0.9
        )
        
        return result['choices'][0]['text']

# 使用示例
backend = ROCmBackend("models/Qwen2.5-7B-Instruct.Q4_K_M.gguf", device_id=0)
output = backend.generate("What is the meaning of life?", max_tokens=256)
print(output)
```

---

### **4. 性能基准测试**

#### **测试脚本设计**

```python
import time
from typing import List, Dict

class ROCmBenchmark:
    """ROCm GPU 推理性能基准"""
    
    def __init__(self, backend):
        self.backend = backend
    
    async def run_benchmark(
        self, 
        prompts: List[str], 
        max_tokens: int = 512
    ) -> Dict:
        """
        运行完整基准测试。
        
        Returns:
            {
                "ttft_ms": float,          # Time to first token (ms)
                "throughput_tps": float,   # Tokens per second
                "total_time_s": float,     # 总耗时
                "gpu_memory_used_mb": int  # GPU 显存使用量
            }
        """
        
        import asyncio
        
        results = []
        
        for prompt in prompts:
            start_time = time.time()
            
            result = await self.backend.generate(prompt, max_tokens)
            
            end_time = time.time()
            total_time = end_time - start_time
            
            # 计算 TTFT (简化版：假设首 token 在 10% 时间内到达)
            ttft = total_time * 0.1
            
            results.append({
                "prompt": prompt[:50],
                "ttft_ms": ttft * 1000,
                "throughput_tps": len(result.split()) / (total_time - ttft),
                "total_time_s": total_time
            })
        
        # 统计汇总
        avg_ttft = sum(r["ttft_ms"] for r in results) / len(results)
        avg_throughput = sum(r["throughput_tps"] for r in results) / len(results)
        
        return {
            "ttft_ms": avg_ttft,
            "throughput_tps": avg_throughput,
            "total_time_s": total_time,
            "gpu_memory_used_mb": self._estimate_gpu_usage()
        }
    
    def _estimate_gpu_usage(self) -> int:
        """估算 GPU 显存使用量 (简化版)"""
        
        # TODO: 通过 ROCm API 获取实际显存使用情况
        
        return 4096  # 假设值

# 使用示例
prompts = [
    "What is the meaning of life?",
    "Explain quantum computing in simple terms.",
    "Write a poem about artificial intelligence."
]

benchmark = ROCmBenchmark(backend)
results = await benchmark.run_benchmark(prompts, max_tokens=256)

print("📊 ROCm GPU 性能基准:")
print(f"   TTFT: {results['ttft_ms']:.1f} ms")
print(f"   Throughput: {results['throughput_tps']:.1f} tokens/s")
print(f"   GPU Memory Used: {results['gpu_memory_used_mb']} MB")
```

---

## 🧪 **测试计划**

### **单元测试覆盖**

| 模块 | 测试项 | 预期结果 |
|------|--------|----------|
| `test_rocm_backend.py` | test_gpu_initialization | GPU 初始化成功 |
| | test_inference_accuracy | 推理输出正确性验证 |
| | test_performance_baseline | 性能达到 CPU 的 3x+ |

### **集成测试**

```bash
# 1. 环境检测
python scripts/test_rocm_environment.py

# 2. GPU 加载测试
python -m exo_windows_porting --backend rocm --model models/Qwen2.5-7B-Instruct.Q4_K_M.gguf --test-gpu-load

# 3. 性能基准测试
python scripts/benchmark_performance.py --backend rocp --gpu rx_7900_xtx
```

---

## 📚 **参考资源**

- [llama.cpp ROCm Support](https://github.com/ggerganov/llama.cpp/tree/master/examples/llama-cli)
- [AMD ROCm for Windows](https://rocm.docs.amd.com/projects/install-on-windows/en/latest/)
- [llama-cpp-python Installation Docs](https://llama-cpp-python.readthedocs.io/en/stable/installation/)

---

## ✅ **验收标准**

- [ ] ROCm for Windows 环境自动安装脚本成功运行
- [ ] llama.cpp GPU 加载成功率 = 100% (RX 7900 XTX/XT)
- [ ] 推理性能 ≥ CPU-only 的 3x
- [ ] TTFT < 100ms (7B 模型，4K context)

---

## 🚨 **故障排查手册**

### **常见问题 1: HIP_VISIBLE_DEVICES 无效**

```bash
# 检查 ROCm 环境变量
set | findstr "HIP"

# 手动设置
set HIP_VISIBLE_DEVICES=0
```

### **常见问题 2: llama.cpp GPU 加载失败**

```python
# 降级到 CPU 模式
llama = Llama(
    model_path=model_path,
    n_gpu_layers=0,  # 强制 CPU-only
    verbose=True
)
```

### **常见问题 3: ROCm 版本不兼容**

```powershell
# 检查当前 ROCm 版本
hipconfig --version

# 降级到稳定版 (7.2.x)
winget install AMD.RadeonSoftwareAdrenalin2024-ROCM
```

---

**任务创建时间**: 2026-04-01  
**状态**: 🟢 待启动  
**优先级**: Critical
