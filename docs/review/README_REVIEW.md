# README Review

> 审查日期: 2026-07-17
> 范围: 安装、快速开始、架构图、CLI、示例、截图、FAQ、Roadmap、Contribution、License

---

## 一、执行摘要

README.md 当前 **131 行**，覆盖了项目核心概念和 Workspace 用法，但存在大量缺口：

| 项目 | 状态 | 评分 |
|------|------|------|
| 安装 | ⚠️ 只有一行 `pip install`，尚未发布到 PyPI | ★☆☆☆☆ |
| 快速开始 | ⚠️ 只展示了 Workspace API，无 CLI 无 Agent | ★★☆☆☆ |
| 架构图 | ✅ ARCHITECTURE.md 中有 ASCII 图，README 未引用 | ★★★☆☆ |
| CLI | ❌ 完全缺失 | ☆☆☆☆☆ |
| 示例 | ❌ examples/ 只有空 `__init__.py` | ☆☆☆☆☆ |
| 截图 | ❌ 无 screenshots/ 目录 | ☆☆☆☆☆ |
| FAQ | ❌ 无 FAQ.md | ☆☆☆☆☆ |
| Roadmap | ✅ ROADMAP.md 存在，README 未链接 | ★★★☆☆ |
| Contribution | ❌ 无 CONTRIBUTING.md | ☆☆☆☆☆ |
| License | ✅ MIT，正确链接 | ★★★★★ |

**综合评分: 2.0/5** — 有基础骨架，但核心信息缺失，无法让新用户快速理解项目价值。

---

## 二、逐项审查

### 2.1 安装

**现状：**
```markdown
pip install zmai
```

**问题：**
1. **未发布到 PyPI** — `pip install zmai` 会失败（包不存在或取到同名其他包）
2. **无开发模式安装** — 没有 `pip install -e .` 或 `git clone + pip install` 说明
3. **无系统依赖说明** — Python 版本要求（>= 3.10）
4. **无环境变量配置** — 没有说明如何设置 `DEEPSEEK_API_KEY` 或 `ANTHROPIC_API_KEY`
5. **无虚拟环境推荐** — 没有 venv/conda 建议

**建议：**
```markdown
## 安装

### 前置要求
- Python 3.10+

### 从源码安装
```bash
git clone https://github.com/your-org/zmai.git
cd zmai
pip install -e .
```

### 配置 API Key
```bash
# DeepSeek（默认）
export DEEPSEEK_API_KEY=sk-xxxxx

# 或 Claude
export ANTHROPIC_API_KEY=sk-xxxxx
```

### 验证安装
```bash
zmai doctor
```
```

### 2.2 快速开始

**现状：** 只有 Workspace API 代码示例。没有展示 ZMAI 核心价值——作为 Agent Runtime。

```
当前快速开始:
  from zmai.workspace import Workspace
  ws = Workspace(root="./workspace")
  ws.prepare("agent_123")
  ...                      ← 只是文件操作，不是 Agent Runtime
```

**问题：**
1. **没展示核心功能** — 用户看不到 Agent 如何运行、CLI 如何工作
2. **Workspace 不是入口** — Workspace 是底层模块，不应该在快速开始中第一个展示
3. **没有 "Hello World"** — 没有最简示例让用户 30 秒内看到效果

**建议的快速开始：**
```markdown
## 快速开始

### 1. 运行任务（CLI）
```bash
zmai "列出当前目录的文件"
```

### 2. 进入交互模式
```bash
zmai
zmai> 帮我写一个 Python 脚本读取 CSV 文件
```

### 3. 编程使用
```python
from zmai.config import Config
from zmai.runtime import Runtime

config = Config()
runtime = Runtime(config=config)
result = runtime.run("agent_1", "列出当前目录的文件")
print(result["output"])
```
```

然后把 Workspace Manager 移到 **Workspace** 独立章节。

### 2.3 架构图

**现状：**
- README 中**没有架构图**，只有文字描述
- `ARCHITECTURE.md` 包含一个三层 ASCII 图，但 README 没有引用

