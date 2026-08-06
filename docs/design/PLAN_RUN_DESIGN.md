# ZMAI Plan + Run 双阶段执行设计

> 目标：`zmai plan "任务"` 先分析规划 → 确认后 `zmai run` 执行
> 原则：不做 Claude Code 的复读机，贴近 ZMAI 现有架构

---

## 1. 使用流程

```
$ zmai plan "创建一个完整博客网站"

  ┌──────────────────────────────────────────────┐
  │  ZMAI Plan                                    │
  ├──────────────────────────────────────────────┤
  │                                              │
  │  任务分析                                     │
  │  主题: 博客网站                              │
  │  复杂度: 复杂 (7 步)                         │
  │                                              │
  │  执行步骤:                                    │
  │                                              │
  │   1/7 分析当前目录                            │
  │      Tool: shell_exec (dir /a)               │
  │                                              │
  │   2/7 创建项目结构                            │
  │      Tool: shell_exec (mkdir)                 │
  │                                              │
  │   3/7 创建 index.html                         │
  │      Tool: write_file                        │
  │      Deliverable: output/blog/index.html      │
  │                                              │
  │   4/7 创建 style.css                          │
  │      Tool: write_file                        │
  │      Deliverable: output/blog/style.css       │
  │                                              │
  │   5/7 创建 app.js                             │
  │      Tool: write_file                        │
  │      Deliverable: output/blog/app.js          │
  │                                              │
  │   6/7 验证文件完整性                           │
  │      Tool: read_file ×3                      │
  │      Check: 所有文件存在且内容有效             │
  │                                              │
  │   7/7 浏览器打开验证                          │
  │      Tool: open_in_browser                   │
  │      Check: 浏览器正常打开                    │
  │                                              │
  │  预计工具:                                    │
  │    shell_exec · write_file · read_file        │
  │    open_in_browser                           │
  │                                              │
  │  风险: 无                                    │
  │                                              │
  ├──────────────────────────────────────────────┤
  │  确认执行？[Y/n]  y                           │
  └──────────────────────────────────────────────┘

  开始执行...

  1/7 ✓ 分析当前目录   (shell_exec  0.3s)
  2/7 ✓ 创建项目结构   (shell_exec  0.1s)
  3/7 ✓ 创建 index.html (write_file  0.02s)
  4/7 ✓ 创建 style.css  (write_file  0.01s)
  5/7 ✓ 创建 app.js     (write_file  0.01s)
  6/7 ✓ 验证文件完整性   (read_file ×3  0.02s)
  7/7 ✓ 浏览器打开验证   (open_in_browser  0.5s)

  ✓ 任务完成 (7/7 全部成功)
```

---

## 2. 命令定义

### `zmai plan <task...>`

```
用途: 分析任务 → 生成计划 → 确认后才执行
参数: task — 自然语言任务描述
模式: 交互式（等待用户确认）或 --json（仅输出计划）
```

### `zmai run [plan.json]`

```
用途: 执行已保存的计划
参数: 可选指定计划文件，默认 ~/.zmai/sessions/latest_plan.json
模式: 静默执行，无确认环节
```

### `zmai run <task...>`

```
用途: 直接执行（跳过规划阶段）
等价于: 当前 zmai <task> 的增强版
```

---

## 3. 三种执行模式

```
zmai plan "task"    → 先计划，确认后执行
zmai run            → 执行上次保存的计划
zmai run "task"     → 直接执行（快速通道）
```

对应三个 CLI 入口：

| 命令 | 计划阶段 | 确认 | 执行阶段 | 适用场景 |
|------|---------|------|---------|---------|
| `zmai run "..."` | ❌ 跳过 | ❌ 无需 | ✅ 直接执行 | 简单任务 |
| `zmai plan "..."` | ✅ 生成并显示 | ✅ 等待 Y/n | ✅ 确认后执行 | 复杂任务 |
| `zmai run` | ❌ 读取上次 | ❌ 无需 | ✅ 执行已批准计划 | 重试/继续 |

---

## 4. 计划数据结构

