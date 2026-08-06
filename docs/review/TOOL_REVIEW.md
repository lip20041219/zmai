# ZMAI Tool System Review

> 审查日期: 2026-07-17
> 范围: ToolRegistry、Tool 生命周期、write_file、read_file、edit、shell_exec、browser、grep、git、show_to_user
> 检查项: 重复注册、失败重试、Fallback、Windows 兼容性
> 原则: 不修改代码

---

## 一、体系概览

| 组件 | 文件 | 行数 |
|------|------|------|
| Tool ABC | `src/zmai/tool/base.py` | 186 |
| ToolRegistry | `src/zmai/tool/registry.py` | 141 |
| ToolRouter | `src/zmai/gateway/tool_router.py` | 118 |
| 工具实现 (8个) | `src/zmai/swe/tools.py` | 512 |
| MCP Client | `src/zmai/gateway/mcp.py` | 204 |
| SWEAgent 注册 | `src/zmai/swe/agent.py` | 8 个工具 |

8 个内置 SWE 工具：

| 工具名 | 类 | 职责 |
|--------|-----|------|
| `read_file` | ReadFileTool | 读取文件（支持行范围） |
| `write_file` | WriteFileTool | 写入文件（覆盖，自动建父目录） |
| `edit` | EditTool | 行级编辑（替换/正则/插入/追加） |
| `grep` | GrepTool | 文本搜索（纯 Python 正则） |
| `shell_exec` | ShellTool | 执行 Shell 命令 |
| `git` | GitTool | 执行 Git 命令 |
| `show_to_user` | ShowToUserTool | 向终端打印内容 |
| `open_in_browser` | OpenInBrowserTool | 在浏览器打开 HTML |

---

## 二、检查结果

### P0 — 严重问题

#### [GrepTool] `glob` 参数声明但未实现

**文件:** `src/zmai/swe/tools.py:355-408`

```python
class GrepTool(Tool):
    name = "grep"
    parameters = {
        "properties": {
            "pattern": {"type": "string"},
            "glob": {"type": "string"},         # ← 声明了
            ...
        },
    }
```

`parameters` 中声明了 `glob` 参数，LLM 可以传入，但 `execute()` 中**完全不使用**：

```python
def execute(self, context, params):
    ...
    root = context.project_path or context.workspace_path
    for f in sorted(root.rglob("*")):   # 始终全量扫描
```

不管 LLM 传什么 `glob` 值，GrepTool 总是遍历项目根下**所有文件**。这导致：
- 大项目中 grep 性能极差（遍历所有文件）
- LLM 以为可以缩小范围但实际不能，造成认知偏差
- 在 node_modules 巨大目录仍然遍历（虽然有 _IGNORE_DIRS，但遍历本身已触发 IO）

---

#### [EditTool] 文件编辑无备份/无回滚

**文件:** `src/zmai/swe/tools.py:265-352`

所有四种编辑模式（`replace_lines`、`regex_replace`、`insert`、`append`）都是直接读取 → 原地修改 → 直接写回，**没有任何备份机制**：

```python
lines = full.read_text(encoding="utf-8").splitlines(keepends=True)
# ... 直接修改 lines ...
full.write_text("".join(lines), encoding="utf-8")
```

如果写入过程中断（断电、OOM、`kill -9`），文件处于**截断+部分写入**状态。
`regex_replace` 更是解构后再一次性写回，风险相同。

---

#### [ShellTool / GitTool] `shell=True` 安全风险

**文件:** `src/zmai/swe/tools.py:458, 499`

```python
r = subprocess.run(cmd, shell=True, cwd=cwd, ...)
r = subprocess.run(f"git {args}", shell=True, cwd=..., ...)
```

`shell=True` 在 Windows 上通过 `cmd.exe /c` 执行，在 POSIX 上通过 `/bin/sh -c` 执行。
当命令来自 LLM 输出（不可信输入）时：

