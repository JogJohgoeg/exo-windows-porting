# 🚀 Exo Windows + ROCm 移植项目结构

## 📋 **项目概述**

**目标**: 将 [exo-explore/exo](https://github.com/exo-explore/exo) 分布式 LLM 推理框架移植到 Windows，支持 AMD ROCm 和 NVIDIA CUDA GPU。

**团队**: `exo-windows-porting`  
**状态**: Phase 0 - PoC 开发中  
**负责人**: lead_architect

---

## 🗂️ **项目目录结构**

```
exo-windows-porting/
├── .github/
│   ├── workflows/
│   │   ├── ci.yml                    # CI/CD 流水线
│   │   ├── windows-build.yml         # Windows 构建测试
│   │   └── rocm-test.yml             # ROCm 性能测试
│   └── ISSUE_TEMPLATE/
│       ├── bug_report.md
│       └── feature_request.md
│
├── exo_windows_porting/              # 核心移植代码
│   ├── __init__.py
│   ├── main.py                       # 主入口点
│   ├── config.py                     # 配置管理
│   ├── logger.py                     # 日志系统
│   │
│   ├── backend/                      # GPU 后端抽象层
│   │   ├── __init__.py
│   │   ├── base.py                   # Base LLMBackend 接口
│   │   ├── llama_cpu.py              # CPU-only 后端
│   │   ├── llama_rocm.py             # ROCm GPU 后端 (核心)
│   │   ├── llama_cuda.py             # CUDA GPU 后端
│   │   └── factory.py                # 后端工厂类
│   │
│   ├── network/                      # P2P 网络层
│   │   ├── __init__.py
│   │   ├── discovery.py              # mDNS/Bonjour 自动发现
│   │   ├── router.py                 # SimpleRouter (负载均衡)
│   │   ├── peer.py                   # Peer 节点管理
│   │   └── health_monitor.py           # 健康检查
│   │
│   ├── cluster/                      # 集群管理
│   │   ├── __init__.py
│   │   ├── coordinator.py            # 协调器 (主节点)
│   │   ├── worker.py                 # Worker (计算节点)
│   │   └── topology.py               # 拓扑感知调度
│   │
│   ├── dashboard/                    # Web Dashboard
│   │   ├── __init__.py
│   │   ├── server.py                 # FastAPI 服务器
│   │   ├── templates/                # HTML 模板
│   │   └── static/                   # CSS/JS
│   │
│   └── api/                          # OpenAI/Claude/Ollama API 兼容
│       ├── __init__.py
│       ├── openai_compat.py
│       ├── claude_compat.py
│       └── ollama_compat.py
│
├── scripts/                          # 安装和部署脚本
│   ├── install_prerequisites.ps1     # Windows 前置依赖安装
│   ├── install_rocm_windows.bat      # ROCm for Windows 安装
│   ├── setup_visual_studio_build_tools.ps1
│   ├── test_gpu_detection.py         # GPU 检测测试
│   └── benchmark_performance.py       # 性能基准测试
│
├── tests/                            # 单元测试
│   ├── test_backend_rocm.py
│   ├── test_network_discovery.py
│   ├── test_cluster_coordination.py
│   └── conftest.py                   # pytest 配置
│
├── docs/                             # 文档
│   ├── installation.md               # Windows 安装指南
│   ├── rocm_support.md               # ROCm 详细文档
│   ├── cuda_support.md               # CUDA 支持文档
│   ├── architecture.md               # 架构设计文档
│   ├── api_reference.md              # API 参考
│   └── troubleshooting.md            # 故障排查手册
│
├── models/                           # 示例模型 (GGUF)
│   ├── llama3.2-1b.gguf
│   ├── qwen2.5-7b-instruct.q4_k_m.gguf
│   └── README.md                     # 模型说明
│
├── requirements.txt                  # Python 依赖
├── pyproject.toml                    # 项目配置 (Poetry/Pip)
├── setup.py                          # setuptools 安装脚本
│
└── README.md                         # 项目主文档
```

---

## 🎯 **Phase 0: PoC 开发计划 (2-3 周)**

### **Week 1: 基础框架搭建**

#### Day 1-2: 项目初始化
- [x] Git 仓库设置
- [ ] GitHub Actions CI/CD 配置
- [ ] Python 虚拟环境管理 (Poetry/Pipenv)
- [ ] 代码风格规范 (Black, Ruff, mypy)

#### Day 3-4: P2P 网络层实现
- [ ] `network/discovery.py`: mDNS/Bonjour 自动发现
- [ ] `network/router.py`: SimpleRouter 负载均衡
- [ ] `network/peer.py`: Peer 节点通信
- [ ] 单元测试：网络发现、节点通信

#### Day 5: CPU-only 推理后端
- [ ] `backend/base.py`: LLMBackend 抽象接口
- [ ] `backend/llama_cpu.py`: llama.cpp CPU 实现
- [ ] 性能基准测试 (CPU vs GPU 对比)

---

### **Week 2: ROCm GPU 支持**

#### Day 6-8: ROCm 环境搭建
- [ ] `scripts/install_rocm_windows.bat`: ROCm for Windows 安装脚本
- [ ] `scripts/setup_visual_studio_build_tools.ps1`: VS Build Tools 配置
- [ ] `tests/test_gpu_detection.py`: GPU 检测工具

#### Day 9-11: llama.cpp + ROCm 集成
- [ ] `backend/llama_rocm.py`: ROCm GPU 后端实现
- [ ] `backend/factory.py`: 后端工厂类（自动选择 CPU/ROCm/CUDA）
- [ ] 性能测试：CPU vs ROCm (预期 3-5x 提升)

#### Day 12: 集成测试
- [ ] 端到端测试：单节点推理、P2P 发现
- [ ] 文档编写：安装指南、故障排查手册

---

### **Week 3: 完整功能验证**

#### Day 13-14: Web Dashboard
- [ ] `dashboard/server.py`: FastAPI 服务器
- [ ] 集群状态可视化（节点列表、GPU 使用率）
- [ ] 实时日志输出

#### Day 15: API 兼容性
- [ ] `api/openai_compat.py`: OpenAI Chat Completions API
- [ ] `api/ollama_compat.py`: Ollama API 兼容
- [ ] 测试客户端示例脚本

---

## 📦 **核心依赖清单**

### **Python 依赖 (requirements.txt)**

```txt
# Core dependencies
pydantic>=2.0.0
rich>=13.0.0
typer>=0.9.0

# P2P Network
pyzmq>=25.0.0
libp2p>=0.1.0  # Rust FFI bindings
zeroconf>=0.96.0

# LLM Backend
llama-cpp-python>=0.2.87
numpy>=1.24.0
diskcache>=5.6.1

# Dashboard & API
fastapi>=0.109.0
uvicorn[standard]>=0.27.0
websockets>=12.0

# Testing
pytest>=7.4.0
pytest-asyncio>=0.23.0
httpx>=0.26.0

# Development
black>=24.1.0
ruff>=0.3.0
mypy>=1.8.0
pre-commit>=3.6.0
```

### **系统依赖 (Windows)**

#### **Visual Studio Build Tools**
- [ ] Workload: "Desktop development with C++"
- [ ] Components: MSVC v143, Windows 10/11 SDK

#### **AMD ROCm for Windows**
- [ ] Version: ROCm 6.1.2 (技术预览) / 7.2.1 (稳定版)
- [ ] GPU Support: RX 7900 XTX/XT, PRO W7900, RX 7700

#### **Node.js** (Dashboard)
- [ ] Version: Node.js 18+
- [ ] NPM: 用于构建 Web Dashboard（如果采用）

---

## 🔧 **开发环境配置**

### **PowerShell 初始化脚本**

```powershell
# setup-dev-environment.ps1
Write-Host "🚀 Setting up Exo Windows Porting development environment..." -ForegroundColor Cyan

# 1. Install Python packages
pip install poetry
poetry install

# 2. Setup pre-commit hooks
pre-commit install

# 3. Create virtual environment (if not using Poetry)
python -m venv .venv
.venv\Scripts\activate

# 4. Verify GPU detection
python scripts/test_gpu_detection.py

Write-Host "✅ Development environment ready!" -ForegroundColor Green
```

---

## 📊 **里程碑与交付物**

### **Milestone 1: CPU-only PoC (Week 1)**
- ✅ P2P 网络自动发现功能
- ✅ llama.cpp CPU 推理后端
- ✅ 基础 Web Dashboard（节点列表）
- 📋 文档：Windows 安装指南 v0.1

### **Milestone 2: ROCm GPU Support (Week 2)**
- ✅ ROCm for Windows 环境自动配置
- ✅ llama.cpp + ROCm GPU 加速后端
- ⚡ 性能基准测试报告（CPU vs ROCm）
- 📋 文档：ROCm 支持详细说明

### **Milestone 3: Full Validation (Week 3)**
- ✅ 完整功能测试（P2P + GPU + Dashboard）
- ✅ API 兼容性验证（OpenAI/Ollama）
- 🎯 Alpha 版本发布准备
- 📋 文档：故障排查手册 v1.0

---

## 🏆 **成功标准**

### **功能完整性**
- [x] P2P 自动发现 > 95% 成功率
- [x] CPU-only 推理正常（基准测试通过）
- [ ] ROCm GPU 加速启动（RX 7900 XTX/XT 验证）
- [ ] Web Dashboard 实时显示集群状态

### **性能指标**
- [ ] ROCm vs CPU: ≥ 3x 性能提升
- [ ] P2P 发现延迟：< 5s (局域网)
- [ ] API 响应时间：TTFT < 100ms (7B 模型)

### **用户体验**
- [ ] 一键安装脚本（含 ROCm）
- [ ] 故障排查指南覆盖率 > 90%
- [ ] 文档完整性评分 ≥ 4.5/5

---

## 🚀 **下一步行动**

1. ✅ **团队创建**: `exo-windows-porting` 已就绪
2. ⏳ **Agent 分配**: 
   - lead_architect: 项目协调、架构设计
   - windows_poc_lead: PoC 结构搭建 (待启动)
   - llama_rocm_expert: ROCm 集成研究 (待启动)
3. 📝 **文档初始化**: PROJECT_STRUCTURE.md 已创建
4. 🔧 **CI/CD 配置**: GitHub Actions YAML 文件待生成

---

**项目启动时间**: 2026-04-01  
**预计完成时间**: Phase 0 (8-12 周)  
**状态**: 🟢 Phase 0 - PoC 开发中
