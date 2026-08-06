# Workspace Security Audit

> 日期：2026-07-22
> 审计方式：静态代码审查，不修改代码

---

## 审计范围

检查 Agent 工作区路径隔离的所有入口点，确认是否存在路径穿越、沙箱逃逸或信息泄露风险。

```
Workspace 隔离边界示意：

  /workspace/             ← Workspace root（配置）
    ├── agent_1/          ← Agent 沙箱
    │   ├── input/
    │   ├── output/
    │   ├── temp/
    │   └── .state/
    ├── agent_2/
    └── agent_1-secret/   ← 不同 Agent，不应被 agent_1 访问
```

---

## 1. Workspace 根路径如何定义

```python
# workspace.py:338
self._root = Path(root).resolve()
```

**结论：** ✅ 安全。

- 传入的 `root`（默认为 `"./workspace"`）被 `Path.resolve()` 转换为绝对路径
- 符号链接被展开
- `..` 段落被展平
- 所有后续操作基于这个已解析的绝对路径

---

## 2. Agent Workspace 如何创建

```python
# workspace.py:835-843
def _agent_path(self, agent_id: str) -> Path:
    if "/" in agent_id or "\\" in agent_id or ".." in agent_id:
        raise WorkspaceError("非法的 Agent ID")
    return self._root / agent_id
```

```python
# workspace.py:845-875
def _validate_path(self, agent_id: str, path: str) -> Path:
    agent_path = self._agent_path(agent_id)
    target = (agent_path / path).resolve()
    agent_path_resolved = agent_path.resolve()

    # 使用 pathlib.relative_to（非字符串 prefix）
    try:
        target.relative_to(agent_path_resolved)
    except ValueError:
        raise WorkspaceError(f"路径穿越被拒绝: {path}")

    # agent_path 必须在 workspace root 下
    try:
        agent_path_resolved.relative_to(self._root)
    except ValueError:
        raise WorkspaceError(...)

    return target
```

**结论：** ✅ 安全（P0-3 已修复）。

- Agent ID 中的 `..` `/` `\` 被拦截
- 路径验证使用 `pathlib.relative_to()`（组件级），`/ws/agent_1-secret/` 不会被误认为 `/ws/agent_1/` 的子路径
- 双重检查：target → agent_path → root

---

## 3. 所有文件工具如何调用 _validate_path()

| 方法 | 调用 _validate_path | 调用 _resolve_tool_path |
|------|---------------------|------------------------|
| `Workspace.read()` | ✅ 是 | — |
| `Workspace.write()` | ✅ 是 | — |
| `Workspace.delete()` | ✅ 是 | — |
| `Workspace.exists()` | ✅ 是 | — |
| `ReadFileTool.execute()` | — | ✅ **另有一套独立验证** |
| `WriteFileTool.execute()` | — | ✅ **另有一套独立验证** |
| `EditTool.execute()` | — | ✅ **另有一套独立验证** |
| `OpenInBrowserTool.execute()` | — | ✅ **另有一套独立验证** |
| `GrepTool.execute()` | — | ❌ 无路径验证，使用 `context.workspace_path` |
| `ShellTool.execute()` | — | ❌ **无路径验证，子进程可访问全盘** |
| `GitTool.execute()` | — | ❌ **无路径验证，git 可访问全盘** |

### 关键发现

**Workspace 文件操作**（read/write/delete/exists）通过 `_validate_path()` → ✅ 安全

**工具路径验证**（ReadFileTool/WriteFileTool/EditTool/OpenInBrowserTool）通过 `_resolve_tool_path()` → 🟡 **另有一套独立逻辑**

---

## 4. _resolve_tool_path 审计

```python
# swe/tools.py:41-86
```

### 4.1 绝对路径 — 允许访问 project 路径（非 workspace）

```python
if p.startswith("/") or (len(p) > 1 and p[1] == ":"):
    resolved = Path(p).resolve()
    project_path = context.project_path
    if project_path:
        try:
            resolved.relative_to(pp.resolve())
            return True, resolved, ""    # 允许！
        except ValueError:
            return False, ...
    return True, resolved, ""             # 无 project_path 时直接允许！
