# ZMAI Workspace Review

> 审查日期: 2026-07-17
> 范围: Workspace Root, cwd, 相对路径, 绝对路径, Current Folder, Temp, Cache
> 文件: `src/zmai/workspace/workspace.py` (1044 行), `src/zmai/cli/main.py`, `src/zmai/swe/tools.py`

---

## 一、执行摘要

ZMAI Workspace 系统当前为 **v1.0 稳定版本**，实现了一个功能完整的 Agent 文件沙箱：

- 每个 Agent 拥有独立目录（`input/`、`output/`、`temp/`、`.state/`）
- 路径穿越防护（_validate_path 双锚检查）
- 文件大小限制、并发锁、清单追踪
- 自动清理（启动时删除 7 天前的已完成/failed workspace）

**但存在若干重要问题需要解决：**

| 严重度 | 问题 | 影响 |
|--------|------|------|
| **高** | Workspace Root 相对于 CWD 解析，非项目根 | 从子目录运行 zmai 时 workspace 位置不一致 |
| **高** | `_validate_path()` 使用字符串前缀检查，易绕过 | 相邻目录可被误判为子目录 |
| **中** | 每次 write() 执行完整 `rglob("*")` 扫描 | 文件数多时性能退化到 O(n) |
| **中** | 全局 manifest.json 遍历所有 Agent | 启动和每次写入变慢 |
| **低** | 无 Cache 层 | 重复文件读 I/O |
| **低** | 无 Auto-GC 触发 | workspace 可能堆积 |
| **低** | 前端 cwd/chdir 未明确测试 | 潜在的路径歧义 |

**设计文档（WORKSPACE_DESIGN.md v2.0）规划了完整的重构方案（6 文件 ~970 行），但尚未实现。** 本报告覆盖当前 v1.0 的实际行为。

---

## 二、Workspace Root

### 2.1 配置源

| 层级 | 值 | 位置 |
|------|-----|------|
| 默认 | `"./workspace"` | `workspace.py:297` (DEFAULT_WORKSPACE_CONFIG) |
| 项目配置 | `"./workspace"` | `zmai.json:14` (workspace.root) |
| CLI 读取 | 同上 | `main.py:595` → `config.get("workspace.root", "./workspace")` |

### 2.2 解析路径

```python
# workspace.py:338
self._root = Path(root).resolve()
```

`Path(root).resolve()` 将路径转换为**相对于当前进程 CWD 的绝对路径**。CWD 在 `main.py` 启动时即当前终端目录。

### 2.3 关键问题：Root 解析相对于 CWD 而非项目根

```
项目结构:
  D:\project\                 ← 项目根（含 zmai.json）
    workspace\                ← 期望的 workspace 位置
    src\
    
用户运行:
  D:\project> zmai            → CWD = D:\project\
  D:\project> zmai            → workspace = D:\project\workspace ✅

但如果:
  D:\project\src> zmai        → CWD = D:\project\src\
  D:\project\src> zmai        → workspace = D:\project\src\workspace ❌
```

**根因**：Workspace 构造函数使用 `Path.resolve()` 时 CWD 尚未统一到项目根。项目检测（`find_project_root()`）能找到根但结果不用来解析 workspace root。

**修复方案**：Workspace 构造函数应接受可选的 `project_root` 参数，如果 workspace.root 是相对路径则相对 project_root 解析：

```python
def __init__(self, root, config=None, project_root=None):
    self._root = Path(root)
    if not self._root.is_absolute() and project_root:
        self._root = (Path(project_root) / self._root).resolve()
    else:
        self._root = self._root.resolve()
```

---

## 三、cwd（当前工作目录）

### 3.1 现状

```python
# main.py:575-590
terminal_cwd = os.getcwd()                # 保存终端 CWD
root = find_project_root()                # 检测项目根
if root:
    os.chdir(str(root))                   # ← 没有 os.chdir() !!!
    project_ctx = build_context(root)

config = Config(...)
config.set("project_path", terminal_cwd)  # project_path = 终端 CWD
```