### 4.1 计划文件 (`~/.zmai/sessions/latest_plan.json`)

```json
{
  "version": "1.0",
  "task": "创建一个完整博客网站",
  "created_at": "2026-07-21T10:00:00Z",
  "executed_at": null,
  "status": "approved",
  "complexity": "complex",
  "workspace": "./workspace/agent_xxx",
  "steps": [
    {
      "id": 1,
      "action": "分析当前目录结构",
      "tool": "shell_exec",
      "params": {"command": "dir /a"},
      "expected": "了解当前目录状态",
      "verify": null
    },
    {
      "id": 2,
      "action": "创建项目目录结构",
      "tool": "shell_exec",
      "params": {"command": "mkdir -p output/blog"},
      "expected": "output/blog/ 目录已创建",
      "verify": {"type": "dir_exists", "target": "output/blog"}
    },
    {
      "id": 3,
      "action": "创建 index.html",
      "tool": "write_file",
      "params": {
        "path": "output/blog/index.html",
        "content_length": 2048
      },
      "expected": "HTML 文件已写入",
      "verify": {"type": "file_exists", "target": "output/blog/index.html"}
    },
    {
      "id": 4,
      "action": "创建 style.css",
      "tool": "write_file",
      "params": {"path": "output/blog/style.css"},
      "expected": "CSS 文件已写入",
      "verify": {"type": "file_exists", "target": "output/blog/style.css"}
    },
    {
      "id": 5,
      "action": "创建 app.js",
      "tool": "write_file",
      "params": {"path": "output/blog/app.js"},
      "expected": "JS 文件已写入",
      "verify": {"type": "file_exists", "target": "output/blog/app.js"}
    },
    {
      "id": 6,
      "action": "验证所有文件内容完整性",
      "tool": "read_file",
      "params": {"path": "output/blog/index.html"},
      "expected": "HTML/CSS/JS 内容有效",
      "verify": {"type": "content_valid", "target": "output/blog/index.html"}
    },
    {
      "id": 7,
      "action": "在浏览器中打开验证",
      "tool": "open_in_browser",
      "params": {"path": "output/blog/index.html"},
      "expected": "浏览器正常打开页面",
      "verify": {"type": "browser_open", "target": "output/blog/index.html"}
    }
  ],
  "tools_required": ["shell_exec", "write_file", "read_file", "open_in_browser"],
  "risks": [],
  "deliverables": [
    {"path": "output/blog/index.html", "description": "博客首页"},
    {"path": "output/blog/style.css", "description": "样式文件"},
    {"path": "output/blog/app.js", "description": "交互脚本"}
  ]
}
```

### 4.2 执行记录 (`~/.zmai/sessions/latest_result.json`)

```json
{
  "plan_task": "创建一个完整博客网站",
  "executed_at": "2026-07-21T10:05:00Z",
  "total_steps": 7,
  "completed": 7,
  "failed": 0,
  "duration_seconds": 45,
  "steps": [
    {"id": 1, "result": "success", "duration_ms": 300, "tool": "shell_exec"},
    {"id": 2, "result": "success", "duration_ms": 100, "tool": "shell_exec"},
    {"id": 3, "result": "success", "duration_ms": 20, "tool": "write_file"},
    {"id": 4, "result": "success", "duration_ms": 10, "tool": "write_file"},
    {"id": 5, "result": "success", "duration_ms": 10, "tool": "write_file"},
    {"id": 6, "result": "success", "duration_ms": 20, "tool": "read_file"},
    {"id": 7, "result": "success", "duration_ms": 500, "tool": "open_in_browser"}
  ],
  "deliverables": [
    {"path": "output/blog/index.html", "status": "verified"},
    {"path": "output/blog/style.css", "status": "verified"},
    {"path": "output/blog/app.js", "status": "verified"}
  ]
}
```

---

## 5. Plan 生成方式

### 5.1 轻量规划 LLM 调用

`zmai plan` 不是调用完整 SWE Agent，而是**一次轻量 LLM 调用**，专用 prompt 只做规划：

