# ZMAI Task Progress Design v1.0

Version: 1.0
Date: 2026-07-16

> **每一步都有输出。** 六阶段进度系统：Analyzing → Planning → Executing → Testing → Repairing → Reporting。
>
> 不修改 Runtime / Agent / Gateway / Memory / Workspace / Workflow 模块。
>
> 仅修改 CLI 层的进度渲染和 Agent 层的阶段上报。

---

## 目录

1. [现状审查](#1-现状审查)
2. [设计原则](#2-设计原则)
3. [六阶段模型](#3-六阶段模型)
4. [架构设计](#4-架构设计)
5. [阶段进度条](#5-阶段进度条)
6. [工具执行流](#6-工具执行流)
7. [阶段转换动画](#7-阶段转换动画)
8. [完成报告](#8-完成报告)
9. [无输出保护](#9-无输出保护)
10. [JSON 模式](#10-json-模式)
11. [文件清单与实现计划](#11-文件清单与实现计划)

---

## 1. 现状审查

### 1.1 当前实现

```python
# main.py:200-213 (当前进度回调)
def on_progress(typ, msg):
    if typ == "token":
        sys.stderr.write(msg)                        # 原始 token 流
    elif typ == "tool":
        sys.stderr.write(f"\n  > {msg}")             # 工具名
    elif typ == "result":
        ok = msg.startswith("OK:")
        sys.stderr.write(f"\n    {'ok' if ok else 'fail'}")  # 工具结果
```

```python
# swe/agent.py:184-197 (当前进度触发)
for tc in response.tool_calls:
    on_progress("tool", tc.name)                      # 发送工具名
    result = context.tools.execute(tc.name, ...)
    tag = "OK" if result.success else "FAIL"
    on_progress("result", f"{tag}: {brief}")          # 发送结果
```

### 1.2 问题

| 问题 | 示例 | 影响 |
|------|------|------|
| **无阶段概念** | 用户看到 `ReadFileTool` 但不知道 Agent 在"分析"还是"执行" | 无法判断任务进度 |
| **工具名内部化** | `ReadFileTool` 而非 `📂 读取文件` | 用户需了解内部实现 |
| **长时间无输出** | Agent 调用 API 等待响应时无反馈 | 用户以为程序卡死 |
| **无时间信息** | 不知道每个步骤花了多久 | 无法估算剩余时间 |
| **无进度比例** | 不知道整体进度 | 不确定还要多久 |
| **无完成报告** | 仅返回 status=completed | 不知道 Agent 做了什么 |

### 1.3 根因

进度系统只传递了"正在调什么工具"，没有传递"正在做什么阶段"和"整体进度如何"。

```
当前:    工具名 → 工具结果 → 工具名 → 工具结果 ...
需要:    阶段 → 步骤 → 进度 → 工具名 → 结果 → 时间
```

---

## 2. 设计原则

### 2.1 六阶段全覆盖

每个任务必然经历：

```
Analyzing     理解任务 → 阅读代码 → 定位问题
Planning      设计方案 → 确定修改范围
Executing     修改代码 → 写入文件
Testing       运行测试 → 验证修改
Repairing     修复失败 → 重试
Reporting     展示结果 → 交付
```

### 2.2 每 3 秒必有输出

```
没有任何操作应该让用户等待超过 3 秒无输出。

Agent 调用 API 等待时 → 显示 "🤔 正在思考..."
工具执行超过 3 秒    → 显示 "⏳ 仍在运行..."
阶段转换            → 显示阶段名和进度
```

### 2.3 进度可衡量

```
每个阶段有进度百分比：
  Analyzing  ████████░░  80%  (4/5 文件已读)
  Planning   ██░░░░░░░░  20%  (正在设计方案)
  Executing  ██████████ 100%  (2/2 文件已修改)

整体进度（Estimator 预测）:
  总体  █████░░░░░  52%  (阶段 2/6, 预计剩余 45s)
```

### 2.4 不修改下游

```
仅修改:
  src/zmai/cli/main.py        ← 替换 on_progress 回调
  src/zmai/cli/progress.py    ← 🔴 新增 — 进度渲染
  src/zmai/swe/agent.py       ← 🔧 少量修改 — 阶段上报

不修改:
  src/zmai/runtime/runtime.py   ✅
  src/zmai/gateway/*            ✅
  src/zmai/agent/base.py        ✅
  src/zmai/workspace/*          ✅
  src/zmai/memory/*             ✅
  src/zmai/workflow/*           ✅
```

---

## 3. 六阶段模型

### 3.1 阶段定义

```python
from enum import Enum

class Phase(str, Enum):
    """任务六阶段。"""

    ANALYZING  = "analyzing"   # 理解任务，阅读代码
    PLANNING   = "planning"    # 设计方案，确定修改
    EXECUTING  = "executing"   # 修改代码，写入文件
    TESTING    = "testing"     # 运行测试，验证修改
    REPAIRING  = "repairing"   # 修复失败，重试
    REPORTING  = "reporting"   # 展示结果，交付
    COMPLETED  = "completed"   # 任务完成

    @property
    def display_name(self) -> str:
        return {
            "analyzing":  "🔍 分析",
            "planning":   "📋 规划",
            "executing":  "🔧 执行",
            "testing":    "🧪 测试",
            "repairing":  "🔨 修复",
            "reporting":  "📊 报告",
            "completed":  "✅ 完成",
        }[self.value]

    @property
    def icon(self) -> str:
        return {
            "analyzing":  "🔍",
            "planning":   "📋",
            "executing":  "🔧",
            "testing":    "🧪",
            "repairing":  "🔨",
            "reporting":  "📊",
            "completed":  "✅",
        }[self.value]
```

### 3.2 阶段判断逻辑

阶段由 Agent 的当前行为自动判断，Agent 不需要显式声明阶段。

```python
class PhaseDetector:
    """根据工具调用自动推断当前阶段。"""

    @staticmethod
    def detect(tool_name: str, tool_result: str | None = None,
               previous_phase: Phase | None = None) -> Phase:
        """根据工具类型和上下文推断阶段。"""

        # 读取/搜索 → Analyzing
        if tool_name in ("ReadFileTool", "GrepTool"):
            if previous_phase in (None, Phase.ANALYZING, Phase.REPAIRING):
                return Phase.ANALYZING
            return Phase.REPAIRING  # 修复阶段重新读代码

        # 写入/编辑 → Executing
        if tool_name in ("WriteFileTool", "EditTool"):
            return Phase.EXECUTING

        # Shell 命令 → 根据内容判断
        if tool_name == "ShellTool":
            if tool_result and ("pytest" in tool_result or "test" in tool_result.lower()):
                return Phase.TESTING
            return Phase.EXECUTING

        # Git → Executing
        if tool_name == "GitTool":
            return Phase.EXECUTING

        # 展示 → Reporting
        if tool_name in ("ShowToUserTool", "OpenInBrowserTool"):
            return Phase.REPORTING

        # 默认：保持当前阶段
        return previous_phase or Phase.EXECUTING
```

### 3.3 阶段转换

```
Agent 开始任务
    │
    ▼
┌────────────┐
│ Analyzing  │ ← ReadFileTool, GrepTool
└─────┬──────┘
      │ 找到关键文件
      ▼
┌────────────┐
│ Planning   │ ← Agent 内部推理（通过 "-- 设计方案 --" 等文本检测）
└─────┬──────┘
      │ 方案确定
      ▼
┌────────────┐
│ Executing  │ ← WriteFileTool, EditTool, ShellTool
└─────┬──────┘
      │ 修改完成
      ▼
┌────────────┐
│ Testing    │ ← ShellTool("pytest ..."), ShellTool("cargo test ...")
└─────┬──────┘
      │ 测试失败
      ▼
┌────────────┐
│ Repairing  │ ← 回到 Analyzing（读取修改后的文件）
└─────┬──────┘
      │ 修复完成
      ▼
┌────────────┐
│ Testing    │ ← 重新测试
└─────┬──────┘
      │ 测试通过
      ▼
┌────────────┐
│ Reporting  │ ← ShowToUserTool, OpenInBrowserTool
└─────┬──────┘
      │ 展示完成
      ▼
┌────────────┐
│ Completed  │ ← 任务结束
└────────────┘
```

---

## 4. 架构设计

### 4.1 数据流

```
Agent
  │
  ├── Before API call:  on_phase("analyzing", "阅读代码...")
  │                      ↕
  ├── After API response:
  │   ├── on_tool("read_file", "src/auth.py")
  │   ├── on_phase("executing", "修改文件...")
  │   └── on_result("OK", "文件已修改")
  │                      ↕
  └── Task complete:    on_complete(result)
                         ↕
                    ProgressRenderer
                      ├── stderr: 实时进度
                      ├── stdout: 最终报告
                      └── JSON:   结构化输出
```

### 4.2 回调接口

```python
# cli/progress.py — 进度回调接口

@dataclass
class ProgressCallbacks:
    """进度回调集合。CLI 层实现，Agent 层调用。"""

    on_phase: Callable[[Phase, str], None]
    """阶段变更。phase: 当前阶段，description: 阶段描述。"""

    on_tool_start: Callable[[str, str], None]
    """工具开始执行。tool_name: 友好名称，detail: 详情（如文件名）。"""

    on_tool_end: Callable[[str, bool, float], None]
    """工具执行结束。tool_name: 友好名称，success: 是否成功，elapsed: 耗时秒。"""

    on_token: Callable[[str], None]
    """Token 流（原始 LLM 输出，可选）。"""

    on_progress: Callable[[float, str], None]
    """进度更新。pct: 0-100，status: 状态描述。"""

    on_complete: Callable[[dict], None]
    """任务完成。result: 完整结果。"""
```

### 4.3 Agent 层集成

```python
# swe/agent.py — 最小修改

class SWEAgent(Agent):
    async def step(self, context):
        progress = context.metadata.get("progress")
        messages = context.metadata.get("messages", [])

        if not messages:
            messages = [{"role": "user", "content": context.task}]

        tool_defs = context.tools.definitions() if context.tools else []
        request = BackendRequest(messages=messages, tools=tool_defs or None, ...)

        # 阶段: 思考中（LLM API 调用前）
        if progress and progress.on_phase:
            progress.on_phase(Phase.ANALYZING, "正在思考任务...")

        try:
            response = context.backend.invoke(request)
        except Exception as e:
            return AgentAction.fail(str(e))

        if response.tool_calls:
            for tc in response.tool_calls:
                # 根据工具自动推断阶段
                phase = PhaseDetector.detect(tc.name)
                if progress and progress.on_phase:
                    progress.on_phase(phase, tc.name)

                # 工具开始
                friendly_name = TOOL_DISPLAY_NAMES.get(tc.name, tc.name)
                detail = _extract_tool_detail(tc)
                if progress and progress.on_tool_start:
                    progress.on_tool_start(friendly_name, detail)

                # 执行
                start = time.time()
                try:
                    result = context.tools.execute(tc.name, tc.params, tctx)
                except Exception as e:
                    result = ToolResult(success=False, error=str(e))
                elapsed = time.time() - start

                # 工具结束
                if progress and progress.on_tool_end:
                    friendly = TOOL_DISPLAY_NAMES.get(tc.name, tc.name)
                    progress.on_tool_end(friendly, result.success, elapsed)

                # 进度估算
                if progress and progress.on_progress:
                    progress.on_progress(50 + len(messages) * 5, tc.name)

                messages.append({
                    "role": "user",
                    "content": f"[工具 {tc.name} 结果]\n{'OK' if result.success else 'FAIL'}: ...",
                })
            
            context.metadata["messages"] = messages
            return AgentAction.cont(...)

        # 阶段: 报告
        if progress and progress.on_phase:
            progress.on_phase(Phase.REPORTING, "生成结果...")
        
        context.metadata["messages"] = messages
        return AgentAction.complete(output=response.content or "")
```

只有两个修改点：
1. `step()` 中：工具执行前后调用 `progress.on_tool_start` / `on_tool_end`
2. `step()` 中：根据工具类型调用 `progress.on_phase` 更新阶段

### 4.4 CLI 层集成

```python
# cli/main.py — 替换 on_progress

class CLIRenderer:
    """CLI 进度渲染器。"""

    def __init__(self, theme: Theme, json_mode: bool = False):
        self._theme = theme
        self._json_mode = json_mode
        self._phase_tracker = PhaseTracker()
        self._timeline: list[StepRecord] = []

    @property
    def callbacks(self) -> ProgressCallbacks:
        """返回 CLI 实现的回调集。"""
        return ProgressCallbacks(
            on_phase=self._on_phase,
            on_tool_start=self._on_tool_start,
            on_tool_end=self._on_tool_end,
            on_token=self._on_token,
            on_progress=self._on_progress,
            on_complete=self._on_complete,
        )

    def _on_phase(self, phase: Phase, description: str):
        if self._json_mode:
            return
        self._phase_tracker.transition_to(phase)
        self._phase_tracker.render()

    def _on_tool_start(self, tool_name: str, detail: str):
        if self._json_mode:
            return
        self._phase_tracker.render_tool_start(tool_name, detail)

    def _on_tool_end(self, tool_name: str, success: bool, elapsed: float):
        if self._json_mode:
            return
        self._phase_tracker.render_tool_end(tool_name, success, elapsed)
        self._timeline.append(StepRecord(
            tool=tool_name, success=success, elapsed=elapsed,
        ))
```

---

## 5. 阶段进度条

### 5.1 进度条渲染

```python
class PhaseBar:
    """阶段进度条。显示当前阶段和进度。"""

    WIDTH = 36

    def __init__(self):
        self._current_phase: Phase | None = None
        self._phase_steps: dict[str, tuple[int, int]] = {}  # phase → (done, total)

    def set_phase(self, phase: Phase) -> None:
        if phase != self._current_phase:
            self._current_phase = phase
            if phase.value not in self._phase_steps:
                self._phase_steps[phase.value] = (0, 0)

    def record_step(self, phase: Phase, success: bool) -> None:
        done, total = self._phase_steps.get(phase.value, (0, 0))
        self._phase_steps[phase.value] = (done + 1, total + 1)

    def render(self) -> str:
        """渲染整体进度条。"""
        phases = ["analyzing", "planning", "executing", "testing", "repairing", "reporting"]
        
        # 计算整体进度
        total_steps = sum(t for _, t in self._phase_steps.values())
        done_steps = sum(d for d, _ in self._phase_steps.values())
        overall_pct = min(int(done_steps / max(total_steps, 1) * 100), 100)

        # 渲染阶段性进度条
        filled = "━" * int(overall_pct / 100 * self.WIDTH)
        empty = "─" * (self.WIDTH - len(filled))
        
        # 当前阶段名
        current = self._current_phase.display_name if self._current_phase else "🚀 启动"
        
        return f"\r  {current} {filled}{empty} {overall_pct}%"
```

### 5.2 显示效果

```
  🔍 分析 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 100%
  📋 规划 ━━━━━━━━━━━━━━━━━━━━░░░░░░░░░░░░░░░░  55%
  🔧 执行 ━━━━━━━━━░░░░░░░░░░░░░░░░░░░░░░░░░░░  28%
  🧪 测试 ━░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░   3%
  🔨 修复 ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░   0%
  📊 报告 ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░   0%
  ─────────────────────────────────────────────
  总体  ████████░░░░░░░░░░░░░░░░░░░░░░░░  52%
```

### 5.3 紧凑模式

```
  🔍 分析 ████████████████████████████████████ 100%
  📋 规划 ████████████░░░░░░░░░░░░░░░░░░░░░░░  37%
  🔧 执行 █████░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  14%
  总体:  🔧 执行中 · 5/12 步 · ⏱ 34s
```

---

## 6. 工具执行流

### 6.1 工具友好名称

```python
TOOL_DISPLAY_NAMES = {
    "ReadFileTool":       "📂 读取文件",
    "WriteFileTool":      "✏️ 写入文件",
    "EditTool":           "📝 编辑文件",
    "GrepTool":           "🔍 搜索代码",
    "ShellTool":          "🖥️ 执行命令",
    "GitTool":            "🔀 Git 操作",
    "ShowToUserTool":     "📋 显示结果",
    "OpenInBrowserTool":  "🌐 打开浏览器",
}
```

### 6.2 工具执行行

```
  📂 读取文件  src/auth/login.py         (0.2s)
  🔍 搜索代码  "def login" in src/**/*.py  (0.3s)
  ✏️ 写入文件  src/auth/login.py         (0.1s)
  🖥️ 执行命令  pytest tests/ -x           (12.5s)
  ✅ 测试通过  42 passed, 0 failed
```

### 6.3 渲染代码

```python
class ToolFlowRenderer:
    """工具执行流的实时渲染。"""

    SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def __init__(self):
        self._spinner_idx = 0
        self._line_count = 0

    def on_tool_start(self, tool_name: str, detail: str) -> None:
        """工具开始执行。显示 spinner + 名称。"""
        spinner = self.SPINNER[self._spinner_idx % len(self.SPINNER)]
        self._spinner_idx += 1
        
        # 截取 detail 到合理长度
        detail = detail[:80] if len(detail) > 80 else detail
        sys.stderr.write(f"\r  {spinner} {tool_name}  {detail}\033[K")

    def on_tool_end(self, tool_name: str, success: bool, elapsed: float) -> None:
        """工具执行结束。显示结果和时间。"""
        icon = "✅" if success else "❌"
        elapsed_str = f"{elapsed:.1f}s" if elapsed < 60 else f"{elapsed/60:.0f}m{elapsed%60:.0f}s"
        sys.stderr.write(f"\r  {icon} {tool_name}  {elapsed_str}\n")
```

### 6.4 显示示例

```
  🔍 分析阶段 ──────────────────────────────────
  📂 读取文件  README.md                          (0.1s)
  📂 读取文件  ARCHITECTURE.md                    (0.1s)
  🔍 搜索代码  "bottleneck" in src/**/*.py          (0.3s)

  📋 规划阶段 ──────────────────────────────────
  🤔 正在设计方案...

  🔧 执行阶段 ──────────────────────────────────
  ✏️ 写入文件  src/core/processor.py              (0.1s)
  ✏️ 写入文件  src/core/optimizer.py              (0.1s)

  🧪 测试阶段 ──────────────────────────────────
  🖥️ 执行命令  pytest tests/ -x                   (12.5s)
  ✅ 测试通过  42 passed

  📊 报告阶段 ──────────────────────────────────
  📋 显示结果  优化完成，性能提升 35%
```

---

## 7. 阶段转换动画

### 7.1 阶段转换提示

```python
PHASE_TRANSITIONS = {
    Phase.ANALYZING:  "🔍 正在分析任务...",
    Phase.PLANNING:   "📋 正在设计方案...",
    Phase.EXECUTING:  "🔧 正在执行修改...",
    Phase.TESTING:    "🧪 正在运行测试...",
    Phase.REPAIRING:  "🔨 正在修复问题...",
    Phase.REPORTING:  "📊 正在生成报告...",
    Phase.COMPLETED:  "✅ 任务完成",
}
```

### 7.2 转换动画

```python
class PhaseTransition:
    """阶段转换动画。"""

    def __init__(self, theme: Theme):
        self._theme = theme

    def show(self, from_phase: Phase | None, to_phase: Phase) -> None:
        """显示阶段转换。"""
        if from_phase == to_phase:
            return

        # 空行分隔
        sys.stderr.write("\n")
        
        # 分隔线 + 新阶段名
        name = to_phase.display_name
        sys.stderr.write(f"  {self._theme.dim('─' * 40)}\n")
        sys.stderr.write(f"  {name}\n")
        sys.stderr.write(f"  {self._theme.dim('─' * 40)}\n")
```

### 7.3 等待动画

```python
class WaitingAnimation:
    """长时间操作等待动画。3 秒无输出时触发。"""

    INTERVAL = 3.0  # 秒

    def __init__(self):
        self._last_output = time.time()
        self._waiting = False

    def tick(self) -> None:
        """每次输出时调用。"""
        self._last_output = time.time()
        if self._waiting:
            self._waiting = False
            sys.stderr.write("\033[K")  # 清除等待消息

    def check(self) -> None:
        """检查是否需要显示等待消息。"""
        if self._waiting:
            return
        elapsed = time.time() - self._last_output
        if elapsed > self.INTERVAL and not self._waiting:
            self._waiting = True
            dots = "." * (int(elapsed / 3) % 4)
            sys.stderr.write(f"\r  ⏳ 仍在处理{dots}\033[K")
```

---

## 8. 完成报告

### 8.1 最终报告

任务完成后显示：

```
  ✅ 任务完成
  ─────────────────────────────────────────────

  📊 执行报告
  ─────────────────────────────────────────────
  任务: 重构 auth 模块
  状态: ✅ 完成 (6 步 · 128s)
  模型: deepseek-chat · 15.2k tokens

  步骤:
  [1] 📂 读取文件  src/auth/login.py       0.2s
  [2] 🔍 搜索代码  "def login"             0.3s
  [3] ✏️ 写入文件  src/auth/login.py       0.1s
  [4] 🖥️ 执行命令  pytest tests/ -x        45.2s
  [5] 🔍 搜索代码  "token refresh"         0.3s
  [6] ✏️ 写入文件  src/auth/token.py       0.1s
  [7] 🖥️ 执行命令  pytest tests/ -x        12.5s
  ─────────────────────────────────────────────
  结论: token 刷新逻辑已修复，测试通过
```

### 8.2 报告数据

```python
@dataclass
class TaskReport:
    """任务执行报告。"""

    # 基本信息
    task: str
    status: str                    # completed / failed / cancelled
    total_steps: int
    total_time: float              # 秒
    model: str = ""

    # Token 统计
    input_tokens: int = 0
    output_tokens: int = 0

    # 步骤明细
    steps: list[StepRecord] = field(default_factory=list)

    # 文件变更
    files_read: list[str] = field(default_factory=list)
    files_modified: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "status": self.status,
            "total_steps": self.total_steps,
            "total_time": f"{self.total_time:.1f}s",
            "model": self.model,
            "tokens": {"input": self.input_tokens, "output": self.output_tokens},
            "steps": [s.to_dict() for s in self.steps],
            "files_read": self.files_read,
            "files_modified": self.files_modified,
        }
```

### 8.3 JSON 模式

```json
{
  "task": "重构 auth 模块",
  "status": "completed",
  "total_steps": 7,
  "total_time": "128.0s",
  "model": "deepseek-chat",
  "tokens": { "input": 12500, "output": 2700 },
  "phases": {
    "analyzing": { "steps": 2, "time": "0.5s" },
    "executing": { "steps": 2, "time": "0.2s" },
    "testing":   { "steps": 2, "time": "57.7s" },
    "repairing": { "steps": 1, "time": "0.3s" }
  },
  "steps": [
    { "tool": "read_file", "detail": "src/auth/login.py", "success": true, "elapsed": 0.2 },
    { "tool": "grep", "detail": "def login", "success": true, "elapsed": 0.3 }
  ],
  "files_read": ["src/auth/login.py", "src/auth/token.py"],
  "files_modified": ["src/auth/login.py", "src/auth/token.py"]
}
```

---

## 9. 无输出保护

### 9.1 保护级别

| 级别 | 触发条件 | 显示内容 |
|------|---------|---------|
| L1 | 3 秒无输出 | `⏳ 正在处理...` |
| L2 | 10 秒无输出 | `⏳ Agent 仍在思考 (10s)...` |
| L3 | 30 秒无输出 | `⏳ API 调用中 (30s)...` + 当前阶段 |
| L4 | 60 秒无输出 | `⚠ Agent 已静默 60 秒，仍在等待 API 响应` |

### 9.2 实现

```python
class SilenceGuard:
    """无输出保护。确保用户不会看到空白终端。"""

    def __init__(self):
        self._last_output = time.time()
        self._last_level = 0
        self._running = False
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """启动监控线程。"""
        self._running = True
        self._thread = threading.Thread(target=self._monitor, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """停止监控。"""
        self._running = False

    def ping(self) -> None:
        """标记有输出。"""
        self._last_output = time.time()
        self._last_level = 0

    def _monitor(self) -> None:
        """后台监控线程。"""
        while self._running:
            elapsed = time.time() - self._last_output
            level = self._detect_level(elapsed)
            if level > self._last_level:
                self._show_warning(level, elapsed)
                self._last_level = level
            time.sleep(1)

    @staticmethod
    def _detect_level(elapsed: float) -> int:
        if elapsed > 60:
            return 4
        if elapsed > 30:
            return 3
        if elapsed > 10:
            return 2
        if elapsed > 3:
            return 1
        return 0

    @staticmethod
    def _show_warning(level: int, elapsed: float) -> None:
        messages = {
            1: f"  ⏳ 正在处理...",
            2: f"  ⏳ Agent 仍在思考 ({elapsed:.0f}s)...",
            3: f"  ⏳ API 调用中 ({elapsed:.0f}s)...",
            4: f"  ⚠ 已静默 {elapsed:.0f}s，仍在等待 API 响应",
        }
        msg = messages.get(level, "")
        if msg:
            sys.stderr.write(f"\r{msg}\033[K")
```

### 9.3 Token 流回退

当 LLM 支持流式输出时，用 token 流替代等待动画：

```python
def _on_token(self, text: str) -> None:
    """Token 流输出。实时显示 LLM 思考过程。"""
    self._silence.ping()
    if not self._token_mode:
        sys.stderr.write(f"\r  🤔 ")
        self._token_mode = True
    # 限速显示（太多 token 会刷屏）
    self._token_buffer += text
    if len(self._token_buffer) > 80 or text.endswith("\n"):
        sys.stderr.write(f"\r  🤔 {self._token_buffer[:80]}")
        self._token_buffer = ""
```

---

## 10. JSON 模式

### 10.1 结构化进度事件

当 `--json` 标志启用时，进度事件以 JSON Lines 格式输出到 stderr：

```
{"type":"phase","phase":"analyzing","description":"阅读代码..."}
{"type":"tool_start","tool":"read_file","detail":"src/auth/login.py"}
{"type":"tool_end","tool":"read_file","success":true,"elapsed":0.2}
{"type":"progress","pct":25,"status":"4/12 steps"}
{"type":"phase","phase":"executing","description":"修改文件..."}
{"type":"complete","status":"completed","total_steps":7,"total_time":128.0}
```

### 10.2 JSON 渲染器

```python
class JSONProgressRenderer:
    """JSON Lines 进度渲染器。"""

    def __init__(self, file=sys.stderr):
        self._file = file

    def on_phase(self, phase: Phase, description: str) -> None:
        self._emit({
            "type": "phase",
            "phase": phase.value,
            "description": description,
            "timestamp": _now_iso(),
        })

    def on_tool_start(self, tool_name: str, detail: str) -> None:
        self._emit({
            "type": "tool_start",
            "tool": tool_name,
            "detail": detail,
            "timestamp": _now_iso(),
        })

    def on_tool_end(self, tool_name: str, success: bool, elapsed: float) -> None:
        self._emit({
            "type": "tool_end",
            "tool": tool_name,
            "success": success,
            "elapsed": round(elapsed, 2),
            "timestamp": _now_iso(),
        })

    def on_complete(self, result: dict) -> None:
        self._emit({
            "type": "complete",
            "status": result.get("status"),
            "total_steps": result.get("steps"),
            "timestamp": _now_iso(),
        })

    def _emit(self, data: dict) -> None:
        json.dump(data, self._file, ensure_ascii=False)
        self._file.write("\n")
        self._file.flush()
```

---

## 11. 文件清单与实现计划

### 11.1 新增文件

```
src/zmai/cli/
└── progress.py             # 🔴 新增 — 进度系统（~350 行）
    ├── Phase               # 六阶段枚举
    ├── PhaseDetector       # 自动阶段推断
    ├── PhaseTracker        # 阶段追踪 + 进度条
    ├── ToolFlowRenderer    # 工具执行流渲染
    ├── PhaseTransition     # 阶段转换动画
    ├── WaitingAnimation    # 等待动画（3s/10s/30s/60s）
    ├── SilenceGuard        # 无输出保护线程
    ├── TaskReport          # 完成报告
    ├── CLIRenderer         # CLI 进度渲染器（集成所有）
    └── JSONProgressRenderer # JSON Lines 渲染器
```

### 11.2 修改文件

```
src/zmai/cli/main.py        # 🔧 修改 — 使用 CLIRenderer 替代简单回调
src/zmai/swe/agent.py       # 🔧 修改 — 添加阶段上报 + 工具计时
```

### 11.3 修改量评估

```
src/zmai/cli/main.py:
  - 替换 on_progress 回调（~14 行 → ~30 行）
  - 新增 CLIRenderer 创建

src/zmai/swe/agent.py:
  - step() 中添加 before/after 工具钩子（~15 行新增）
  - 引入 PhaseDetector（~5 行）
  - 接口不变，仅扩展回调
  - 不修改任何业务逻辑

新增:
  cli/progress.py           ~350 行
```

### 11.4 不变文件

```
src/zmai/runtime/runtime.py   ✅ 不变
src/zmai/gateway/*            ✅ 不变
src/zmai/workspace/*          ✅ 不变
src/zmai/memory/*             ✅ 不变
src/zmai/workflow/*           ✅ 不变
src/zmai/config/*             ✅ 不变
src/zmai/auth/*               ✅ 不变
src/zmai/agent/base.py        ✅ 不变
src/zmai/cli/formatters.py    ✅ 不变
```

### 11.5 实现优先级

```
P0 — 基础进度（1 天）
├── Phase 枚举 + PhaseDetector 自动推断
├── CLIRenderer 基础版（阶段名 + 工具名 + 结果）
├── main.py 集成替换
└── swe/agent.py 工具钩子

P1 — 可视化（1 天）
├── PhaseBar 进度条（整体 + 阶段）
├── ToolFlowRenderer 工具流（icon + 时间）
├── PhaseTransition 阶段转换动画
└── TaskReport 完成报告

P2 — 保护（0.5 天）
├── SilenceGuard 无输出保护线程
├── WaitingAnimation 等待动画
├── Token 流显示
└── JSONProgressRenderer
```

### 11.6 三秒无输出保证

```
                    Agent step 开始
                         │
                    ┌────┴────┐
              ┌─────┤ API 调用 │
              │     └────┬────┘
              │          │ < 3s
              │          ▼
              │     on_phase("thinking")  ← 保证 ≤ 3s 有输出
              │          │
              │          ▼
              │    API 返回
              │          │
              │    ┌─────┴─────┐
              │    │ 有工具调用？│
              │    └─────┬─────┘
              │    ┌─────┴──────┐
              │    │            │
              │    ▼            ▼
              │  on_tool_    on_phase(
              │  start()     "reporting")
              │    │            │
              │    ▼            ▼
              │  execute()   complete
              │    │
              │    ▼
              │  on_tool_end()  ← 每个工具执行完立即输出
              │
              └── 循环到步骤结束

          没有路径能让用户等待超过 3 秒无输出。
```

---

> **总结：**
>
> ZMAI Task Progress v1.0 将用户看到的从内部工具名流水账升级为结构化六阶段进度系统：
>
> 1. **六阶段模型** — Analyzing → Planning → Executing → Testing → Repairing → Reporting，每个阶段有图标 + 名称
> 2. **实时反馈** — 每步工具执行显示友好名称、操作对象、耗时、结果
> 3. **进度条** — 阶段进度 + 总体进度，用户知道"做到哪了"和"还剩多少"
> 4. **无输出保护** — 3/10/30/60 秒四级警报，确保不会出现空白终端
> 5. **阶段转换** — 分隔线 + 阶段名，清晰展示任务推进
> 6. **完成报告** — 结构化报告含步骤明细、时间消耗、文件变更
> 7. **JSON 模式** — 所有进度事件以 JSON Lines 输出，管道友好
>
> **核心指标：任何操作路径中，用户都不会看到超过 3 秒的无输出间隔。**
