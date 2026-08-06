# ZMAI Terminal UI Design v1.0

Version: 1.0
Date: 2026-07-16

> **彩色终端 UI：启动 Dashboard + 状态指示器 + 动画 + 进度。**
>
> 不修改 Runtime / Agent / Gateway / Memory / Workspace / Workflow 模块。
>
> 仅修改 `src/zmai/cli/` 层的输出渲染。

---

## 目录

1. [现状审查](#1-现状审查)
2. [设计原则](#2-设计原则)
3. [启动 Dashboard](#3-启动-dashboard)
4. [状态指示器](#4-状态指示器)
5. [面板系统](#5-面板系统)
6. [动画系统](#6-动画系统)
7. [进度条](#7-进度条)
8. [Thinking 动画](#8-thinking-动画)
9. [任务中 UI 布局](#9-任务中-ui-布局)
10. [彩色输出规范](#10-彩色输出规范)
11. [文件清单与实现计划](#11-文件清单与实现计划)

---

## 1. 现状审查

### 1.1 当前输出

当前启动时输出（`main.py:386-388`）：

```
zmai  my-project (python 3.13)  test:pytest  git:master  [deepseek]
```

当前进度输出（`main.py:200-213`）：

```
  > ReadFileTool
    ok
  > ShellTool
    fail
```

### 1.2 当前 `formatters.py`

已有 9 个 style key、6 个 print 函数、`Theme` 类支持 dark/light/plain：

| Style | ANSI 码 | 用途 |
|-------|---------|------|
| `success` | BRIGHT_GREEN | 成功信息 |
| `error` | BRIGHT_RED | 错误信息 |
| `warning` | BRIGHT_YELLOW | 警告信息 |
| `info` | CYAN | 信息提示 |
| `dim` | DIM | 次要信息 |
| `highlight` | BRIGHT_BLUE | 高亮 |
| `label` | BRIGHT_CYAN | 标签 |
| `value` | WHITE | 值 |
| `bold` | BOLD | 粗体（未在主题中） |

### 1.3 缺失能力

| 能力 | 说明 |
|------|------|
| **启动 Dashboard** | 无结构化启动界面，仅一行文本 |
| **状态指示器** | 无统一的状态颜色/图标规范 |
| **面板系统** | 无框线/分区/标题等布局元素 |
| **动画** | 无 spinner、thinking、loading 等动态效果 |
| **进度条** | 无 Unicode 进度条 |
| **彩色渐变更丰富** | 只有 9 个 style，缺少 TAG/STATUS/DIM_TEXT 等 |

---

## 2. 设计原则

### 2.1 信息分层

```
每个显示元素按重要性分三层：

主要（Bright）  → 当前状态、关键数字、阶段名
次要（Dim）     → 路径、时间戳、描述文本
强调（Bold）    → 命令名、高亮词
```

### 2.2 一致的颜色语义

```
🟢 绿色  → 成功、完成、活跃、可用
🔴 红色  → 错误、失败、不可用
🟡 黄色  → 警告、注意、运行中
🔵 蓝色  → 信息、链接、高亮
⚪ 灰色  → 次要、描述、停用
```

### 2.3 终端宽度自适应

```
所有面板、分隔线、进度条根据终端宽度自动调整。

宽终端 (>100):  完整面板 + 全宽进度条
窄终端 (<60):   紧凑面板 + 短进度条
未知宽度:       默认 72 字符
```

### 2.4 不修改 Runtime

```
仅修改:  src/zmai/cli/formatters.py   ← 扩展 Theme + 新函数
         src/zmai/cli/main.py         ← 替换启动/进度输出
         src/zmai/cli/ui/             ← 🔴 新增 UI 组件目录

不修改:  src/zmai/runtime/*   ✗  src/zmai/agent/*    ✗
         src/zmai/gateway/*   ✗  src/zmai/workspace/* ✗
         src/zmai/memory/*    ✗  src/zmai/workflow/*  ✗
```

---

## 3. 启动 Dashboard

### 3.1 设计

```
  ╭──────────────────────────────────────────────────────────╮
  │  ⚡ ZMAI v0.1.0                                          │
  │  ──────────────────────────────────────────────────────── │
  │                                                          │
  │  项目    ● my-project · python 3.13 · src/ · pytest      │
  │  Backend ● DeepSeek (deepseek-chat) · 已验证              │
  │  记忆    ○ 3 条目 · 上次: "重构 auth 模块"                 │
  │  Workspace ○ ./workspace/ · 1 活跃 Agent                  │
  │  Git     ● master · 0 未提交                              │
  │                                                          │
  │  ──────────────────────────────────────────────────────── │
  │  输入自然语言描述任务 · /help 查看命令 · exit 退出          │
  ╰──────────────────────────────────────────────────────────╯
```

### 3.2 渲染代码

```python
# cli/ui/dashboard.py

class Dashboard:
    """启动 Dashboard。"""

    def __init__(self, theme: Theme):
        self._theme = theme
        self._width = _terminal_width()

    def render(self, info: StartupInfo) -> None:
        """渲染启动 Dashboard。"""
        t = self._theme
        w = self._width
        bar = t.dim("─" * (w - 4))

        lines = [
            f"  ╭{'─' * (w - 4)}╮",
            f"  │  {t.highlight('⚡ zmai')} {t.dim('v0.1.0')}"
            f"{' ' * (w - 22 - len('v0.1.0'))}│",
            f"  │  {bar} │",
            "",
            self._project_line(info, w),
            self._backend_line(info, w),
            self._memory_line(info, w),
            self._workspace_line(info, w),
            self._git_line(info, w),
            "",
            f"  │  {bar} │",
            f"  │  {t.dim('输入自然语言描述任务 · /help 查看命令 · exit 退出')}"
            f"{' ' * (w - 54)}│",
            f"  ╰{'─' * (w - 4)}╯",
            "",
        ]
        sys.stderr.write("\n".join(lines))

    def _project_line(self, info: StartupInfo, w: int) -> str:
        t = self._theme
        status = t.success("●") if info.project_type else t.dim("○")
        text = f"{info.project_name or '无项目'}"
        if info.project_type:
            text += f" · {info.project_type}"
            if info.language_version:
                text += f" {info.language_version}"
        if info.test_framework:
            text += f" · {info.test_framework}"
        return f"  │  {t.dim('项目')}    {status} {text}{' ' * (w - 12 - len(text))}│"

    def _backend_line(self, info: StartupInfo, w: int) -> str:
        t = self._theme
        if info.backend_name:
            status = t.success("●")
            text = f"{info.backend_name} ({info.backend_model})"
            if info.backend_verified:
                text += " · 已验证"
            else:
                text += " · 未验证"
        else:
            status = t.error("○")
            text = "未配置"
        return f"  │  {t.dim('Backend')} {status} {text}{' ' * (w - 14 - len(text))}│"

    def _memory_line(self, info: StartupInfo, w: int) -> str:
        t = self._theme
        status = t.info("○") if info.memory_entries > 0 else t.dim("○")
        text = f"{info.memory_entries} 条目"
        if info.last_task:
            text += f" · 上次: {info.last_task[:40]}"
        return f"  │  {t.dim('记忆')}    {status} {text}{' ' * (w - 12 - len(text))}│"

    def _workspace_line(self, info: StartupInfo, w: int) -> str:
        t = self._theme
        active = info.workspace_active_agents
        status = t.success("●") if active > 0 else t.dim("○")
        text = f"{info.workspace_root}"
        if active > 0:
            text += f" · {active} 活跃 Agent"
        return f"  │  {t.dim('Workspace')} {status} {text}{' ' * (w - 16 - len(text))}│"

    def _git_line(self, info: StartupInfo, w: int) -> str:
        t = self._theme
        if info.git_branch:
            status = t.success("●")
            text = f"{info.git_branch}"
            if info.git_uncommitted > 0:
                text += f" · {t.warning(f'{info.git_uncommitted} 未提交')}"
            else:
                text += " · 0 未提交"
        else:
            status = t.dim("○")
            text = "无 Git 仓库"
        return f"  │  {t.dim('Git')}     {status} {text}{' ' * (w - 12 - len(text))}│"
```

### 3.3 数据源

```python
@dataclass
class StartupInfo:
    """Dashboard 数据源。"""

    # 项目
    project_name: str = ""
    project_type: str = ""
    language_version: str = ""
    test_framework: str = ""
    src_dirs: list[str] = field(default_factory=list)

    # Backend
    backend_name: str = ""
    backend_model: str = ""
    backend_verified: bool = False

    # 记忆
    memory_entries: int = 0
    last_task: str = ""

    # Workspace
    workspace_root: str = ""
    workspace_active_agents: int = 0

    # Git
    git_branch: str = ""
    git_uncommitted: int = 0

    @classmethod
    def from_project_context(cls, ctx: ProjectContext | None,
                             runtime: Runtime | None,
                             memory_mgr: MemoryManager | None) -> StartupInfo:
        """从已有检测结果构建 Dashboard 数据。"""
        info = cls()

        if ctx:
            info.project_name = ctx.name
            info.project_type = ctx.type
            info.language_version = ctx.language_version
            info.test_framework = ctx.test_framework
            info.src_dirs = ctx.src_dirs
            info.git_branch = ctx.git_branch
            info.git_uncommitted = 1 if ctx.git_has_uncommitted else 0

        if runtime:
            info.backend_name = runtime._gateway.default_name or ""
            # Backend model 通过 gateway 获取

        if memory_mgr:
            info.memory_entries = 0  # 从 memory_mgr 读取

        return info
```

### 3.4 紧凑模式（窄终端）

```
  ⚡ zmai v0.1.0
  ─────────────────────────
  ● 项目     my-project · python 3.13
  ● Backend  DeepSeek · 已验证
  ○ 记忆     3 条目
  ○ Workspace ./workspace/
  ● Git      master
  ─────────────────────────
  输入描述 · /help · exit
```

---

## 4. 状态指示器

### 4.1 状态枚举

```python
class Status(str, Enum):
    """统一状态指示。"""
    ACTIVE    = "active"     # 🟢 绿色 ●
    INACTIVE  = "inactive"   # ⚪ 灰色 ○
    WARNING   = "warning"    # 🟡 黄色 ◉
    ERROR     = "error"      # 🔴 红色 ○
    LOADING   = "loading"    # 🟡 黄色 spinner
    COMPLETED = "completed"  # 🟢 绿色 ✅
    FAILED    = "failed"     # 🔴 红色 ❌
    UNKNOWN   = "unknown"    # ⚪ 灰色 ?
```

### 4.2 状态渲染

```python
class StatusIndicator:
    """状态指示器。统一状态的颜色和图标。"""

    STYLES = {
        Status.ACTIVE:    {"icon": "●", "color": "success"},
        Status.INACTIVE:  {"icon": "○", "color": "dim"},
        Status.WARNING:   {"icon": "◉", "color": "warning"},
        Status.ERROR:     {"icon": "○", "color": "error"},
        Status.LOADING:   {"icon": "◌", "color": "warning"},
        Status.COMPLETED: {"icon": "✅", "color": "success"},
        Status.FAILED:    {"icon": "❌", "color": "error"},
        Status.UNKNOWN:   {"icon": "?",  "color": "dim"},
    }

    def __init__(self, theme: Theme):
        self._theme = theme

    def render(self, status: Status, label: str = "") -> str:
        """渲染状态指示器。"""
        style = self.STYLES.get(status, self.STYLES[Status.UNKNOWN])
        icon = self._theme.colorize(style["icon"], style["color"])
        if label:
            return f"{icon} {label}"
        return icon
```

### 4.3 状态在 Dashboard 中的使用

```
● my-project · python 3.13    ← 项目 ACTIVE
● DeepSeek · 已验证            ← Backend ACTIVE
○ 无会话                       ← 记忆 INACTIVE
○ ./workspace/                 ← Workspace INACTIVE
● master · 0 未提交            ← Git ACTIVE

◉ 正在运行测试...              ← 任务 WARNING
✅ 测试通过 (42 passed)        ← 结果 COMPLETED
❌ 编译失败                    ← 结果 FAILED
```

---

## 5. 面板系统

### 5.1 框线面板

```python
class Panel:
    """带框线的信息面板。"""

    def __init__(self, theme: Theme, title: str = ""):
        self._theme = theme
        self._title = title
        self._lines: list[str] = []
        self._width = _terminal_width() - 4

    def add_line(self, label: str, value: str,
                 status: Status | None = None) -> None:
        """添加一行 标签: 值 数据。"""
        t = self._theme
        indicator = StatusIndicator(t).render(status) if status else " "
        label_fmt = t.colorize(f"{label}:", "label")
        self._lines.append(f"  │  {indicator} {label_fmt} {value}"
                           f"{' ' * (self._width - len(label) - len(value) - 6)}│")

    def add_divider(self) -> None:
        bar = self._theme.dim("─" * (self._width))
        self._lines.append(f"  │  {bar}  │")

    def render(self) -> str:
        """渲染完整面板。"""
        t = self._theme
        lines = [f"  ╭{'─' * self._width}╮"]
        if self._title:
            lines.append(f"  │  {t.highlight(self._title)}"
                         f"{' ' * (self._width - len(self._title) - 2)}│")
            lines.append(f"  │  {t.dim('─' * (self._width - 2))}  │")
        lines.extend(self._lines)
        lines.append(f"  ╰{'─' * self._width}╯")
        return "\n".join(lines)
```

### 5.2 分隔线

```python
def rule(theme: Theme, title: str = "", width: int | None = None) -> str:
    """渲染分隔线。可选标题。"""
    w = width or _terminal_width() - 4
    t = theme
    if title:
        side = (w - len(title) - 2) // 2
        return f"  {t.dim('─' * side)} {t.highlight(title)} {t.dim('─' * side)}"
    return f"  {t.dim('─' * w)}"
```

### 5.3 执行中面板

```
  ╭─ 当前执行 ─────────────────────────────────────────────╮
  │                                                        │
  │  ● 阶段    🔧 执行中 (步骤 3/8)                         │
  │  ● 工具    ✏️ 写入文件                                  │
  │  ● 目标    src/auth/login.py · L45                      │
  │  ◉ 进度    ████████░░░░░░░░░░  42%                      │
  │  ● 耗时    45s · 预计剩余 62s                            │
  │                                                        │
  ╰────────────────────────────────────────────────────────╯
```

---

## 6. 动画系统

### 6.1 Spinner

```python
class Spinner:
    """Spinner 动画。"""

    FRAMES = {
        "dots":     "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏",
        "line":     "─╮╭╰╯",
        "bracket":  "⠂⠁⠉⠝⠯⠽⠻⠟",
        "arrow":    "←↖↑↗→↘↓↙",
        "pulse":    "█▓▒░▒▓",
    }

    def __init__(self, style: str = "dots", text: str = ""):
        self._frames = self.FRAMES.get(style, self.FRAMES["dots"])
        self._idx = 0
        self._text = text

    def next(self) -> str:
        """下一帧。返回当前帧。"""
        frame = self._frames[self._idx % len(self._frames)]
        self._idx += 1
        if self._text:
            return f"{frame} {self._text}"
        return frame
```

### 6.2 动画管理器

```python
class AnimationManager:
    """动画管理器。管理活跃动画的渲染循环。"""

    def __init__(self):
        self._spinners: dict[str, Spinner] = {}
        self._running = False
        self._thread: threading.Thread | None = None

    def start_spinner(self, name: str, text: str = "",
                      style: str = "dots") -> None:
        """启动一个 spinner。"""
        self._spinners[name] = Spinner(style=style, text=text)

    def update_spinner(self, name: str, text: str) -> None:
        """更新 spinner 文本。"""
        if name in self._spinners:
            self._spinners[name]._text = text

    def stop_spinner(self, name: str) -> None:
        """停止一个 spinner。"""
        self._spinners.pop(name, None)

    def render(self) -> str:
        """渲染所有活跃 spinner 的当前帧。"""
        parts = []
        for name, spinner in self._spinners.items():
            parts.append(f"{spinner.next()}")
        return "  ".join(parts) if parts else ""
```

### 6.3 Spinner 使用场景

```
思考中:    🤔 ⠋ 正在分析代码...
           🤔 ⠙ 正在设计方案...
           🤔 ⠹ 正在执行修改...

等待 API:  ⏳ ⠋ 等待 API 响应...
           ⏳ ⠙ 等待 API 响应...

加载中:    📦 ⠋ 加载项目配置...
           📦 ⠙ 加载 Workspace...
           📦 ⠹ 恢复会话...

❌ 任务失败  (最终状态，不再旋转)
```

---

## 7. 进度条

### 7.1 设计

使用 Unicode 块字符，不引入第三方库：

```python
class ProgressBar:
    """Unicode 进度条。"""

    # 字符按填充度递增: 空心 → 1/8 → 2/8 → ... → 实心
    BLOCKS = ["░", "▒", "▓", "█"]
    # 更精细: 空格 → ▏→▎→▍→▌→▋→▊→▉→█
    FINE = [" ", "▏", "▎", "▍", "▌", "▋", "▊", "▉", "█"]

    def __init__(self, width: int = 30, style: str = "default"):
        self._width = width
        self._style = style  # "default" | "fine" | "simple"

    def render(self, pct: float, label: str = "") -> str:
        """渲染进度条。"""
        pct = max(0, min(100, pct))
        filled = int(pct / 100 * self._width)
        empty = self._width - filled

        if self._style == "simple":
            bar = "█" * filled + "░" * empty
        elif self._style == "fine":
            # 使用精确块字符
            bar = ""
            for i in range(self._width):
                cell_pct = (i + 0.5) / self._width * 100
                if pct >= cell_pct:
                    idx = min(8, int((pct - cell_pct + 100/self._width/2)
                                     / (100/self._width) * 8))
                    bar += self.FINE[min(idx, 8)]
                else:
                    bar += " "
        else:
            bar = "█" * filled + "░" * empty

        return f"{bar} {pct:.0f}%"
```

### 7.2 使用场景

```
阶段进度:
  🔍 分析 ████████████████████████████████████░░  92%

整体进度:
  总体  ████████████████░░░░░░░░░░░░░░░░░░░░  44%

文件上传:
  📤 upload ████████████████████░░░░░░░░░░  62% (12.4/20 MB)
```

### 7.3 多级进度

```python
class MultiProgress:
    """多级进度显示。显示阶段 + 总体两层。"""

    PHASE_ORDER = ["analyzing", "planning", "executing",
                   "testing", "repairing", "reporting"]

    PHASE_ICONS = {
        "analyzing": "🔍", "planning": "📋",
        "executing": "🔧", "testing": "🧪",
        "repairing": "🔨", "reporting": "📊",
    }

    def __init__(self, theme: Theme, width: int = 30):
        self._theme = theme
        self._bar = ProgressBar(width=width)
        self._phase_done: dict[str, int] = {}
        self._phase_total: dict[str, int] = {}

    def record(self, phase: str, success: bool) -> None:
        """记录一个阶段步骤。"""
        d, t = self._phase_done.get(phase, 0), self._phase_total.get(phase, 0)
        self._phase_done[phase] = d + 1
        self._phase_total[phase] = t + 1

    def render(self, current_phase: str) -> str:
        """渲染多级进度。"""
        t = self._theme
        lines = []

        # 各阶段进度
        for phase in self.PHASE_ORDER:
            done = self._phase_done.get(phase, 0)
            total = self._phase_total.get(phase, 0)
            if total == 0 and phase != current_phase:
                # 未开始的阶段和已完成但无步骤的阶段
                if phase not in self._phase_done:
                    continue
            icon = self.PHASE_ICONS.get(phase, " ")
            pct = int(done / max(total, 1) * 100) if total > 0 else 0

            if phase == current_phase:
                bar = self._bar.render(pct)
                lines.append(f"  {icon} {t.highlight(phase.capitalize())} {bar}")
            elif total > 0 and done == total:
                lines.append(f"  {icon} {t.dim(phase.capitalize())} {t.success('✅ 完成')}")
            elif done > 0:
                bar = self._bar.render(pct)
                lines.append(f"  {icon} {t.dim(phase.capitalize())} {bar}")

        return "\n".join(lines)
```

---

## 8. Thinking 动画

### 8.1 设计

```python
class ThinkingAnimation:
    """"正在思考"动画。在 LLM API 调用期间显示。"""

    # 动态变化的思考短语
    THOUGHTS = [
        "正在理解任务...",
        "正在分析代码...",
        "正在设计方案...",
        "正在考虑多种方法...",
        "正在评估最优方案...",
        "正在检查依赖关系...",
        "正在规划执行步骤...",
        "正在准备工具调用...",
    ]

    def __init__(self, theme: Theme):
        self._theme = theme
        self._spinner = Spinner(style="dots")
        self._thought_idx = 0
        self._start_time: float = 0
        self._active = False

    def start(self) -> None:
        """启动 thinking 动画。"""
        self._active = True
        self._start_time = time.time()
        self._thought_idx = 0

    def stop(self) -> None:
        """停止 thinking 动画。"""
        self._active = False
        # 清除 thinking 行
        sys.stderr.write("\033[2K\r")

    def render(self) -> str:
        """渲染当前 thinking 帧。"""
        if not self._active:
            return ""

        elapsed = time.time() - self._start_time
        # 每 3 秒更换思考短语
        thought = self.THOUGHTS[self._thought_idx % len(self.THOUGHTS)]
        if int(elapsed) % 3 == 0 and elapsed > 0:
            self._thought_idx = (elapsed // 3) % len(self.THOUGHTS)
            thought = self.THOUGHTS[int(self._thought_idx)]

        spinner = self._spinner.next()
        elapsed_str = f"{elapsed:.0f}s"
        return f"\r  🤔 {spinner} {thought} ({elapsed_str})\033[K"
```

### 8.2 显示效果

```
  🤔 ⠋ 正在理解任务... (0s)
  🤔 ⠙ 正在理解任务... (1s)
  🤔 ⠹ 正在分析代码... (3s)
  🤔 ⠸ 正在设计方案... (6s)
  🤔 ⠼ 正在考虑多种方法... (9s)
```

### 8.3 切换回退

当 LLM API 耗时超过阈值时，thinking 动画自动降级为时间显示：

```
  🤔 ⠋ 正在理解任务... (0s)
  🤔 ⠙ 正在理解任务... (1s)
  🤔 ⠹ 正在分析代码... (3s)
  🤔 ⠸ 正在分析代码... (4s)
  ⏳ 等待 API 响应... (10s)     ← 超 10s 自动切换
  ⏳ 等待 API 响应... (11s)
```

---

## 9. 任务中 UI 布局

### 9.1 完整布局

```
  ╭─ 当前任务 ─────────────────────────────────────────────╮
  │                                                         │
  │  🔧 执行中 (步骤 3/8)   总体 ██████░░░░░░░░  44%       │
  │                                                         │
  │  🔍 分析 ████████████████████████████████████░░  92%   │
  │  📋 规划 ████████████████████████████████████ 100% ✅  │
  │  🔧 执行 ████████░░░░░░░░░░░░░░░░░░░░░░░░░░  22%     │
  │                                                         │
  ╰────────────────────────────────────────────────────────╯

  📂 读取文件  src/auth/login.py                       (0.2s)
  🔍 搜索代码  "token" in src/auth/*                      (0.3s)
  ✏️ 写入文件  src/auth/login.py                       (0.1s)
  🖥️ 执行命令  pytest tests/test_auth.py -x            仍在运行...

  ╭─ 执行详情 ────────────────────────────────────────────╮
  │                                                         │
  │  🤔 ⠋ 正在运行测试...                                   │
  │  ⏱ 已用: 12.5s  Token: 4,230 输入 / 1,200 输出         │
  │                                                         │
  ╰────────────────────────────────────────────────────────╯
```

### 9.2 紧凑布局（任务中默认）

```
  🔧 执行 (3/8)  ████████████░░░░░░░░  44%
  ─────────────────────────────────────────────
  📂 读取文件  src/auth/login.py              (0.2s)
  🔍 搜索代码  "token" in src/auth/           (0.3s)
  ✏️ 写入文件  src/auth/login.py              (0.1s)
  🖥️ 执行命令  pytest tests/test_auth.py -x    12.5s
  ✅ 测试通过  42 passed, 0 failed
  ─────────────────────────────────────────────
  🤔 ⠋ 正在分析测试结果...
```

---

## 10. 彩色输出规范

### 10.1 扩展 Theme

```python
# formatters.py — 扩展

class Theme:
    """输出主题。扩展 style 集合。"""

    colors: dict[str, str] = field(default_factory=lambda: dict(DARK_THEME))
    enabled: bool = True

    # ── 以下为不变部分 ──
    @classmethod
    def dark(cls): ...
    @classmethod
    def light(cls): ...
    @classmethod
    def plain(cls): ...
    def colorize(self, text, style): ...
    def success(self, text): ...
    def error(self, text): ...
    def warning(self, text): ...

    # ── 新增 style 快捷方法 ──
    def tag(self, text: str) -> str:
        """标签：BRIGHT_CYAN 背景色效果。"""
        return self.colorize(text, "label")

    def dim(self, text: str) -> str:
        """次要信息：DIM。"""
        return self.colorize(text, "dim")

    def bold(self, text: str) -> str:
        """粗体：BOLD。"""
        if not self.enabled:
            return text
        return f"{_ANSICodes.BOLD}{text}{_ANSICodes.RESET}"

    # ── 新增输出函数 ──
    def status_icon(self, status: Status) -> str:
        """状态图标。"""
        return StatusIndicator(self).render(status)

    def progress(self, pct: float, width: int = 30) -> str:
        """进度条字符串。"""
        return ProgressBar(width=width).render(pct)

    def header(self, text: str) -> str:
        """标题（大号）。"""
        w = _terminal_width() - 4
        side = (w - len(text) - 2) // 2
        return f"\n  {self.dim('─' * side)} {self.highlight(text)} {self.dim('─' * side)}"
```

### 10.2 颜色规范

| 用途 | Style | 暗色 | 亮色 | 示例 |
|------|-------|------|------|------|
| 标题 | `highlight` | BRIGHT_BLUE | BLUE | `项目` |
| 标签 | `label` | BRIGHT_CYAN | BLUE | `项目:` |
| 值 | `value` | WHITE | BLACK | `my-project` |
| 成功 | `success` | BRIGHT_GREEN | GREEN | `●`, `✅` |
| 错误 | `error` | BRIGHT_RED | RED | `❌` |
| 警告 | `warning` | BRIGHT_YELLOW | YELLOW | `◉`, `⚠` |
| 信息 | `info` | CYAN | BLUE | `ℹ` |
| 次要 | `dim` | DIM | DIM | `(0.2s)`, `v0.1.0` |
| 强调 | `bold` | BOLD | BOLD | `文件名` |

### 10.3 输出位置规范

| 内容类型 | 输出位置 | 说明 |
|---------|---------|------|
| Dashboard | stderr | 启动信息 |
| 进度条 | stderr | 任务执行中 |
| 动画 | stderr | 动态效果 |
| 执行记录 | stderr | 工具调用流 |
| 最终结果 | stdout | 任务输出 |
| JSON 输出 | stdout | `--json` 模式 |
| 错误信息 | stderr | 异常 |
| 日志 | stderr | 调试信息 |

### 10.4 NO_COLOR 支持

```python
def _should_use_color() -> bool:
    """自动检测终端是否支持颜色。"""
    if not sys.stdout.isatty() and not sys.stderr.isatty():
        return False
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("TERM") == "dumb":
        return False
    if os.environ.get("CLICOLOR_FORCE"):
        return True
    return True
```

---

## 11. 文件清单与实现计划

### 11.1 新增文件

```
src/zmai/cli/ui/
├── __init__.py             # 导出 UI 组件
├── dashboard.py            # 启动 Dashboard
├── status.py               # 状态指示器 StatusIndicator
├── panel.py                # 面板系统 Panel
├── spinner.py              # Spinner 动画
├── progress.py             # 进度条 ProgressBar + MultiProgress
├── thinking.py             # Thinking 动画
└── layout.py               # 布局工具（分隔线、终端宽度检测）
```

### 11.2 修改文件

```
src/zmai/cli/formatters.py   # 🔧 扩展 Theme（header/status_icon/progress/tag/bold）
src/zmai/cli/main.py         # 🔧 集成 Dashboard + 动画
```

### 11.3 不变文件

```
src/zmai/runtime/*             ✅ 不变
src/zmai/gateway/*             ✅ 不变
src/zmai/agent/*               ✅ 不变
src/zmai/workspace/*           ✅ 不变
src/zmai/memory/*              ✅ 不变
src/zmai/workflow/*            ✅ 不变
src/zmai/config/*              ✅ 不变
src/zmai/auth/*                ✅ 不变
src/zmai/cli/detector.py       ✅ 不变
src/zmai/cli/context.py        ✅ 不变
src/zmai/cli/detectors/*       ✅ 不变
```

### 11.4 代码量估算

```
新增:
  ui/__init__.py        ~15 行
  ui/dashboard.py       ~150 行
  ui/status.py           ~60 行
  ui/panel.py           ~100 行
  ui/spinner.py          ~80 行
  ui/progress.py        ~120 行
  ui/thinking.py         ~80 行
  ui/layout.py           ~40 行
  新增总计              ~645 行

修改:
  formatters.py          ~30 行（扩展 Theme）
  main.py                ~40 行（集成 Dashboard）
  修改总计               ~70 行

不变:                    ~2000+ 行
```

### 11.5 实现优先级

```
P0 — 核心 UI 基础（1 天）
├── ui/layout.py               — 终端宽度检测、分隔线
├── ui/status.py               — StatusIndicator
├── ui/spinner.py              — Spinner 动画
├── formatters.py 扩展          — header/status_icon/progress
└── main.py 集成               — 替换启动行

P1 — Dashboard + 进度（1 天）
├── ui/dashboard.py            — 启动 Dashboard
├── ui/progress.py             — ProgressBar + MultiProgress
├── ui/panel.py                — Panel 框线面板
└── main.py 启动集成           — Dashboard 取代单行

P2 — 动画（0.5 天）
├── ui/thinking.py             — Thinking 动画
├── AnimationManager           — 动画渲染循环
└── 任务中 UI 布局集成          — 完整任务 UI
```

---

> **总结：**
>
> ZMAI Terminal UI v1.0 从单行文本启动进化为完整的彩色终端界面系统：
>
> **启动 Dashboard** — 6 行结构化面板：项目 / Backend / 记忆 / Workspace / Git，每行带彩色状态指示器 ●/○
>
> **状态指示器** — 统一 Status 枚举：ACTIVE(●)/INACTIVE(○)/WARNING(◉)/ERROR(○)/COMPLETED(✅)/FAILED(❌)
>
> **面板系统** — 带框线的信息面板 `╭─╮│╰─╯`，标题、分隔线、标签-值布局
>
> **Spinner 动画** — 6 种帧类型（dots/line/bracket/arrow/pulse），多 spinner 并发
>
> **进度条** — Unicode 块字符，3 种样式（default/fine/simple），多级进度显示
>
> **Thinking 动画** — 8 个思考短语轮换 + 计时器，10 秒超时自动降级为等待提示
>
> **标准颜色** — 9 色语义规范，NO_COLOR/CLICOLOR_FORCE 兼容，暗色/亮色双主题
>
> **零依赖** — 所有 UI 仅用 Unicode + ANSI 转义码，不引入任何第三方库