```
系统: 你是 ZMAI 任务规划器。你的目标是将用户的任务拆解为可执行的步骤。
       输出 JSON，不要多余文字。

用户: 创建一个完整博客网站

输出 JSON:
{
  "steps": [
    {"id": 1, "action": "...", "tool": "...", "params": {...}, "verify": {...}},
    ...
  ]
}
```

可用工具列表内嵌在 prompt 中：

```
可用工具:
  read_file      — 读取文件（params: path, start_line?, end_line?）
  write_file     — 写入文件（params: path, content）
  edit           — 行级编辑（params: path, mode, ...）
  grep           — 搜索文本（params: pattern）
  shell_exec     — 执行命令（params: command）
  git            — Git 操作（params: args）
  show_to_user   — 展示内容（params: content）
  open_in_browser — 浏览器打开（params: path）
```

### 5.2 规则补充（无需 LLM）

Plan 返回后，CLI 层补充以下信息：

```
- 复杂度判定（步骤数 ≤ 3 → simple，否则 complex）
- 预计工具汇总（去重）
- 交付物路径整理
- 风险预判（是否存在浏览器打开、Git 等操作）
```

### 5.3 不涉及的能力分析

Plan 阶段不做 full capability gap analysis。只做：
- 任务分步
- 工具映射
- 验证条件

能力检查在 `zmai plan` 结束后、执行前做一次快速校验：检查每个步骤的工具名是否已注册。

---

## 6. CLI 改动范围

### 6.1 main.py 新增路由

```python
# 在 main() 的子命令分支中新增
if cmd == "plan":
    _run_plan(rest)
    return
if cmd == "run":
    _run_execute(rest)
    return
```

### 6.2 新增文件

```
src/zmai/cli/
├── main.py          # [修改] +2 个子命令路由
├── planner.py       # [新增] plan/run 核心逻辑
└── formatters.py    # [不改] 复用主题/输出函数
```

### 6.3 planner.py 结构

```python
def run_plan(task: str, theme: Theme, runtime: Runtime) -> dict:
    """`zmai plan` 入口：生成计划 → 显示 → 确认 → 执行"""

def run_execute(plan_path: str | None, theme: Theme, runtime: Runtime) -> dict:
    """`zmai run` 入口：加载计划 → 执行 → 报告"""

def generate_plan(task: str, backend: Backend) -> dict:
    """轻量 LLM 调用，生成结构化计划"""

def display_plan(plan: dict, theme: Theme) -> None:
    """格式化输出计划到终端"""

def confirm_execution() -> bool:
    """等待用户 Y/n 确认"""

def execute_plan(plan: dict, runtime: Runtime, agent_id: str, theme: Theme) -> dict:
    """执行已审批的计划"""
```

---

## 7. 执行流程

### 7.1 `zmai plan "task"` 完整流程

```
zmai plan "task"
  │
  ├─ 1. 轻量 LLM 调用 → 生成 JSON plan
  │     prompt: 任务分步 + 工具映射 + 验证条件
  │
  ├─ 2. 规则增强
  │     ├─ 复杂度标记
  │     ├─ 工具去重列表
  │     └─ 交付物提取
  │
  ├─ 3. 显示计划
  │     ├─ 步骤列表（编号 + 动作 + 工具）
  │     ├─ 预计工具汇总
  │     ├─ 交付物预览
  │     └─ 风险提示（如有）
  │
  ├─ 4. 快速校验
  │     ├─ 所有 tool 名是否已注册
  │     └─ 标记不可用的步骤
  │
  ├─ 5. 确认提示
  │     └─ 确认执行？[Y/n]
  │          │
  │          ├─ Y → 执行
  │          └─ n → 退出
  │
  └─ 6. 执行
        ├─ 创建临时 workspace
        ├─ 逐步骤执行（调用现有 ToolRegistry）
        ├─ 每步输出结果状态标记
        ├─ 验证步骤后检查 verify 条件
        └─ 最终报告
```

### 7.2 `zmai run` 流程

