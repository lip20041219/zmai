# ZMAI Open Source Audit

> 审查视角: GitHub Maintainer / PyPI Maintainer / 开源贡献者 / 新用户
> 审查日期: 2026-07-17
> 原则: 不新增功能，不修改代码

---

## 一、执行摘要

ZMAI 是一个 **零第三方依赖** 的 Agent Runtime，代码质量中等偏上，但 **开源实践严重不足**。

| 视角 | 评分 | 一句话结论 |
|------|------|-----------|
| 新用户 | ★★☆☆☆ | README 无法指导完成 Hello World，examples/ 空 |
| 贡献者 | ★★☆☆☆ | 无 CONTRIBUTING、无 CI、无 PR 模板、中文注释 |
| GitHub Maintainer | ★★★☆☆ | 30+ 设计文档堆在根目录，无 Issues/PR 模板 |
| PyPI Maintainer | ★☆☆☆☆ | 未发布、`zmai` 名称未检查、无 `__version__` |

---

## 二、P0 — 新人第一次使用一定会踩坑

### P0.1 README 无法指导完成 Hello World

**视角**: 新用户

**问题**: README 的 "Quick Start" 展示的是 Workspace API（文件操作），不是 Agent Runtime 核心功能。
新人按 README 操作：

```bash
pip install zmai    # ❌ 包不存在！PyPI 上 zmai 可能是其他项目
```

假设从源码装：

```bash
git clone ...
cd zmai
pip install -e .    # README 没写这步
zmai "1+1=?"        # README 没展示 CLI
zmai doctor         # README 没展示诊断
```

**每一步都需要猜测**，前 15 分钟都在试错。

### P0.2 `pip install zmai` 不可用

**视角**: 新用户 / PyPI Maintainer

**问题**:
- `pyproject.toml` 写了 `name = "zmai"` 但从未发布到 PyPI
- 没有检查 `zmai` 在 PyPI 上是否已被占用
- 没有人能通过 `pip install zmai` 安装

### P0.3 项目根目录堆了 30+ 设计文档

**视角**: 新用户 / 贡献者

**问题**: 项目根目录有 30+ `.md` 文件：

```
ARCHITECTURE.md
BACKEND_REVIEW.md
CLASS.md
CLI_REVIEW.md
CLI_UX_DESIGN.md
CONFIG_DESIGN.md
CONSTITUTION.md
DESIGN.md
DEVELOPMENT_RULES.md
ERROR_DESIGN.md
EXAMPLE_REVIEW.md
HELP_DESIGN.md
IDENTITY_DESIGN.md
INSTALL_DESIGN.md
LICENSE
LOGGING_REVIEW.md
LONG_TERM_CLI_DESIGN.md
LONG_TERM_DX.md
MEMORY_DESIGN.md
MEMORY_REVIEW.md
MCP_DESIGN.md
MODULES.md
MULTI_AGENT_DESIGN.md
OPEN_BROWSER_FIX.md
PACKAGE_REVIEW.md
PLUGIN_DESIGN.md
PRODUCT_POLISH.md
PRODUCT_REVIEW.md
PROJECT_DETECTION.md
README.md
README_REVIEW.md
REPL_DESIGN.md
ROADMAP.md
RUNTIME_REVIEW.md
SPECIFICATION.md
STARTUP_DESIGN.md
TASK_PROGRESS.md
TERMINAL_UI.md
TEST_REVIEW.md
TOOL_ARCHITECTURE_REVIEW.md
TOOL_DIAGNOSIS.md
TOOL_DOCTOR.md
TOOL_LOGGING.md
TOOL_REGISTRY_REVIEW.md
TOOL_REVIEW.md
WORKSPACE_DESIGN.md
WORKSPACE_REVIEW.md
WRITE_FILE_FALLBACK.md
WRITE_FILE_FIX.md
```

**50 个文件**，其中 40+ 是设计/审查文档。新用户打开项目完全不知道从哪开始。

**建议**：所有设计文档移到 `docs/design/`，审查文档移到 `docs/review/`。

### P0.4 无 CI/CD

**视角**: GitHub Maintainer / 贡献者

**问题**: 项目没有 `.github/workflows/` 目录，没有 CI 配置：