- 路径含特殊字符时可注入额外命令
- `git` 工具直接将 `args` 拼接到 `f"git {args}"` — 如果 `args` 包含 `; rm -rf /` 等，会执行
- 当前 Agent 上下文中用户是可信的，但工具本身的设计没有考虑输入来源的可信分级

---

#### [ShellTool] Windows 命令翻译过于简单

**文件:** `src/zmai/swe/tools.py:411-426`

```python
def _translate_cmd(cmd: str) -> str:
    if sys.platform != "win32":
        return cmd
    stripped = cmd.strip().lower()
    if stripped == "ls":
        return "dir"
    if stripped.startswith("ls "):
        return "dir" + cmd[2:]
    if stripped == "pwd":
        return "cd"
    if stripped.startswith("pwd "):
        return "cd" + cmd[3:]
    if stripped.startswith("cat "):
        return "type " + cmd[4:]
    return cmd  # 不匹配的不翻译
```

严重问题：

1. **不匹配的命令静默通过** — 大量 Linux 命令（`rm`、`mv`、`cp`、`grep`、`find`、`which`、`ps`、`kill`、`sort`、`wc`、`uname`、`chmod`、`head`、`tail`、`diff`、`less`、`more` 等）不被翻译，直接传给 Windows cmd.exe，必然失败
2. **翻译模式是前缀匹配，不是 token 匹配** — `ls -la` 翻译为 `dir -la`（`ls `→`dir `+其余），但 `dir -la` 在 Windows 上是非法参数
3. **没有用户提示** — 失败后不提示用户可用哪些 Windows 等价命令
4. **`cat file.txt` 译为 `type file.txt`** — `type` 在 cmd 中是内置命令，但不带行号且对大文件性能很差

---

### P1 — 重要问题

#### [ToolRegistry] 重复注册只记 warning，不做去重保护

**文件:** `src/zmai/tool/registry.py:31-43`

```python
def register(self, tool: Tool) -> None:
    with self._lock:
        if tool.name in self._tools:
            logger.warning("工具已存在，将被覆盖: %s", tool.name)
        self._tools[tool.name] = tool
```

同名工具被覆盖时只输出 warning。但：

- 如果 `tool_a` 注册了 `EchoTool()`，然后被用户注册的另一个 `EchoTool()`（同名但不同子类或不同配置）覆盖，之前的注册丢失
- 没有版本号或优先级机制标识哪个是"正确的"
- SWEAgent.initialize() 注册时检查 `if tool.name not in existing`，但这只能防止同一来源重复注册，不能防止用户提前注册了同名工具后被 SWEAgent 跳过

#### [ToolRegistry] `validate()` 参数校验有类型盲区

**文件:** `src/zmai/tool/base.py:141-177`

```python
def validate(self, params):
    ...
    for key, val in params.items():
        if key in props:
            prop = props[key]
            expected = prop.get("type")
            if expected and val is not None:
                if expected == "string" and not isinstance(val, str):
                    return False
                ...
```

校验问题：

1. **不校验 enum 约束** — EditTool.parameters 中 `mode` 声明了 `"enum": ["replace_lines", "regex_replace", "insert", "append"]`，但 `validate()` 完全不检查 enum 值
2. **不校验嵌套 JSON Schema** — 如 `properties` 中的嵌套 `object` 类型的子字段
3. **不校验 `minLength` / `maxLength` / `minimum` / `maximum`** 等约束
4. **`"array"` 类型只检查是否是 list，不检查元素类型**
5. **`required` 检测有缺陷** — `validate` 返回 `False` 后，`execute()` 重新推断缺失字段（第 101 行），说明 validate 本身没有提供足够的错误信息

#### [WriteFileTool] 两种备选机制实际极少触发 — 场景混淆

**文件:** `src/zmai/swe/tools.py:239-261`

```python
# Attempt 1: Path.write_text()
try:
    full.write_text(content, encoding="utf-8")
    ...
except Exception as e:
    errors.append(f"Attempt 1 (Path.write_text): ...")

# Attempt 2: Python open()
try:
    with open(str(full), "w", encoding="utf-8", errors="strict") as f:
        f.write(content)
    ...
except Exception as e3:
    errors.append(f"Attempt 2 (open): ...")
```

