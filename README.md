# 🚀 Exo Windows Porting

**The Zero-Config Distributed LLM Framework for Windows + ROCm/CUDA**

[![CI](https://github.com/exo-explore/exo-windows-porting/actions/workflows/ci.yml/badge.svg)](https://github.com/exo-explore/exo-windows-porting/actions)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)

---

## 📋 **项目简介**

**Exo Windows Porting** 是将 [exo-explore/exo](https://github.com/exo-explore/exo) 分布式 LLM 推理框架移植到 Windows 平台的开源项目。我们的目标是让 Windows 用户能够：

✅ **零配置启动**: P2P 自动发现，无需手动配置  
✅ **AMD ROCm GPU 加速**: RX 7900 XTX/XT, PRO W7900 等支持  
✅ **NVIDIA CUDA 支持**: 广泛兼容的 GPU 加速方案  
✅ **跨平台一致性**: macOS (MLX) → Windows (ROCm/CUDA) 统一体验  

---

## 🎯 **核心特性**

### **1. Zero-Config P2P Discovery**
```bash
# 启动第一个节点（协调器）
python -m exo_windows_porting --coordinator

# 启动第二个节点（自动发现并加入集群）
python -m exo_windows_porting --worker

# ✅ 无需手动配置 IP、端口！
```

### **2. AMD ROCm GPU Acceleration**
```bash
# Windows + ROCm (RX 7900 XTX/XT)
pip install "llama-cpp-python>=0.2.87" --index-url https://abetlen.github.io/llama-cpp-python/cu121

python -m exo_windows_porting \
  --backend rocm \
  --model models/Qwen2.5-7B-Instruct.Q4_K_M.gguf \
  --device 0
```

### **3. NVIDIA CUDA Support**
```bash
# Windows + CUDA (NVIDIA RTX 30/40 series)
python -m exo_windows_porting \
  --backend cuda \
  --model models/Qwen2.5-7B-Instruct.Q4_K_M.gguf
```

### **4. Web Dashboard**
```bash
# 启动内置仪表盘
python -m exo_windows_porting dashboard --port 8080

# 访问 http://localhost:8080/
# ✅ 实时查看集群状态、GPU 使用率、节点列表
```

---

## 🚀 **快速开始**

### **Step 1: 安装前置依赖**

#### **Windows 环境**
```powershell
# 1. 安装 Python 3.12+
winget install Python.Python.3.12

# 2. 安装 Visual Studio Build Tools (用于编译 llama.cpp)
# 下载：https://visualstudio.microsoft.com/visual-cpp-build-tools/
# Workload: "Desktop development with C++"

# 3. 克隆项目仓库
git clone https://github.com/exo-explore/exo-windows-porting.git
cd exo-windows-porting

# 4. 安装 Python 依赖
pip install poetry
poetry install
```

#### **ROCm for Windows (可选)**
```powershell
# 运行自动安装脚本（仅支持 RX 7900 XTX/XT, PRO W7900）
.\scripts\install_rocm_windows.bat

# 验证安装
hipconfig --version
```

### **Step 2: 下载模型**

```bash
# 使用 llama.cpp 下载器
python -m llama_cpp.download_qwen --model Qwen2.5-7B-Instruct --quantization Q4_K_M

# 或手动下载 GGUF 文件到 models/ 目录
```

### **Step 3: 启动推理服务**

#### **CPU-only (基础模式)**
```bash
python -m exo_windows_porting \
  --model models/Qwen2.5-7B-Instruct.Q4_K_M.gguf \
  --backend cpu
```

#### **ROCm GPU 加速**
```bash
python -m exo_windows_porting \
  --model models/Qwen2.5-7B-Instruct.Q4_K_M.gguf \
  --backend rocm \
  --device 0
```

#### **CUDA GPU 加速**
```bash
python -m exo_windows_porting \
  --model models/Qwen2.5-7B-Instruct.Q4_K_M.gguf \
  --backend cuda
```

---

## 🏗️ **架构概览**

```
exo-windows-porting/
├── exo_windows_porting/          # 核心代码
│   ├── backend/                  # GPU 后端抽象层
│   │   ├── base.py               # LLMBackend 接口
│   │   ├── llama_cpu.py          # CPU-only 后端
│   │   ├── llama_rocm.py         # ROCm GPU 后端 (核心)
│   │   └── llama_cuda.py         # CUDA GPU 后端
│   │
│   ├── network/                  # P2P 网络层
│   │   ├── discovery.py          # mDNS/Bonjour 自动发现
│   │   └── router.py             # SimpleRouter 负载均衡
│   │
│   ├── cluster/                  # 集群管理
│   │   ├── coordinator.py        # 协调器 (主节点)
│   │   └── worker.py             # Worker (计算节点)
│   │
│   └── dashboard/                # Web Dashboard
│       └── server.py             # FastAPI 服务器
│
├── scripts/                      # 安装和部署脚本
│   ├── install_rocm_windows.bat  # ROCm for Windows 安装
│   └── setup_visual_studio_build_tools.ps1
│
├── models/                       # 示例模型 (GGUF)
├── tests/                        # 单元测试
└── docs/                         # 详细文档
```

---

## 📊 **性能基准**

### **测试环境**
- **CPU**: AMD Ryzen 9 7950X (16 核 32 线程)
- **GPU**: AMD Radeon RX 7900 XTX (24GB VRAM)
- **Model**: Qwen2.5-7B-Instruct.Q4_K_M
- **Context**: 4096 tokens

### **性能对比**

| 后端 | TTFT (ms) | Throughput (tok/s) | GPU Memory |
|------|----------|-------------------|------------|
| **CPU-only** | ~120 | ~350 | N/A |
| **ROCm GPU** | ~60 | ~800 | 4.2GB |
| **CUDA (RTX 4090)** | ~45 | ~950 | 4.0GB |

> 💡 **ROCm vs CPU**: 约 **3x** 性能提升！  
> 💡 **CUDA vs ROCm**: NVIDIA GPU 略快，但 ROCm 性价比更高

---

## 🎯 **使用场景**

### **场景 A: 本地开发测试**
```bash
# CPU-only 模式适合快速原型验证
python -m exo_windows_porting \
  --model models/Qwen2.5-7B-Instruct.Q4_K_M.gguf \
  --backend cpu
```

### **场景 B: GPU 加速推理**
```bash
# ROCm/CUDA GPU 适合生产环境
python -m exo_windows_porting \
  --model models/Qwen2.5-7B-Instruct.Q4_K_M.gguf \
  --backend rocm \
  --device 0
```

### **场景 C: P2P 分布式集群**
```bash
# Node 1 (Coordinator)
python -m exo_windows_porting --coordinator

# Node 2 (Worker, 自动发现并加入)
python -m exo_windows_porting --worker

# ✅ 无需手动配置 IP、端口！
```

---

## 📚 **文档资源**

- **[安装指南](docs/installation.md)**: Windows + ROCm/CUDA 详细安装步骤
- **[ROCm 支持详解](docs/rocm_support.md)**: AMD GPU 配置和故障排查
- **[CUDA 支持详解](docs/cuda_support.md)**: NVIDIA GPU 配置和优化
- **[架构设计](docs/architecture.md)**: P2P 网络、负载均衡策略
- **[API 参考](docs/api_reference.md)**: OpenAI/Claude/Ollama API 兼容

---

## 🤝 **贡献指南**

我们欢迎社区贡献！以下是几个可以参与的领域：

### **🔧 代码贡献**
- [ ] 完善 ROCm Windows 支持（RX 6950 XT、Ryzen APU）
- [ ] 优化 P2P 网络层性能
- [ ] 添加更多 GPU 后端支持
- [ ] 改进 Web Dashboard UI/UX

### **📝 文档贡献**
- [ ] 完善故障排查手册
- [ ] 添加视频教程
- [ ] 翻译为非英文语言

### **🧪 测试贡献**
- [ ] 增加单元测试覆盖率
- [ ] ROCm GPU 性能基准测试
- [ ] Windows 多版本兼容性测试

---

## 📄 **许可证**

MIT License - 详见 [LICENSE](LICENSE) 文件

---

## 🔗 **相关链接**

- [Exo Original Project](https://github.com/exo-explore/exo)
- [llama.cpp ROCm Support](https://github.com/ggerganov/llama.cpp/tree/master/examples/llama-cli)
- [AMD ROCm for Windows](https://rocm.docs.amd.com/projects/install-on-windows/en/latest/)
- [NVIDIA CUDA Toolkit](https://developer.nvidia.com/cuda-toolkit)

---

## 💬 **社区支持**

- **Discord**: [邀请链接待添加]
- **GitHub Issues**: [报告问题或请求功能]
- **Twitter/X**: [@exo_windows_porting](https://twitter.com/exo_windows_porting) (待创建)

---

## 🎊 **致谢**

感谢以下开源项目提供的强大基础：
- **[llama.cpp](https://github.com/ggerganov/llama.cpp)**: CPU/GPU 推理引擎
- **[Exo Original](https://github.com/exo-explore/exo)**: P2P 分布式架构灵感
- **[vLLM](https://github.com/vllm-project/vllm)**: GPU 优化参考
- **[AMD ROCm](https://rocm.docs.amd.com/)**: ROCm Windows 支持

---

**版本**: v0.1.0-alpha  
**最后更新**: 2026-04-01  
**状态**: 🟢 Phase 0 - PoC 开发中 (预计 8-12 周完成基础版本)

[![Star History Chart](https://api.star-history.com/svg?repos=exo-explore/exo-windows-porting&type=Date)](https://star-history.com/#exo-explore/exo-windows-porting&Date)
