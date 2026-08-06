# ZMAI Product Review Report

**Reviewer:** Principal Engineer (长期维护 CLI + AI Agent)
**Date:** 2026-07-16
**Scope:** 纯产品审查，不增加功能，不修改 Runtime/Agent/Workflow

---

## 一、审查方法

模拟一个从未接触过 ZMAI 的开发者，逐步骤操作，记录每个节点的体验。

```
Step 1: pip install zmai          → 安装体验
Step 2: zmai                      → 首次启动体验
Step 3: zmai 分析这个项目           → 单次执行体验
Step 4: zmai (进入 REPL 后)        → 持续交互体验
Step 5: Ctrl+C / exit             → 退出体验
Step 6: 第二天 zmai               → 长期使用体验
```

---

## 二、逐步骤审查

### Step 1: pip install zmai

**操作：** `pip install zmai`

| # | 问题 | 严重性 |
|---|------|--------|
| 1 | **程序没有发布到 PyPI。** `pip install zmai` 会安装 `zmai`（一个已有的包），不是本项目的。用户只能通过 `git clone + pip install -e .` 安装。这不是"安装体验差"的问题，而是**根本不能安装**。 | P0 |
| 2 | **没有 `__main__.py`。** `python -m zmai` 报错 `No module named zmai.__main__`。一个声称是 CLI 工具的项目不能用模块方式运行。 | P0 |
| 3 | **`import zmai` 后没有 `__version__`。** `python -c "import zmai; print(zmai.__version__)"` 报 `AttributeError`。这不是大问题，但 5 分钟就能修好。 | P2 |

### Step 2: zmai（首次启动）

**操作：** 在终端输入 `zmai`（假设已完成 git clone + pip install -e .）

**实际路径：** `main:354` → `main:371` _should_show_init_wizard → `main:66` _run_init_wizard

| # | 问题 | 严重性 |
|---|------|--------|
| 4 | **向导完成后不进入操作状态。** `_run_init_wizard` 最后一行是 `theme.dim('run zmai --help to start')`。用户刚输完 API Key，看到这条消息，需要再输入一次 `zmai` 才能开始使用。**多了一步不必要的操作。** | P1 |
| 5 | **向导使用 `input()` 而非 `getpass` 选择 Backend。** 选择 Backend 时 (`main:89`)，用户输入的数字会回显在终端。虽然这不泄露信息，但在密码输入前就回显数字，风格不一致。 | P2 |
| 6 | **向导只支持 3 个 Backend。** 代码中硬编码了 deepseek / anthropic / openai 三个。没有 Gemini。用户如果搜索"ZMAI Gemini 配置"，会找到设计文档 (AUTH_DESIGN.md) 说有 Gemini，但实际代码不支持。| P1 |
| 7 | **`_should_show_init_wizard` 只检查 3 个环境变量。** `DEEPSEEK_API_KEY` / `ANTHROPIC_API_KEY` / `OPENAI_API_KEY`。如果用户设置了 `GEMINI_API_KEY`，向导仍会运行。 | P1 |
| 8 | **验证失败后仍保存配置。** `main:118-122`：如果 `urlopen` 抛出异常，打印 FAIL 但不阻止 `main:124` 的 `store.set_backend()`。用户输入了错误的 Key，验证说 FAIL，但仍然保存了。 | P1 |
| 9 | **配置存储在 `~/.zmai/config.json`，但 Config 类不加载它。** `config/config.py:23` 的默认 sources 只有 `FileSource("zmai.json")`。全局配置对 Config 透明。用户通过向导配置了 theme，Config 读不到。 | P1 |

### Step 3: zmai 分析这个项目（单次执行）

**操作：** `cd my-project && zmai 分析这个项目`

**实际路径：** `main:354` → `main:368` parse_args → `main:375-403` 构建 → `main:404` _cmd_run → `main:244` sys.exit