```

**问题：** 当 `project_path` 为 None（未设置）时，绝对路径被直接允许，绕过所有 workspace 隔离。这意味着 Agent 可以通过 `read_file("/etc/passwd")` 读取系统文件。

**风险：** 🟠 中。默认情况下 `project_path` 可能为 None。

### 4.2 相对路径 — 优先 project_path，回退 workspace_path

```python
# 相对路径规则：project_path > workspace_path
resolved = (pp / p).resolve()
resolved.relative_to(pp.resolve())  # 允许访问整个 project 目录
# 如果逃逸了 project_path，然后尝试 workspace_path
resolved = (context.workspace_path / p).resolve()
resolved.relative_to(context.workspace_path.resolve())
```

**问题：** Agent 工具（ReadFile/WriteFile/Edit）可以访问整个 project 目录，不仅限于 workspace。这不是漏洞而是设计特性，但意味着 workspace 隔离不能保护 project 文件不被 Agent 修改。

**风险：** 🟢 低，工具默认以 workspace 为工作区，只在明确设置了 project_path 时扩展。

---

## 5. ShellTool（shell_exec）审计

```python
# swe/tools.py:540
cwd = str(context.project_path or context.workspace_path)
r = subprocess.run(cmd, shell=True, cwd=cwd, ...)
```

**ShellTool 没有任何路径验证。** `cwd` 只是初始工作目录，Shell 命令可以：

```bash
ls /etc/passwd           # 读取系统文件
cd / && rm -rf /         # 破坏系统
cat ~/.zmai/credentials  # 读取其他工具的凭据
```

**风险：** 🔴 **严重。** shell_exec 是 Agent 最强大的工具，完全没有沙箱。这是有意为之（Agent 需要执行 shell 命令完成任务），但开源后必须明确文档化这一风险。

**缓解：** `on_confirm` 回调可以在执行前提示用户确认 shell 命令。

---

## 6. OpenInBrowserTool 审计

```python
# swe/tools.py:130-131
is_safe, full, err_msg = _resolve_tool_path(context, path)
```

OpenInBrowserTool 使用 `_resolve_tool_path()` 验证路径，安全性与 ReadFileTool 相同。

此外，它还使用 `subprocess.run(["cmd", "/c", "start", "", abs_path])` 在浏览器中打开文件，这本身不包含路径穿越风险。

**结论：** 🟡 中，与 `_resolve_tool_path` 相同风险。

---

## 7. Symlink 是否可能绕过 Workspace

`_validate_path()` 使用 `target.resolve()` 展开符号链接。如果 `target` 是一个指向 workspace 外部的 symlink，`resolve()` 会变成外部路径，然后 `relative_to()` 检查会拒绝它。

```python
# 示例：
# /workspace/agent_1/link -> /etc/passwd
target = (agent_path / "link").resolve()  # -> /etc/passwd
target.relative_to(agent_path_resolved)    # ValueError！拒绝
```

**结论：** ✅ 安全。`resolve()` 展开 symlink 后，外部路径会被 `relative_to` 拒绝。

**但是**：symlink 是在写入阶段被验证的，写入时 `write()` 调用 `_validate_path()` 拒绝任何指向外部的 symlink。然而，如果用户在 workspace 内创建了一个指向外部的 symlink（通过 `shell_exec` 执行 `ln -s`），后续 `read()` 调用会看到这个 symlink 并通过 `resolve()` 展开它，然后被 `relative_to()` 拒绝。

**结论：** ✅ 安全，但依赖于 `resolve()` 的路径展平。

---

## 8. Windows Junction 是否可能绕过 Workspace

Windows junctions 的行为类似于 Unix symlink。`Path.resolve()` 在 Windows 上会展开 junctions。

```python
# Python 3.11+ 的 Path.resolve() 默认不跟随 symlink，除非 strict=True
# 但 Path.resolve() 的默认行为在 Python 3.11 中已改为不跟随
```

**需要检查 Python 版本：** 项目要求 `python >= 3.10`。在 Python 3.11+ 中，`Path.resolve()` 默认不跟随 symlink。

```python
# workspace.py:338
self._root = Path(root).resolve()  # 3.11+ 默认不跟随 symlink！
```

**风险：** 🟠 **中。** 如果 `self._root` 本身是一个 junction 且 `resolve()` 在 3.11+ 上不展开它，后续路径比较可能基于未被解析的路径。攻击者可以：
1. 创建指向外部目录的 junction
2. 将 workspace root 设为 junction
3. `_validate_path()` 中的 `relative_to()` 比较的是未解析的路径，可能允许逃逸

**但：** 在 `_validate_path()` 中，`target = (agent_path / path).resolve()` 对 target 做 `resolve()`，而 `agent_path_resolved = agent_path.resolve()` 也对 agent_path 做 `resolve()`。所以两者要么都 resolve，要么都不 resolve，比较保持一致。

**结论：** 🟢 低，在当前的 `_validate_path()` 实现（`relative_to`）下安全。

---

## 9. 相对路径是否可能使用 `..` 绕过

**Workspace 层面：**
```python
# Agent ID 拦截
".." in agent_id  # 拒绝

