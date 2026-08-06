# ZMAI Roadmap

Version: 1.0

> 本文档定义 ZMAI 项目的开发路线图，按阶段划分里程碑。

---

## Phase Overview

```
Phase 1 ─── Phase 2 ─── Phase 3 ─── Phase 4 ─── Phase 5 ─── Phase 6 ─── Phase 7
  ARCH       CORE        AGENT       PLUGIN       SWE      STABILIZE     FUTURE
```

| Phase | Name | Focus | Est. Duration |
|---|---|---|---|
| **1** | Architecture | 架构设计 | ✅ 已完成 |
| **2** | Core Foundation | Runtime + Memory + Gateway | Q3 2026 |
| **3** | Agent System | Agent + Tool + Workflow | Q3 2026 |
| **4** | Plugin & Workspace | Plugin + Workspace + CLI | Q4 2026 |
| **5** | SWE Agent | 第一个官方 Agent 实现 | Q4 2026 |
| **6** | Stabilization | 测试 + 文档 + 发布 | Q1 2027 |
| **7** | Future | 社区 + 生态 + 扩展 | Q1 2027+ |

---

## Phase 1: Architecture (当前阶段)

**状态:** ✅ 已完成

**目标:** 完成 ZMAI 整体架构设计。

**交付物:**

- [x] `ARCHITECTURE.md` — 整体架构设计
- [x] `ROADMAP.md` — 开发路线图
- [x] `MODULES.md` — 模块定义与职责
- [x] `CONSTITUTION.md` — 项目宪法

---

## Phase 2: Core Foundation

**状态:** 🔜 待开始

**目标:** 实现 Runtime 核心层，包括错误系统、配置、基础 Runtime 和 Memory。

### 任务分解

#### 2.1 基础模块

```
Priority: P0
Dependencies: 无
```

- [ ] 实现 `zmai/errors/errors.py` — 所有错误类型
- [ ] 实现 `zmai/config/sources.py` — 配置源（文件 + 环境变量）
- [ ] 实现 `zmai/config/config.py` — 配置管理器

#### 2.2 Memory 系统

```
Priority: P0
Dependencies: 2.1
```

- [ ] 实现 `zmai/memory/base.py` — Memory 抽象基类 + MemoryEntry
- [ ] 实现 `zmai/memory/working.py` — Working Memory（内存存储）
- [ ] 实现 `zmai/memory/long_term.py` — Long-term Memory（文件持久化）
- [ ] 实现 `zmai/memory/manager.py` — MemoryManager

#### 2.3 Gateway 层

```
Priority: P0
Dependencies: 2.1
```

- [ ] 实现 `zmai/tool/base.py` — Tool 抽象基类
- [ ] 实现 `zmai/tool/registry.py` — Tool 注册表
- [ ] 实现 `zmai/gateway/base.py` — Backend 抽象基类
- [ ] 实现 `zmai/gateway/registry.py` — Backend 注册表
- [ ] 实现 `zmai/gateway/tool_router.py` — 工具路由
- [ ] 实现 `zmai/gateway/backends/claude.py` — Claude Backend

#### 2.4 Runtime 核心

```
Priority: P0
Dependencies: 2.2, 2.3
```

- [ ] 实现 `zmai/runtime/state.py` — 状态管理器
- [ ] 实现 `zmai/runtime/lifecycle.py` — 生命周期管理器
- [ ] 实现 `zmai/runtime/scheduler.py` — 调度器
- [ ] 实现 `zmai/runtime/runtime.py` — Runtime 主类

### 里程碑: Core Foundation 完成

```
✅ 所有错误类型定义
✅ 配置系统可工作
✅ Memory 可读可写
✅ Gateway 可调用 Backend
✅ Runtime 可运行至完成
```

---

## Phase 3: Agent System

**状态:** 🔜 待开始

**目标:** 实现 Agent 抽象、工具系统和工作流引擎。

### 任务分解

#### 3.1 Agent 抽象

```
Priority: P0
Dependencies: Phase 2
```

- [ ] 实现 `zmai/agent/lifecycle.py` — 生命周期状态 + 事件定义
- [ ] 实现 `zmai/agent/base.py` — Agent 抽象基类
- [ ] 编写 Agent 单元测试

#### 3.2 Workspace

```
Priority: P1
Dependencies: Phase 2
```

- [ ] 实现 `zmai/workspace/workspace.py` — Workspace 管理器
- [ ] 编写 Workspace 单元测试

#### 3.3 Workflow 引擎

```
Priority: P1
Dependencies: 3.1
```

- [ ] 实现 `zmai/workflow/base.py` — Workflow 抽象基类
- [ ] 实现 `zmai/workflow/engine.py` — Workflow 执行引擎
- [ ] 编写 Workflow 单元测试

### 里程碑: Agent System 完成

```
✅ Agent 可被 Runtime 加载执行
✅ Workspace 提供隔离沙箱
✅ Workflow 可编排多步任务
```

---

## Phase 4: Plugin & CLI

**状态:** 🔜 待开始

**目标:** 实现插件系统和命令行界面。

### 任务分解

#### 4.1 Plugin 系统

```
Priority: P1
Dependencies: Phase 2
```

- [ ] 实现 `zmai/plugin/hooks.py` — Hook 定义
- [ ] 实现 `zmai/plugin/base.py` — Plugin 抽象基类
- [ ] 实现 `zmai/plugin/manager.py` — Plugin 管理器
- [ ] 编写 Plugin 单元测试