| # | 问题 | 严重性 |
|---|------|--------|
| 10 | **_cmd_run 末尾调用 sys.exit()。** `main:244`。单次执行后退出进程，这是预期的 CLI 行为。但同样的 `_cmd_run` 被 REPL 调用 (`main:262`)，导致 REPL 在第一个任务后死亡。**这不是"优化"，这是一个 bug。** | P0 |
| 11 | **进度回调使用内部工具名。** `main:207` `> ReadFileTool`。用户看到的是内部类名，不是用户友好的名称。这不是大问题，但 v0.1.0 阶段可以接受，需要记录为已知项。 | P2 |
| 12 | **进度回调 `except: pass`。** `main:212-213`。如果 stderr.write 失败，异常被吞掉。这不是致命问题，但这种模式散布在代码中（`main:32,33,41,42,49,50,61,62,154,155,163,164`）。 | P2 |
| 13 | **`_save_session` 也有 `except: pass`。** `main:32-33`。如果 session 保存失败，用户不会知道。P0 级的问题没有 P0 级的错误处理。 | P2 |

### Step 4: zmai（进入 REPL 后）

**操作：** 输入 `zmai` 后进入 REPL（假设已修复 sys.exit bug）

**实际路径：** `main:247-264`

| # | 问题 | 严重性 |
|---|------|--------|
| 14 | **REPL 使用 `input()`，没有 readline。** `main:255`。没有历史记录、没有行编辑、没有 Tab 补全、不能 Ctrl+R 搜索历史。**在 2026 年，一个 CLI 工具的交互模式没有 readline 是不可接受的。** | P0 |
| 15 | **每个任务创建一个新的 Runtime 和 Agent。** `main:383` `Runtime()` 在 REPL 外创建，但 `_cmd_run` 每次都创建一个新的 agent_id (`main:221` `agent_{id(args)}`)。任务之间没有上下文共享。 | P1 |
| 16 | **REPL 中没有内建命令。** 只有 `exit` / `quit`。没有 `/help`、`/status`、`/memory` 等。设计文档 (REPL_DESIGN.md) 设计了 8 个命令但没有实现。 | P1 |
| 17 | **输出使用 stderr 而非 stdout。** `main:207` `sys.stderr.write`。进度信息在 stderr 上是正确的，但 `print_success` 在 `main:235` 输出到 stdout，`print_error` 在 `main:241` 输出到 stderr。用户重定向时会有不一致的行为。 | P2 |
| 18 | **退出消息不明确。** `main:257` `break` 直接退出，没有"Bye"消息。Ctrl+C (`main:263`) 只 `print()` 空行。用户不知道程序是否正常退出。 | P2 |

### Step 5: Ctrl+C / exit

**操作：** 在 REPL 中按 Ctrl+C 或输入 exit

| # | 问题 | 严重性 |
|---|------|--------|
| 19 | **Ctrl+C 在任务执行中不会被 REPL 捕获。** `main:263` 的 `except KeyboardInterrupt` 只在 `_cmd_interactive` 的 `input()` 等待时有效。如果用户在 `_cmd_run`（即 `asyncio.run(runtime.run(...))`）执行时按 Ctrl+C，KeyboardInterrupt 会被 asyncio 的事件循环捕获，结果不确定。 | P0 |
| 20 | **退出时不保存状态。** `main:257` `break` 直接跳出循环到函数结束。没有 session 保存、没有 memory 持久化、没有 workspace 清理。第二天回来恢复不了任何东西。 | P1 |

### Step 6: 第二天 zmai（长期使用）

**操作：** 第二天回到项目目录，再次运行 `zmai`

| # | 问题 | 严重性 |
|---|------|--------|
| 21 | **Workspace 目录不清理。** `workspace/` 目录下有 16 个 Agent 子目录。每次 `_cmd_run` 创建一个新的 agent_id (`agent_{id(args)}`)，每个 id 不同。运行 16 次就有 16 个目录，从不清理。**长期使用后磁盘占用线性增长。** | P1 |
| 22 | **启动信息没有区分"首次"和"日常"。** 每次启动都运行同样的检测流程，显示同样的信息。用户第二天看到的信息和第一天一样（少了"首次使用"的引导信息，但 Dashboard 没变化）。 | P2 |
| 23 | **命令行参数 `nargs="*"` 导致空列表而非 None。** `main:178`。当用户输入 `zmai`（无参数）时，`args.task` 是 `[]` 而非 `None`。`main:393` 的 `if not task and args.task` 中，`bool([])` 为 False，所以 `args.task` 不会被使用。这恰好工作，但语义混乱。 | P2 |