两个备选方案实际上都使用相同的基础调用（UTF-8 编码写入）。如果 `Path.write_text` 因
权限、磁盘满、文件锁等原因失败，`open()` 几乎必然以相同的理由失败：

- 两种方法都调用 `full.write_text()` / `f.write()` — 写入方式本质相同
- 唯一的微小差别是 `errors="strict"` vs `write_text` 的默认行为 — 但这个阶段编码已经固定为 utf-8
- 两个方法都捕获 `Exception`，如果真正的问题是 `PermissionError`，备选不会解决问题

真正有区别的 fallback 应该是：
- 写入临时文件后 rename（原子写入）
- 尝试不同编码
- 尝试不同目录

#### [OpenInBrowserTool] Windows 平台打开失败仅检查 returncode，不读错误输出

**文件:** `src/zmai/swe/tools.py:139-146`

```python
if sys.platform == "win32":
    r = subprocess.run(
        ["cmd", "/c", "start", "", abs_path],
        capture_output=True, text=True, timeout=10,
    )
    if r.returncode != 0:
        result = ToolResult.err(f"浏览器打开失败 (exit {r.returncode})")
```

- `subprocess.run` 捕获了 `stdout/stderr` 但错误时完全不输出
- `start` 命令在 cmd 中返回码不一定反映实际打开结果 — `start` 启动成功后返回 0，但浏览器本身可能打不开文件
- macOS 和 Linux 路径甚至不检查 returncode

#### [ReadFileTool] 不支持二进制文件 / 非 UTF-8 编码

**文件:** `src/zmai/swe/tools.py:188-189`

```python
lines = full.read_text(encoding="utf-8").splitlines(keepends=True)
```

- 硬编码 `utf-8`，无编码检测或 fallback
- 二进制文件（`.png`, `.pdf`, `.exe`）读取时抛出 `UnicodeDecodeError`，被 `except Exception` 捕获返回 `"read error"`
- 没有通知用户文件是二进制的提示

#### [EditTool] `mode == "append"` 路径与其余模式不一致

**文件:** `src/zmai/swe/tools.py:300-304`

```python
if mode == "append":
    full.parent.mkdir(parents=True, exist_ok=True)
    with full.open("a", encoding="utf-8") as f:
        f.write(new_text)
    result = ToolResult.ok(output=f"appended {path}")
```

`append` 模式：
- 跳过 `not full.exists()` 检查（合理 — 新文件也可以 append）
- 但不检查文件是否存在就打开追加，如果路径指向已存在的目录则静默写入失败
- 使用 `full.open("a")` 直接在文件末尾追加，但如果在 append 之前有其他模式修改了文件，这个句柄可能不一致

#### [EditTool] `insert` 模式在 line 超出范围时静默延展

**文件:** `src/zmai/swe/tools.py:340-346`

```python
if mode == "insert":
    ln = params.get("start_line", 1)
    ins = new_text.splitlines(keepends=True)
    lines[ln-1:ln-1] = ins
```

Python 列表切片在起始索引超出范围时不会抛异常 — `lines[100:100]` 等于在末尾插入。
但用户可能期望 `line 100` 不存在时得到错误反馈。测试 `test_insert_beyond_end` 确认此行为是"预期"的，
但从用户角度看缺少验证。

#### [SWEAgent] 工具注册暴露内部实现细节

**文件:** `src/zmai/swe/agent.py:161-169`

```python
async def initialize(self, context):
    if context.tools:
        existing = {t.name for t in context.tools.list()}
        for tool in [ReadFileTool(), ...]:
            if tool.name not in existing:
                context.tools.register(tool)
```

- 每次 `initialize()` 都创建 8 个新实例，即使之前已经注册过同名工具（仅检查 `existing`，但如果是不同类同名则会跳过）
- 工具实例一次性创建，不支持懒加载
- 没有清理/反注册机制（因为 Runtime 的 LifecycleManager 在 remove 时不会清理 tools）

