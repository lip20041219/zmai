# ZMAI Product Polish v1.0

Version: 1.0
Date: 2026-07-16

> **四个角色审视：产品经理、CLI Designer、Principal Engineer、开源维护者。**
>
> 不增加功能。不修改 Runtime / Agent / Workflow。
>
> 找出所有影响开发体验的问题，按优先级排序。

---

## 目录

1. [产品经理视角](#1-产品经理视角)
2. [CLI Designer 视角](#2-cli-designer-视角)
3. [Principal Engineer 视角](#3-principal-engineer-视角)
4. [开源维护者视角](#4-开源维护者视角)
5. [优先级矩阵](#5-优先级矩阵)
6. [最终排名](#6-最终排名)

---

## 1. 产品经理视角

**关注：用户获取、激活、留存、学习成本、竞争差异。**

### P1.1 26 份设计文档 = 26 个阅读门槛

**发现：** 项目根目录有 26 个 `.md` 文件。

```
API.md  ARCHITECTURE.md  AUTH_DESIGN.md  CLASS.md  CLAUDE.md
CLI_UX_DESIGN.md  CONFIG_DESIGN.md  CONSTITUTION.md  DESIGN.md
DEVELOPMENT_RULES.md  ERROR_DESIGN.md  HELP_DESIGN.md
INSTALL_DESIGN.md  LONG_TERM_CLI_DESIGN.md  LONG_TERM_DX.md
MEMORY_DESIGN.md  MODULES.md  PROJECT_DETECTION.md  README.md
REPL_DESIGN.md  ROADMAP.md  SPECIFICATION.md  STARTUP_DESIGN.md
TASK_PROGRESS.md  TERMINAL_UI.md  WORKSPACE_DESIGN.md
```

**问题：** 贡献者/新用户打开项目，看到 26 个文档文件，第一反应是"学习成本太高"。ZMAI 代码只有 3191 行，设计文档却有 26 篇。文档比代码多这个信号本身就在劝退用户。

**修复：**

```
高优先级：
  合并设计文档到 docs/ 目录，根目录只保留 README.md、CLAUDE.md、LICENSE

中优先级：
  README.md 需要回答三个问题：
    1. ZMAI 是什么？（一句话）
    2. 怎么安装？（一行命令）
    3. 怎么用？（一个例子）

低优先级：
  API.md / CLASS.md / SPECIFICATION.md 可以删除
  — 这些信息应该从代码 docstring 生成，而非手动维护
```

### P1.2 零安装体验不存在

**发现：** 当前唯一的安装方式是 `pip install -e .`。没有 PyPI 包、没有 `pipx`、没有 `uvx`。

```bash
# 当前:
git clone <repo>
cd ZMAI
pip install -e .
zmai --version

# 期望:
pipx install zmai
zmai --version
```

**修复：**

```
高优先级：
  发布到 PyPI：python -m build && twine upload dist/*
  确保 [project.scripts] 和 __main__.py 就绪

中优先级：
  验证 pipx install zmai 可用
  zmai doctor 命令可诊断安装问题

低优先级：
  uvx zmai（零安装运行）
  安装脚本 install.sh / install.ps1
```

### P1.3 用户第一个任务的成功率不明确

**发现：** 首次运行流程存在但嵌入在 `main.py` 的 70 行内联代码中。没有独立的 onboarding 体验。

```python
# main.py:66-136 — 初始化向导（70 行）
# 内联在 main() 中，不可测试，不可复用
```

**问题：** 向导完成后显示 `run zmai --help to start`——用户还要再输一次命令。应该直接进入 REPL。

**修复：**

```
高优先级：
  向导完成后直接进入 REPL，不退出、不要求重新运行
  向导逻辑从 main.py 提取到独立模块 wizard.py

中优先级：
  首次运行的 Dashboard 显示入门提示而非通用信息
  未配置 Backend 时进入"有限功能模式"而非报错
```

### P1.4 产品定位传达不清晰

**发现：** 项目描述是"Model-Agnostic Agent Runtime"。开发者理解这句话需要先理解三个概念：

```
Model-Agnostic + Agent + Runtime

开发者："所以是... 可以切换模型的 Agent 框架？Claude Code 的平替？"
```

**修复：**

```
高优先级：
  README.md 第一段用一个比喻说清楚：
    "ZMAI 是终端里的 AI 工程师。进入项目目录，输入 zmai，它帮你写代码、跑测试、修 bug。"

中优先级：
  用 zmai --version 的输出强化品牌感知
  Dashboard 的第一行需要传递产品价值
```

---

## 2. CLI Designer 视角

**关注：命令结构、参数设计、输出格式、一致性、错误信息、发现性。**

### P2.1 REPL 在第一个任务后退出（致命 bug）

**发现：** `main.py:244` 在 `_cmd_run()` 末尾调用 `sys.exit()`。REPL 调用 `_cmd_run()` 后进程退出。

```python
# main.py:244
sys.exit(0 if result.get("status") == "completed" else 3)

# main.py:262 — REPL 调用 _cmd_run
_cmd_run(task, runtime, config, args)  # → sys.exit() → 进程死了
```

**影响：** 所谓"交互模式"只能执行一个任务。这是 CLI 设计中最基本的错误。

**修复：**

```
高优先级（阻塞 bug）：
  1. 分离"单次执行"和"REPL 执行"两条路径
  2. _cmd_run 不调用 sys.exit()
  3. _run_oneshot() 包装 sys.exit()
  4. REPL 使用 _repl_run() 不 exit
```

### P2.2 参数过多且不一致

**发现：** 当前 parser 定义 8 个参数/标志：

```
--version  --json  --no-color  -p/--prompt
task nargs*  -i/--interactive  -r/--resume  -c/--confirm
```

但大部分可以被自动检测替代：

| 参数 | 替代方案 | 简化方向 |
|------|---------|---------|
| `-p/--prompt` | 位置参数直接作为 task | 删除 |
| `-i/--interactive` | 无参数自动交互模式 | 删除 |
| `-r/--resume` | 启动时自动检测未完成任务 | 删除 |
| `-c/--confirm` | 移入 `/config` 配置项 | 删除 |
| `task nargs*` 与 `-p` 并存 | 统一为单个位置参数 | 合并 |

**修复：**

```
中优先级：
  精简参数：
    zmai                  → REPL
    zmai <task>           → 单次执行
    zmai --backend <name> → 切换 Backend
    zmai --json           → JSON 输出
    其余全部删除或移入子命令
```

### P2.3 子命令不是子命令

**发现：** `config` 和 `auth` 是硬编码的字符串比较，不是 argparse 子命令。

```python
# main.py:360-366
if argv:
    if argv[0] == "config":
        _run_config(argv[1:])
        return
    if argv[0] == "auth":
        _run_auth(argv[1:])
        return
```

这样做的结果是：
- `zmai --help` 不显示 `config` 和 `auth`
- 用户不知道这两个子命令存在
- 扩展新子命令需要手动加 if

**修复：**

```
中优先级：
  使用 argparse subparsers 定义 config 和 auth
  确保 --help 列出所有子命令
```

### P2.4 输出不一致

**发现：** 当前输出有以下不一致：

| 场景 | 当前输出 | 问题 |
|------|---------|------|
| 成功 | `print_success()` → `+ 消息` | 用 `+` 而非 `✅` |
| 错误 | `print_error()` → `x 消息` | 用 `x` 而非 `❌` |
| 启动 | `\033[2mzmai ...\033[0m` | 原始 ANSI 码，未用 Theme |
| 进度 | `sys.stderr.write(f"\n  > {msg}")` | `>` 而非工具图标 |
| 分隔线 | `"--"` 或 `"─" * n` | 无统一函数 |

`formatters.py` 中有 Theme 系统但没有被一致性使用——启动行直接写 ANSI 码跳过了 Theme。

```python
# main.py:388 — 绕过 Theme 直接写 ANSI
sys.stderr.write(f"\033[2mzmai  {project_ctx.summary()}  [{runtime._gateway.default_name or ''}]\033[0m\n")
```

**修复：**

```
低优先级：
  统一所有输出通过 Theme 类
  formatters.py 新增 rule()、header()、status_line() 函数
  消除 main.py 中所有的裸 ANSI 码
```

### P2.5 错误信息无上下文

**发现：** 当前错误输出 `print_error(str(e))`。没有错误分类、没有修复建议、没有上下文。

```python
# main.py:413-415
except Exception as e:
    print_error(str(e))
    sys.exit(1)
```

用户看到 `BackendError: API HTTP 401`，但不知道应该做什么。

**修复：**

```
高优先级：
  实现 classify_error() 转换函数
  每个错误附带修复建议：
    "API Key 无效 → 运行 zmai auth update deepseek"
    "网络错误 → 检查连接或等待 30 秒重试"
```

---

## 3. Principal Engineer 视角

**关注：架构、可维护性、性能、可测试性、错误隔离、技术债务。**

### P3.1 CLI 层直接导入 Runtime 内部状态

**发现：** `main.py` 在多个地方访问 `runtime._gateway._default` 和 `runtime._tools` 等私有属性。

```python
# main.py:388
runtime._gateway.default_name          # 访问私有属性

# main.py:158-164
runtime._tools.register(...)           # 操作私有对象

# main.py:108
runtime._gateway.default_name or ''    # 再次访问私有属性
```

这破坏了封装。`Runtime` 的内部实现变化会直接破坏 CLI 层。

**修复：**

```
高优先级：
  Runtime 新增公共属性：
    runtime.backend_name → 替代 _gateway.default_name
    runtime.register_tool(tool) → 替代 _tools.register()

中优先级：
  CLI 层只应调用 Runtime 的公共 API
  设计 Runtime 接口时考虑 CLI 的消费方
```

### P3.2 3191 行源代码没有测试

**发现：** `grep -rn "def test_" tests/` 未确认测试存在。

**问题：** 这不是"增加测试"的功能性要求，而是软件开发的基本实践。没有测试意味着：
- 重构引入回归只能靠人工发现
- 新贡献者不知道如何验证修改是否正确
- 持续集成没有价值

**修复：**

```
高优先级（技术债）：
  为以下关键路径添加集成测试：
    - 启动流程（main.py）不崩溃
    - 配置加载（config.py）合并不覆盖
    - 认证检测链（auth/store.py）降级正确
    - Workspace 写入（workspace.py）路径穿越防护

中优先级：
  将测试纳入 CI（GitHub Actions）
  测试覆盖率目标：核心路径 80%
```

### P3.3 环境变量注入是脆弱的适配模式

**发现：** 认证凭证通过修改 `os.environ` 传递给 Gateway。

```python
# main.py:151
os.environ[env_key] = info["api_key"]   # 注入环境变量
```

这样做的原因是 Gateway Backend 从环境变量读取 Key。但 `os.environ` 修改影响整个进程，可能导致：
- 子进程继承敏感环境变量
- 多线程竞争条件
- 调试时难以追踪值从哪里来

**修复：**

```
中优先级：
  Gateway Backend 构造函数接受 api_key 参数
  CLI 层直接传入，不经过环境变量
  环境变量只作为最后备选（已在 backend 构造函数中实现了）

  当前 claude.py:
    self._api_key = self._config.get("api_key", os.environ.get("ANTHROPIC_API_KEY", ""))

  期望:
    auth_store → api_key → Runtime → Backend.__init__(api_key=key)
    不走 os.environ
```

### P3.4 `main.py` 承担过多职责（419 行）

**发现：** `main.py:419` 行包含了：

| 职责 | 行数 |
|------|------|
| 参数解析 | 16 行 |
| 初始化向导 | 70 行 |
| 认证注入 | 20 行 |
| 工具注册 | 10 行 |
| 单次执行 | 50 行 |
| REPL 交互 | 20 行 |
| 配置管理 | 30 行 |
| Auth 命令 | 60 行 |
| 启动编排 | 60 行 |

这是一个 God Object。所有功能都耦合在这个文件里。

**修复：**

```
中优先级：
  拆分 main.py：
    cli/main.py          → 入口 + 参数解析（~50 行）
    cli/wizard.py        → 初始化向导
    cli/runner.py        → _cmd_run / 单次执行
    cli/auth_commands.py → auth 子命令
    cli/config_commands.py → config 子命令
```

### P3.5 MemoryManager.restore() 是空方法

**发现：** `memory/manager.py:49-56` 中 `restore()` 只有 `pass`。

```python
def restore(self, agent_id: str) -> None:
    lm = self.long_term(agent_id)
    wm = self.working(agent_id)
    for ns in lm.list_namespaces():
        pass  # 空循环，什么都不做
```

这是代码中最大的"承诺未兑现"之处。Memory 系统声称支持恢复但实际不能。

**修复：**

```
高优先级：
  实现 restore()：
    遍历 Long-term Memory 的所有 namespace
    将条目写入 Working Memory
    返回恢复摘要（条目数、namespace 列表）

  这是 bug 修复而非功能增加——系统声明但不实现就是 bug。
```

### P3.6 每次 file write() 执行全量 rglob 扫描

**发现：** `workspace.py:621-632` 每次写入文件都执行 `agent_path.rglob("*")`。

```python
file_count = len([p for p in agent_path.rglob("*") if p.is_file()])
```

n=1000 文件时每次写入 O(1000)。这是已发现的性能问题。

**修复：**

```
高优先级：
  改为增量计数器维护，消除 rglob
  每次 write() 增加计数器，每次 delete() 减少
  详见 WORKSPACE_DESIGN.md 第 5 章
```

---

## 4. 开源维护者视角

**关注：贡献门槛、Issue 管理、发布流程、社区沟通、项目健康度。**

### P4.1 没有 CONTRIBUTING.md

**发现：** 项目没有贡献指南。

- 如何 fork？如何提 PR？如何运行测试？
- 代码风格是什么？（pyproject.toml 有 ruff 配置但没写在文档里）
- Commit message 格式是什么？

**修复：**

```
高优先级：
  创建 CONTRIBUTING.md，包含：
    - 本地开发环境搭建
    - 运行测试
    - 代码风格（ruff）
    - PR 流程
    - Issue 模板
```

### P4.2 没有 Issue / PR 模板

**发现：** `.github/` 目录不存在。

- Bug report 没有统一格式
- Feature request 没有统一格式
- PR 没有 checklist

**修复：**

```
中优先级：
  创建 .github/ISSUE_TEMPLATE/ 和 .github/PULL_REQUEST_TEMPLATE/
  模板包含：
    - 环境信息（ZMAI 版本、Python 版本、OS）
    - 复现步骤
    - 预期行为 vs 实际行为
    - 相关日志
```

### P4.3 没有 CI/CD

**发现：** 没有 `.github/workflows/` 目录。

- 提交代码后不知道测试是否通过
- 不知道 lint 是否通过
- 没有自动发布到 PyPI

**修复：**

```
高优先级：
  创建 GitHub Actions workflow：
    - PR 时运行 pytest
    - PR 时运行 ruff check
    - tag 时自动发布到 PyPI
```

### P4.4 许可证文件存在但缺少相关信息

**发现：** 有 `LICENSE` 文件（MIT），但：

- `pyproject.toml` 缺少 `license` 字段（当前是 `license = {text = "MIT"}`）
- README 底部有 License 章节但只有一句"MIT"
- 没有提及贡献者协议

**修复：**

```
低优先级：
  README 中增加 License 的完整说明
  添加 DCO（Developer Certificate of Origin）或 CLA 说明
```

### P4.5 项目没有版本发布策略

**发现：** 版本号 `v0.1.0` 硬编码在 `main.py` 和 `pyproject.toml` 中，但没有发布流程。

```python
# main.py:174
p.add_argument("--version", action="version", version="ZMAI v0.1.0")
```

- 没有 `CHANGELOG.md`
- 没有 release 脚本
- 没有语义版本规范

**修复：**

```
低优先级：
  创建 CHANGELOG.md（保持 unreleased 章节）
  定义语义版本规范（v0.x.y 期间 API 不稳定）
  pyproject.toml 作为版本单一真相来源
```

---

## 5. 优先级矩阵

### 5.1 评分标准

```
影响面:  用户/开发者/维护者 三个群体
严重性:  阻塞/严重/一般/轻微
修复成本:  小时/天/周
优先级:  影响面 × 严重性 ÷ 修复成本
```

### 5.2 完整评分

| # | 问题 | 视角 | 影响面 | 严重性 | 修复成本 | 优先级 |
|---|------|------|--------|--------|---------|--------|
| 1 | REPL 在第一个任务后退出 (P2.1) | CLI Designer | 用户 | 阻塞 | 2h | P0 |
| 2 | MemoryManager.restore() 是空方法 (P3.5) | Principal Engineer | 用户 | 阻塞 | 1h | P0 |
| 3 | 没有测试 (P3.2) | Principal Engineer | 维护者 | 严重 | 周 | P0 |
| 4 | 没有 CI/CD (P4.3) | 开源维护者 | 维护者 | 严重 | 2h | P0 |
| 5 | 没有 CONTRIBUTING.md (P4.1) | 开源维护者 | 开发者 | 严重 | 1h | P0 |
| 6 | 首次用户不能直接成功 (P1.3) | 产品经理 | 用户 | 严重 | 3h | P1 |
| 7 | CLI 层访问 Runtime 私有属性 (P3.1) | Principal Engineer | 维护者 | 严重 | 3h | P1 |
| 8 | 26 份设计文档 (P1.1) | 产品经理 | 开发者 | 一般 | 1h | P1 |
| 9 | 环境变量注入脆弱 (P3.3) | Principal Engineer | 维护者 | 一般 | 天 | P1 |
| 10 | 子命令不是子命令 (P2.3) | CLI Designer | 用户 | 一般 | 3h | P1 |
| 11 | 参数过多 (P2.2) | CLI Designer | 用户 | 一般 | 4h | P2 |
| 12 | main.py 419 行 (P3.4) | Principal Engineer | 维护者 | 一般 | 天 | P2 |
| 13 | write() rglob 全量扫描 (P3.6) | Principal Engineer | 用户 | 一般 | 天 | P2 |
| 14 | 没有 PyPI 发布 (P1.2) | 产品经理 | 用户 | 一般 | 周 | P2 |
| 15 | 错误信息无建议 (P2.5) | CLI Designer | 用户 | 一般 | 天 | P2 |
| 16 | 输出不一致 (P2.4) | CLI Designer | 用户 | 轻微 | 3h | P3 |
| 17 | 产品定位不清晰 (P1.4) | 产品经理 | 用户 | 一般 | 2h | P3 |
| 18 | 没有 Issue 模板 (P4.2) | 开源维护者 | 维护者 | 一般 | 1h | P3 |
| 19 | 没有发布策略 (P4.5) | 开源维护者 | 维护者 | 轻微 | 2h | P3 |
| 20 | 许可证信息不完整 (P4.4) | 开源维护者 | 开发者 | 轻微 | 0.5h | P3 |

### 5.3 优先级分组

```
P0 — 必须立即修复（阻碍使用的 bug）
  ├── REPL 执行一次后退出（2h 修复）
  ├── MemoryManager.restore() 空实现（1h 修复）
  ├── 没有测试（周级投入，但必须先建框架）
  ├── 没有 CI/CD（2h 搭建）
  └── 没有 CONTRIBUTING.md（1h 编写）

P1 — 应在下一个版本修复（影响开发者和用户）
  ├── 首次用户不能直接成功
  ├── CLI 层访问 Runtime 私有属性
  ├── 26 份设计文档淹没根目录
  ├── 环境变量注入脆弱
  └── 子命令不是子命令

P2 — 重要但不紧急（可排入迭代）
  ├── 参数过多
  ├── main.py 419 行 God Object
  ├── write() 每次全量 rglob
  ├── 没有 PyPI 发布
  └── 错误信息无修复建议

P3 — 锦上添花（有空再做）
  ├── 输出不一致
  ├── 产品定位不清晰
  ├── 没有 Issue/PR 模板
  ├── 没有发布策略
  └── 许可证信息不完整
```

---

## 6. 最终排名

### 6.1 Top 10 必须修复

```
P0 — 立刻（1-2 天）
────────────────────────────────────────────────────

1. REPL 执行一次后退出
   文件: main.py:244
   修复: 分离 REPL 和单次执行路径，_cmd_run 不 exit
   耗时: 2 小时
   影响: 根本性地修复"交互模式"的定义

2. MemoryManager.restore() 是空方法
   文件: memory/manager.py:49-56
   修复: 实现 restore()，遍历 Long-term → 写入 Working
   耗时: 1 小时
   影响: Memory 系统兑现"恢复"承诺

3. 没有测试框架
   文件: tests/
   修复: 创建 tests/conftest.py + 首个集成测试
   耗时: 第一次搭建 1 天
   影响: 后续所有修改可验证

4. 没有 CI/CD
   文件: .github/workflows/
   修复: 创建 pytest + ruff check workflow
   耗时: 2 小时
   影响: 每次 PR 自动验证

5. 没有贡献指南
   文件: CONTRIBUTING.md（新增）
   修复: 写一份简明贡献指南
   耗时: 1 小时
   影响: 降低社区贡献门槛

P1 — 下一个版本（3-5 天）
────────────────────────────────────────────────────

6. 首次用户不能直接成功
   文件: main.py:66-136
   修复: 向导完成后进入 REPL，提取到 wizard.py
   耗时: 3 小时
   影响: 用户从安装到第一条命令 < 2 分钟

7. CLI 层访问 Runtime 私有属性
   文件: runtime.py + main.py
   修复: Runtime 新增公共属性 backend_name / register_tool()
   耗时: 3 小时
   影响: 封装边界清晰，重构不破坏 CLI

8. 26 份设计文档
   文件: 根目录 26 个 .md
   修复: 合并到 docs/，根目录留 3 个
   耗时: 1 小时
   影响: 新贡献者打开项目不被文档淹没

9. 环境变量注入
   文件: main.py:151 + gateway/backends/*.py
   修复: Backend 构造函数接受 api_key 参数
   耗时: 1 天
   影响: 消除脆弱的环境变量注入模式

10. 子命令不是子命令
    文件: main.py:360-366
    修复: 使用 argparse subparsers
    耗时: 3 小时
    影响: --help 显示所有子命令
```

### 6.2 修复总工作量

```
P0:  ~2 天（5 项）
P1:  ~3 天（5 项）
P2:  ~4 天（5 项）
P3:  ~2 天（5 项）

总计: ~11 天（20 项）
```

### 6.3 不修改的原则

```
所有 20 项修复都遵循：

不修改:
  src/zmai/runtime/runtime.py     ← 仅新增公共属性（P1#7）
  src/zmai/gateway/base.py        ← 不变
  src/zmai/gateway/backends/*     ← 仅构造函数签名（P1#9）
  src/zmai/agent/*                ← 不变
  src/zmai/workspace/*            ← 不变
  src/zmai/memory/base.py         ← 不变
  src/zmai/workflow/*             ← 不变
  src/zmai/swe/*                  ← 不变

仅修改:
  src/zmai/cli/main.py            ← 拆分、精简
  src/zmai/memory/manager.py      ← 实现 restore()
  src/zmai/runtime/runtime.py     ← 新增公共属性（不修改现有方法）

新增:
  src/zmai/cli/wizard.py          ← 从 main.py 提取
  tests/test_startup.py           ← 首个测试
  CONTRIBUTING.md                 ← 贡献指南
  .github/workflows/*             ← CI/CD
```

---

> **总结：**
>
> 从四个角色视角审视 ZMAI，最大的 5 个问题：
>
> 1. **REPL 执行一次后退出** — 这是 bug，不是功能。2 小时修复。
> 2. **MemoryManager.restore() 是空方法** — 系统声称"恢复"但什么都不做。1 小时修复。
> 3. **没有测试** — 3191 行代码零测试。不可维护，不可验证。
> 4. **没有 CI/CD** — 每次 PR 需要人工验证。不可扩展。
> 5. **首次用户不能直接成功** — 向导完成后不进入 REPL。3 小时修复。
>
> 这些不是"新功能"。这些是**让现有功能可工作、可验证、可维护**的基础设施。
>
> **20 项修复 × 11 天 = 从 v0.1.0 到可发布 v0.2.0。**
