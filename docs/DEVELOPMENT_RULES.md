# ZMAI Development Rules

> 本文件是 [CONSTITUTION.md](CONSTITUTION.md) 的实践指南。宪法是最高纲领，本文件提供日常开发引用。

---

## 开发流程

任何开发必须按顺序经过以下阶段，禁止跳过：

```
Architecture → Specification → Design → Implementation → Verification → Documentation → Review → Release
```

## 编码规范

| 规则 | 要求 |
|---|---|
| Python 版本 | 3.11+ |
| 代码风格 | PEP8 |
| 类型注解 | 所有公共 API 必须有完整类型注解 |
| 文档字符串 | 所有公共模块、类、函数必须有 docstring |
| 日志 | 使用标准库 `logging`，不得使用 `print` |
| 测试 | 每个模块必须有单元测试 |
| 依赖 | 优先标准库，最小化第三方依赖 |

### 禁止

- 复制代码（抽取公共模块）
- 大型函数（超过 50 行应考虑拆分）
- 魔法数字（使用命名常量）
- 全局状态（使用显式状态管理）

### 命名约定

| 元素 | 约定 |
|---|---|
| 包名 | `lowercase_with_underscore` |
| 模块名 | `lowercase_with_underscore` |
| 类名 | `PascalCase` |
| 函数/方法 | `lowercase_with_underscore` |
| 常量 | `UPPERCASE_WITH_UNDERSCORE` |
| 私有成员 | 前缀 `_` |
| 测试文件 | `test_<module_name>.py` |

## 模块设计

每个模块必须：

- **单一职责** — 一个模块只做一件事
- **接口稳定** — 公共接口一旦确定，不得随意修改
- **依赖明确** — 所有依赖显式声明，禁止隐式依赖
- **可测试** — 模块应易于隔离测试
- **可替换** — 模块应面向接口编程，而非具体实现
- **无循环依赖** — 严格禁止

## Runtime 设计

Runtime 是 ZMAI 的核心，只负责：
- 生命周期管理
- 任务调度
- 运行状态
- Memory 管理
- Plugin 管理
- Workflow 编排

Runtime **不得**直接实现业务逻辑。

## Backend 设计

Backend 是 Runtime 与模型之间的桥梁，只负责：
- 调用模型 API
- 工具调用（Tool Use）
- MCP 协议通信

Backend **不得**承担 Runtime、Memory、Workflow 职责。

## Memory 设计

Memory 分为：
- **Working Memory** — 当前会话上下文
- **Long-term Memory** — 持久化存储

Memory 只负责存储、读取、更新，**不得**直接控制 Workflow。

## Plugin 设计

Plugin 必须：
- 独立（独立包/模块）
- 可安装
- 可卸载
- 可禁用
- 不影响 Runtime 稳定性

## 测试要求

- 所有新模块必须包含 **Unit Test** + **Integration Test**
- 使用 `pytest` 作为测试运行器
- 测试文件位于 `tests/`，目录结构镜像 `src/`
- 所有测试必须可自动执行
- Mock 外部服务，CI 环境中不调用真实 API

## 文档要求

任何新增功能必须同步更新：

- README.md
- API 文档
- 示例代码
- 架构文档

## 发布要求

Release 必须：
- 全部测试通过
- 文档完整
- 遵守语义化版本号
- CHANGELOG 更新

---

## 参考

- 所有规则的最终权威来源：[CONSTITUTION.md](CONSTITUTION.md)