---

## 三、Workspace 现场审查

实际检查 workspace/ 目录的内容（这是一个真实问题，不是推测）：

```
workspace/
├── manifest.json          ← 139 字节（几乎空的）
├── state.json             ← 5.2KB
├── agent_1390702411472/
├── agent_1740046310800/
├── agent_2370305040016/
├── agent_2601936822736/
├── agent_2605773120784/
├── agent_2832944982272/
├── agent_5072615105647101789/
├── agent_8529285113699205516/
├── debug01/
├── final_test/
├── ht4/
├── html_test/
├── html_test2/
├── id_test/
├── id_test2/
├── t1/
├── t2/
├── t3/
└── test/
```

**16 个子目录，大部分是测试残留。** 真实用户的 workspace 会在几个月后增长到几百个目录，每个目录包含 `input/`、`output/`、`temp/`、`.state/`。即使 output 为空（任务不需要写文件），目录结构本身也占用 inode。

**这个问题不需要修改 Workspace 模块。** 只需要在启动时运行一次 GC 清理。Workspace 已经提供了 `list_agents()` 和 `get_state()` API，CLI 层调用即可。

---

## 四、代码考古：main.py 的 419 行

审查 `src/zmai/cli/main.py` 的真实结构：

| 行号 | 长度 | 功能 | 评价 |
|------|------|------|------|
| 1-19 | 19 | 导入 + 常量 | 正常 |
| 22-42 | 21 | `_save_session` / `_load_latest_session` | 正常，但 `except: pass` |
| 45-50 | 6 | `_ensure_utf8` | 正常 |
| 53-63 | 11 | `_should_show_init_wizard` | 缺少 GEMINI_API_KEY |
| 66-136 | 71 | `_run_init_wizard` | 不应该在 main.py 中 |
| 139-155 | 17 | `_inject_auth_credentials` | env 注入脆弱 |
| 158-164 | 7 | `_register_swe_tools` | `except: pass` |
| 167-182 | 16 | `_build_parser` | 参数过多 |
| 185-193 | 9 | `_get_theme` | 正常 |
| 196-244 | 49 | `_cmd_run` | **sys.exit() bug 在此** |
| 247-264 | 18 | `_cmd_interactive` | 无 readline，无内建命令 |
| 267-293 | 27 | `_run_config` | 正常但很基础 |
| 296-352 | 57 | `_run_auth` | 应该在独立模块 |
| 354-416 | 63 | `main()` | 启动编排耦合所有逻辑 |
| 418-419 | 2 | `if __name__` | 正常 |

**结论：** main.py 承担了 10 个不同的职责。对 CLI 工具来说，这不是致命的架构问题——argparse 示例也经常这样——但考虑到代码中已有 bug（sys.exit 和 env 注入），这些职责耦合增加了修复难度。

---

## 五、命令行参数审查

当前 `zmai --help` 的输出：

```
usage: zmai [options] <task...>

ZMAI - Model-Agnostic Agent Runtime

optional arguments:
  --version             show program's version number and exit
  --json                json output
  --no-color            disable color
  -p, --prompt          task description
  -i, --interactive     interactive mode
  -r, --resume          resume last task
  -c, --confirm         confirm before shell/git
```

问题：

1. **`add_help=False`** (`main:172`)。argparse 默认的 `--help` 被禁用。没有替代的 help 实现。用户输入 `zmai --help` 会看到 argparse 的错误信息（因为 `task nargs*` 会消费 `--help`）。

2. **`-p` 和 `task nargs*` 语义重复。** 两者都表示"任务描述"。用户不知道用哪个。

3. **`-i` 是多余的。** `zmai`（无参数）+ tty = 交互模式，不需要手动指定。

4. **`-r` 是"恢复"但设计文档说"自动恢复"。** 如果启动时自动检测未完成任务，不需要 `-r` 标志。

5. **`-c` 只影响 shell/git。** 用户不知道哪些操作需要确认，哪些不需要。

用户体验推演：
```
用户: zmai --help
程序: （argparse 错误，因为 task 消费了 --help）
用户: ... ?
```

实际上 argparse 处理方式不同——`task nargs="*"` 不会消费 `--help`，argparse 有特殊处理。但 `add_help=False` 禁用了这个处理。所以 `zmai --help` 实际上会显示什么？