```
❌ pytest 不在 CI 中运行
❌ ruff/mypy 不在 CI 中检查
❌ 无法确保 PR 不破坏测试
❌ 无法自动发布到 PyPI
❌ 没有 pre-commit hooks
```

**后果**：外部贡献者提交 PR 后，维护者需要手动 `git pull && pytest` 才能验证。

---

## 三、P1 — 长期维护成本高

### P1.1 中文注释 + 中文错误消息混入英文代码

**视角**: 开源贡献者

**问题**: 代码中中英文混杂：

```python
# swe/tools.py
logger.warning("工具已存在，将被覆盖: %s", tool.name)  # 中文
logger.debug("Tool registered: %s", tool.name)          # 英文
```

```python
# runtime.py
raise RuntimeError(action.error)   # 英文异常
# 但从 backend 传过来的错误消息是中文：
"Claude API 调用失败: ..."         # 中文
```

**影响**：
- 英文不好的贡献者看不懂错误日志
- 中文不好的贡献者看不懂代码注释
- 国际化（i18n）困难

**建议**：代码、注释、日志统一用英文。用户面向文档可以用中文。

### P1.2 500+ 行 main.py 包含所有 CLI 逻辑

**视角**: 贡献者 / Maintainer

**问题**: `cli/main.py` 620+ 行，包含：

```
- Session 管理 save/load
- Init 向导（4 步骤交互）
- Auth 子命令（5 个操作）
- Config 子命令
- REPL 交互循环
- 一次性任务执行
- Readline 配置
- Help 文本
- Argparse 构建
- Theme 解析
- Workspace 清理
- 主入口 main()
```

**一个文件承担了 12 个职责**。贡献者想改 auth 子命令，必须先理解整个文件的 620 行。

**建议**：拆分为 `cli/config_cmd.py`、`cli/auth_cmd.py`、`cli/repl.py`、`cli/session.py`。

### P1.3 `Config(sources=[])` 不生效（因为 `[]` 是 falsy）

**视角**: 贡献者

**问题**:

```python
# config/config.py:23-27
self._sources = sources or [
    FileSource("zmai.json"),
    EnvSource(),
    CLISource(),
]
```

传入 `sources=[]` 时，`[] or [...]` 返回 `[...]`。调用者以为传了空列表，实际加载了默认源。

**同样的问题在 `CLISource.__init__`**:
```python
self._args = args or sys.argv[1:]
```

`CLISource([])` 不生效，会读取真实 `sys.argv`。

**这是 Python 中经典的 `None-or-empty-list` 陷阱**。贡献者容易踩坑。

**建议**：改为 `if sources is not None`。

### P1.4 `_validate_path()` 使用字符串前缀检查路径

**视角**: 贡献者 / Maintainer

**问题**:

```python
# workspace/workspace.py:862
if not str(target).startswith(str(agent_path)):
```

```python
# swe/tools.py:_resolve_tool_path
if str(resolved).startswith(str(context.project_path)):
```

**两处字符串前缀检查都可以被绕过**：

```
agent_path = /ws/agent_1
target     = /ws/agent_1-secret/config.json  ← startswith 通过！
```

**这不是理论漏洞，是真实的安全 bug**。

**建议**：改为 `try: target.relative_to(agent_path)`。

### P1.5 `_quiet` 死代码

**视角**: 贡献者 / Maintainer

**问题**:

```python
# swe/tools.py:22
if context.config.get("_quiet"):
    return  # 但这个值永远没人设置
```

`_quiet` 配置项没有任何代码路径设置为 `True`，导致所有工具执行日志**总是输出**到 stderr。

### P1.6 `Runtime._execute_task()` 死代码

**视角**: 贡献者

**问题**: `runtime.py:177-255` 的 `_execute_task()` 方法包含完整的流式执行逻辑，但**从未被调用**。实际执行走的是 `run()` → `SWEAgent.step()`。

**77 行死代码，包含测试未覆盖的 stream 处理路径。**

---

## 四、P2 — 过度设计 / 文档不足 / 不符合开源最佳实践

### 4.1 过度设计

#### P2.1 15 份设计文档在实现之前编写

**视角**: Maintainer / 贡献者

项目有 15+ 份设计文档（`*_DESIGN.md`），总计约 **9000 行**，覆盖 CLI、REPL、Memory、Plugin、Workspace、Error、Help、Config 等所有子系统的 v2.0 设计。

