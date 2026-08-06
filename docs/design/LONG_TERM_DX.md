# ZMAI Long-Term DX Design v1.0

Version: 1.0
Date: 2026-07-16

> **站在每天使用 ZMAI 八小时、连续使用一年的软件工程师角度。**
>
> 不增加功能。只优化 Developer Experience。
>
> 思考哪些操作变得烦人、哪些命令太长、哪些步骤可以自动完成。

---

## 目录

1. [方法论](#1-方法论)
2. [一年后的摩擦点](#2-一年后的摩擦点)
3. [启动类](#3-启动类)
4. [REPL 类](#4-repl-类)
5. [上下文类](#5-上下文类)
6. [输出类](#6-输出类)
7. [记忆类](#7-记忆类)
8. [清理类](#8-清理类)
9. [配置类](#9-配置类)
10. [编译原则](#10-编译原则)

---

## 1. 方法论

### 1.1 模拟对象

```
角色:      全栈软件工程师
工具链:    Python + TypeScript + Go + Docker
日常工作:  代码审查、重构、调试、写测试、写文档
使用频率:  每天 8 小时，连续 365 天
```

### 1.2 摩擦累积曲线

```
每次摩擦:  ~2 秒 annoyance
频率:      每天 50 次
累积:      100 秒/天 ≈ 10 小时/年

一个看似微小的 2 秒延迟，一年累积超过 10 小时。
```

### 1.3 分析维度

```
每个摩擦点按三个维度评估：

  频率  ───  每天发生多少次？
  痛感  ───  每次发生时的烦躁程度 (1-5)
  累积  ───  一年下来浪费的时间

  优先级 = 频率 × 痛感 × 累积
```

---

## 2. 一年后的摩擦点

### 2.1 完整摩擦清单

| # | 摩擦 | 阶段 | 频率/天 | 痛感 | 年耗时 |
|---|------|------|---------|------|--------|
| F1 | 每次启动等检测完成才能输入 | 启动 | 20 | 4 | 3h |
| F2 | Dashboard 每天看 20 遍相同的项目信息 | 启动 | 20 | 2 | 1h |
| F3 | 每天输入 `zmai` 后再敲一次回车 | 启动 | 20 | 1 | 0.5h |
| F4 | REPL 中复制粘贴代码块很麻烦 | REPL | 10 | 5 | 2h |
| F5 | Tab 补全不认识项目文件名 | REPL | 30 | 3 | 1h |
| F6 | 历史搜索不能跨会话 | REPL | 15 | 4 | 2h |
| F7 | Agent 停下来等 API 响应时无进度 | 执行 | 40 | 3 | 2h |
| F8 | Ctrl+C 暂停后不知道如何恢复 | 执行 | 5 | 3 | 0.5h |
| F9 | 换项目后 Agent 忘记上一项目的上下文 | 上下文 | 5 | 5 | 3h |
| F10 | Agent 每次重新发现相同的项目事实 | 上下文 | 5 | 4 | 2h |
| F11 | 进度条太长，滚动把有效信息冲走了 | 输出 | 40 | 2 | 1h |
| F12 | 成功消息每次都用 `✓ 完成`，没有变化 | 输出 | 30 | 1 | 0.5h |
| F13 | Agent 不记得昨天做的技术决策 | 记忆 | 3 | 5 | 2h |
| F14 | 想不起上周做过什么任务 | 记忆 | 2 | 4 | 1h |
| F15 | Workspace 目录里有 30 个旧 Agent 目录 | 清理 | 1 | 3 | 0.5h |
| F16 | zmai.json 里有过期的 Backend 配置 | 配置 | 1 | 2 | 0.5h |
| F17 | 想用其他 Backend 但不确定 API Key 是否有效 | 配置 | 2 | 3 | 1h |
| F18 | Agent 说"文件不存在"但路径是对的 | 工具 | 3 | 4 | 1h |

### 2.2 优先排序

```
高优先级 (频率 × 痛感 ≥ 15):
  F4  REPL 多行输入      (10×5=50)
  F9  换项目上下文丢失     (5×5=25)
  F13 Agent 忘记决策      (3×5=15)
  F6  历史不跨会话        (15×4=60)
  F5  Tab 补全不完整      (30×3=90)

中优先级 (频率 × 痛感 ≥ 10):
  F7  API 等待无进度      (40×3=120)
  F10 Agent 重复发现事实   (5×4=20)
  F14 记不起上周任务      (2×4=8)
  F1  启动等待            (20×4=80)

低优先级 (频率 × 痛感 < 10):
  F2  Dashboard 疲劳      (20×2=40)
  F3  多敲一次回车        (20×1=20)
  F11 进度条滚动          (40×2=80)
  F12 成功消息单调         (30×1=30)
```

---

## 3. 启动类

### 3.1 F1: 每次启动等检测完成才能输入

**问题：**

```
$ zmai
  ⚡ zmai v0.1.0 · 87ms        ← 看起来很快
  ─── 项目检测 ───             ← 但这 87ms 内不能输入
  ● 项目     my-project
  ● Backend  DeepSeek
  ...
  zmai> _                      ← 终于可以输入了
```

一年后：每天启动 20 次 × 87ms × 365 = **10.6 分钟** 的纯等待。

**优化：**

```python
# 启动不阻塞输入。Dashboard 在后台渲染，用户立即可以输入。

$ zmai
  zmai> _
  ⚡ zmai v0.1.0  (loading...)   ← 背景信息异步展示
```

**异步 Dashboard：**

```python
class AsyncDashboard:
    """异步 Dashboard。启动后立即显示 prompt，后台加载信息。"""

    def __init__(self):
        self._loaded = False
        self._task: asyncio.Task | None = None

    async def start(self):
        """立即显示 prompt，后台加载。"""
        # 先显示最小 prompt
        sys.stderr.write("  zmai> ")
        sys.stderr.flush()

        # 后台加载
        self._task = asyncio.create_task(self._load())

    async def _load(self):
        """后台加载 Dashboard 信息。"""
        result = await load_startup_info()
        self._info = result
        self._loaded = True

        # 信息加载完成后，在下一行输出 Dashboard
        sys.stderr.write(f"\n  ⚡ zmai v0.1.0 · {result.startup_time_ms:.0f}ms\n")
        sys.stderr.write(f"  ● {result.project_name} · {result.backend_name}\n")
```

**原则：** 启动不阻塞。用户输入优先，信息展示其次。

### 3.2 F2: Dashboard 疲劳

**问题：**

```
# 第一天
  ╭─ 6 行彩色面板 ─╮
  │ 项目 ... Backend ... │    ← "哇，好看"
  │ Git ... 会话 ...      │
  ╰──────────────────────╯

# 第 30 天
  ╭─ 6 行彩色面板 ─╮
  │ 项目 ... Backend ... │    ← "知道了，快让我输入"
  │ Git ... 会话 ...      │
  ╰──────────────────────╯

# 第 365 天
  ╭─ 6 行彩色面板 ─╮
  │ 项目 ... Backend ... │    ← "关掉它"
  │ Git ... 会话 ...      │
  ╰──────────────────────╯
```

**优化：**

```python
# 默认只显示一行摘要。更多信息通过 /status 查看。
# 首次使用或有重要变化时显示完整 Dashboard。

# 日常启动:
  zmai> _                    ← 没有 Dashboard，直接可输入
  my-project · DeepSeek      ← 一行状态（异步，不阻塞输入）

# 首次启动:
  ╭─ 完整 Dashboard ─╮      ← 首次或版本更新时显示
  │ ...               │
  ╰───────────────────╯

# 有重要状态变化时:
  ⚠ Workspace 有 3 个过期 Agent  ← 只在需要时通知
  zmai> _
```

### 3.3 F3: 每天敲 `zmai` 后再敲一次回车

**问题：**

```
$ zmai                        ← 输入 zmai
  zmai> _                     ← 还要再等一次 prompt
```

**优化：**

```python
# 无参数时立即进入 REPL，不需要第二行。
# 如果有单次任务，zmai <task> 直接执行。

$ zmai                        ← 终端直接显示 zmai> prompt
  zmai> _
```

**实现：** 启动后立即显示 prompt，不先换行。Dashboard 信息在背景输出。

---

## 4. REPL 类

### 4.1 F4: 多行输入很麻烦

**问题：**

```
zmai> ```python              ← 想贴一段代码
... def hello():             ← 多行模式
...     print("hello")
... ```
...                           ← 空行提交
```

一年后：每天 10 次 × 每次 3 秒 × 365 = **3 小时** 浪费在调整缩进上。

**优化：**

```python
# 检测到用户粘贴时自动进入多行模式。
# 不需要手动输入 ``` 前缀。

# 使用 brackets/paste 检测：
# 输入以 { 或 [ 或 ( 或 def/class/import 开头时，
# 自动进入多行模式直到空行提交。
```

### 4.2 F5: Tab 补全不完整

**问题：**

```
zmai> read src/auth/l<TAB>    ← 应该补全 login.py 但什么都没发生

zmai> /h<TAB>                  ← 补全到 /help
zmai> /st<TAB>                 ← 补全到 /status
```

一年后：每天 30 次无效 Tab × 每次 1 秒 × 365 = **3 小时** 在手动输入路径。

**优化：**

```python
# Tab 补全的优先级：
#   1. 内建命令          /help /status /memory ...
#   2. 项目文件路径       src/auth/login.py ...
#   3. Backend 名称      claude deepseek openai ...
#   4. 当前目录           git status, pytest tests/ ...
```

**项目文件补全：**

```python
class PathCompleter:
    """项目文件路径补全。"""

    def __init__(self, project_root: Path):
        self._root = project_root
        self._file_cache: dict[str, list[str]] = {}
        self._cache_time: float = 0

    def complete(self, partial: str) -> list[str]:
        """补全文件路径。"""
        # 将相对路径转换为绝对路径
        full_path = self._root / partial
        parent = full_path.parent if partial else self._root
        prefix = full_path.name

        if not parent.exists():
            return []

        # 缓存文件列表（30 秒刷新）
        self._refresh_cache(parent)

        # 匹配前缀
        matches = [
            str(p.relative_to(self._root).as_posix())
            for p in self._file_cache.get(str(parent), [])
            if p.name.startswith(prefix)
        ]
        return matches

    def _refresh_cache(self, directory: Path) -> None:
        """缓存目录文件列表。"""
        now = time.time()
        key = str(directory)
        if key in self._file_cache and now - self._cache_time < 30:
            return
        try:
            self._file_cache[key] = list(directory.iterdir())
            self._cache_time = now
        except PermissionError:
            self._file_cache[key] = []
```

### 4.3 F6: 历史不跨会话

**问题：**

```
# 今天早上:
zmai> 重构 auth 模块的 login 函数

# 今天下午:
zmai> 帮我跑测试

# 明天早上: Ctrl+R 搜索 "重构"
# 没有任何结果 — 历史已经丢失了
```

一年后：每天 15 次无效搜索 × 每次 3 秒 × 365 = **4.6 小时** 在重新输入。

**优化：**

```python
# 历史持久化到 ~/.zmai/history
# 跨会话，跨项目，永久保存

# 搜索:
zmai> Ctrl+R
(reverse-i-search) '重构': 重构 auth 模块的 login 函数   ← 昨天的命令

# 频率排序:
zmai> Ctrl+R
Most used:
  1. 重构 auth 模块          (12 次)
  2. 帮我跑测试              (8 次)
  3. 读取 README             (5 次)
```

**历史文件格式：**

```
~/.zmai/history
  1 行 = 1 条命令
  最多 5000 条
  自动去重（连续重复）
  自动标记时间戳和项目名
```

---

## 5. 上下文类

### 5.1 F9: 换项目后上下文丢失

**问题：**

```
# 在 project-a:
zmai> 帮我看看这个模块的架构

# 切换到 project-b:
$ cd ../project-b
$ zmai
  Agent 不知道 project-a 的任何信息
  之前的对话、决策、文件修改全部丢失

# 再切回 project-a:
$ cd ../project-a
$ zmai
  Agent 也不记得 project-a 了
```

一年后：每天 5 次上下文切换 × 每次 30 秒重新建立 × 365 = **15 小时** 浪费。

**优化：**

```python
# 每个项目独立记忆。切回时自动恢复。
# 不需要任何显式操作。

# 数据结构:
~/.zmai/memory/projects/
  project-a.jsonl    ← 项目 A 的记忆
  project-b.jsonl    ← 项目 B 的记忆

# 切换回 project-a 时:
$ cd ../project-a
$ zmai
  ● 恢复 project-a 上下文 (3 条记忆)
  zmai> 上次的架构分析结果是什么？
  Agent: 上次分析表明 project-a 使用三层架构...
```

**关键实现：**

```python
def get_project_memory_path(project_root: Path) -> Path:
    """根据项目根路径哈希确定记忆文件。"""
    import hashlib
    # 使用路径哈希而非项目名，避免同名项目冲突
    hash_ = hashlib.sha256(str(project_root.resolve()).encode()).hexdigest()[:16]
    return Path.home() / ".zmai" / "memory" / "projects" / f"{hash_}.jsonl"
```

### 5.2 F10: Agent 重复发现相同的事实

**问题：**

```
# 第 1 天:
zmai> 这个项目用什么测试框架？
Agent: 用 pytest，配置在 pyproject.toml 中

# 第 2 天（Agent 不记得了）:
zmai> 跑一下测试
Agent: 让我先看看项目用什么测试框架...  （重复发现）
Agent: 用 pytest
Agent: 运行 pytest tests/

# 第 3 天:
zmai> 帮我写个测试
Agent: 告诉我用什么框架...                （再次重复发现）
```

一年后：每天 5 次重复发现 × 每次 10 秒 × 365 = **5 小时** 浪费。

**优化：**

```python
# 项目检测结果持久化到 Project Memory。
# Agent 启动时自动注入，不需要重新发现。

# 注入 Prompt:
"""
=== 项目上下文 (自动恢复) ===
项目: my-project (python 3.13)
测试框架: pytest
包管理器: uv
代码位置: src/
测试位置: tests/
"""

# Agent 不需要再问"用什么框架"。
# 第一天检测一次，之后每次启动自动注入。
```

**缓存 TTL：**

```python
# 项目检测结果的缓存策略：
# - pyproject.toml 未修改 → 使用缓存（30 天有效）
# - pyproject.toml 已修改 → 重新检测
# - 首次检测 → 写入缓存

def get_cached_context(project_root: Path) -> ProjectContext | None:
    """获取缓存的检测结果。"""
    cache_file = project_root / ".zmai" / "detect-cache.json"
    if not cache_file.exists():
        return None
    data = json.loads(cache_file.read_text())
    # 检查依赖文件是否变化
    for dep in data.get("dependencies", []):
        dep_path = project_root / dep
        if dep_path.stat().st_mtime > data["cached_at"]:
            return None  # 缓存过期
    return ProjectContext(**data["context"])

def cache_context(ctx: ProjectContext) -> None:
    """缓存检测结果。"""
    cache_dir = ctx.root / ".zmai"
    cache_dir.mkdir(parents=True, exist_ok=True)
    data = {
        "cached_at": time.time(),
        "dependencies": ["pyproject.toml", "setup.py", "package.json"],
        "context": ctx.to_dict(),
    }
    (cache_dir / "detect-cache.json").write_text(json.dumps(data))
```

---

## 6. 输出类

### 6.1 F7: Agent 停下来等 API 响应时无进度

**问题：**

```
zmai> 重构 auth 模块

  🔍 分析阶段 ──────────────────
  📂 读取文件  src/auth/login.py     (0.2s)
  📂 读取文件  src/auth/middleware.py (0.1s)

  （这里停了 45 秒等待 API）
  （终端没有任何输出）
  （用户以为程序卡死了）
  （用户按了 Ctrl+C）

  ⏸ 已暂停

zmai> /resume

  （又停了 45 秒）
  （用户再次以为卡死了）
```

一年后：每天 40 次 API 等待 × 每次 3 秒焦虑 × 365 = **12 小时** 的不确定感。

**优化：**

```python
# API 调用期间必须有持续输出。

# 方案 1: Token 流（如果 Backend 支持流式）
  正在思考... 我们需要重构 auth 模块，首先要理解现有的登录流程。

# 方案 2: 状态轮换（不支持流式时）
  🤔 ⠋ 正在分析代码... (3s)
  🤔 ⠙ 正在设计方案... (6s)
  🤔 ⠹ 正在考虑多种方法... (9s)
  🤔 ⠸ 正在评估最优方案... (12s)
  ⏳ 等待 API 响应... (15s)     ← 10 秒后自动降级

# 方案 3: 历史等待时间预测
  ⏳ 预计等待 15-30 秒 (基于历史: 上次 22 秒)
```

**等待预测：**

```python
class WaitPredictor:
    """API 等待时间预测器。基于历史统计。"""

    def __init__(self):
        self._history: list[float] = []

    def record(self, elapsed: float) -> None:
        self._history.append(elapsed)
        if len(self._history) > 20:
            self._history.pop(0)

    def predict(self) -> tuple[float, float]:
        """预测最小和最大等待时间。"""
        if not self._history:
            return (5, 30)
        return (min(self._history), max(self._history))
```

### 6.2 F11: 进度条太长，滚动冲走信息

**问题：**

```
  🔍 分析 ████████████████████████████████████░░  92%
  📋 规划 ████████████████████████████████████ 100% ✅
  🔧 执行 ████████░░░░░░░░░░░░░░░░░░░░░░░░░░  22%
  🧪 测试 ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░   0%
  🔨 修复 ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░   0%
  📊 报告 ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░   0%
  ─────────────────────────────────────────────
  总体  ██████████░░░░░░░░░░░░░░░░░░░░░░  22%

  📂 读取文件  src/auth/login.py     (0.2s)  ← 有效信息
  🔍 搜索代码  "token"               (0.3s)  ← 有效信息
  ✏️ 写入文件  src/auth/login.py     (0.1s)  ← 有效信息
```

一年后：每天 40 次 × 每次扫一眼 2 秒 × 365 = **8 小时** 在从进度条中找有效信息。

**优化：**

```python
# 进度信息默认紧凑。执行记录永远可见。

# 紧凑进度（默认）:
  🔧 步骤 3/8  ████████░░  65%  |  📂 read_file  src/auth/login.py  (0.2s)

# 步骤执行记录（可滚动查看，不会被进度条冲走）:
  [1] 📂 读取文件  src/auth/login.py      0.2s
  [2] 🔍 搜索代码  "def login"            0.3s
  [3] ✏️ 写入文件  src/auth/login.py      0.1s   ← 当前步骤
```

### 6.3 F12: 成功消息单调

**问题：**

```
✅ 完成
✅ 完成
✅ 完成
✅ 完成       ← 一年后看这个符号已经没感觉了
✅ 完成
```

**优化：**

```python
# 成功消息根据任务类型变化。

TASK_COMPLETIONS = {
    "refactor": "✨ 重构完成",
    "test":     "🧪 测试通过",
    "fix":      "🔧 已修复",
    "doc":      "📝 文档已更新",
    "build":    "📦 构建成功",
    "default":  "✅ 完成",
}

# 通过任务描述关键词自动选择:
def select_completion(task: str) -> str:
    if any(w in task for w in ["重构", "refactor", "rewrite"]):
        return TASK_COMPLETIONS["refactor"]
    if any(w in task for w in ["测试", "test", "pytest"]):
        return TASK_COMPLETIONS["test"]
    if any(w in task for w in ["修复", "fix", "bug"]):
        return TASK_COMPLETIONS["fix"]
    return TASK_COMPLETIONS["default"]
```

---

## 7. 记忆类

### 7.1 F13: Agent 不记得昨天做的技术决策

**问题：**

```
# 第 1 天:
zmai> 我需要决定用 JWT 还是 session 来做认证
Agent: 建议用 JWT，原因如下: ...

zmai> 好的，用 JWT，帮我实现
Agent: 实现完成

# 第 2 天:
zmai> 为什么认证用 JWT 而不是 session？
Agent: 我不确定，让我看看代码...    ← 不记得昨天的决策了
```

一年后：每天 3 次重复解释 × 每次 20 秒 × 365 = **6 小时** 重复。

**优化：**

```python
# Agent 在做技术决策时自动记录到 Memory。
# 第二天 Agent 自动加载。

# Agent 行为变化:
# Agent 做出决策时 → on_decision("用 JWT 而非 session", "原因: ...")
# Agent 下次启动 → 加载 decisions namespace
# 被问到时 → 直接从 Memory 读取，不需要重新分析

# Memory 结构:
{
  "namespace": "decisions",
  "entries": [
    {
      "topic": "认证方案",
      "decision": "使用 JWT",
      "reason": "无状态、可扩展、跨服务共享",
      "date": "2026-07-15",
      "alternatives": ["session", "OAuth"]
    }
  ]
}
```

### 7.2 F14: 记不起上周做过什么任务

**问题：**

```
zmai> 我上周做了什么？
Agent: 我不知道，我没有记录     ← 没有任务历史
```

**优化：**

```python
# 每条任务完成后自动记录到 Project Memory。
# 支持按时间范围查询。

zmai> 我上周做了什么？

  ── 上周任务 ──
  周一  重构 auth 模块              ✅ 完成 (42m)
  周二  修复数据库连接池             ✅ 完成 (18m)
  周三  添加 API 文档               ⏸ 暂停 (30%)
  周四  优化查询性能                 ✅ 完成 (1h 12m)
  周五  升级依赖版本                 ❌ 失败

zmai> 上周三的那个 API 文档任务
Agent: 让我恢复那个任务的上下文...

  ● 恢复任务 "添加 API 文档"
  ● 上次进度: 编写了 users.md 和 auth.md
  ● 剩余: projects.md 和 deployment.md
  ● 上次关注: users.md 的 GET 参数表格没写完
```

**任务历史存储：**

```python
# ~/.zmai/sessions/
# 每条任务完成后追加一条记录

{
  "task": "重构 auth 模块",
  "status": "completed",
  "steps": 7,
  "duration_seconds": 2520,
  "files_modified": ["src/auth/login.py", "src/auth/middleware.py"],
  "timestamp": "2026-07-15T10:00:00Z",
  "project": "my-project"
}
```

---

## 8. 清理类

### 8.1 F15: Workspace 目录有 30 个旧 Agent 目录

**问题：**

```
workspace/
├── agent_abc123/        ← 3 个月前的任务
├── agent_def456/        ← 2 个月前的任务
├── agent_ghi789/        ← 1 个月前的任务
├── agent_jkl012/        ← 上周的任务
├── ... (30 个目录)
├── state.json
└── manifest.json
```

一年后：30-50 个过期 Agent 目录，占用 ~200MB 磁盘空间。`list_agents()` 越来越慢。

**优化：**

```python
# 自动清理策略（不改 Workspace 模块，仅增强触发机制）:

class AutoGC:
    """自动清理过期工作区。"""

    MAX_AGE_DAYS = 7       # 7 天前的任务自动清理
    MAX_AGENTS = 10        # 最多保留 10 个 Agent 目录
    CHECK_INTERVAL = 3600  # 每小时检查一次

    def clean(self, workspace_path: Path) -> int:
        """清理过期 Agent。返回清理数量。"""
        cleaned = 0
        now = time.time()

        for agent_dir in sorted(workspace_path.iterdir()):
            if not agent_dir.is_dir():
                continue

            state_file = agent_dir / ".state" / "state.json"
            if not state_file.exists():
                continue

            try:
                state = json.loads(state_file.read_text())
                status = state.get("status", "")
                updated = state.get("updated_at", "")

                # 只清理已完成或失败的任务
                if status not in ("completed", "failed"):
                    continue

                # 清理超过 7 天的
                age = now - datetime.fromisoformat(updated).timestamp()
                if age > self.MAX_AGE_DAYS * 86400:
                    shutil.rmtree(agent_dir)
                    cleaned += 1

            except (json.JSONDecodeError, KeyError):
                continue

        return cleaned
```

**GC 触发时机：**

```
启动时:     后台异步运行一次 GC
任务完成时:  检查是否超过 MAX_AGENTS，是则清理最旧的
空闲时:     每分钟检查一次（仅在 REPL 模式）
```

---

## 9. 配置类

### 9.1 F16: zmai.json 有过期的 Backend 配置

**问题：**

```json
{
  "gateway": {
    "backends": {
      "claude": { "model": "claude-sonnet-4-6" },
      "deepseek": { "model": "deepseek-v4-flash" },
      "openai": { "model": "gpt-4-turbo" },       ← 半年前就不用了
      "ollama": { "model": "llama3" },             ← 再也没用过
      "gemini": { "model": "gemini-pro" }          ← 试了一次就没用
    }
  }
}
```

一年后：配置文件中 50% 的 Backend 从未使用，但没人清理。

**优化：**

```python
# 自动追踪 Backend 使用频率，超过 30 天未使用的给出警告。

zmai> /backend

  当前: ● DeepSeek (deepseek-chat)

  可用:
    claude    ✅ 已验证 (上次使用: 今天)
    deepseek  ✅ 已验证 (上次使用: 今天)  (当前)
    openai    ❌ 未配置
    gemini    ❌ 未配置

  ⚠ 检测到未使用的 Backend 配置:
    ollama    (180 天未使用)
    运行 /backend clean 清理
```

### 9.2 F17: 不确定 API Key 是否有效

**问题：**

```
zmai --backend claude <task>
  ❌ Backend 调用失败: HTTP 401

  # 用户: 我上次更新 Key 了吗？Key 过期了？
  # 用户: 切换到另一个 Backend 试试

zmai --backend deepseek <task>
  ✅ 成功

  # 用户: 哦，claude 的 Key 过期了
  # 用户: 回到 claude 的配置页面复制 Key
  # 用户: zmai auth update claude
```

一年后：每天 2 次 Key 验证 × 每次 15 秒 × 365 = **3 小时** 排错。

**优化：**

```python
# 启动时后台验证所有已配置的 Backend。
# 验证结果缓存 1 小时。
# 不阻塞启动，验证失败不显示错误，仅在 /backend 中标记。

zmai> /backend

  当前: ● DeepSeek (deepseek-chat)

  可用:
    claude    ❌ API Key 无效 (上次验证: 1 小时前)
              🔧 修复: zmai auth update claude

    deepseek  ✅ 已验证 (当前)

# 不需要等失败时才发现 Key 有问题。
```

### 9.3 配置漂移检测

```python
# 启动时检测配置与默认值的差异。
# 只显示有变更的配置项，不显示全部。

zmai> /config

  非默认配置:
    gateway.default_backend = "deepseek"    (默认: auto)
    runtime.max_iterations = 200            (默认: 100)

  其余 12 项配置使用默认值。
```

---

## 10. 编译原则

### 10.1 一年后还能用的设计

```python
"""
一个每天用 8 小时、用了一年的开发者会：

1. 讨厌等待       → 启动不阻塞、API 等待有进度、异步一切
2. 讨厌重复       → Agent 记住决策、检测结果持久化、历史跨会话
3. 讨厌噪音       → Dashboard 按需显示、进度条紧凑、成功消息多样化
4. 讨厌遗忘       → 任务历史可搜索、换项目上下文恢复、Agent 记得昨天的事
5. 讨厌堆积       → 自动 GC 清理过期 workspace、未使用的配置自动标记

好的 DX 不是第一天觉得好用，而是第三百六十五天仍然觉得好用。
"""
```

### 10.2 修改汇总

```
不新增模块。只优化现有行为。

# 启动 (startup.py)
  - 异步 Dashboard，启动不阻塞输入
  - Dashboard 疲劳防护：首次显示完整版，日常显示一行
  - 后台 Backend 验证，不阻塞启动

# REPL (repl.py + commands.py)
  - 多行输入自动检测（粘贴检测）
  - Tab 补全项目文件路径
  - 历史持久化跨会话（~/.zmai/history, 5000 条）

# 上下文 (context.py + detector.py)
  - 项目检测结果持久化到缓存（30 天 TTL）
  - 按路径哈希隔离不同项目的记忆
  - Agent 启动时自动注入项目上下文

# 输出 (progress.py)
  - 进度条紧凑模式：单行显示当前状态
  - 执行记录不会被进度条冲走
  - API 等待预测基于历史统计

# 记忆 (memory/manager.py 调用端)
  - Agent 技术决策自动记录到 Memory
  - 任务历史可按项目/时间查询
  - 换项目时自动切换记忆上下文

# 清理 (startup.py 中触发)
  - 自动 GC：7 天过期、最多 10 个 Agent
  - 未使用的 Backend 配置标记警告
  - 非默认配置项对比显示

# 配置 (config.py)
  - 后台验证所有已配置 Backend 的 Key 有效性
  - 配置漂移检测：只显示与默认值不同的项
```

### 10.3 不修改

```
src/zmai/runtime/*           ✅ 一行不改
src/zmai/gateway/*           ✅
src/zmai/agent/*             ✅
src/zmai/workspace/*         ✅
src/zmai/memory/*            ✅
src/zmai/workflow/*          ✅
src/zmai/swe/*               ✅
```

---

> **总结：**
>
> 站在每天 8 小时、连续使用一年的软件工程师角度，最大的 5 个痛苦：
>
> | 排名 | 痛苦 | 年耗时 | 修复 |
> |------|------|--------|------|
> | 1 | **Agent 重复发现事实** | 15h+ | 项目检测持久化 + 自动注入 |
> | 2 | **API 等待无进度** | 12h+ | Token 流 + 等待预测 + 状态轮换 |
> | 3 | **启动等待** | 10h+ | 异步启动，不阻塞输入 |
> | 4 | **历史不跨会话** | 4.6h+ | 持久化 `~/.zmai/history` |
> | 5 | **Tab 补全不完整** | 3h+ | 项目文件路径补全 |
>
> 核心原则：**好的 DX 不是第一天觉得好用，而是第三百六十五天仍然觉得好用。**