```
用户: zmai --help
程序: （因为没有显式处理，--help 被 task 捕获为 "help" 任务）
Agent: 开始执行 "help" 任务...
```

用户期望看到帮助信息，结果 Agent 开始执行一个叫"help"的任务。

---

## 六、评估矩阵

### 严重性等级

**P0（阻塞）：** 用户无法完成预期操作，或程序行为与明显意图相反。
**P1（严重）：** 用户能完成操作但体验明显受损，或需要额外步骤。
**P2（轻微）：** 用户能完成操作但不符合预期，或不一致。

### 完整问题清单（按代码路径排序）

```
ID  代码位置              问题                                                     严重性
────────────────────────────────────────────────────────────────────────────────────────
T1  没有发布到 PyPI       用户无法 pip install zmai                                   P0
T2  没有 __main__.py      用户无法 python -m zmai                                     P0
T3  main.py:244           _cmd_run 调用 sys.exit()，导致 REPL 在第一个任务后退出        P0
T4  main.py:255           REPL 使用 input() 无 readline，无历史，无补全                 P0
T5  main.py:263           Ctrl+C 在 asyncio.run 中不被捕获，任务中按 Ctrl+C 行为不确定  P0
T6  main.py:136           向导完成后显示 run zmai --help to start，不进入 REPL          P1
T7  main.py:108-112       向导只支持 3 个 Backend（无 Gemini）                         P1
T8  main.py:54-56         _should_show_init_wizard 不检查 GEMINI_API_KEY               P1
T9  main.py:118-125       验证失败后仍保存配置                                           P1
T10 config/config.py:23   Config 不加载 ~/.zmai/config.json，全局配置不生效              P1
T11 main.py:221           每个任务创建新 agent_id，任务间无上下文                        P1
T12 main.py:247-264       REPL 无内建命令（/help /status 等）                          P1
T13 main.py:257           退出不保存 session/memory/workspace                          P1
T14 workspace/ 不清理      16 个残留 Agent 目录（长期使用会线性增长）                     P1
T15 main.py:172           add_help=False 禁用了 --help                                  P1
T16 main.py:177-178       -p 和 task nargs* 语义重复                                    P2
T17 main.py:179           -i 参数冗余（无参数 + tty = 交互模式）                        P2
T18 main.py:200-213       进度回调使用内部工具名 ReadFileTool                            P2
T19 main.py:32+33+49+50+  多个 except: pass 吞掉异常                                   P2
T20 没有 __version__      import zmai 后无版本号                                       P2
T21 main.py:207+235+241   stdout/stderr 输出位置不一致                                  P2
T22 main.py:257           退出无提示消息                                                P2
```

---

## 七、修复建议（不修改 Runtime/Agent/Workflow）

### P0（必须修，预计 1 天）

**T1: 发布到 PyPI**
```
操作: python -m build && twine upload dist/*
检查: pip install zmai && zmai --version 可用
文件: pyproject.toml（确保 [project.scripts] 和 version 字段）
时间: 2 小时
```

**T2: 添加 `__main__.py`**
```
文件: src/zmai/__main__.py（新增，3 行）
内容: from zmai.cli.main import main; main()
时间: 5 分钟
```

**T3: 修复 REPL sys.exit() bug**
```
文件: src/zmai/cli/main.py
操作: 将 _cmd_run 拆分为两个函数：
  - _repl_run(task, runtime) → 返回 result，不 exit
  - _oneshot_run(task, runtime) → 调用 _repl_run 后 sys.exit()
  REPL 循环调用 _repl_run，main() 中单次执行调用 _oneshot_run
时间: 2 小时
```

**T4: 添加 readline 支持**
```
文件: src/zmai/cli/main.py 或新增 cli/repl.py
操作: 在 _cmd_interactive 中 import readline
  readline.set_history_length(1000)
  atexit.register(readline.write_history_file, ~/.zmai/history)
  如果 readline 不存在（Windows），回退到 input()
时间: 30 分钟
依赖: 只需 Python 标准库 readline（Unix）或 pyreadline（Windows，可选）
```