**但其中 90% 未实现**：

| 设计文档 | 行数 | 实现状态 |
|---------|------|---------|
| `MEMORY_DESIGN.md` | 1235 | ~30% 实现 |
| `WORKSPACE_DESIGN.md` | 1029 | ~60% 实现（单文件） |
| `PLUGIN_DESIGN.md` | ~300 | 0% 实现 |
| `REPL_DESIGN.md` | 1177 | ~20% 实现 |
| `STARTUP_DESIGN.md` | 1242 | ~30% 实现 |
| `ERROR_DESIGN.md` | 1213 | 0% 实现 |
| `HELP_DESIGN.md` | 737 | 0% 实现 |

**先写设计再实现本身不是问题，问题是设计写了太多 v2.0 超前内容**。
贡献者想参与开发，打开设计文档看到的是 v2.0 目标，打开代码看到的是 v1.0 现状，两者严重不匹配。

**建议**：为设计文档标注 `status: planned / in-progress / done`。

#### P2.2 AuthStore 的 "加密" 是 XOR

**视角**: 贡献者 / Maintainer

```python
# auth/store.py:43-47
def _encrypt(plain: str, key: bytes) -> str:
    data = plain.encode()
    encrypted = bytes(data[i] ^ key[i % len(key)] for i in range(len(data)))
    return base64.b64encode(encrypted).decode()
```

这不是加密，是 **XOR obfuscation**。文档中写着 "AES 加密降级方案"，但 AES 部分从未实现。

**影响**：API Key 以可逆方式存储在 `~/.zmai/credentials` 中。掌握了机器访问权限的攻击者可以轻松解密。

**建议**：要么实现真正的 AES 加密，要么在文档中明确说明这是 "secure storage" 而不是 "encryption"。

#### P2.3 `WORKSPACE_DESIGN.md` 规划 6 文件重构

**视角**: Maintainer

当前 `workspace.py` 1044 行一个文件。设计文档规划拆分为 6 个文件共 970 行。

但 1044 行的工作量是 **一个文件**，而 6 个文件 970 行的工作量是 **六个文件的接口设计和协同**。
后者维护成本不一定比前者低。

**建议**：保持单文件，直到有明确的性能需求（如 rglob O(n) 问题真实出现）。

### 4.2 文档不足

#### P2.4 缺少 CONTRIBUTING.md

**视角**: 贡献者

贡献者想知道：
- 怎么安装开发环境？ → 无
- 代码规范是什么？ → 无（虽然有 ruff/mypy 配置）
- 测试要求？ → 无
- PR 流程？ → 无
- 怎么添加新 Backend？ → 无

#### P2.5 缺少 Issue/PR 模板

**视角**: GitHub Maintainer

没有 `.github/ISSUE_TEMPLATE/` 和 `.github/PULL_REQUEST_TEMPLATE.md`。

意味着：
- 用户报 Bug 不提供复现步骤
- 功能请求没有清晰描述
- PR 没有关联 Issue

#### P2.6 缺少 Code of Conduct

**视角**: 开源贡献者

没有 `CODE_OF_CONDUCT.md`。对于希望吸引多样化贡献者的开源项目，这是基本要求。

### 4.3 不符合开源最佳实践

#### P2.7 无 `__version__`

**视角**: 用户 / 贡献者

```python
>>> import zmai
>>> zmai.__version__
AttributeError
```

虽然 `pyproject.toml` 中有 `version = "0.1.0"`，但 Python 代码中无法读取。

用户在 CI 脚本、错误报告、依赖声明中无法获知版本。

#### P2.8 无 `py.typed` 标记

**视角**: 贡献者

项目配置了 `mypy strict = true`，但没有 `src/zmai/py.typed` 文件。
使用类型检查的消费者无法获得 ZMAI 的类型信息。

#### P2.9 无 `[project.dependencies]`

**视角**: PyPI Maintainer

```toml
[project]
# 没有 dependencies 字段
```

虽然零依赖，但应该显式声明：

```toml
[project.dependencies]
# ZMAI 无第三方依赖
```

否则打包工具和用户无法确认。

#### P2.10 Review 文档与设计文档混在根目录

**视角**: 新用户