---

### P2 — 次要问题

#### [ToolRegistry] `execute()` 和 ToolRouter 有双重执行路径

**文件:** `src/zmai/tool/registry.py:88` + `src/zmai/gateway/tool_router.py:35`

- `ToolRouter.execute()` 调用 `tool.execute(exec_context, tool_call.params)` **直接执行**（第 70 行）
- `ToolRegistry.execute()` 调用 `tool.execute(context, params)` **也在执行**（第 109 行）
- 但 `SWEAgent.step()` 使用的是 `context.tools.execute(tc.name, tc.params, tctx)`（swe/agent.py:211）
- 而 `Runtime._execute_task()` 使用 `self._tool_router.execute(tc, ctx)`（runtime/runtime.py:232）
- 两条执行路径并存，`_execute_task` 是死代码，但 `ToolRouter` 作为组件仍可被外部调用

#### [WriteFileTool] 大文件写入没有进度反馈

`write_file` 执行成功时返回 `"written {path} ({len(content)} chars)"`。但如果写入 100MB 文件，
线程池超时机制（`_execute_with_timeout` 在 registry.py）可能触发超时中断，但文件已经部分写入，
没有清理机制。

#### [ReadFileTool] 大文件无读取限制

`read_file` 不限制读取大小。一个 2GB 的文件会被完整读入内存然后 splitlines，导致 OOM。
没有 `max_size` 或 `max_lines` 保护。GrepTool 的 `read_text()` 同理。

#### [GitTool] 硬编码 30 秒超时 — 无法配置

**文件:** `src/zmai/swe/tools.py:499`

```python
r = subprocess.run(f"git {args}", shell=True, cwd=str(context.workspace_path),
                   capture_output=True, text=True, timeout=30, ...)
```

超时硬编码为 30s，不用 context.timeout，不读工具参数中的 timeout 字段。

#### [ShellTool] 输出截断 5000/10000 字符可能丢失关键信息

- 错误输出截断到 5000 字符（第 465 行）
- 成功输出截断到 10000 字符（第 468 行）
- 截断后无提示（如 `...(truncated)`），用户不知道输出不完整

#### [WriteFileTool] `OSError` 不包含 errno

**文件:** `src/zmai/swe/tools.py:232-235`

```python
except OSError as e:
    result = ToolResult.err(f"无法创建目录 {full.parent} (OS error {e.errno}): {e.strerror}")
```

父目录创建失败时返回错误但不终止。后续 `Path.write_text()` 会再次失败并触发 fallback 机制。
更好的做法是目录创建失败直接返回错误，不再尝试写入。

#### [ShowToUserTool] 输出目标为 stderr — 非预期

**文件:** `src/zmai/swe/tools.py:106**

```python
sys.stderr.write(f"{header}{content}\n\n")
```

`show_to_user` 工具的目的是"向用户展示内容"，但写入 stderr 而非 stdout。
这让用户在管道场景下无法捕获工具输出：

```bash
zmai "生成报告" > report.txt   # show_to_user 的内容跑到终端而不是 report.txt
```

#### [GrepTool] `_IGNORE_DIRS` 忽略模式只检查子目录不检查根目录

```python
if any(part in _IGNORE_DIRS for part in rel.split("/")[:-1]):
    continue