**问题：**
```
缺少视觉入口。用户打开 README 的前 3 秒看不到项目结构。

ARCHITECTURE.md 中有:
┌─────────────────────────────────────┐
│           Runtime Core              │
│  (Lifecycle · Scheduler · State)    │
├─────────────────────────────────────┤
│           Gateway Layer             │
│    (Backend Abstraction · Router)   │
├─────────────────────────────────────┤
│           Backend Layer             │
│      (Claude · DeepSeek · ...)      │
└─────────────────────────────────────┘

但 README 没有引用它。
```

**建议：**
```markdown
## Architecture

ZMAI 采用三层架构。详见 [ARCHITECTURE.md](ARCHITECTURE.md)。

```
┌─────────────────────────────────────┐
│           Runtime Core              │
├─────────────────────────────────────┤
│           Gateway Layer             │
├─────────────────────────────────────┤
│           Backend Layer             │
└─────────────────────────────────────┘
```

### 2.4 CLI

**现状：** ❌ 完全缺失

README 中没有提到任何 CLI 命令。用户不知道：
- `zmai` 进入 REPL
- `zmai "task"` 运行一次性任务
- `zmai config list` 查看配置
- `zmai auth` 管理凭证
- `zmai doctor` 诊断

**建议新增章节：**
```markdown
## CLI 使用

```bash
zmai                         # 进入交互式 REPL
zmai "列出当前目录的文件"      # 运行一次性任务
zmai --backend deepseek "task" # 指定 backend
zmai --json "task"           # JSON 格式输出

zmai config list             # 查看配置
zmai config get runtime.max_iterations  # 获取配置项
zmai config set cli.theme light        # 设置配置项

zmai auth list               # 查看已配置的 Backend
zmai auth update claude sk-xxx  # 配置 API Key
zmai auth switch deepseek    # 切换默认 Backend

zmai doctor                  # 诊断安装和环境
zmai doctor --json           # JSON 格式诊断报告
```
```

### 2.5 示例

**现状：** `examples/` 目录只有空的 `__init__.py`

**问题：**
1. 无可运行的示例文件
2. `README.md` 第 116 行引用了 `examples/` 但目录为空
3. 新用户没有参考代码可运行

**建议添加的示例：**

| 文件 | 内容 |
|------|------|
| `examples/hello_world.py` | CLI 调用 + 编程调用 |
| `examples/custom_tool.py` | 注册自定义工具 |
| `examples/workspace_demo.py` | Workspace 完整用法 |
| `examples/multi_backend.py` | 多 Backend 切换 |

### 2.6 截图

**现状：** ❌ 无 screenshots/ 目录

**问题：**
1. 潜在用户无法看到 ZMAI 的运行界面
2. REPL 模式的交互体验无法通过文字传达

**建议：**
```markdown
## Screenshots

![REPL Mode](screenshots/repl.png)
![Doctor Check](screenshots/doctor.png)
```

需要截取：
- `zami` 进入 REPL 的界面
- `zmai doctor` 的诊断输出
- `zmai config list` 的表格输出

### 2.7 FAQ

**现状：** ❌ 无 FAQ.md

**建议新增文件 `FAQ.md`**，覆盖：

| 问题 | 答案 |
|------|------|
| ZMAI 怎么读？ | 同 "芝麻 AI"（zmai = zhi ma AI） |
| 支持哪些模型？ | DeepSeek、Claude，通过 Gateway 架构可扩展任意模型 |
| 和 LangChain 的区别？ | ZMAI 是 Runtime 不是 Framework，设计原则不同 |
| 需要 GPU 吗？ | 不需要，所有模型通过 API 调用 |
| 支持 Windows 吗？ | 支持，Windows 是头等公民（命令自动转换） |
| 如何添加新 Backend？ | 参考 `ARCHITECTURE.md` 和 `BACKEND_REVIEW.md` |

### 2.8 Roadmap

**现状：** ✅ ROADMAP.md 存在（7 Phase，详细），但 README 没有引用

**建议：** 在 README 的 Philosophy 或 Features 后增加：

```markdown
## Roadmap

查看完整路线图 [ROADMAP.md](ROADMAP.md)。