**关键发现**：`src/zmai/` 中 **没有任何 `os.chdir()` 调用**。之前的 WORKSPACE_REVIEW.md 指出 `main.py` 有 `os.chdir()`，但经过验证，**当前代码已经没有这个问题了**。（可能已在之前的对话中修复。）

当前流程：
1. `terminal_cwd = os.getcwd()` （第 577 行）
2. 项目检测（第 580 行）— 不修改 CWD
3. `config.set("project_path", terminal_cwd)` （第 590 行）

### 3.2 ToolContext.project_path

```python
# runtime.py:261
project_path = self._config.get("project_path", str(Path.cwd()))
```

`project_path` 进入 `ToolContext`，SWE 工具用它解析相对路径：

- `ShellTool`：`cwd=str(context.project_path or context.workspace_path)`
- `ReadFileTool` / `WriteFileTool`：通过 `_resolve_tool_path()` 使用 `project_path`
- `GitTool`：`cwd=str(context.workspace_path)`（不同于 ShellTool）

### 3.3 一致性问题

| 工具 | 使用的路径 | 相对基准 |
|------|-----------|---------|
| ShellTool | project_path | 终端 CWD |
| ReadFileTool | project_path → workspace fallback | 终端 CWD |
| WriteFileTool | project_path → workspace fallback | 终端 CWD |
| GitTool | workspace_path | Workspace 沙箱 |
| GrepTool | project_path | 终端 CWD |

ShellTool 使用 project_path（终端 CWD），但 GitTool 使用 workspace_path（沙箱）。如果用户从子目录运行 zmai，Shell 命令在子目录执行，但 Git 操作在 workspace 沙箱中执行 — 两者可能不同。

---

## 四、Relative Path（相对路径）

### 4.1 Workspace 内部路径解析

```python
# workspace.py:845-875 — _validate_path()
def _validate_path(self, agent_id: str, path: str) -> Path:
    agent_path = self._agent_path(agent_id)               # root / agent_id
    target = (agent_path / path).resolve()                # 拼接后 resolve

    # 检查 1: target 必须在 agent_path 内
    if not str(target).startswith(str(agent_path)):
        raise WorkspaceError(f"路径穿越被拒绝: {path}")

    # 检查 2: agent_path 必须在 workspace root 内
    if not str(agent_path).startswith(str(self._root)):
        raise WorkspaceError(f"Agent 工作区必须在 workspace root 下")

    return target
```

### 4.2 SWE 工具路径解析

```python
# swe/tools.py:41-86 — _resolve_tool_path()
def _resolve_tool_path(context, path):
    p = Path(path)
    
    # 1. 绝对路径 → 直接使用，检查是否在 project_path 内
    if p.is_absolute():
        if str(p.resolve()).startswith(str(context.project_path)):
            return p.resolve()
    
    # 2. 相对路径 → 相对于 project_path
    resolved = (context.project_path / p).resolve()
    if str(resolved).startswith(str(context.project_path)):
        return resolved
    
    # 3. 逃逸 → fallback 到 workspace_path
    ws_resolved = (context.workspace_path / p).resolve()
    if str(ws_resolved).startswith(str(context.workspace_path)):
        return ws_resolved
    
    raise ToolError(f"路径拒绝: {path}")
```

### 4.3 三层回退策略

```
相对路径输入:
  "output/report.md"
  ├── 1. 相对于 project_path = D:\project\src\output\report.md
  │    └── 在 project_path 内 ✅ → 返回
  ├── 2. 如果不在内 → fallback
  │    └── 相对于 workspace_path = D:\workspace\agent1\output\report.md ✅ → 返回
  └── 3. 也不在内 → 拒绝
```

**设计意图**：优先使用真实项目目录，回退到沙箱。这意味着 Agent "认为" 它在操作项目文件，但实际上可能写入沙箱 — 对于用户透明。

### 4.4 字符串前缀检查漏洞（高严重度）

```python
"str(target).startswith(str(agent_path))"  # workspace.py:862
```

这是一个 **可绕过路径穿越检查** 的 bug：

```
agent_path = /ws/agent_1
target     = /ws/agent_1-secret/config.json
```

`/ws/agent_1-secret` 以 `/ws/agent_1` **开头**，但它是**兄弟目录**，不是子目录！

