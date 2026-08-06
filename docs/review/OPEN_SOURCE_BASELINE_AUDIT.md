# ZMAI Open Source Baseline Audit

> 审计日期: 2026-07-23
> 审计方式: 逐项检查实际代码，不依赖文档推测
> 原则: 不修改代码、不创建文件、保留零第三方依赖架构

---

## 执行摘要

相比 2026-07-17 的 `OPEN_SOURCE_AUDIT.md`，**此前报告的多个严重问题已经修复**。但影响新用户的第一印象问题（README、根目录整洁度、社区基建文档）仍大面积缺失。

```
2026-07-17 审计:  8 P0 + 8 P1   ← 当时有多个真实 bug
2026-07-23 基线:  4 P0 + 5 P1   ← bug 已修，剩下的是社区基建 + 工程优化
```

---

## 检查项总览

| 优先级 | 编号 | 检查项 | 结论 | 分类 |
|--------|------|--------|------|------|
| P0 | 1 | GitHub Actions CI/CD | ⚠️ 部分完成 | 工程优化 |
| P0 | 2 | README Quick Start | ⚠️ 部分完成 | 工程优化 |
| P0 | 3 | 根目录结构 | ❌ 未完成 | 工程优化 |
| P0 | 4 | CONTRIBUTING.md | ❌ 不存在 | 缺失文件 |
| P0 | 5 | Issue/PR 模板 | ❌ 不存在 | 缺失文件 |
| P0 | 6 | CODE_OF_CONDUCT.md | ❌ 不存在 | 缺失文件 |
| P0 | 7 | SECURITY.md | ❌ 不存在 | 缺失文件 |
| P0 | 8 | CHANGELOG.md | ❌ 不存在 | 缺失文件 |
| P0 | 9 | examples/ | ❌ 空 | 工程优化 |
| P0 | 10 | PyPI 发布配置 | ⚠️ 部分完成 | 工程优化 |
| P1 | 1 | main.py 职责集中 | ⚠️ 真实问题 | 工程优化 |
| P1 | 2 | 中文注释与英文代码混用 | ⚠️ 真实问题 | 工程优化 |
| P1 | 3 | Workspace 路径安全 | ✅ 已修复 | 已修复 |
| P1 | 4 | CredentialStore 安全 | ⚠️ 已改进，非阻塞 | 工程优化 |
| P1 | 5 | Runtime 死代码 | ✅ 已修复 | 已修复 |
| P1 | 6 | Config sources=[] 语义 | ✅ 已修复 | 已修复 |
| P1 | 7 | py.typed | ✅ 已存在 | 已完成 |
| P1 | 8 | pyproject.toml 完整度 | ⚠️ 部分完成 | 工程优化 |

---

## P0 检查项详情

### P0.1 GitHub Actions CI/CD — ⚠️ 部分完成

**实际代码检查结果：**

`.github/workflows/test.yml` 存在，内容：

```yaml
# ✅ security: 检查源码中是否泄露 API Key (grep sk-)
# ✅ test: Python 3.10 / 3.11 / 3.12 矩阵
# ✅ ruff check src/ tests/
# ✅ mypy src/zmai/ || true    ← ⚠️ || true 会吞掉类型错误
# ✅ pytest --ignore=tests/test_live_api.py
```

**已完成：**
- CI 配置存在并覆盖了 lint、type check、test 三个环节
- Python 多版本矩阵测试（3.10 / 3.11 / 3.12）
- 安全扫描（API Key 泄露检测）

**缺失：**
- `mypy || true` 会吞掉类型检查失败——mypy 报错不阻断 CI
- 没有覆盖率收集与报告（如 `pytest-cov` 或 Python `coverage.py`）
- 没有 PyPI 自动发布 workflow
- 没有 pre-commit hook 配置（虽然 `.pre-commit-config.yaml` 存在于根目录但未被 CI 引用）

**影响：**
mypy 错误不会拦截 PR 合入。但考虑到 ruff 已经在检查，影响有限。

---

### P0.2 README Quick Start — ⚠️ 部分完成