```
zmai run
  │
  ├─ 1. 读取 ~/.zmai/sessions/latest_plan.json
  │
  ├─ 2. 校验 plan 格式
  │
  ├─ 3. 执行计划（同上 6）
  │
  └─ 4. 报告
```

### 7.3 `zmai run "task"` 流程

```
zmai run "task"
  │
  ├─ 1. 直接进入当前 Runtime 执行流程
  │     （复用现有 _oneshot_run / REPL）
  │
  └─ 2. 报告
```

---

## 8. 输出格式

### 8.1 终端输出（彩色）

```
  ┌──────────────────────────────────────────────┐
  │  ZMAI Plan                                    │
  ├──────────────────────────────────────────────┤
  │                                              │
  │  1/7  分析当前目录                            │
  │       Tool: shell_exec                       │
  │                                              │
  │  2/7  创建项目结构                            │
  │       Tool: shell_exec                       │
  │                                              │
  │  ...                                         │
  │                                              │
  │  预计工具: shell_exec · write_file · ...      │
  │  交付物: output/blog/index.html               │
  │                                              │
  │  确认执行？[Y/n]                              │
  └──────────────────────────────────────────────┘

  1/7 ✓ 分析当前目录   (shell_exec  0.3s)
```

### 8.2 JSON 输出（`--json`）

```json
{
  "plan": {
    "steps": [...],
    "tools_required": ["shell_exec", "write_file"],
    "complexity": "complex"
  },
  "execution": {
    "status": "completed",
    "steps": [...]
  }
}
```

### 8.3 状态标记

```
✓  success   — 步骤成功
✗  fail      — 步骤失败
⚠  skip      — 步骤跳过
→  running   — 正在执行
⏳ pending   — 等待执行
```

---

## 9. 与现有架构的关系

### 9.1 复用的现有组件

| 组件 | 复用方式 |
|------|---------|
| `Runtime.run()` | 执行阶段调用，传入 plan 步骤 |
| `ToolRegistry` | 校验工具是否存在 |
| `ToolRouter` | 路由每一步的 tool 调用 |
| `Workspace` | 管理执行沙箱 |
| `Theme` | 彩色输出 |
| `Backend` | 轻量 LLM 调用生成 plan |
| `SESSION_DIR` | 保存 plan.json / result.json |

### 9.2 不改的文件

```
runtime/runtime.py     — 不改
agent/base.py          — 不改
tool/base.py           — 不改
tool/registry.py       — 不改
swe/agent.py           — 不改
swe/tools.py           — 不改
gateway/*              — 不改
memory/*               — 不改
```

### 9.3 改动的文件

```
src/zmai/cli/main.py       — 新增 plan/run 子命令路由（+4 行）
```

### 9.4 新增文件

```
src/zmai/cli/planner.py    — plan 生成 + 显示 + 执行逻辑
```

---

## 10. 单元测试

测试文件 `tests/test_planner.py`

