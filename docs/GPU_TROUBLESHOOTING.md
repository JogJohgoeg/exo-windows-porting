# 🛠️ GPU 故障排查手册 - Exo Windows Porting

## 🔍 **常见问题分类**

### **1. ROCm for Windows 安装问题**
- [HIP_VISIBLE_DEVICES 无效](#hipvisibledevices-无效)
- [llama.cpp GPU 加载失败](#llamacpp-gpu-加载失败)
- [ROCm 版本不兼容](#rocm-版本不兼容)

### **2. NVIDIA CUDA 安装问题**
- [CUDA Toolkit 检测失败](#cuda-toolkit-检测失败)
- [llama-cpp-python CUDA 编译错误](#llama-cpp-python-cuda-编译错误)

### **3. 性能异常问题**
- [GPU 加速未生效](#gpu-加速未生效)
- [推理速度低于预期](#推理速度低于预期)

---

## 🔧 **ROCm for Windows 常见问题**

### **问题 1: HIP_VISIBLE_DEVICES 无效**

#### **症状**
```bash
python -m exo_windows_porting --backend rocm
# 输出：GPU not detected! Falling back to CPU-only mode
```

#### **原因分析**
- ROCm 环境变量未正确设置
- GPU 驱动版本过旧
- HIP SDK 路径配置错误

#### **解决方案**

**Step 1: 检查当前环境**
```powershell
# 查看 ROCm 相关环境变量
set | findstr "HIP"

# 期望输出包含：HIP_PATH, HIP_VISIBLE_DEVICES
```

**Step 2: 手动设置环境变量**
```powershell
# 设置 HIP 路径
$env:HIP_PATH = "C:\Program Files\AMD\ROCm\7.2.0"

# 指定 GPU 设备 ID (0 = 第一块 GPU)
$env:HIP_VISIBLE_DEVICES = "0"

# 验证设置
echo $env:HIP_PATH
echo $env:HIP_VISIBLE_DEVICES
```

**Step 3: 重新运行应用**
```powershell
python -m exo_windows_porting --backend rocm --device 0
```

---

### **问题 2: llama.cpp GPU 加载失败**

#### **症状**
```python
from exo_windows_porting.backend import create_backend

backend = create_backend(
    model_path="models/model.gguf",
    backend_type="rocm"
)
# 抛出：RuntimeError: Failed to initialize llama.cpp with ROCm
```

#### **原因分析**
- llama-cpp-python 未正确编译 ROCm 支持
- GPU 显存不足
- GGUF 模型格式不兼容

#### **解决方案**

**Step 1: 检查 llama-cpp-python 版本**
```powershell
pip show llama-cpp-python

# 期望版本：>=0.2.87
```

**Step 2: 重新安装 ROCm 支持版**
```powershell
# 卸载现有版本
pip uninstall llama-cpp-python -y

# 安装 ROCm 支持版
pip install "llama-cpp-python>=0.2.87" --index-url https://abetlen.github.io/llama-cpp-python/rocm61

# 验证安装
python -c "import llama_cpp; print(llama_cpp.__version__)"
```

**Step 3: 降级到 CPU-only (备选方案)**
```python
from exo_windows_porting.backend import create_backend

backend = create_backend(
    model_path="models/model.gguf",
    backend_type="cpu"  # 强制 CPU-only
)
```

---

### **问题 3: ROCm 版本不兼容**

#### **症状**
```bash
hipconfig --version
# 输出：HIP version X.XX (不支持的 GPU)
```

#### **原因分析**
- ROCm 6.1.x 技术预览版与某些显卡不兼容
- Python 3.12+ PyTorch ROCm 构建问题

#### **解决方案**

**Step 1: 检查当前 ROCm 版本**
```powershell
hipconfig --version
```

**Step 2: 降级到稳定版 (推荐)**
```powershell
# 卸载现有 ROCm
winget uninstall AMD.RadeonSoftwareAdrenalin2024-ROCM

# 安装 ROCm 7.2.x (最新稳定版)
winget install AMD.RadeonSoftwareAdrenalin2024-ROCM --version "7.2.1"

# 验证安装
hipconfig --version
```

**Step 3: 降级 Python 版本 (备选)**
```powershell
# 卸载当前 Python 3.12+
winget uninstall Python.Python.3.12

# 安装 Python 3.11
winget install Python.Python.3.11

# 重新创建虚拟环境
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

---

## 🔧 **NVIDIA CUDA 常见问题**

### **问题 4: CUDA Toolkit 检测失败**

#### **症状**
```powershell
nvcc --version
# 输出：'nvcc' is not recognized as an internal or external command
```

#### **原因分析**
- CUDA Toolkit 未正确安装
- PATH 环境变量缺失

#### **解决方案**

**Step 1: 重新安装 CUDA Toolkit**
```powershell
# 下载 CUDA 12.1+
Invoke-WebRequest -Uri "https://developer.download.nvidia.com/compute/cuda/12.1.0/local_installers/cuda_12.1.0_windows.exe" -OutFile "C:\Temp\cuda_12.1.0_windows.exe"

# 安装 (静默模式)
Start-Process "C:\Temp\cuda_12.1.0_windows.exe" -ArgumentList "-s" -Wait
```

**Step 2: 配置环境变量**
```powershell
# 添加 CUDA bin 目录到 PATH
$env:Path += ";C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.1\bin"

# 验证安装
nvcc --version

# 期望输出：Cuda compilation tools, release 12.1,X XXXX
```

---

### **问题 5: llama-cpp-python CUDA 编译错误**

#### **症状**
```powershell
pip install "llama-cpp-python>=0.2.87" --index-url https://abetlen.github.io/llama-cpp-python/cu121
# 输出：error: command 'cl.exe' failed with exit code 2
```

#### **原因分析**
- Visual Studio Build Tools 未安装
- MSVC v143 编译器缺失

#### **解决方案**

**Step 1: 安装 Visual Studio Build Tools**
```powershell
# 下载 Build Tools 启动器
Invoke-WebRequest -Uri "https://aka.ms/vs/17/release/vs_buildtools.exe" -OutFile "C:\Temp\vs_buildtools.exe"

# 安装 C++ Workload
Start-Process "C:\Temp\vs_buildtools.exe" -ArgumentList "--add Microsoft.VisualStudio.Workload.VCTools", "--includeRecommended", "--quiet", "--wait" -Wait
```

**Step 2: 重新编译 llama-cpp-python**
```powershell
# 清理缓存
pip cache purge

# 强制重新编译
pip install "llama-cpp-python>=0.2.87" --index-url https://abetlen.github.io/llama-cpp-python/cu121 --no-cache-dir
```

---

## 📊 **性能异常问题**

### **问题 6: GPU 加速未生效**

#### **症状**
- CPU-only 模式运行 (无 GPU 加速)
- 推理速度无明显提升

#### **诊断步骤**

**Step 1: 检查后端类型**
```python
from exo_windows_porting.backend import create_backend

backend = create_backend(
    model_path="models/model.gguf",
    backend_type="rocm"  # 或 "cuda"
)

print(f"Backend type: {backend.config.backend_type}")
print(f"GPU device: {backend.config.gpu_device}")
```

**Step 2: 验证 GPU 加载状态**
```python
# ROCm 后端检查
if backend.config.use_rocm and sys.platform == 'win32':
    if os.environ.get('HIP_VISIBLE_DEVICES'):
        print("✅ ROCm enabled")
    else:
        print("❌ ROCm not enabled (missing HIP env vars)")

# CUDA 后端检查  
elif backend.config.backend_type == "cuda":
    import torch
    if torch.cuda.is_available():
        print(f"✅ CUDA available on {torch.cuda.device_count()} GPUs")
    else:
        print("❌ CUDA not available")
```

---

### **问题 7: 推理速度低于预期**

#### **症状**
- TTFT > 100ms (预期 < 60ms)
- Throughput < 500 tok/s (预期 > 800 tok/s)

#### **优化建议**

**Step 1: 增加 GPU 层数**
```python
from exo_windows_porting.backend import create_backend

backend = create_backend(
    model_path="models/model.gguf",
    backend_type="rocm",
    gpu_layers=-1  # -1 = 所有层都加载到 GPU
)
```

**Step 2: 优化上下文窗口**
```python
from exo_windows_porting.backend import create_backend

backend = create_backend(
    model_path="models/model.gguf",
    backend_type="rocm",
    n_ctx=4096  # 默认 4K，可根据需求调整
)
```

**Step 3: 检查 GPU 显存使用**
```powershell
# ROCm GPU 监控
hipconfig --list-devices

# NVIDIA GPU 监控
nvidia-smi
```

---

## 📋 **故障排查清单**

### **ROCm for Windows**
- [ ] HIP_PATH 环境变量已设置
- [ ] HIP_VISIBLE_DEVICES = "0"
- [ ] ROCm 版本 ≥ 6.1.x 或 7.2.x
- [ ] llama-cpp-python ≥ 0.2.87 with ROCm support
- [ ] GPU 型号在支持列表中 (RX 7900 XTX/XT, PRO W7900)

### **NVIDIA CUDA**
- [ ] CUDA Toolkit 12.1+ 已安装
- [ ] nvcc --version 正常工作
- [ ] llama-cpp-python with CUDA support
- [ ] Visual Studio Build Tools (C++ Workload)
- [ ] GPU 型号在支持列表中 (RTX/A/H系列)

### **通用问题**
- [ ] Python 版本 3.10-3.12
- [ ] llama.cpp GGUF 格式正确
- [ ] GPU 显存足够 (>4GB for 7B model)
- [ ] 防火墙未阻止 P2P 通信

---

## 📞 **获取帮助**

### **官方资源**
- [AMD ROCm Documentation](https://rocm.docs.amd.com/)
- [NVIDIA CUDA Toolkit Docs](https://docs.nvidia.com/cuda/)
- [llama.cpp GitHub Issues](https://github.com/ggerganov/llama.cpp/issues)

### **社区支持**
- Discord: [邀请链接待添加]
- GitHub Issues: [报告问题或请求功能]
- Twitter/X: [@exo_windows_porting](https://twitter.com/exo_windows_porting) (待创建)

---

**最后更新**: 2026-04-01  
**版本**: v0.1-alpha  
**状态**: ✅ ROCm/CUDA 故障排查指南已就绪！