```

`[:-1]` 切片忽略了文件本身所在的目录名。如果文件直接在 ignore 目录下（如 `.git/config`），
`rel.split("/")[:-1]` → `['.git']`，但 `[:-1]` 会取空列表 `[]`，所以 `.git/config`
**不会被忽略**。只有 `.git/objects/abc123` 这种二级目录才会被正确忽略。

#### [GrepTool] 逐文件 `read_text` 性能差

对每个文件都调用 `read_text().splitlines()`，逐行正则搜索。没有使用 ripgrep、grep -r 或
任何优化方式。大目录下数千个文件时性能堪忧。

#### [所有工具] 没有统一的重试机制

单个工具都不实现重试。`ToolRegistry` 的超时机制（之前修复的）只负责超时终止，不负责重试。
如果 `shell_exec` 因为临时网络错误失败、`write_file` 因为文件锁竞争失败，没有自动重试。

#### [所有工具] `_emit_tool_result` 每次都写入 sys.stderr

**文件:** `src/zmai/swe/tools.py:18-38`

每个工具的每次执行都调用 `_emit_tool_result` 向 stderr 写日志。高频工具调用（如 grep 在循环中）
会大量输出。虽然有 `_quiet` 配置开关，但需要在 ToolContext 中手动设置。

---

## 三、专项检查

### 3.1 重复注册

| 场景 | 风险等级 | 说明 |
|------|---------|------|
| 同名工具被覆盖 | P1 | ToolRegistry 只记 warning，无版本/优先级 |
| SWEAgent 跳过同名但不同实现的注册 | P1 | `if tool.name not in existing` 检查名称而非类型 |
| MCP 工具命名冲突 | P2 | MCPClient.list_tools() 返回的工具有同名风险但无冲突检测 |
| Doctor 测试注册不同工具集 | P2 | CLI doctor 构建独立 ToolRegistry，不影响主注册表 |

### 3.2 失败重试

| 工具 | 重试机制 | 说明 |
|------|---------|------|
| write_file | **有** (2次尝试) | Path.write_text → open(), 但本质相同 |
| ClaudeBackend API | **有** (指数退避 3次) | gateway 级别，不是 tool 级别 |
| 其余所有工具 | **无** | 失败即返回 |

### 3.3 Fallback

| 场景 | Fallback | 说明 |
|------|---------|------|
| 路径解析 (project_path 不可用) | 退回到 workspace_path | _resolve_tool_path 的 three-tier 设计 |
| 文件写入失败 | 两种 Python 写法备选 | 但本质相同，极少生效 |
| Windows 命令翻译 | 不匹配时原样传递 | 大量命令未覆盖 |
| ShellTool 编码 | `errors="replace"` | 非 UTF-8 输出可读但不保真 |
| GrepTool 解码失败 | `except Exception: pass` | 静默跳过不可读文件 |

### 3.4 Windows 兼容性

| 工具 | Windows 状态 | 问题 |
|------|-------------|------|
| `read_file` | ✅ 正常 | 路径中的 `\` 被 `_resolve_tool_path` 转 `/` |
| `write_file` | ✅ 正常 | 同上 |
| `edit` | ✅ 正常 | 同上 |
| `grep` | ✅ 正常 | 纯 Python，跨平台 |
| `shell_exec` | ⚠️ 部分 | Windows 命令翻译不全，大量命令不匹配 |
| `git` | ⚠️ 部分 | `shell=True` 在 Windows 上用 cmd.exe，含空格路径可能出问题 |
| `show_to_user` | ✅ 正常 | stderr 写入 |
| `open_in_browser` | ✅ 正常 | Windows 专用 `cmd /c start` 路径 |

---

## 四、汇总

| 层级 | P0 | P1 | P2 |
|------|----|----|----|
| ToolRegistry | 0 | 2 | 2 |
| Tool 生命周期 | 0 | 1 | 1 |
| write_file | 0 | 1 | 2 |
| read_file | 0 | 1 | 1 |
| edit | 1 | 2 | 0 |
| shell_exec | 2 | 0 | 1 |
| browser | 0 | 1 | 0 |
| grep | 1 | 0 | 2 |
| git | 0 | 0 | 1 |
| show_to_user | 0 | 0 | 1 |
| **合计** | **4** | **8** | **11** |

### 最需要优先处理的 3 个问题

1. **GrepTool glob 形同虚设** (P0) — 声明了 `glob` 参数却不使用，LLM 以为能过滤但实际全量搜索
2. **EditTool 无备份/无回滚** (P0) — 编辑过程中断导致文件损坏，无恢复手段
3. **ShellTool 命令翻译不完整** (P0) — Windows 上大量 Linux 命令静默传入 cmd.exe 而失败