**实际代码检查结果：**

README 的 Quick Start 展示了以下步骤：

```
1. git clone + pip install -e .
2. export DEEPSEEK_API_KEY=...
3. zmai doctor
4. zmai "用一句话回答: 1+1=?"
5. zmai (交互式)
```

**已完成：**
- 从安装到运行的完整 CLI 流程已展示
- 包含验证命令 `zmai doctor`

**问题：**
- Quick Start 标题下展示的是 Workspace API 代码示例（Python import），**不是 CLI 使用**。新用户看到 Python 代码会困惑——"我以为这是 CLI 工具，怎么要写代码？"
- `pip install zmai` 不可用（未发布到 PyPI），但 README 没有明确说明"目前只支持源码安装"

**结论：**
Quick Start 的 CLI 步骤**存在但不突出**，Workspace API 示例的位置会分散新用户注意力。这不是内容缺失，是信息层级问题。

---

### P0.3 根目录结构 — ❌ 未完成

**实际代码检查结果：**

```
根目录 .md 文件（非 README）: 46 个
其中 docs/design/ 下有 23 个设计文档
其中 docs/review/ 下有 24 个审查文档
其中 docs/security/ 下有 2 个安全文档
```

项目根目录有 46 个 `.md` 文件，包括 `BACKEND_DIAGNOSIS.md`、`AUTH_REFACTOR_PLAN.md`、`TEST_QUALITY_REPORT.md` 等内部审查/诊断文档。

**注意：** `docs/design/`、`docs/review/`、`docs/security/` 目录结构已经存在，但根目录仍然有大量与这些目录中内容重复或类似的文件。这表明已有整理动作**只完成了目录骨架，没有迁移文件**。

**结论：**
46 个文件对新人来说是严重的信息噪音。新用户打开项目在 README 之前先看到一堆看不懂的审查报告。

---

### P0.4 CONTRIBUTING.md — ❌ 不存在

**实际检查：** 文件不存在。

贡献者不知道：
- 如何安装开发环境（`pip install -e ".[dev]"`）
- 代码规范（ruff/mypy 配置已存在但没文档说明）
- 测试要求（新增功能是否需要测试？覆盖率要求？）
- PR 流程（分支策略、commit message 规范）
- 如何添加新 Backend（这个有文档但分散）

---

### P0.5 Issue/PR 模板 — ❌ 不存在

**实际检查：** `.github/ISSUE_TEMPLATE/` 和 `.github/PULL_REQUEST_TEMPLATE.md` 均不存在。

**影响：**
- Bug report 没有结构化模板 → 缺少复现步骤、环境信息
- 功能请求没有清晰描述 → 无法 triage
- PR 没有链接 Issue → 无法追溯

---

### P0.6 CODE_OF_CONDUCT.md — ❌ 不存在

**实际检查：** 文件不存在。GitHub 会在缺少此文件时在仓库页面上方显示提示。

---

### P0.7 SECURITY.md — ❌ 不存在

**实际检查：** 文件不存在。

安全研究者无法知道如何报告漏洞。考虑到项目涉及 API Key 管理，这是重要的缺失项。

---

### P0.8 CHANGELOG.md — ❌ 不存在

**实际检查：** 文件不存在。

用户在升级时无从知晓变更内容。但项目尚未发布（version 0.1.0），在发布前创建即可。

---

### P0.9 examples/ — ❌ 空

**实际检查：** `examples/` 下只有 `__init__.py`，没有任何示例代码。

**建议补充的示例：**
1. 基础使用：`examples/basic_chat.py` — 用 Runtime 执行一个简单任务
2. 自定义 Backend：`examples/custom_backend.py` — 添加新 LLM 提供商
3. 自定义 Tool：`examples/custom_tool.py` — 注册自定义工具
4. SWE Agent：`examples/swe_demo.py` — 演示代码阅读/修改

---

### P0.10 PyPI 发布配置 — ⚠️ 部分完成

**实际检查：**