项目根目录有 15+ 份 `*_REVIEW.md` 文件（`TEST_REVIEW.md`、`MEMORY_REVIEW.md`、`WORKSPACE_REVIEW.md` 等）。

这些是审查报告，不是项目文档。放在根目录对新用户是噪音。

**建议**：所有 `*_REVIEW.md` 移到 `docs/review/`。

#### P2.11 无 ChangeLog / Release Notes

**视角**: 用户

没有 `CHANGELOG.md`。用户无法知道每个版本的变化。

#### P2.12 无 Secutity Policy

**视角**: Maintainer

没有 `SECURITY.md`。安全研究者不知道如何报告漏洞。

---

## 五、优先级排序

| 优先级 | 编号 | 问题 | 视角 | 影响 |
|--------|------|------|------|------|
| **P0** | 2.1 | README 无法完成 Hello World | 新用户 | 新人直接流失 |
| **P0** | 2.2 | `pip install zmai` 不可用 | 新用户 | 无法安装 |
| **P0** | 2.3 | 根目录 50 个文件 | 新用户 | 找到入口需要 5 分钟 |
| **P0** | 2.4 | 无 CI/CD | Maintainer | 无法合并 PR |
| **P1** | 3.1 | 中文注释 + 消息混入 | 贡献者 | 国际贡献者看不懂 |
| **P1** | 3.2 | main.py 620+ 行 12 职责 | 贡献者 | 改一行需要读 620 行 |
| **P1** | 3.3 | `sources=[]` falsy 陷阱 | 贡献者 | 必踩坑 |
| **P1** | 3.4 | 路径前缀检查安全 bug | 贡献者 | 真实安全漏洞 |
| **P1** | 3.5 | `_quiet` 死代码 | Maintainer | 无用代码 |
| **P1** | 3.6 | `_execute_task()` 死代码 | Maintainer | 77 行死代码 |
| **P2** | 4.1 | 15 份超前设计文档 | 贡献者 | 设计与实现不匹配 |
| **P2** | 4.2 | XOR "加密" | 贡献者 | 虚假安全 |
| **P2** | 4.3 | 无 CONTRIBUTING.md | 贡献者 | 不知道如何参与 |
| **P2** | 4.4 | 无 Issue/PR 模板 | Maintainer | Issue 质量低 |
| **P2** | 4.5 | 无 Code of Conduct | 贡献者 | 社区门槛 |
| **P2** | 4.6 | 无 `__version__` | 用户 | 无法查版本 |
| **P2** | 4.7 | 无 `py.typed` | 贡献者 | 类型不可用 |
| **P2** | 4.8 | 无 `[project.dependencies]` | PyPI | 打包不规范 |
| **P2** | 4.9 | Review 文档混在根目录 | 新用户 | 噪音 |
| **P2** | 4.10 | 无 Changelog | 用户 | 不知道变化 |
| **P2** | 4.11 | 无 Security Policy | Maintainer | 安全报告无渠道 |

---

## 六、按视角总结

### 新用户

```
pip install zmai     → PackageNotFoundError ❌
README "Quick Start" → Workspace API, 不是 CLI ❌
ls 根目录            → 50 个文件，不知道入口 ❌
zmai "1+1=?"         → 没装好，不知道 `pip install -e .` ❌
```

P0 问题全部影响新用户。**这是最严重的群体**。

### 贡献者

```
我想加个新 backend → 读 main.py 620 行 → 里面 12 件事 → 放弃 😩
代码里中英文混杂 → 看不懂注释 → 不确定改得对不对 → 放弃
sources=[] 不生效 → debug 30 分钟 → 原来是 falsy 陷阱 → 失望
```

P1 问题影响贡献者效率。

### GitHub Maintainer

```
收到 PR → 没有 CI → 手动 git pull && pytest → 15 分钟
没有 PR 模板 → PR 没有描述 → 不知道改了什么
没有 Issue 模板 → Bug report 没有复现步骤
```

P0 + P1 问题让维护成本高。

### PyPI Maintainer

```
python -m build → 成功
twine upload → 才发现 zmai 可能已被占名
没有 CI → 每次手动发布 → 容易出错
```

P2 问题让发布流程不专业。

---

*Audited by `claude` — Principal Engineer review for open-source readiness*