#### 4.2 CLI

```
Priority: P1
Dependencies: Phase 2, 3, 4.1
```

- [ ] 实现 `zmai/cli/main.py` — CLI 入口
- [ ] 实现 `zmai/cli/commands/run.py` — 运行 Agent
- [ ] 实现 `zmai/cli/commands/init.py` — 初始化项目
- [ ] 实现 `zmai/cli/commands/config.py` — 配置管理
- [ ] 实现 `zmai/cli/commands/plugin.py` — 插件管理
- [ ] 配置 pyproject.toml `[project.scripts]` 入口

### 里程碑: Plugin & CLI 完成

```
✅ Plugin 可发现、加载、启用、禁用
✅ CLI 可与 Runtime 交互
✅ 完整的端到端用户流程可用
```

---

## Phase 5: SWE Agent

**状态:** 🔜 待开始

**目标:** 实现 ZMAI 的第一个官方 Agent — SWE Agent。

### 任务分解

#### 5.1 SWE Agent 核心

```
Priority: P0
Dependencies: Phase 3, 4
```

- [ ] 实现 SWE Agent 基类（继承 `zmai.agent.Agent`）
- [ ] 实现代码阅读工具集（grep, read, list）
- [ ] 实现代码编辑工具集（edit, write, patch）
- [ ] 实现 Shell 执行工具
- [ ] 实现 Git 操作工具

#### 5.2 SWE Agent 能力

```
Priority: P0
Dependencies: 5.1
```

- [ ] 任务理解与分解
- [ ] 自主代码搜索
- [ ] 代码修改与验证
- [ ] 测试执行与结果分析
- [ ] 错误恢复

#### 5.3 SWE Agent 集成

```
Priority: P1
Dependencies: 5.2
```

- [ ] CLI 集成 (`zmai run swe`)
- [ ] 预置 Prompt 模板
- [ ] 示例项目

### 里程碑: SWE Agent 完成

```
✅ SWE Agent 可运行并完成代码任务
✅ 端到端测试通过
✅ 示例文档完整
```

---

## Phase 6: Stabilization

**状态:** 🔜 待开始

**目标:** 全面测试、文档完善、发布准备。

### 任务分解

- [ ] 单元测试覆盖率 ≥ 85%
- [ ] 集成测试（端到端场景）
- [ ] API 文档完成
- [ ] README 完善
- [ ] 示例代码完成
- [ ] CHANGELOG 初始化
- [ ] 版本号 v0.1.0
- [ ] PyPI 发布准备

### 里程碑: v0.1.0 Release

```
✅ 全部测试通过
✅ 文档完整
✅ PyPI 可用
```

---

## Phase 7: Future

**状态:** 🔮 规划中

**目标:** 社区建设、生态扩展、高级特性。

### 可能的方向

- **多 Backend 支持:** OpenAI, Grok, Ollama（本地模型）
- **MCP 协议支持:** 通过 Plugin 实现 MCP 客户端
- **Web API:** RESTful API，支持远程调用
- **IDE 集成:** VS Code Extension
- **Agent 市场:** 社区贡献 Agent 的注册中心
- **分布式 Runtime:** 多机多 Agent 协作
- **可观测性:** OpenTelemetry 集成

---

## Dependency Map

```
Phase 2 ─────────────────────────────────────────────────┐
  ├── errors (无依赖)                                      │
  ├── config (依赖: errors)                                │
  ├── tool (依赖: errors)                                  │
  ├── memory (依赖: errors)                                │
  ├── gateway (依赖: tool, errors)                         │
  └── runtime (依赖: memory, gateway, config, errors)     │
                                                        ▼
Phase 3 ──── 依赖 Phase 2 ──────────────────→ Phase 5 ──→ Phase 6
  ├── agent (依赖: memory, tool, gateway)      └── SWE Agent
  ├── workspace (依赖: config)
  └── workflow (依赖: agent, memory)
                                                        ▲
Phase 4 ──── 依赖 Phase 2, Phase 3 ──────────────────────┘
  ├── plugin (依赖: errors)
  └── cli (依赖: runtime, config, plugin)
```

---

## Risk & Mitigation

| Risk | Impact | Probability | Mitigation |
|---|---|---|---|
| Claude Backend 强绑定 | High | Medium | Gateway 抽象层确保可替换；Claude 只是默认实现 |
| Plugin 系统过于复杂 | Medium | Medium | 最小 Hook 集合起步，后续按需扩展 |
| Memory 性能瓶颈 | Medium | Low | Working Memory 内存存储；Long-term 异步持久化 |
| Workflow 引擎过度设计 | Medium | Medium | 仅支持线性 Workflow v1，复杂 DAG 延后 |
| 依赖膨胀 | Medium | Low | CI 中检查依赖数量；每次新增依赖需 Review |

---

## 版本规划

| Version | Phase | Date | Features |
|---|---|---|---|
| v0.1.0 | Phase 6 | Q1 2027 | 首次发布，Core + Agent + CLI |
| v0.2.0 | Post-release | Q2 2027 | Plugin 系统 + SWE Agent |
| v0.3.0 | Post-release | Q3 2027 | 多 Backend + MCP |
| v1.0.0 | Stable | 2027+ | API 稳定，生产可用 |

---

> **最终目标:** 一个简单、开放、稳定、长期维护、可扩展、社区驱动的 Agent Runtime。