```toml
[project.dependencies]
# ZMAI has zero third-party runtime dependencies.    # ← 显式声明，正确

[project.optional-dependencies]
dev = ["pytest>=7", "ruff>=0.1", "mypy>=1.0"]       # ← 存在，正确

[project.urls]
Homepage = "https://github.com/zmai/zmai"            # ← 配置了但还未上线
Documentation = "https://zmai.dev"                   # ← 文档站点尚未搭建
```

**已完成：**
- `pyproject.toml` 结构完整（build、project、scripts、URLs）
- 零依赖已显式声明
- MIT License 存在

**缺失：**
- 没有 PyPI 发布 CI workflow（需要 GitHub Actions + PyPI token）
- `zmai` 包名在 PyPI 上未检查是否可用
- `Documentation = "https://zmai.dev"` 指向一个尚未搭建的网站，新人点击会 404
- License 中 `Copyright (c) 2026 ZMAI Contributors` — 如果个人发布，建议明确版权归属

---

## P1 检查项详情

### P1.1 main.py 职责集中 — ⚠️ 真实问题

**实际代码检查：**

`src/zmai/cli/main.py` — **1207 行**，包含以下职责：

| 职责 | 行数范围 | 备注 |
|------|---------|------|
| 错误脱敏 `_sanitize_error` | 32-50 | |
| Auth fix 交互 | 53-64 | 与 auth 子命令逻辑分离但相关 |
| Session save/load | 72-88 | 可独立为 `session.py` |
| Workspace 清理 | 98-124 | 可独立 |
| 首次配置向导 | 149-259 | **111 行的交互向导** |
| 凭据注入 | 262-277 | |
| 帮助文本 | 282-308 | |
| 参数解析 | 311-335 | |
| REPL 执行 | 340-383 | |
| 单次任务执行 | 386-437 | |
| Readline 配置 | 442-453 | |
| REPL 交互循环 | 456-522 | |
| Config 子命令 | 527-553 | |
| Auth 子命令状态 | 559-601 | |
| Auth 子命令 test | 604-837 | **233 行** |
| Auth 子命令路由 | 840-972 | |
| Plugin 子命令 | 1064-1118 | |
| Doctor 子命令 | 1121-1128 | |
| 主入口 | 1133-1228 | **96 行** |

**结论：**
1207 行承担了 **12 个以上不同职责**。对于开源项目，这是一个真实的维护负担——想改 auth 的人要先读 1200 行文件。建议拆分为 5-6 个文件。

**注意：** 这不是"必须修"的阻塞项。对于 0.1.0 版本，可以接受。但在简历中被问到"你的 CLI 架构"时会是一个弱点。

---

### P1.2 中文注释与英文代码混用 — ⚠️ 真实问题

**实际代码检查：**

中文出现在以下位置：
- 所有模块的文档字符串（docstring）— 约 22 个 `__init__.py`
- 配置文件的注释
- Memory 模块的注释和字符串
- Runtime 的异常消息
- SWE Agent 的提示词（prompt）
- Auth store 的安全声明文档字符串

**具体情况：**
- Docstring 中英文混用最为普遍——模块级 docstring 是中文，函数级 docstring 是英文
- 用户面向的文档可用中文，但**代码注释和 docstring 统一用英文**是开源项目的基本要求
- 错误消息虽然不影响功能，但混合语言会增加国际贡献者的理解成本

**影响程度：**
低到中等。不会阻止开源发布，但会降低国际贡献者的参与意愿。

---

### P1.3 Workspace 路径安全 — ✅ 已修复

**实际代码检查：**

```python
# workspace/workspace.py:868
target.relative_to(agent_path_resolved)  # ✅ 正确的路径包含检查

# swe/tools.py:60-61
resolved.relative_to(pp.resolve())  # ✅ 同样正确
```

2026-07-17 审计报告中报告的 `str.startswith` 路径穿越漏洞**已经修复**。两处都使用 `pathlib.Path.relative_to()` 来验证路径包含关系，不再存在 `/ws/agent_1` 与 `/ws/agent_1-secret` 混淆的问题。

**结论：** 真实 Bug → 已修复 ✅

---