# path resolve 展平
target = (agent_path / path).resolve()  # "a/../../etc" → "/etc"
target.relative_to(agent_path_resolved)  # ValueError！拒绝
```

**结论：** ✅ 安全。

**工具层面（_resolve_tool_path）：**
```python
resolved = (pp / p).resolve()  # 同样 resolve 展平
resolved.relative_to(pp.resolve())  # relative_to 拒绝
```

**结论：** ✅ 安全。

---

## 10. 绝对路径是否可以访问 Workspace 外部

**Workspace 层面（read/write/delete）：** 通过 `_validate_path()` → `relative_to()` → ✅ 安全，拒绝所有外部路径。

**工具层面（_resolve_tool_path）：** 当 `project_path` 为 None 时，绝对路径被直接允许。⚠️

**ShellTool：** 允许任意绝对路径（子进程级别）。🔴

**GitTool：** 允许任意路径（通过 `git --git-dir`）。🔴

---

## 11. agent_1 与 agent_1-secret 的隔离

`_validate_path()`（已修复为 `relative_to`）：
```
/ws/agent_1/file.txt      → relative_to(/ws/agent_1)        → ✅ 通过
/ws/agent_1-secret/file    → relative_to(/ws/agent_1)        → ❌ ValueError（修复前 startswith 放行）
```

**结论：** ✅ 安全（P0-3 修复后）。

---

## 总体风险矩阵

| # | 入口 | 风险 | 说明 |
|---|------|------|------|
| 1 | `Workspace.read/write/delete` via `_validate_path()` | 🟢 低 | `relative_to` 正确阻止穿越 |
| 2 | Agent ID 注入 | 🟢 低 | `..` `/` `\` 被拦截 |
| 3 | Symlink 指向外部 | 🟢 低 | `resolve()` 展开后拒绝 |
| 4 | Windows Junction | 🟢 低 | `resolve()` 行为一致 |
| 5 | `..` 路径段落 | 🟢 低 | `resolve()` 展平 |
| 6 | **`_resolve_tool_path` 绝对路径绕过** | 🟠 **中** | project_path=None 时允许任意绝对路径 |
| 7 | **ShellTool 无沙箱** | 🔴 **严重** | 任意 shell 命令，无路径限制 |
| 8 | **GitTool 无沙箱** | 🟠 **中** | 可通过 `--git-dir` 访问任意路径 |
| 9 | Agent 间隔离 | 🟢 低 | `relative_to` 正确隔离 `/agent_1/` 和 `/agent_1-secret/` |

---

## 核心结论

**Workspace._validate_path()** — ✅ 正确。P0-3 的 `relative_to` 修复已解决字符串 prefix 绕过问题。symlink/junction/`..` 均被正确处理。

**ShellTool** — 🔴 最大风险。子进程可以访问整个文件系统。这是 Agent 的核心能力，不能硬性限制，但开源后必须文档化。

**_resolve_tool_path** — 🟠 发现一个新问题：当 `context.project_path` 为 None 时，**绝对路径被直接放行**，绕过所有 workspace 隔离。

```
_read_file("C:\\users\\me\\secrets.txt")  # 当 project_path=None 时允许
```

**建议修复：** 即使 `project_path` 为 None，绝对路径也应回退到 `workspace_path` 检查，而不是直接放行。

---

## 文件入口总结

```
Agent 路径访问拓扑：

Agent (LLM)
  │
  ├── read_file("path")           → _resolve_tool_path()    → 🟡 project_path 绕过
  ├── write_file("path")          → _resolve_tool_path()    → 🟡 同上
  ├── edit("path")                → _resolve_tool_path()    → 🟡 同上
  ├── open_in_browser("path")     → _resolve_tool_path()    → 🟡 同上
  ├── grep("pattern")             → workspace_path           → ✅ 无路径注入
  ├── shell_exec("command")       → 无限制                   → 🔴 全盘访问
  ├── git("args")                 → workspace_path           → 🟡 可通过参数注入
  │
  └── Workspace.read/write        → _validate_path()         → ✅ 安全