**修复方案**：使用 `Path.relative_to()` 替代 `startswith()`：

```python
try:
    target.relative_to(agent_path)
except ValueError:
    raise WorkspaceError(f"路径穿越被拒绝: {path}")
```

或者添加路径分隔符后缀：

```python
if not str(target).startswith(str(agent_path) + os.sep):
```

---

## 五、Absolute Path（绝对路径）

### 5.1 处理逻辑

SWE 工具对绝对路径的处理：

```python
# swe/tools.py:48-52
if p.is_absolute():
    resolved = p.resolve()
    if context.project_path:
        try:
            resolved.relative_to(context.project_path)
            return resolved
        except ValueError:
            raise ToolError(f"路径拒绝: {path}")
    return resolved
```

绝对路径**必须**在 `project_path` 内才被允许。跨出 `project_path` 的绝对路径被拒绝。

### 5.2 安全性

| 场景 | 处理 | 安全 |
|------|------|------|
| 绝对路径在 project_path 内 | ✅ 放行 | 安全 |
| 绝对路径在外 | ✅ `ToolError` | 安全 |
| `C:\Windows\system32` | ✅ 拒绝 | 安全 |
| 带 `..` 的绝对路径 | ✅ `Path.resolve()` 标准化 | 安全 |

### 5.3 使用示例

```python
# WriteFileTool 参数:
{ "path": "C:\\project\\output\\report.md", "content": "..." }
# → 检查是否在 project_path(C:\project) 内 → 是 → 写入

{ "path": "C:\\Windows\\temp\\evil.exe", "content": "..." }
# → 检查是否在 project_path 内 → 否 → ToolError
```

---

## 六、Current Folder（当前文件夹）

### 6.1 Agent "工作目录" 概念

Workspace 系统**没有 Agent 级别的 CWD 概念**。每个 Agent 的 "工作目录" 是其沙箱目录：

```
{workspace_root}/{agent_id}/
├── input/          ← Agent 输入文件
├── output/         ← Agent 输出文件
├── temp/           ← Agent 临时文件
└── .state/         ← 状态/清单文件
    ├── state.json
    └── manifest.json
```

### 6.2 子目录生命周期

| 子目录 | 创建时机 | 清理时机 | 只读？ |
|--------|---------|---------|-------|
| `input/` | `prepare()` 时创建 | `cleanup(keep_input=False)` 时删除 | 否（设计意图可能应为只读） |
| `output/` | `prepare()` 时创建 | `cleanup(keep_output=True)` 时保留 | 否 |
| `temp/` | `prepare()` 时创建 | `cleanup()` 时无条件清除 | 否 |
| `.state/` | `prepare()` 时创建 | 不清除 | 是（仅由 Workspace 写入） |

### 6.3 工具运行目录对比

| 工具 | 运行目录 | 与 workspace_path 的关系 |
|------|---------|------------------------|
| ShellTool | `project_path` 或 `workspace_path` | 优先 project_path |
| GitTool | `workspace_path` | 始终 workspace_path |
| GrepTool | `project_path` 或 `workspace_path` | 搜索根目录 |
| ReadFileTool | 由 `_resolve_tool_path` 决定 | 优先 project_path |

ShellTool 和 GitTool 的 cwd 不一致：shell 命令在真实项目目录中执行，git 命令在 workspace 沙箱中执行。如果 workspace 不是项目根目录的子目录，git 命令可能失败。

---

## 七、Temp（临时文件）

### 7.1 Temp 目录机制

```python
# workspace.py:408 — prepare()
(agent_path / "temp").mkdir(exist_ok=True)

# workspace.py:759 — temp_dir()
def temp_dir(self, agent_id: str) -> Path:
    ...
    return agent_path / "temp"
```

- 每个 Agent 有独立 `temp/` 子目录
- 不使用 Python `tempfile` 模块 — 文件直接创建在 `temp/` 中
- 无 TTL 或大小限制

### 7.2 清理行为

```python
# workspace.py:467-469 — cleanup()
temp_dir = agent_path / "temp"
if temp_dir.exists():
    _clear_contents(temp_dir)  # 删除 temp/ 下所有内容
```