### P1.4 CredentialStore 安全 — ⚠️ 已改进，非阻塞

**实际代码检查：**

当前实现：
```python
# auth/store.py:57-76
# 密钥: os.urandom(32) → SHA-256 → XOR 混淆密钥
# 文件: ~/.zmai/credentials.key (base64 存储)
# 凭据: ~/.zmai/credentials (XOR + base64)
```

**相比旧审计报告的变化：**
- 旧审计报告称"XOR 加密 + MachineGuid 密钥"，现在已改为 **`os.urandom(32)` 文件密钥**
- 安全声明从无到有——现在有完整的安全声明文档字符串，明确指出是 obfuscation 不是 encryption
- 密钥从 MachineGuid（公开/可预测）升级为随机文件密钥
- 提供 `store_keyring.py` 作为可选的安全替代方案

**对开源发布的影响：**
- 对于零依赖项目，基于文件密钥的 XOR 混淆 + OS 原生凭据存储是合理的设计选择
- 安全声明已足够诚实和完整
- **不阻塞发布**。如果有人问"你们怎么存储 API Key 的"，可以直接引用安全声明中的解释

---

### P1.5 Runtime 死代码 — ✅ 已修复

**实际代码检查：**

`_execute_task()` 方法在 `runtime.py` 中**不存在**。旧审计报告中的 77 行死代码已经被清理。

但是 `_quiet` 标记在 `swe/tools.py:22` 仍然存在：

```python
if context.config.get("_quiet"):
    return
```

没有任何代码路径设置 `_quiet` 为 `True`，所以工具执行日志总是输出到 stderr。这是非常轻微的"死逻辑"，影响可以忽略。

---

### P1.6 Config sources=[] 语义 — ✅ 已修复

**实际代码检查：**

```python
# config/config.py:31-34
# sources=None → 使用默认配置源
# sources=[]  → 明确不加载任何源（禁止用 falsy 判断混淆二者）
self._sources = sources if sources is not None else [
    FileSource("zmai.json"),
    ...
]
```

旧审计报告中的 falsy 陷阱（`[] or [...]`）**已经修复**。现在使用 `is not None` 判断，`sources=[]` 与 `sources=None` 语义不同。

**此外 `CLISource` 的同样问题也已修复**（`CLISource([])` 会正确使用空列表而非回退到 `sys.argv`）。

---

### P1.7 py.typed — ✅ 已存在

**实际检查：** `src/zmai/py.typed` 存在。使用类型检查的消费者可以获取 ZMAI 的类型信息。

---

### P1.8 pyproject.toml 完整度 — ⚠️ 部分完成

**实际检查：**

```toml
[project]
name = "zmai"
version = "0.1.0"
description = "Model-Agnostic Agent Runtime"
readme = "README.md"
license = {text = "MIT"}
requires-python = ">=3.10"
keywords = ["agent", "runtime", "ai", "llm"]
classifiers = [
    "Development Status :: 3 - Alpha",
    "Intended Audience :: Developers",
    "License :: OSI Approved :: MIT License",
    "Programming Language :: Python :: 3",
    "Programming Language :: Python :: 3.10",
    "Programming Language :: Python :: 3.11",
    "Programming Language :: Python :: 3.12",
    "Topic :: Software Development :: Libraries",
]
```

**已完成：**
- 项目元数据完整
- `[project.dependencies]` 显式声明零依赖
- `[project.optional-dependencies]` 包含 dev 依赖
- `[project.scripts]` 配置了 `zmai` 入口
- 分类器完整

**缺失：**
- `[project.urls]` 中 `Documentation = "https://zmai.dev"` 指向不存在的网站
- 没有 `Homepage` 的实际 GitHub 仓库（但 README 中有 clone URL）
- `classifiers` 缺少 `Operating System :: POSIX :: Linux`、`Operating System :: Microsoft :: Windows`、`Operating System :: MacOS` 等

---

## 与前次审计的差异

