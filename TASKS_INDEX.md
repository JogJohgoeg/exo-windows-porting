# 📋 Exo Windows Porting - 任务索引

## ✅ **已完成任务**

### Phase 0: PoC 开发 (Week 1-3)

| ID | 任务名称 | 状态 | 负责人 | 完成日期 |
|----|----------|------|--------|----------|
| TASK-000 | 项目初始化 | ✅ 已启动 | lead_architect | 2026-04-01 |
| TASK-001 | P2P 网络层实现 | 🟡 进行中 | p2p_network_specialist (待分配) | Week 1 Day 3-5 |
| TASK-002 | ROCm Windows 集成 | 🔴 未开始 | llama_rocm_expert (待启动) | Week 2 Day 6-12 |

---

## 📝 **任务详情**

### TASK-001: P2P 网络层自动发现实现

**负责人**: `p2p_network_specialist` (待分配)  
**优先级**: High  
**预计完成**: Week 1 Day 5  

**目标**:
- ✅ mDNS/Bonjour 服务注册和发现
- ✅ Peer 节点身份识别和连接管理
- ✅ 简单路由器和负载均衡策略
- ✅ 健康检查和自动故障转移

**交付物**:
- [ ] `exo_windows_porting/network/discovery.py` - mDNS/Bonjour 实现
- [ ] `exo_windows_porting/network/router.py` - SimpleRouter 负载均衡
- [ ] `exo_windows_porting/network/peer.py` - Peer 节点管理
- [ ] `tests/test_discovery.py` - 单元测试

**详细设计**: [`TASK-001-p2p-network.md`](tasks/TASK-001-p2p-network.md)

---

### TASK-002: AMD ROCm Windows 集成与优化

**负责人**: `llama_rocm_expert` (待启动)  
**优先级**: Critical  
**预计完成**: Week 2 Day 12  

**目标**:
- ✅ ROCm for Windows 环境自动配置
- ✅ llama-cpp-python ROCm 支持集成
- ✅ GPU 加载、推理性能基准测试
- ✅ 故障排查手册编写

**交付物**:
- [ ] `scripts/install_rocm_windows.bat` - ROCm 安装脚本 (已创建✅)
- [ ] `exo_windows_porting/backend/llama_rocm.py` - ROCm GPU 后端 (已创建✅)
- [ ] `tests/test_rocm_backend.py` - GPU 加载测试
- [ ] `scripts/benchmark_performance.py` - 性能基准测试

**详细设计**: [`TASK-002-rocm-integration.md`](tasks/TASK-002-rocm-integration.md)

---

## 🚀 **未来任务 (Phase 1+)**

### Phase 1: CUDA GPU 支持 (Week 4-5)

| ID | 任务名称 | 状态 | 预计开始 |
|----|----------|------|----------|
| TASK-003 | NVIDIA CUDA 后端实现 | 🔴 未开始 | Week 4 Day 1 |
| TASK-004 | CUDA ROCm 性能对比分析 | 🔴 未开始 | Week 5 Day 1 |

### Phase 2: Web Dashboard (Week 6)

| ID | 任务名称 | 状态 | 预计开始 |
|----|----------|------|----------|
| TASK-005 | FastAPI API 服务器实现 | 🔴 未开始 | Week 6 Day 1 |
| TASK-006 | Web UI 前端开发 | 🔴 未开始 | Week 6 Day 3 |

### Phase 3: 完整功能验证 (Week 7-8)

| ID | 任务名称 | 状态 | 预计开始 |
|----|----------|------|----------|
| TASK-007 | API 兼容性测试 (OpenAI/Ollama) | 🔴 未开始 | Week 7 Day 1 |
| TASK-008 | Alpha 版本发布准备 | 🔴 未开始 | Week 8 Day 5 |

---

## 📊 **任务追踪仪表盘**

### Phase 0: PoC 开发进度

```
Week 1 (P2P + CPU-only):    ████████░░ 80%
Week 2 (ROCm GPU):          ░░░░░░░░░░   0%
Week 3 (Full Validation):   ░░░░░░░░░░   0%
```

### 整体进度

| 里程碑 | 状态 | 完成度 |
|--------|------|--------|
| M1: CPU-only PoC | 🟡 进行中 | 60% |
| M2: ROCm GPU Support | 🔴 未开始 | 0% |
| M3: Full Validation | 🔴 未开始 | 0% |

---

## 💡 **任务分配建议**

### 立即启动的 Agent

1. **`p2p_network_specialist`**: 
   - 负责 TASK-001 (P2P 网络层)
   - 优先级: High
   - 技能要求: Python, mDNS/Bonjour, ZeroMQ

2. **`llama_rocm_expert`**: 
   - 负责 TASK-002 (ROCm Windows 集成)
   - 优先级: Critical
   - 技能要求: llama.cpp, ROCm, GPU 优化

### 后续启动的 Agent

3. **`cuda_windows_specialist`**: 
   - 负责 TASK-003/004 (CUDA GPU 支持)
   - 预计启动时间: Week 4

4. **`dashboard_fullstack_dev`**: 
   - 负责 TASK-005/006 (Web Dashboard)
   - 预计启动时间: Week 6

---

## 🔗 **相关文档**

- [项目结构](PROJECT_STRUCTURE.md)
- [README](../README.md)
- [CI/CD配置](.github/workflows/ci.yml)
- [安装脚本](scripts/install_rocm_windows.bat)

---

**最后更新**: 2026-04-01  
**版本**: v0.1-alpha  
**状态**: Phase 0 - PoC 开发中 (8-12 周)