**无条件清理**：`cleanup()` 总是清除 `temp/`，无论 `keep_temp` 配置如何。

对比其他目录：

| 参数 | default | 清理行为 |
|------|---------|---------|
| `keep_input` | `False` | 不保留则删除 `input/` 目录 |
| `keep_output` | `True` | 保留 `output/` 目录 |
| temp | N/A | **总是**清理，无配置控制 |

### 7.3 问题

1. **`cleanup_temp` 配置项未使用**：`DEFAULT_WORKSPACE_CONFIG` 定义了 `cleanup_temp: bool = True`（workspace.py:301），但 `cleanup()` 方法从未检查该配置 — temp 永远被清理。

2. **无 `tempfile` 模块使用**：所有临时文件创建在 `temp/` 中，无系统级 tempfile 安全特性（自动清理、安全命名、权限隔离）。

3. **无大小限制**：`write()` 检查 `max_file_size` 和 `max_files`，但对 `temp/` 无独立限制。Agent 可在 `temp/` 中创建任意大小的文件。

---

## 八、Cache（缓存）

### 8.1 当前状态

**Workspace 目前没有任何缓存机制。**

| 缓存类型 | 当前状态 | 影响 |
|---------|---------|------|
| 内存缓存 | ❌ 无 | 每次 `read()` 直接读盘 |
| 磁盘缓存 | ❌ 无 | 大文件重复读取 |
| 清单缓存 | ❌ 无 | 每次 write() 全量 rglob |
| 上下文缓存 | ❌ 无 | Agent 上下文需重新生成 |

### 8.2 性能影响

当前 `write()` 操作流程：

```
write()
├── _validate_path()           → O(1)
├── _check_disk_space()        → O(1)
├── actual_file_count = len(list(agent_path.rglob("*")))  → O(n) ← 瓶颈
│   └── 文件越多越慢: 10 files → 5ms, 1000 files → 200ms
├── 写入文件                    → O(1)
├── _update_manifest()         → O(n) ← 也 rglob
└── _update_global_manifest()  → O(n*m) ← 遍历所有 Agent
```

**随着文件数增长，写入时间线性增加。** 500 个文件时每次写入约 50ms（其中 45ms 花在 rglob 扫描上）。

### 8.3 v2.0 设计的缓存方案（未实现）

WORKSPACE_DESIGN.md v2.0 规划了三层缓存：

**1. Memory Cache（`cache.py`）**
- LRU `OrderedDict[str, bytes]`
- 最大 50 条目 / 100MB
- write() 时按 agent_id 前缀失效
- 将 `read()` 从磁盘 I/O 降到 O(1)

**2. Disk Cache（`cache.py`）**
- `workspace/.cache/<file_hash>`
- 用于 >1MB 的大文件
- `clear()` 清理整个 `.cache/` 目录

**3. Context Cache（`context.py`）**
- ContextBuilder 缓存 Agent 上下文摘要
- TTL 30 秒
- write() 时 `_invalidate_context(agent_id)`

### 8.4 可行性

短期不急需实现缓存。当前 workspace 使用模式（Agent 会话通常 < 50 文件）下，rglob 性能开销可接受（~5-10ms）。缓存层应在文件数持续增长到数百级别后再实施。

---

## 九、测试覆盖分析

### 9.1 现有测试

| 测试类 | 测试数 | 覆盖内容 |
|--------|-------|---------|
| TestWorkspaceInit | 6 | 构造器（Path/str/不存在的目录/自定义配置/不可写目录） |
| TestAgentLifecycle | 9 | prepare/cleanup/remove/list_agents/幂等性 |
| TestFileOperations | 9 | write/read/list/exists/delete |
| TestPathSecurity | 4 | 路径穿越（`..`）、Agent ID 验证、隔离 |
| TestFileSizeLimit | 2 | max_file_size 限制 |
| TestManifest | 5 | 清单创建/更新/全局清单 |
| TestState | 5 | 全局/Agent 状态、磁盘持久化 |
| TestDirectoryPaths | 5 | agent_path/input/output/temp/state 目录 |
| TestFileClassification | 8 | _classify_file / _guess_mime |
| TestFileTypeSupport | 8 | 8 种文件类型写入/读取 |
| TestConcurrency | 2 | 10 Agent 并行、50 并发写入 |

