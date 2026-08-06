# ZMAI Error Handling Design v1.0

Version: 1.0
Date: 2026-07-16

> **每个异常都转换为用户可理解的信息 + 解决建议。** 支持 Retry / Repair / Report。
>
> 不修改 Runtime / Agent / Gateway / Memory / Workspace / Workflow 核心逻辑。
>
> 仅修改 `src/zmai/errors/` 和 CLI 层的错误捕获展示。

---

## 目录

1. [现状审查](#1-现状审查)
2. [设计原则](#2-设计原则)
3. [错误分类体系](#3-错误分类体系)
4. [错误显示格式](#4-错误显示格式)
5. [错误建议系统](#5-错误建议系统)
6. [Retry 机制](#6-retry-机制)
7. [Repair 机制](#7-repair-机制)
8. [Report 机制](#8-report-机制)
9. [CLI 层错误捕获](#9-cli-层错误捕获)
10. [文件清单与实现计划](#10-文件清单与实现计划)

---

## 1. 现状审查

### 1.1 当前错误类型

`src/zmai/errors/__init__.py` 定义了 8 种异常类型：

```python
ZMAIError       → 基类 (code + message)
 ├─ WorkspaceError  → WORKSPACE_ERROR
 ├─ ConfigError     → CONFIG_ERROR
 ├─ RuntimeError    → RUNTIME_ERROR
 ├─ MemoryError     → MEMORY_ERROR
 ├─ BackendError    → BACKEND_ERROR
 ├─ PluginError     → PLUGIN_ERROR
 ├─ ToolError       → TOOL_ERROR
 └─ AgentError      → AGENT_ERROR
```

### 1.2 当前错误处理

```python
# main.py:413-415 — 顶层错误处理
except Exception as e:
    print_error(str(e))
    sys.exit(1)
```

```python
# main.py:241 — 执行结果错误
err = result.get("error", "")
msg = err if err else f"agent {st}"
print_error(msg, theme)
```

```python
# 各处散落的 except
except Exception:
    pass
```

### 1.3 问题分析

| 问题 | 代码位置 | 影响 |
|------|---------|------|
| **`print_error(str(e))`** | `main.py:414` | 用户看到内部错误消息，非可读信息 |
| **无解决建议** | `main.py:241` | 提示"Backend 调用失败"但不告诉如何修复 |
| **`except: pass`** | 散布各文件 | 错误被静默吞掉 |
| **无 Retry** | 无处实现 | 网络错误需用户重新运行 |
| **无 Repair** | 无处实现 | 配置错误不自动修复 |
| **无 Report** | 无处实现 | 无法收集错误信息用于调试 |
| **`sys.exit(1)`** | `main.py:415` | 所有错误统一退出码，无法区分 |

---

## 2. 设计原则

### 2.1 三 R 原则

```
每个错误必须支持：

Retry   → 这个错误可以重试吗？如果可以，如何重试？
Repair  → 这个错误可以自动修复吗？如果可以，如何修复？
Report  → 这个错误应该报告吗？报告什么信息？
```

### 2.2 用户可见

```
错误消息必须：
  ❌ "BackendError: API HTTP 401"         ← 内部消息，用户不懂
  ✅ "API Key 无效。请更新 API Key。"       ← 用户可理解
  ✅ "命令: zmai auth update deepseek"      ← 同时给出解决命令
```

### 2.3 不吞没

```
不允许裸 except: pass。

每个 except 必须至少：
  1. 记录日志（logging）
  2. 或者显示给用户
  3. 或者重新抛出
```

### 2.4 不修改下游核心逻辑

```
仅修改：
  src/zmai/errors/          ← 扩展错误类
  src/zmai/cli/             ← 改进错误捕获和显示

不修改：
  src/zmai/runtime/         ✗
  src/zmai/gateway/         ✗
  src/zmai/agent/           ✗
  src/zmai/workspace/       ✗
  src/zmai/memory/          ✗
  src/zmai/workflow/        ✗
  src/zmai/swe/             ✗
```

---

## 3. 错误分类体系

### 3.1 错误象限

```
                     可重试
                        │
      ┌─────────────────┼─────────────────┐
      │                 │                 │
      │  网络超时        │   API 429       │
      │  DNS 解析失败    │   速率限制      │
      │  连接被拒绝      │   Token 耗尽    │
      │                 │                 │
      ├─────────────────┼─────────────────┤
      │                 │                 │
      │  配置错误        │   API Key 无效   │
      │  文件不存在      │   权限不足       │
      │  参数格式错      │   模型不存在     │
      │                 │                 │
      └─────────────────┼─────────────────┘
                        │
                    不可重试
```

### 3.2 错误分类

```python
from enum import Enum

class ErrorCategory(str, Enum):
    """错误分类。决定错误如何展示和处理。"""

    # ── 配置类（不可重试，但可修复）
    CONFIG     = "config"      # 配置缺失/错误
    AUTH       = "auth"        # 认证/授权失败
    NOT_FOUND  = "not_found"   # 文件/模块/资源不存在
    FORMAT     = "format"      # 格式错误

    # ── 网络类（可重试）
    NETWORK    = "network"     # 网络连接失败
    TIMEOUT    = "timeout"     # 请求超时
    RATE_LIMIT = "rate_limit"  # 速率限制

    # ── API 类（不可重试）
    API        = "api"         # API 调用失败
    MODEL      = "model"       # 模型返回错误
    TOOL       = "tool"        # 工具执行错误

    # ── 内部类（报告）
    INTERNAL   = "internal"    # 内部错误（bug）
    BUG        = "bug"         # 程序缺陷

    # ── 特性
    @property
    def retriable(self) -> bool:
        return self in (ErrorCategory.NETWORK, ErrorCategory.TIMEOUT,
                        ErrorCategory.RATE_LIMIT)

    @property
    def repairable(self) -> bool:
        return self in (ErrorCategory.CONFIG, ErrorCategory.AUTH,
                        ErrorCategory.NOT_FOUND)
```

### 3.3 错误信息类

```python
@dataclass
class ErrorInfo:
    """标准化的错误信息。"""

    # 基本信息
    message: str               # 用户可读的消息
    category: ErrorCategory    # 错误分类
    code: str = ""             # 错误码（如 "BACKEND_ERROR"）
    detail: str = ""           # 技术细节（可选）

    # 三 R
    retry: RetryInfo | None = None     # 如何重试
    repair: RepairInfo | None = None   # 如何修复
    report: ReportInfo | None = None   # 如何报告

    # 源
    source: str = ""           # 错误来源（模块名）
    timestamp: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "message": self.message,
            "category": self.category.value,
            "code": self.code,
            "detail": self.detail,
            "retry": self.retry.to_dict() if self.retry else None,
            "repair": self.repair.to_dict() if self.repair else None,
            "source": self.source,
        }


@dataclass
class RetryInfo:
    """重试信息。"""
    command: str               # 重试命令
    description: str = ""      # 重试说明
    wait_seconds: int = 0      # 建议等待时间（秒）
    auto_retry: bool = False   # 是否可以自动重试


@dataclass
class RepairInfo:
    """修复信息。"""
    commands: list[str]        # 修复命令列表
    description: str = ""      # 修复说明
    auto_repair: bool = False  # 是否可以自动修复


@dataclass
class ReportInfo:
    """报告信息。"""
    log_file: str = ""         # 日志文件路径
    github_url: str = ""       # GitHub Issue 链接
    debug_command: str = ""    # 调试命令
```

---

## 4. 错误显示格式

### 4.1 标准错误面板

```
  ╭─ ❌ 错误 ───────────────────────────────────────────╮
  │                                                      │
  │  API Key 无效                                         │
  │  当前使用的 DeepSeek API Key 无法通过身份验证。         │
  │                                                      │
  │  🔧 修复:                                            │
  │  $ zmai auth update deepseek                         │
  │                                                      │
  │  ℹ 详情: HTTP 401 Unauthorized                       │
  │                                                      │
  ╰──────────────────────────────────────────────────────╯
```

### 4.2 渲染代码

```python
# cli/errors.py — 错误显示渲染

class ErrorRenderer:
    """错误渲染。将 ErrorInfo 渲染为终端 UI。"""

    def __init__(self, theme: Theme):
        self._theme = theme

    def render(self, error: ErrorInfo) -> str:
        """渲染错误面板。"""
        t = self._theme
        w = _terminal_width() - 4
        lines = [
            f"  ╭─ ❌ {t.error(error.message)} {'─' * (w - len(error.message) - 6)}╮",
            f"  │{' ' * w}│",
        ]

        # 详细信息
        if error.detail:
            for line in self._wrap(error.detail, w - 4):
                lines.append(f"  │  {t.dim(line)}{' ' * (w - len(line) - 2)}│")

        lines.append(f"  │{' ' * w}│")

        # Repair 信息
        if error.repair:
            lines.append(f"  │  {t.success('🔧 修复:')}{' ' * (w - 10)}│")
            for cmd in error.repair.commands:
                lines.append(f"  │  {t.highlight(f'$ {cmd}')}{' ' * (w - len(cmd) - 4)}│")
            lines.append(f"  │{' ' * w}│")

        # Retry 信息
        if error.retry:
            lines.append(f"  │  {t.info('🔄 重试:')}{' ' * (w - 10)}│")
            lines.append(f"  │  {t.highlight(f'$ {error.retry.command}')}"
                         f"{' ' * (w - len(error.retry.command) - 4)}│")
            if error.retry.wait_seconds > 0:
                lines.append(f"  │  {t.dim(f'建议等待 {error.retry.wait_seconds} 秒后重试')}"
                             f"{' ' * (w - 30)}│")
            lines.append(f"  │{' ' * w}│")

        # 技术详情（折叠）
        if error.code:
            lines.append(f"  │  {t.dim(f'错误码: {error.code}')}"
                         f"{' ' * (w - len(error.code) - 8)}│")

        lines.append(f"  ╰{'─' * w}╯")
        return "\n".join(lines)

    @staticmethod
    def _wrap(text: str, width: int) -> list[str]:
        """文本换行。"""
        words = text.split()
        lines = []
        current = ""
        for word in words:
            if len(current) + len(word) + 1 > width:
                lines.append(current)
                current = word
            else:
                current = f"{current} {word}" if current else word
        if current:
            lines.append(current)
        return lines
```

### 4.3 常见错误显示示例

**API Key 无效：**
```
  ╭─ ❌ API Key 无效 ──────────────────────────────────╮
  │                                                      │
  │  DeepSeek API Key 无法通过身份验证。                    │
  │                                                      │
  │  🔧 修复:                                            │
  │  $ zmai auth update deepseek                         │
  │                                                      │
  ╰──────────────────────────────────────────────────────╯
```

**速率限制：**
```
  ╭─ ❌ API 请求过于频繁 ───────────────────────────────╮
  │                                                      │
  │  当前 Backend (DeepSeek) 触发了速率限制。               │
  │                                                      │
  │  🔄 重试:                                            │
  │  $ zmai                                              │
  │  建议等待 30 秒后重试                                 │
  │                                                      │
  │  🔧 修复:                                            │
  │  $ zmai --backend claude                             │
  │  或切换到其他 Backend                                 │
  │                                                      │
  ╰──────────────────────────────────────────────────────╯
```

**网络错误：**
```
  ╭─ ❌ 网络连接失败 ───────────────────────────────────╮
  │                                                      │
  │  无法连接到 api.deepseek.com:443                      │
  │                                                      │
  │  🔄 重试:                                            │
  │  $ zmai                                              │
  │                                                      │
  │  ℹ 请检查网络连接或代理设置                             │
  │                                                      │
  ╰──────────────────────────────────────────────────────╯
```

**配置缺失：**
```
  ╭─ ❌ 未配置 Backend ─────────────────────────────────╮
  │                                                      │
  │  未找到可用的 API Key。ZMAI 需要至少一个 Backend。      │
  │                                                      │
  │  🔧 修复:                                            │
  │  $ zmai auth                                         │
  │                                                      │
  ╰──────────────────────────────────────────────────────╯
```

---

## 5. 错误建议系统

### 5.1 建议注册

```python
# cli/errors.py

class SuggestionRegistry:
    """错误建议注册。将异常/错误码映射到修复建议。"""

    _registry: dict[str, ErrorInfo] = {}

    @classmethod
    def register(cls, error_cls: type, info: ErrorInfo) -> None:
        """注册错误类对应的建议信息。"""
        cls._registry[error_cls.__name__] = info

    @classmethod
    def get(cls, error: Exception) -> ErrorInfo | None:
        """获取异常对应的建议信息。"""
        # 精确匹配
        info = cls._registry.get(type(error).__name__)
        if info:
            return info

        # 父类匹配
        for cls_name, info in cls._registry.items():
            try:
                if isinstance(error, eval(cls_name)):
                    return info
            except NameError:
                continue

        return None


# 注册常见错误
SuggestionRegistry.register(BackendError, ErrorInfo(
    message="Backend 调用失败",
    category=ErrorCategory.API,
    repair=RepairInfo(
        commands=["zmai auth list", "zmai auth verify"],
        description="检查 Backend 配置和凭证是否有效",
    ),
))

SuggestionRegistry.register(ConfigError, ErrorInfo(
    message="配置错误",
    category=ErrorCategory.CONFIG,
    repair=RepairInfo(
        commands=["zmai config list"],
        description="检查配置设置",
    ),
))

SuggestionRegistry.register(WorkspaceError, ErrorInfo(
    message="Workspace 操作失败",
    category=ErrorCategory.NOT_FOUND,
    repair=RepairInfo(
        commands=["zmai clean", "zmai status"],
        description="清理 Workspace 后重试",
    ),
))
```

### 5.2 HTTP 状态码建议

```python
HTTP_STATUS_SUGGESTIONS: dict[int, ErrorInfo] = {
    401: ErrorInfo(
        message="API Key 无效",
        category=ErrorCategory.AUTH,
        detail="当前使用的 API Key 无法通过身份验证。",
        repair=RepairInfo(
            commands=["zmai auth update <backend>"],
            description="更新 API Key",
        ),
    ),
    403: ErrorInfo(
        message="无访问权限",
        category=ErrorCategory.AUTH,
        detail="当前账号没有访问该模型的权限。",
        repair=RepairInfo(
            commands=["zmai --backend <other>"],
            description="切换到其他 Backend 或检查账号权限",
        ),
    ),
    429: ErrorInfo(
        message="API 请求过于频繁",
        category=ErrorCategory.RATE_LIMIT,
        detail="触发了 API 速率限制。",
        retry=RetryInfo(
            command="zmai",
            wait_seconds=30,
            description="等待 30 秒后重试",
        ),
        repair=RepairInfo(
            commands=["zmai --backend <other>"],
            description="切换到其他 Backend",
        ),
    ),
    500: ErrorInfo(
        message="API 服务暂时不可用",
        category=ErrorCategory.API,
        detail="LLM 服务端返回 500 错误，通常是临时问题。",
        retry=RetryInfo(
            command="zmai",
            wait_seconds=10,
            description="等待 10 秒后重试",
            auto_retry=True,
        ),
    ),
    502: ErrorInfo(
        message="API 网关错误",
        category=ErrorCategory.NETWORK,
        detail="LLM 服务端返回 502 错误，通常是临时问题。",
        retry=RetryInfo(
            command="zmai",
            wait_seconds=15,
            description="等待 15 秒后重试",
            auto_retry=True,
        ),
    ),
    503: ErrorInfo(
        message="API 服务暂时不可用",
        category=ErrorCategory.NETWORK,
        detail="LLM 服务端返回 503 错误，服务正在维护。",
        retry=RetryInfo(
            command="zmai",
            wait_seconds=30,
            description="等待 30 秒后重试",
        ),
    ),
}
```

### 5.3 从异常到 ErrorInfo

```python
def classify_error(error: Exception) -> ErrorInfo:
    """将任意异常转换为 ErrorInfo。"""

    # 1. 精确匹配
    info = SuggestionRegistry.get(error)
    if info:
        return info

    # 2. 检查具体异常类型
    if isinstance(error, BackendError):
        # 检查 HTTP 状态码
        status = getattr(error, "status_code", None)
        if status:
            info = HTTP_STATUS_SUGGESTIONS.get(status)
            if info:
                return info
        return ErrorInfo(
            message="Backend 调用失败",
            category=ErrorCategory.API,
            detail=str(error),
            retry=RetryInfo(command="zmai", description="重试"),
        )

    if isinstance(error, ConfigError):
        return ErrorInfo(
            message="配置错误",
            category=ErrorCategory.CONFIG,
            detail=str(error),
            repair=RepairInfo(
                commands=[f"zmai config set {error.key} <value>"],
                description="检查配置项",
            ),
        )

    if isinstance(error, WorkspaceError):
        return ErrorInfo(
            message="Workspace 操作失败",
            category=ErrorCategory.NOT_FOUND,
            detail=str(error),
            repair=RepairInfo(
                commands=["zmai clean"],
                description="清理 Workspace 后重试",
            ),
        )

    if isinstance(error, MemoryError):
        return ErrorInfo(
            message="Memory 操作失败",
            category=ErrorCategory.INTERNAL,
            detail=str(error),
            report=ReportInfo(debug_command="zmai doctor"),
        )

    if isinstance(error, ToolError):
        return ErrorInfo(
            message="工具执行失败",
            category=ErrorCategory.TOOL,
            detail=str(error),
            retry=RetryInfo(command="zmai", description="重试"),
        )

    if isinstance(error, AgentError):
        return ErrorInfo(
            message="Agent 执行失败",
            category=ErrorCategory.INTERNAL,
            detail=str(error),
        )

    if isinstance(error, ConnectionError):
        return ErrorInfo(
            message="网络连接失败",
            category=ErrorCategory.NETWORK,
            detail=str(error),
            retry=RetryInfo(
                command="zmai",
                wait_seconds=5,
                description="检查网络连接后重试",
            ),
        )

    if isinstance(error, TimeoutError):
        return ErrorInfo(
            message="请求超时",
            category=ErrorCategory.TIMEOUT,
            detail=str(error),
            retry=RetryInfo(
                command="zmai",
                wait_seconds=10,
                auto_retry=True,
                description="自动重试",
            ),
        )

    if isinstance(error, PermissionError):
        return ErrorInfo(
            message="权限不足",
            category=ErrorCategory.CONFIG,
            detail=str(error),
            repair=RepairInfo(
                commands=["zmai doctor"],
                description="检查权限设置",
            ),
        )

    if isinstance(error, FileNotFoundError):
        return ErrorInfo(
            message="文件不存在",
            category=ErrorCategory.NOT_FOUND,
            detail=str(error),
        )

    if isinstance(error, json.JSONDecodeError):
        return ErrorInfo(
            message="数据格式错误",
            category=ErrorCategory.FORMAT,
            detail=str(error),
        )

    # 3. 兜底：未知错误 → 报告
    return ErrorInfo(
        message="未知错误",
        category=ErrorCategory.BUG,
        detail=str(error),
        code=type(error).__name__,
        report=ReportInfo(
            debug_command="zmai doctor --verbose",
        ),
    )
```

---

## 6. Retry 机制

### 6.1 自动重试

```python
class RetryHandler:
    """自动重试处理器。"""

    MAX_RETRIES = 3
    BASE_DELAY = 2.0  # 秒

    def __init__(self, theme: Theme):
        self._theme = theme
        self._attempt = 0

    def should_retry(self, error: ErrorInfo) -> bool:
        """判断是否应该自动重试。"""
        if not error.category.retriable:
            return False
        if error.retry and not error.retry.auto_retry:
            return False
        return self._attempt < self.MAX_RETRIES

    async def execute(self, fn, *args, **kwargs):
        """执行函数，失败时自动重试。"""
        while True:
            try:
                self._attempt += 1
                return await fn(*args, **kwargs)
            except Exception as e:
                error = classify_error(e)
                if not self.should_retry(error):
                    raise

                delay = self.BASE_DELAY * (2 ** (self._attempt - 1))
                sys.stderr.write(
                    f"\r  🔄 重试 ({self._attempt}/{self.MAX_RETRIES}) "
                    f"等待 {delay:.0f}s...\033[K"
                )
                await asyncio.sleep(delay)

    def user_retry_prompt(self, error: ErrorInfo) -> bool:
        """询问用户是否重试。"""
        if not error.retry:
            return False
        try:
            ans = input(f"\n  🔄 重试？ [Y/n]: ").strip().lower()
            return ans in ("", "y", "yes")
        except (EOFError, KeyboardInterrupt):
            return False
```

### 6.2 用户选择重试

```
  ╭─ ❌ API 请求过于频繁 ───────────────────────────────╮
  │                                                      │
  │  触发了 DeepSeek 的速率限制 (429)。                    │
  │                                                      │
  │  🔄 重试？ [Y/n]: _                                   │
  │                                                      │
  │  输入 Y → 30 秒后自动重试                              │
  │  输入 n → 显示修复建议                                 │
  │  按 Ctrl+C → 取消                                     │
  │                                                      │
  ╰──────────────────────────────────────────────────────╯
```

### 6.3 各场景重试策略

| 场景 | 自动重试 | 最大次数 | 延迟策略 |
|------|---------|---------|---------|
| 网络超时 (timeout) | ✅ 是 | 3 | 指数退避 (2s, 4s, 8s) |
| 速率限制 (429) | ❌ 否 | — | 用户确认后 30s |
| 服务不可用 (502/503) | ✅ 是 | 2 | 固定 10s |
| API 内部错误 (500) | ✅ 是 | 2 | 固定 5s |
| DNS 解析失败 | ❌ 否 | — | 用户确认后 5s |
| API Key 无效 (401) | ❌ 否 | — | 不重试 |

---

## 7. Repair 机制

### 7.1 自动修复

```python
class RepairHandler:
    """自动修复处理器。"""

    def __init__(self, theme: Theme):
        self._theme = theme

    def auto_repair(self, error: ErrorInfo) -> bool:
        """尝试自动修复。返回 True 表示修复成功。"""
        if not error.repair or not error.repair.auto_repair:
            return False

        for cmd in error.repair.commands:
            if self._try_repair(cmd):
                return True
        return False

    def _try_repair(self, command: str) -> bool:
        """尝试执行一条修复命令。"""
        try:
            sys.stderr.write(f"  🔧 正在修复: {command}\033[K\n")
            result = subprocess.run(
                command.split(), capture_output=True, text=True, timeout=30,
            )
            if result.returncode == 0:
                sys.stderr.write(f"  ✅ 修复成功\n")
                return True
            return False
        except Exception:
            return False

    def show_repair_instructions(self, error: ErrorInfo) -> None:
        """显示修复指令。"""
        if not error.repair:
            return
        t = self._theme
        sys.stderr.write(f"\n  {t.success('🔧 修复:')}\n")
        for cmd in error.repair.commands:
            sys.stderr.write(f"  {t.highlight(f'$ {cmd}')}\n")
        if error.repair.description:
            sys.stderr.write(f"  {t.dim(error.repair.description)}\n")
```

### 7.2 常见自动修复场景

| 场景 | 检测条件 | 修复行为 |
|------|---------|---------|
| Workspace 状态文件损坏 | `state.json` 解析失败 | 备份旧文件，创建新文件 |
| ~/.zmai 目录丢失 | 目录不存在 | 自动创建目录结构 |
| 缓存目录过大 | >500MB | 自动清理过期缓存 |
| 配置格式错误 | `config.json` 解析失败 | 重置为默认配置 |
| Memory 索引损坏 | `index.json` 解析失败 | 重建索引 |

### 7.3 修复编排

```python
async def orchestrate_repair(error: ErrorInfo) -> bool:
    """编排修复流程。"""
    renderer = ErrorRenderer(Theme.dark())

    # 1. 显示错误
    sys.stderr.write(renderer.render(error))

    # 2. 尝试自动修复
    repair = RepairHandler(Theme.dark())
    if repair.auto_repair(error):
        sys.stderr.write(f"  ✅ 已自动修复\n")
        return True

    # 3. 询问用户是否手动修复
    if error.repair and error.repair.commands:
        try:
            ans = input(f"\n  🔧 应用修复？ [Y/n]: ").strip().lower()
            if ans in ("", "y", "yes"):
                for cmd in error.repair.commands:
                    subprocess.run(cmd.split())
                return True
        except (EOFError, KeyboardInterrupt):
            pass

    return False
```

---

## 8. Report 机制

### 8.1 错误报告格式

```python
@dataclass
class ErrorReport:
    """结构化错误报告。"""

    # 错误信息
    error_code: str
    error_type: str
    message: str
    detail: str

    # 上下文（不包含敏感信息）
    version: str = ""
    platform: str = ""
    python_version: str = ""
    backend: str = ""
    timestamp: str = ""

    # 诊断
    log_snippet: str = ""       # 最近 20 行日志
    config_snapshot: str = ""   # 配置摘要（过滤敏感字段）

    def to_dict(self) -> dict[str, Any]:
        return {
            "error_code": self.error_code,
            "error_type": self.error_type,
            "message": self.message,
            "detail": self.detail,
            "version": self.version,
            "platform": self.platform,
            "python_version": self.python_version,
            "backend": self.backend,
            "timestamp": self.timestamp,
        }

    def save(self, path: Path) -> None:
        """保存错误报告到文件。"""
        path.write_text(
            json.dumps(self.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
```

### 8.2 报告收集

```python
class ErrorReporter:
    """错误报告收集器。"""

    REPORT_DIR = Path.home() / ".zmai" / "error_reports"

    def __init__(self):
        self.REPORT_DIR.mkdir(parents=True, exist_ok=True)

    def collect(self, error: Exception, context: dict[str, Any] | None = None) -> ErrorReport:
        """收集错误上下文，生成报告。"""
        import platform

        error_info = classify_error(error)

        report = ErrorReport(
            error_code=error_info.code,
            error_type=type(error).__name__,
            message=error_info.message,
            detail=error_info.detail or str(error),
            version="v0.1.0",
            platform=sys.platform,
            python_version=sys.version,
            backend=os.environ.get("ZMAI_BACKEND", ""),
            timestamp=_now_iso(),
        )

        return report

    def save(self, report: ErrorReport) -> Path:
        """保存报告到文件。"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"error_{timestamp}_{report.error_code.lower()}.json"
        path = self.REPORT_DIR / filename
        report.save(path)
        return path

    def show_diagnostics(self, report: ErrorReport) -> None:
        """显示诊断信息。"""
        t = Theme.dark()
        lines = [
            f"\n  {t.info('📋 诊断信息')}",
            f"  {t.dim('─' * 40)}",
            f"  错误类型: {report.error_type}",
            f"  错误代码: {report.error_code}",
            f"  平台:     {report.platform}",
            f"  Python:   {report.python_version.split()[0]}",
            f"  版本:     {report.version}",
            f"",
            f"  报告已保存: {report.filepath}",
        ]
        sys.stderr.write("\n".join(lines))
```

### 8.3 崩溃回退

当程序意外崩溃时，自动保存现场：

```python
def crash_guard():
    """全局崩溃保护。捕获未处理异常。"""

    def excepthook(exc_type, exc_value, exc_tb):
        """未处理异常处理器。"""
        if issubclass(exc_type, KeyboardInterrupt):
            # Ctrl+C 不捕获
            return

        # 收集报告
        reporter = ErrorReporter()
        report = reporter.collect(exc_value)

        # 保存
        path = reporter.save(report)

        # 显示
        renderer = ErrorRenderer(Theme.dark())
        error_info = classify_error(exc_value)
        sys.stderr.write(renderer.render(error_info))
        sys.stderr.write(f"\n  📄 错误报告已保存: {path}\n")
        sys.stderr.write(f"  请提交 Issue: https://github.com/zmai/zmai/issues/new\n")

    sys.excepthook = excepthook
```

---

## 9. CLI 层错误捕获

### 9.1 错误捕获架构

```
用户操作
    │
    ▼
try:
    zmai.run()
    │
except Exception as e:
    │
    ├── classify_error(e) → ErrorInfo
    │
    ├── retry = RetryHandler()
    │   ├── should_retry? → 自动重试
    │   └── user_wants_retry? → 用户选择重试
    │
    ├── repair = RepairHandler()
    │   ├── auto_repair? → 自动修复
    │   └── show_repair_instructions() → 显示修复命令
    │
    ├── render = ErrorRenderer()
    │   └── render(error_info) → 显示错误面板
    │
    └── report = ErrorReporter()
        ├── collect(e) → ErrorReport
        └── save(report) → 保存到文件
```

### 9.2 main.py 改进

```python
# main.py — 错误处理改进

def main(argv=None):
    argv = argv or sys.argv[1:]

    # 注册崩溃保护
    crash_guard()

    try:
        # ... 正常逻辑 ...

        if task:
            # 单次执行
            try:
                result = _cmd_run(task, runtime, config, args)
            except Exception as e:
                _handle_error(e)
                sys.exit(1)
        else:
            # REPL
            try:
                _cmd_interactive(runtime, config, args)
            except Exception as e:
                _handle_error(e)
                # 不 exit，回到 REPL
                sys.stderr.write("\n  ⚠ 错误已处理，继续会话\n")

    except Exception as e:
        _handle_error(e)
        sys.exit(1)


def _handle_error(error: Exception) -> None:
    """统一错误处理。"""
    theme = Theme.dark()
    renderer = ErrorRenderer(theme)

    # 1. 分类
    error_info = classify_error(error)

    # 2. 尝试自动重试（如果上下文允许）
    retry = RetryHandler(theme)
    if retry.should_retry(error_info):
        sys.stderr.write(renderer.render(error_info))
        if retry.user_retry_prompt(error_info):
            return  # 调用者重试

    # 3. 尝试自动修复
    repair = RepairHandler(theme)
    if repair.auto_repair(error_info):
        return

    # 4. 显示错误面板
    sys.stderr.write(renderer.render(error_info))

    # 5. 显示修复指令
    repair.show_repair_instructions(error_info)

    # 6. 收集报告（内部错误）
    if error_info.category == ErrorCategory.BUG:
        reporter = ErrorReporter()
        report = reporter.collect(error)
        path = reporter.save(report)
        sys.stderr.write(f"\n  📄 错误报告: {path}\n")
```

### 9.3 错误处理路径汇总

| 场景 | 显示 | Retry | Repair | Report | 退出码 |
|------|------|-------|--------|--------|--------|
| API Key 无效 | 错误面板 | ❌ | `zmai auth update` | ❌ | 3 |
| 速率限制 429 | 错误面板 | ✅ 30s 后重试 | 切换 Backend | ❌ | 3 |
| 网络超时 | 错误面板 | ✅ 自动重试 3 次 | 检查网络 | ❌ | 3 |
| 配置错误 | 错误面板 | ❌ | `zmai config set` | ❌ | 2 |
| 文件不存在 | 错误面板 | ❌ | 重新指定路径 | ❌ | 2 |
| Workspace 损坏 | 错误面板 | ❌ | `zmai clean` | ✅ 报告 | 4 |
| 未知错误 | 错误面板 | ❌ | ❌ | ✅ 报告 | 9 |
| Agent 执行失败 | 结果显示 | ❌ | ❌ | ❌ | 3 |
| 用户 Ctrl+C | 暂停提示 | ❌ | ❌ | ❌ | 0 |

### 9.4 退出码规范

```python
class ExitCode:
    """统一退出码。"""

    SUCCESS         = 0   # 正常完成
    GENERAL_ERROR   = 1   # 通用错误
    CONFIG_ERROR    = 2   # 配置错误
    EXECUTION_ERROR = 3   # 执行错误
    WORKSPACE_ERROR = 4   # Workspace 错误
    AUTH_ERROR      = 5   # 认证错误
    TIMEOUT         = 6   # 超时
    NETWORK_ERROR   = 7   # 网络错误
    INTERNAL_ERROR  = 9   # 内部错误
```

---

## 10. 文件清单与实现计划

### 10.1 新增文件

```
src/zmai/cli/
└── error_handler.py         # 🔴 新增 — 错误处理（~350 行）
    ├── ErrorCategory        # 错误分类枚举
    ├── ErrorInfo             # 标准化错误信息
    ├── RetryInfo / RepairInfo / ReportInfo
    ├── classify_error()     # 异常 → ErrorInfo 转换
    ├── ErrorRenderer        # 错误面板渲染
    ├── SuggestionRegistry   # 建议注册
    ├── RetryHandler         # 重试处理器
    ├── RepairHandler        # 修复处理器
    ├── ErrorReporter        # 报告收集器
    └── crash_guard()        # 崩溃保护
```

### 10.2 修改文件

```
src/zmai/cli/main.py         # 🔧 修改 — 统一错误处理 _handle_error
src/zmai/errors/__init__.py  # 🔧 修改 — 添加 status_code 到 BackendError
```

### 10.3 不变文件

```
src/zmai/runtime/*             ✅ 不变（异常的抛出不修改）
src/zmai/gateway/*             ✅ 不变（BackendError 等继续抛出）
src/zmai/agent/*               ✅ 不变
src/zmai/workspace/*           ✅ 不变
src/zmai/memory/*              ✅ 不变
src/zmai/workflow/*            ✅ 不变
src/zmai/cli/formatters.py     ✅ 不变
src/zmai/cli/detector.py       ✅ 不变
```

### 10.4 代码量变化

```
新增:
  cli/error_handler.py     ~350 行
  总计                     ~350 行

修改:
  errors/__init__.py       ~5 行（BackendError 添加 status_code）
  cli/main.py              ~30 行（_handle_error + 集成）
  总计                     ~35 行

不变:                     ~3000+ 行
```

### 10.5 实现优先级

```
P0 — 错误分类 + 显示（1 天）
├── ErrorCategory 枚举
├── ErrorInfo / RetryInfo / RepairInfo / ReportInfo
├── classify_error() 转换函数（覆盖所有已知异常类型）
├── ErrorRenderer 错误面板渲染
└── main.py 集成 _handle_error

P1 — Retry + Repair（1 天）
├── RetryHandler 自动重试 + 指数退避
├── RepairHandler 自动修复 + 修复指令显示
├── SuggestionRegistry 建议注册
└── HTTP 状态码建议映射

P2 — Report（0.5 天）
├── ErrorReporter 报告收集
├── crash_guard 崩溃保护
├── 退出码规范
└── 诊断信息输出
```

---

> **总结：**
>
> ZMAI Error Handling v1.0 将当前 `print_error(str(e))` 的简单处理升级为完整的 Retry / Repair / Report 系统：
>
> **三 R 原则** — 每个错误分类后自动判断 Retry（可重试？）、Repair（可修复？）、Report（需报告？）
>
> **错误分类** — 11 种分类：CONFIG / AUTH / NOT_FOUND / FORMAT / NETWORK / TIMEOUT / RATE_LIMIT / API / MODEL / TOOL / INTERNAL / BUG
>
> **错误面板** — 带框线的彩色面板：错误消息 + 详情 + 修复命令 + 重试选项
>
> **Retry** — 自动重试（指数退避，网络超时/服务不可用）+ 用户确认重试（速率限制）
>
> **Repair** — 自动修复（缓存清理/目录重建）+ 修复命令显示（`zmai auth update` / `zmai config set`）
>
> **Report** — 结构化报告收集（含版本/平台/错误栈）+ 保存到 `~/.zmai/error_reports/` + 崩溃保护
>
> **退出码规范** — 6 种退出码区分错误类型
>
> **所有修改仅在 CLI 层和 errors 层。** Runtime / Gateway / Agent 等继续抛出标准异常，由 CLI 层统一捕获、分类、渲染。