```python
class TestPlanGeneration:
    def test_generate_plan_returns_steps(self, mock_backend):
        plan = generate_plan("创建 HTML 文件", mock_backend)
        assert "steps" in plan
        assert len(plan["steps"]) > 0
        for s in plan["steps"]:
            assert "id" in s
            assert "action" in s
            assert "tool" in s

    def test_generate_plan_tool_exists(self, mock_backend):
        plan = generate_plan("读取文件并打印", mock_backend)
        for s in plan["steps"]:
            assert s["tool"] in TOOL_NAMES  # 工具名必须合法

    def test_plan_includes_verify(self, mock_backend):
        plan = generate_plan("创建 HTML 并打开浏览器", mock_backend)
        verify_steps = [s for s in plan["steps"] if s.get("verify")]
        # 写文件的操作必须有验证步骤
        write_steps = [s for s in plan["steps"] if s["tool"] == "write_file"]
        if write_steps:
            assert len(verify_steps) > 0


class TestDisplayPlan:
    def test_display_includes_step_count(self, theme, capsys):
        plan = {"steps": [{"id": 1, "action": "test", "tool": "shell_exec"}]}
        display_plan(plan, theme)
        captured = capsys.readouterr()
        assert "1" in captured.out

    def test_display_includes_tool_summary(self, theme, capsys):
        plan = {
            "steps": [
                {"id": 1, "action": "a", "tool": "shell_exec"},
                {"id": 2, "action": "b", "tool": "write_file"},
            ],
            "tools_required": ["shell_exec", "write_file"],
        }
        display_plan(plan, theme)
        captured = capsys.readouterr()
        assert "shell_exec" in captured.out
        assert "write_file" in captured.out


class TestConfirmExecution:
    def test_confirm_yes(self, monkeypatch):
        monkeypatch.setattr("sys.stdin.readline", lambda: "y\n")
        assert confirm_execution() is True

    def test_confirm_no(self, monkeypatch):
        monkeypatch.setattr("sys.stdin.readline", lambda: "n\n")
        assert confirm_execution() is False

    def test_confirm_default_yes(self, monkeypatch):
        monkeypatch.setattr("sys.stdin.readline", lambda: "\n")
        assert confirm_execution() is True


class TestExecutePlan:
    def test_execute_all_steps_success(self, runtime, plan, theme):
        result = execute_plan(plan, runtime, "test_agent", theme)
        assert result["status"] == "completed"
        assert result["completed"] == len(plan["steps"])

    def test_execute_with_failure(self, runtime, plan_with_fail, theme):
        result = execute_plan(plan_with_fail, runtime, "test_agent", theme)
        assert result["failed"] >= 1

    def test_execute_saves_result(self, runtime, plan, theme, tmp_path):
        execute_plan(plan, runtime, "test_agent", theme)
        result_file = tmp_path / "latest_result.json"
        assert result_file.exists()


class TestCLIIntegration:
    def test_plan_command_runs(self, runner):
        result = runner.invoke(main, ["plan", "创建 HTML 文件"])
        assert result.exit_code == 0

    def test_run_command_with_task(self, runner):
        result = runner.invoke(main, ["run", "简单任务"])
        assert result.exit_code == 0

    def test_run_command_without_task_loads_saved(self, runner):
        # 先保存一个 plan
        runner.invoke(main, ["plan", "test", "--json"])
        result = runner.invoke(main, ["run"])
        assert result.exit_code == 0
```

---

## 11. `zmai plan` vs 现有 `zmai "task"` 对比

| 维度 | `zmai "task"` (现有) | `zmai plan "task"` | `zmai run "task"` |
|------|-------------------|-------------------|-------------------|
| 规划 | 无 | ✅ 显式规划 | 无 |
| 确认 | 无 | ✅ 用户确认 | 无 |
| 步骤展示 | 无 | ✅ 执行前可见 | 无 |
| 验证 | ❌ 靠 LLM 自觉 | ✅ 每步预置验证 | ❌ 靠 LLM 自觉 |
| 适用 | 简单任务 | 复杂/不确定任务 | 已知明确的简单任务 |
| 额外耗时 | 0 | +1 次轻量 LLM 调用 | 0 |

---

## 12. 实现顺序

```
Phase 1: planner.py + CLI 路由
  ├─ generate_plan() — 轻量 LLM 调用
  ├─ display_plan()  — 格式化输出
  ├─ confirm_execution() — Y/n 交互
  └─ main.py 路由（plan/run + 3 行代码）

Phase 2: execute_plan()
  ├─ 逐步骤调用 ToolRegistry.execute()
  ├─ 实时输出状态标记
  ├─ verify 检查
  └─ 最终报告

Phase 3: zmai run（无参数模式）
  ├─ 读取 latest_plan.json
  ├─ 校验 → 执行
  └─ 保存 latest_result.json
```

---

> **对比 Claude Code 的区别**
>
> Claude Code 的 plan 是内部多轮推理，用户看不到完整步骤树。
> ZMAI 的 `zmai plan` 输出**扁平的步骤列表**，每步标明工具 + 验证条件，用户确认后才执行。
> 执行时每步实时输出状态标记，执行后保存完整记录。
> 核心差异：**用户始终知道 Agent 将做什么，且每步结果可审计。**
