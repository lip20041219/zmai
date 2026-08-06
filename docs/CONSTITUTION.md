# ZMAI Project Constitution

Version: 1.0

---

# Mission

ZMAI 是一个开源、可扩展、模型无关（Model-Agnostic）的 Agent Runtime。

ZMAI 的目标不是替代大语言模型，而是构建一个稳定、可维护、可扩展的 Agent Runtime，使开发者能够构建和运行各种专业 Agent。

SWE Agent 是 ZMAI 的第一个官方实现。

---

# Core Principles

## Principle 1 — Runtime First

ZMAI 的核心是 Runtime，而不是 Prompt。

任何设计都应优先增强 Runtime，而不是依赖复杂 Prompt。

---

## Principle 2 — Model Agnostic

Runtime 不依赖任何特定模型。

Claude Code 只是默认 Backend。

Runtime 必须支持未来扩展其他 Backend。

---

## Principle 3 — Open Source First

所有核心功能必须可开源。

不得依赖无法替换的私有组件。

---

## Principle 4 — Keep It Simple

优先最简单方案。

优先标准库。

优先最少依赖。

避免过度设计。

---

## Principle 5 — Modular Architecture

所有模块必须独立。

禁止循环依赖。

禁止模块职责混乱。

每个模块必须只有一个主要职责。

---

## Principle 6 — Plugin First

任何未来可能扩展的能力，应优先设计为 Plugin。

不要写死实现。

---

## Principle 7 — Configuration Over Hardcode

任何配置不得硬编码。

统一配置入口。

---

## Principle 8 — Everything Testable

所有公共模块必须可以测试。

所有核心流程必须可以验证。

---

## Principle 9 — Documentation Driven

任何新增模块必须：

- 更新文档
- 更新 README
- 更新 API
- 更新示例

---

## Principle 10 — Backward Compatibility

任何更新：

- 不得无故破坏已有功能
- 重大修改必须提供迁移方案

---

# Development Workflow

所有开发必须遵守以下阶段，禁止跳过：

```
Architecture → Specification → Design → Implementation → Verification → Documentation → Review → Release
```

---

# Coding Rules

- Python 3.11+
- PEP8
- Type Hints
- Docstring
- Logging
- Unit Test
- Minimal Dependency
- 禁止复制代码
- 禁止大型函数
- 禁止魔法数字
- 禁止全局状态

---

# Module Rules

每个模块必须：

- 职责单一
- 接口稳定
- 依赖明确
- 可测试
- 可替换

---

# Runtime Rules

Runtime 只负责：

- 生命周期
- 调度
- 状态
- Memory
- Plugin
- Workflow

Runtime 不得直接实现业务逻辑。

---

# Backend Rules

Backend 只负责：

- 调用模型
- 工具
- MCP

Backend 不得负责 Runtime、Memory、Workflow。

---

# Memory Rules

Memory 分为：

- Working Memory
- Long-term Memory

Memory 只负责：

- 存储
- 读取
- 更新

Memory 不得直接控制 Workflow。

---

# Plugin Rules

Plugin 必须：

- 独立
- 可安装
- 可卸载
- 可禁用

Plugin 不得影响 Runtime。

---

# State Rules

所有运行状态统一使用 JSON，禁止多个状态来源。

---

# Prompt Rules

Prompt 只是 Runtime 的输入，不得承担：

- 业务逻辑
- 状态管理
- 长期记忆
- Workflow

---

# Testing Rules

所有新增模块必须包含：

- Unit Test
- Integration Test

所有测试自动执行。

---

# Documentation Rules

任何新增功能必须同步更新：

- README
- API
- Example
- Architecture

---

# Release Rules

Release 必须满足：

- 全部测试通过
- 文档完整
- 版本号更新
- CHANGELOG 更新

---

# Vision

ZMAI 希望成为：

一个简单、开放、稳定、长期维护、可扩展、社区驱动的 Agent Runtime。

而不是一个依赖单一模型的代码生成工具。