| Phase | Focus | Status |
|-------|-------|--------|
| 1 Architecture | 架构设计 | ✅ 已完成 |
| 2 Core Foundation | Runtime + Memory + Gateway | 🔄 进行中 |
| 3 Agent System | Agent + Tool + Workflow | 📅 Q4 2026 |
| 4 Plugin & Workspace | Plugin + CLI | 📅 Q4 2026 |
```

### 2.9 Contribution

**现状：** ❌ 无 CONTRIBUTING.md

**建议新增文件 `CONTRIBUTING.md`**，包含：
1. 开发环境搭建（`pip install -e .`）
2. 代码规范（ruff、mypy）
3. 测试要求（`pytest tests/`）
4. PR 流程
5. 设计文档优先原则

### 2.10 License

**现状：** ✅ MIT，正确引用 `LICENSE` 文件

**问题：** 过于简单。建议扩展为：

```markdown
## License

MIT — see [LICENSE](LICENSE).

Copyright (c) 2026 ZMAI Contributors
```

---

## 三、Structure 章节问题

当前 Project Structure 列出 `workspace/` 下的生成文件：

```
workspace/
├── manifest.json    ← 运行时生成，属于 .gitignore
├── state.json       ← 运行时生成，属于 .gitignore
```

不应该在项目结构图中展示运行时生成的文件。建议改为：

```
ZMAI/
├── src/zmai/            # 核心运行时
│   ├── cli/             # CLI 命令
│   ├── config/          # 配置管理
│   ├── gateway/         # Backend 抽象层
│   ├── memory/          # 记忆系统
│   ├── runtime/         # 运行时
│   ├── swe/             # SWE Agent
│   ├── tool/            # 工具系统
│   └── workspace/       # 文件沙箱
├── tests/               # 测试（430+）
├── zmai.json            # 项目级配置
└── pyproject.toml       # 项目元数据
```

---

## 四、缺失章节

### 需要新增

| 章节 | 优先级 | 说明 |
|------|--------|------|
| CLI 用法 | P0 | 用户最常用的交互方式 |
| 快速开始（重写） | P0 | 展示 Agent Runtime 核心价值 |
| 安装（补充） | P0 | 源码安装 + API Key 配置 |
| 示例代码 | P1 | 可运行的 Python 示例 |
| FAQ | P1 | 常见问题 |
| Contribution | P2 | 贡献指南 |
| 截图 | P2 | 运行界面 |

### 可删除/合并

| 章节 | 原因 |
|------|------|
| Workspace Manager（作为快速开始） | 应移到独立章节，快速开始应展示 CLI |
| Philosophy → Features → 迁移到顶部 | 当前 "Philosophy" 和 "Features" 内容有重叠 |

---

## 五、总结

### 当前评分

| 维度 | 评分 | 说明 |
|------|------|------|
| 安装说明 | ★☆☆☆☆ | 一行不可用的 `pip install` |
| 快速开始 | ★★☆☆☆ | 展示了错误的使用场景 |
| 架构图 | ★★★☆☆ | 有但不在 README 中 |
| CLI 文档 | ★☆☆☆☆ | 完全缺失 |
| 示例 | ★☆☆☆☆ | examples/ 为空 |
| 截图 | ☆☆☆☆☆ | 不存在 |
| FAQ | ☆☆☆☆☆ | 不存在 |
| Roadmap | ★★★☆☆ | 有但未链接 |
| Contribution | ☆☆☆☆☆ | 不存在 |
| License | ★★★★★ | 完整正确 |

**综合: 2.0/5**

### 建议重写顺序

```
Phase 1（立即）:
├── 补充安装说明（源码 + API Key）
├── 重写快速开始（CLI 优先）
├── 新增 CLI 用法章节
├── 引用 ARCHITECTURE.md 架构图
└── 引用 ROADMAP.md

Phase 2（1-2天）:
├── examples/ 添加 3 个可运行示例
├── CONTRIBUTING.md
└── FAQ.md

Phase 3（工具）:
├── 截图（REPL、doctor、config list）
└── 修复 Structure 中的运行时文件
```

---

*Report generated by `claude` — 基于 README.md + 项目实际状态审计*