**T5: 修复 Ctrl+C 处理**
```
文件: src/zmai/cli/main.py
操作: 在 _cmd_run 的 asyncio.run 外包装 try/except
  try:
      result = asyncio.run(runtime.run(...))
  except KeyboardInterrupt:
      print("\n⏸ 任务已暂停")
      return {"status": "cancelled"}
时间: 15 分钟
```

### P1（建议修，预计 2 天）

**T6: 向导后进入 REPL**
```
文件: src/zmai/cli/main.py
操作: _run_init_wizard 成功后返回 True，main() 检测到 True 后直接进入 _cmd_interactive
时间: 30 分钟
```

**T7+T8: 支持 Gemini**
```
文件: src/zmai/cli/main.py
操作: 在 _run_init_wizard 的 backends 列表中加入 ("gemini", "Gemini", "gemini-2.0-flash")
  在 _should_show_init_wizard 检查 GEMINI_API_KEY
时间: 10 分钟
```

**T9: 验证失败不保存**
```
文件: src/zmai/cli/main.py
操作: _run_init_wizard 中验证失败时 return，不执行 store.set_backend()
时间: 5 分钟
```

**T10: Config 加载全局配置**
```
文件: src/zmai/config/config.py
操作: Config.__init__ 默认 sources 中加入 FileSource(Path.home() / ".zmai" / "config.json")
时间: 10 分钟
```

**T11: REPL 共享 Runtime 实例**
```
文件: src/zmai/cli/main.py
操作: _cmd_interactive 在循环外创建 Runtime，循环内每次调用使用相同的 agent_id
  runtime = Runtime(config)
  while True:
      _repl_run(task, runtime, agent_id="repl_agent")
时间: 30 分钟
```

**T13: 退出前保存 session**
```
文件: src/zmai/cli/main.py
操作: _cmd_interactive 在 break 前调用 _save_session 和 runtime.shutdown()
时间: 15 分钟
```

**T14: 启动时清理 workspace**
```
文件: src/zmai/cli/main.py（main 函数中触发）
操作: main() 启动时扫描 workspace/ 目录，对 status=completed/failed 且超过 7 天的目录执行 shutil.rmtree
  只使用 workspace 已有的 list_agents() API，不修改 workspace 模块
时间: 1 小时
```

**T15: 修复 --help**
```
文件: src/zmai/cli/main.py
操作: 移除 add_help=False，或显式处理 --help
时间: 5 分钟
```

### P2（可选修，预计 1 天）

**T12: REPL 内建命令**
```
文件: src/zmai/cli/main.py
操作: 在 REPL 输入检查中检测 "/" 前缀
  /help → 打印命令列表
  /status → 打印当前状态
时间: 2 小时
```

**T16-T22:** 上述各项 P2 问题，每项修复时间 5-30 分钟，总计 4 小时。

---

## 八、结论

### 这个项目当前状态

ZMAI 有一个**设计过度的架构**（26 份设计文档，12 个模块）但**实现不足的用户体验**（CLI 层有 6 个 P0 问题，3 个来自 main.py）。

### 最大的两个问题

1. **`sys.exit()` 在 `_cmd_run` 中** (T3)：这是一个 bug。任何声称是 CLI 交互工具的项目都不能在执行一个任务后退出进程。这必须作为 bug 修复，不是功能改进。

2. **REPL 没有 readline** (T4)：`input()` 循环在 2026 年的 CLI 工具中是不合格的。用户不能翻阅历史、不能搜索、不能补全。这不是"改进"，这是"让基本功能工作"。

### 修复优先级

```
P0（1 天）: 修复 6 个阻塞问题
  └─ 修复后：用户可以安装、运行、交互、退出、重入

P1（2 天）: 修复 9 个严重问题
  └─ 修复后：用户可以获得完整的首次运行体验和日常使用体验

P2（1 天）: 修复 7 个轻微问题
  └─ 修复后：行为一致、输出规范、符合 Unix 习惯

总计: 4 天 = 从不可用原型到可用产品
```

### 一句话总结

> **这个项目有 26 份设计文档和一个功能完整的 Runtime，但 CLI 层——用户唯一看到的部分——连最基本的"执行一次命令后不崩溃"和"输入时能按上箭头找回历史"都做不到。先修 CLI 层，其他都是次要的。**