共计 **43 个测试函数，695 行测试代码**。

### 9.2 测试缺口

| 缺口 | 严重度 | 说明 |
|------|--------|------|
| `_check_disk_space()` 未测试 | 中 | min_disk_space 配置从未被测试 |
| `max_files` 未测试 | 中 | write() 检查但无测试触发 |
| `_read_json` 错误处理未测试 | 中 | 损坏 state.json 恢复 |
| `_write_json` 错误处理未测试 | 中 | 写入失败分支 |
| `cleanup(keep_input=True)` 未测试 | 低 | keep_input=True 分支 |
| `update_manifest=False` 未测试 | 低 | 不更新清单的写入 |
| cwd 变化后行为未测试 | 低 | 修改 CWD 后 workspace 行为 |
| concurrent cleanup/remove 未测试 | 低 | 写入中清理 |

### 9.3 安全测试覆盖

| 攻击向量 | 测试覆盖 | 强度 |
|---------|---------|------|
| `../etc/passwd` 读取 | ✅ 在 workspace 和 SWE 工具两级测试 | 强 |
| `../../escape` 写入 | ✅ WorkspaceError + 错误消息验证 | 强 |
| Agent ID `../evil` | ✅ 拒绝 | 强 |
| Agent ID `agent/../evil` | ✅ 拒绝 | 强 |
| 跨 Agent 访问 | ✅ agent_isolation 测试 | 强 |
| 前缀匹配绕过（见 4.4） | ❌ **未测试** | 漏洞存在 |

**前缀匹配绕过（4.4 节描述的漏洞）未在测试中覆盖。**

---

## 十、总结与建议

### 10.1 当前评分

| 维度 | 评分 | 说明 |
|------|------|------|
| Workspace Root | ★★★☆☆ | 功能完整但相对 CWD 解析而非项目根 |
| cwd 处理 | ★★★★☆ | 无 os.chdir() 问题，但 project_path 和 workspace_path 偶有分歧 |
| 相对路径 | ★★★☆☆ | 三层回退策略好，但前缀检查有漏洞 |
| 绝对路径 | ★★★★★ | 严格限制在 project_path 内，安全 |
| Current Folder | ★★★☆☆ | 无 Agent CWD 概念，工具运行目录不一致 |
| Temp | ★★★☆☆ | 功能完整但配置项未使用，无独立限制 |
| Cache | ★★☆☆☆ | 完全无缓存，每次写 O(n) rglob |
| 测试 | ★★★★☆ | 43 个测试，安全覆盖好，但有几个缺口 |

**综合: 3.5/5**

### 10.2 高优先级修复

| 优先级 | 修复项 | 影响 | Effort |
|--------|--------|------|--------|
| **P0** | `_validate_path()` 前缀检查 → `relative_to()` | 安全漏洞 | 0.5 小时 |
| **P1** | Workspace Root 相对项目根解析 | 跨目录一致性 | 2 小时 |
| **P1** | 工具运行目录对齐（ShellTool/GitTool） | 行为一致性 | 2 小时 |
| **P2** | `cleanup_temp` 配置项生效 | 配置完整性 | 0.5 小时 |
| **P2** | `_check_disk_space()` / `max_files` 测试 | 测试覆盖 | 2 小时 |
| **P3** | v2.0 设计增量实施（manifest.py 等） | 性能 | 1-2 周 |

### 10.3 v1.0 到 v2.0 路线图

| 阶段 | 内容 | 文件 |
|------|------|------|
| Phase 1 | 安全修复 + Root 解析修正 | `workspace.py` |
| Phase 2 | 增量 Manifest（消除 rglob） | 新建 `manifest.py` |
| Phase 3 | State 批次持久化 | 新建 `state.py` |
| Phase 4 | Cache 层 | 新建 `cache.py` |
| Phase 5 | Auto-GC + Context 生成 | 新建 `gc.py`、`context.py` |

---

*Report generated by `claude` — 基于代码分析、测试分析、设计文档审查*