| 问题 | 2026-07-17 审计 | 2026-07-23 实际 | 说明 |
|------|-----------------|-----------------|------|
| CI/CD | ❌ 无 | ⚠️ 有但缺发布 | workflow 已添加 |
| `str.startswith` 路径穿越 | 🐛 真实 bug | ✅ 已修复 | 改用 `relative_to` |
| `sources=[]` falsy 陷阱 | 🐛 真实 bug | ✅ 已修复 | 改用 `is not None` |
| `_execute_task` 死代码 | 🐛 77 行 | ✅ 已删除 | |
| XOR 加密 | ⚠️ 虚假安全 | ✅ 已改进 | 随机密钥 + 明确文档 |
| `CLISource([])` falsy 陷阱 | 🐛 真实 bug | ✅ 已修复 | |
| `py.typed` | ❌ 无 | ✅ 已存在 | |
| `[project.dependencies]` | ❌ 无 | ✅ 已存在 | |

**整体趋势：** 审计周期内修复了 5 个真实 bug/问题，剩余问题主要是社区基建缺失。

---

## 按风险与收益排序的优先级

### P0 阻塞项（发布前必须解决）

| 排序 | 项目 | 风险 | 收益 | 预计工时 |
|------|------|------|------|---------|
| 1 | **根目录 46 个 .md 文件整理** | 新用户第一印象最差 | 清理后仓库瞬间变整洁 | 0.5h |
| 2 | **README Quick Start 优化** | 新用户前 5 分钟流失 | 新人上手率大幅提升 | 1h |
| 3 | **examples/ 补至少 2 个示例** | 空示例目录扣分严重 | 展示项目能力的最直接方式 | 2h |
| 4 | **CONTRIBUTING.md** | 贡献者不知道如何参与 | 降低贡献门槛 | 1h |
| 5 | **CHANGELOG.md** | 发版前必须 | 用户知道变化 | 0.5h |
| 6 | **CODE_OF_CONDUCT.md** | GitHub 会提示缺失 | 开源社区标配 | 0.3h |
| 7 | **SECURITY.md** | 安全漏洞无报告渠道 | 安全合规 | 0.3h |
| 8 | **Issue/PR 模板** | Issue 质量无保证 | 减少维护者沟通成本 | 0.5h |
| 9 | **PyPI 发布 CI** | 无法 `pip install` | 用户可直接安装 | 1h |

### P1 高优先级项（发布前建议解决）

| 排序 | 项目 | 风险 | 收益 | 预计工时 |
|------|------|------|------|---------|
| 1 | **main.py 拆分** | 维护负担，代码 review 困难 | 模块清晰，贡献者易参与 | 3h |
| 2 | **CI mypy `|| true` 修复** | 类型错误可被合入 | 类型安全保障 | 0.2h |
| 3 | **中文文档字符串英文化** | 国际贡献者看不懂注释 | 降低国际参与门槛 | 2h |

### P2 可选优化项（发布后可做）

| 排序 | 项目 | 理由 |
|------|------|------|
| 1 | pyproject.toml 分类器补充 | 小改动，增加准确度 |
| 2 | `docs/` 迁移根目录 .md 文件 | 已有目录结构，只需 mv |
| 3 | `_quiet` 死代码清理 | 极小改动 |
| 4 | PyPI 包名确认 | 发布前需要确认 |
| 5 | CI 覆盖率报告 | 展示工程质量 |

---

## 结论

**当前状态：** 距离可公开发布还剩约 **10 小时工作量**（主要是社区基建文档 + 示例 + README 优化）。

**建议发布策略：**
1. 先花约 **6 小时**处理 P0 前 8 项（社区文档 + 示例 + README）
2. 再花约 **5 小时**处理 P1 的前 3 项（main.py + mypi + 英文化）
3. 发布 v0.1.0-alpha，接受社区反馈
4. 根据反馈迭代 v0.1.0

**亮点（值得在简历上写）：**
- 零第三方依赖的核心架构
- Gateway 抽象层（支持 DeepSeek/Claude/Gemini）
- 598 个测试用例
- Workspace 路径安全（已通过审计修复）
- 凭据多层解析（env → keyring → file → config）
- MIT 开源许可
