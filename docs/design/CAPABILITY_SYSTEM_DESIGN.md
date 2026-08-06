# ZMAI Agent Capability System 设计文档

> 版本: 1.0
> 日期: 2026-07-21
> 状态: 设计稿（未实现）

---

## 目录

1. [动机与目标](#1-动机与目标)
2. [当前架构分析](#2-当前架构分析)
3. [能力系统设计](#3-能力系统设计)
   - 3.1 核心概念
   - 3.2 能力声明
   - 3.3 Agent 能力映射
   - 3.4 任务需求分析
   - 3.5 能力差距检测
   - 3.6 缺失处理策略
4. [当前能力清单](#4-当前能力清单)
5. [当前工具清单](#5-当前工具清单)
6. [Agent 能力映射表](#6-agent-能力映射表)
7. [未来扩展方案](#7-未来扩展方案)
8. [附录](#8-附录)

---

## 1. 动机与目标

### 现状问题

ZMAI 当前可以执行多种任务（读文件、写文件、编辑、搜索、执行命令、Git 操作等），但存在以下问题：

1. **能力不透明** — Agent 不声明自己能做什么，用户/系统无法预判任务能否完成。
2. **任务不分析** — 任务到达后直接进入执行循环，没有前置的需求-能力匹配环节。
3. **失败难诊断** — Agent 能力不足时不会提前告知，而是在执行循环中耗尽步骤或抛出难以理解的错误。
4. **扩展无框架** — 新增工具或 Agent 类型时，缺乏统一的"能力声明"规范。

### 设计目标

```
明确声明能力 → 分析任务需求 → 匹配能力 → 执行或报缺
```

1. **能力声明**：所有 Agent 和 Tool 必须声明其所提供的能力。
2. **任务分析**：执行前分析任务所需能力。
3. **能力匹配**：判断当前 Agent 是否拥有所需能力。
4. **优雅回退**：能力不足时：
   - 不假装能完成。
   - 不无限循环。
   - 明确告知缺少什么能力。
   - 给出可行替代方案。

### 约束

- **不修改当前 Runtime 核心逻辑**（Runtime.run()、SWEAgent.step() 等现有流程保持不变）。
- 能力系统作为**可插拔的前置分析层**加入。

---

## 2. 当前架构分析

### 2.1 现有组件关系

```
┌─────────────────────────────────────────────────────────────────┐
│                         CLI (main.py)                            │
└──────────────────────────┬──────────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────────┐
│                     Runtime (runtime.py)                         │
│                                                                  │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────────────────┐  │
│  │ Lifecycle   │  │ StateManager │  │ Scheduler              │  │
│  │ Manager     │  │              │  │ (并发控制)              │  │
│  └─────────────┘  └──────────────┘  └────────────────────────┘  │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │              SWEAgent (swe/agent.py)                      │   │
│  │   initialize → step（循环）→ finalize                       │   │
│  └────────────────────┬─────────────────────────────────────┘   │
│                       │                                         │
│  ┌────────────────────▼─────────────────────────────────────┐   │
│  │            ToolRouter (gateway/tool_router.py)             │   │
│  │                 路由 ToolCall → Tool                       │   │
│  └────────────────────┬─────────────────────────────────────┘   │
│                       │                                         │
│  ┌────────────────────▼─────────────────────────────────────┐   │
│  │            ToolRegistry (tool/registry.py)                 │   │
│  │          注册 / 发现 / 执行 Tool                            │   │
│  └────────────────────┬─────────────────────────────────────┘   │
│                       │                                         │
│  ┌────────────────────▼─────────────────────────────────────┐   │
│  │    Tool 实例 (swe/tools.py)                                │   │
│  │  read_file / write_file / edit / grep / shell / git /...   │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                  │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────────────────┐  │
│  │  Workspace  │  │    Memory    │  │   Backend Gateway       │  │
│  │  (沙箱)     │  │    Manager   │  │   (Claude/DeepSeek/Gemini)│
│  └─────────────┘  └──────────────┘  └────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

### 2.2 关键发现

| 组件 | 当前能力意识 | 说明 |
|------|-------------|------|
| `Agent` (ABC) | ❌ 无 | 基类只有 `initialize/step/finalize`，没有能力相关方法 |
| `SWEAgent` | ❌ 隐式 | 通过注册的 8 个工具隐式拥有能力，但不声明 |
| `Tool` (ABC) | ❌ 无 | 只有 name/description/parameters，无 capability 分类 |
| `ToolRegistry` | ❌ 无 | 纯注册/查找/执行，不关心能力语义 |
| `ToolRouter` | ❌ 无 | 纯路由，无能力检查 |
| `Backend` (ABC) | ✅ 有 | `BackendCapability` 枚举 (STREAMING/TOOL_USE/SYSTEM_PROMPT/MULTI_TURN/VISION/STRUCTURED_OUTPUT) |
| `Runtime` | ❌ 无 | 启动即执行，无前置能力分析 |

### 2.3 现有可复用模式

**BackendCapability** 提供了一种可复用的能力声明模式：

```python
class BackendCapability(Enum):
    STREAMING = "streaming"
    TOOL_USE = "tool_use"
    # ...

class Backend(ABC):
    @property
    @abstractmethod
    def capabilities(self) -> set[BackendCapability]:
        ...

    def supports(self, capability: BackendCapability) -> bool:
        return capability in self.capabilities
```

Agent Capability System 应**沿用此模式**，保持一致性。

---

## 3. 能力系统设计

### 3.1 核心概念

```
┌─────────────────────────────────────────────────────────────────────────┐
│                      Capability System 概念模型                           │
│                                                                         │
│  ┌────────────┐     ┌──────────┐     ┌───────────┐     ┌──────────┐   │
│  │ Capability │────▶│  Agent   │────▶│   Tool    │────▶│  Backend  │   │
│  │  (能力)    │     │  (代理)  │     │  (工具)   │     │  (后端)   │   │
│  └────────────┘     └──────────┘     └───────────┘     └──────────┘   │
│        │                                                               │
│        ├── 原子能力 (不可拆)                                            │
│        ├── 复合能力 (由多个原子能力组成)                                  │
│        └── 层级关系 (parent / child)                                    │
│                                                                         │
│  ┌──────────────────────────────────────────────┐                      │
│  │            CapabilityAnalyzer                 │                      │
│  │  分析任务 → 提取所需能力 → 匹配 Agent → 报告缺口 │                      │
│  └──────────────────────────────────────────────┘                      │
└─────────────────────────────────────────────────────────────────────────┘
```

### 3.2 能力声明

#### 能力分类体系

```
CAPABILITY_CATEGORIES
├── file_operation    # 文件操作
│   ├── file_read     # 读取文件
│   ├── file_write    # 写入文件
│   ├── file_edit     # 编辑文件
│   └── file_search   # 文件搜索
├── code_operation    # 代码操作
│   ├── code_search   # 代码搜索 (grep)
│   ├── code_modify   # 代码修改
│   └── code_review   # 代码审查
├── execution         # 命令执行
│   ├── shell         # Shell 执行
│   ├── git           # Git 操作
│   └── build_test    # 构建/测试
├── delivery          # 交付
│   ├── show_output   # 终端展示
│   ├── open_browser  # 浏览器打开
│   └── file_output   # 文件输出
├── communication     # 通信
│   ├── http_request  # HTTP 请求
│   ├── email         # 邮件
│   └── api_call      # API 调用
├── data_processing   # 数据处理
│   ├── db_query      # 数据库查询
│   ├── data_format   # 格式转换
│   └── data_analyze  # 数据分析
├── ai_capability     # AI 能力
│   ├── llm_chat      # 对话
│   ├── llm_stream    # 流式输出
│   ├── llm_tool_use  # 工具使用
│   ├── llm_vision    # 图像识别
│   └── llm_structured # 结构化输出
└── workflow          # 工作流
    ├── plan          # 任务规划
    ├── execute       # 任务执行
    └── verify        # 结果验证
```

#### Capability 数据结构

```python
@dataclass(frozen=True)
class Capability:
    """能力声明。"""
    name: str                             # 唯一标识（如 "file_read"）
    description: str                      # 人类可读描述
    category: str                         # 所属类别（如 "file_operation"）
    requires_tools: list[str]             # 需要哪些工具
    requires_backend: list[str] | None    # 需要 Backend 的哪些能力
    dependencies: list[str] | None        # 依赖的其他能力
    level: str = "atomic"                 # "atomic" | "composite"
```

#### 实现位置

建议新建文件：`src/zmai/capability/__init__.py` 和 `src/zmai/capability/registry.py`，不侵入现有核心模块。

### 3.3 Agent 能力映射

#### Agent 扩展

在 Agent 基类增加可选能力声明接口，不破坏现有抽象方法：

```python
class Agent(ABC):
    # ... 现有方法不变 ...

    # ── 新增：能力声明（可选覆写） ────────

    @property
    def capabilities(self) -> set[str]:
        """返回此 Agent 提供的能力名称集合。
        
        默认实现：遍历已注册的工具，收集其关联的能力。
        子类可覆写声明额外能力。
        """
        return set()

    @property
    def capability_details(self) -> list[Capability]:
        """返回详细能力列表（含描述）。"""
        return []

    def has_capability(self, name: str) -> bool:
        """检查是否拥有指定能力。"""
        return name in self.capabilities
```

#### SWEAgent 能力映射

当前 SWEAgent 通过注册 8 个工具隐式拥有的能力：

| 工具 | 提供的能力 |
|------|-----------|
| `ReadFileTool` | `file_read` |
| `WriteFileTool` | `file_write` |
| `EditTool` | `file_edit` |
| `GrepTool` | `file_search`, `code_search` |
| `ShellTool` | `shell`（含间接的 `git`, `build_test`, `http_request` 等） |
| `GitTool` | `git` |
| `ShowToUserTool` | `show_output` |
| `OpenInBrowserTool` | `open_browser` |

SWEAgent 不拥有的能力案例：
- `db_query` — 没有数据库工具
- `llm_vision` — 没有图像分析能力
- `email` — 没有邮件能力
- `http_request` — 没有 HTTP 客户端工具（仅通过 ShellTool 间接 curl）

### 3.4 任务需求分析

#### CapabilityAnalyzer

```python
class CapabilityAnalyzer:
    """任务需求分析器。
    
    分析任务描述，推理出所需能力集合。
    支持多种分析模式。
    """

    def analyze(self, task: str) -> TaskRequirement:
        """分析任务，返回需求描述。
        
        Args:
            task: 自然语言任务描述。
        
        Returns:
            TaskRequirement 结构化需求。
        """
        ...

    def estimate_required_capabilities(
        self, task: str
    ) -> list[tuple[str, float]]:
        """估算所需能力及置信度。
        
        Returns:
            [(能力名, 置信度0~1), ...]
        """
        ...


@dataclass
class TaskRequirement:
    """任务需求结构化描述。"""
    task: str
    required_capabilities: list[tuple[str, float]]  # (能力名, 置信度)
    estimated_difficulty: str  # "easy" | "medium" | "hard"
    estimated_steps: int
    suggested_agent_type: str | None
    risk_flags: list[str]
```

#### 分析策略（多种可选）

**策略 A：关键词规则匹配**（轻量，零成本）
- 建立关键词 → 能力映射表
- 例如 `"read.*file" → file_read`, `"修改.*代码" → code_modify`
- 可维护，可扩展

**策略 B：LLM 驱动的分析**（准确，需一次 API 调用）
- 使用轻量 Prompt 让 LLM 分析任务
- 输出结构化能力需求

**策略 C：混合模式**
- 先用规则快速判断
- 复杂任务或规则匹配不到时 fallback 到 LLM

> **推荐：** 初始实现使用策略 A（关键词规则），预留策略 B 接口。

### 3.5 能力差距检测

#### GapAnalysis 结果

```python
@dataclass
class CapabilityGap:
    """单个能力差距。"""
    capability: str
    severity: str              # "missing" | "partial" | "limited"
    description: str
    affected_tools: list[str]
    suggestion: str | None     # 替代方案

@dataclass
class GapAnalysisResult:
    task: str
    agent_id: str
    agent_type: str
    required: list[tuple[str, float]]
    available: set[str]
    gaps: list[CapabilityGap]
    has_gap: bool              # True = 能力不足
    has_critical_gap: bool     # True = 存在关键缺口
    summary: str               # 人类可读摘要
```

#### 差距等级

| 等级 | 含义 | 处理方式 |
|------|------|---------|
| `missing` | 完全没有 | 阻止执行，提供替代方案 |
| `partial` | 有但功能受限 | 继续执行但提示用户注意限制 |
| `limited` | 通过间接方式实现 | 继续执行并告知用户使用方式 |

### 3.6 缺失处理策略

当能力缺失时，按以下优先级处理：

```
┌──────────────────────────────────────────────────┐
│               CapabilityGapDetected               │
└──────────────────┬───────────────────────────────┘
                   │
          ┌────────▼────────┐
          │  严重缺失?       │
          │  (critical)      │
          └────────┬────────┘
                   │
      ┌────────────┴────────────┐
      ▼                         ▼
  [阻止执行]                 [继续执行]
  │                          │
  ├─ 1. 显示友好错误          ├─ 1. 标记限制
  ├─ 2. 列出缺失能力          ├─ 2. 提示用户
  ├─ 3. 给出替代方案           ├─ 3. 提供替代工具
  │    - 建议安装插件         │
  │    - 建议更换 Agent       │
  │    - 建议手动完成部分      │
  └──────────────────         └──────────────────
```

#### 用户交互示例

```
╔══════════════════════════════════════════════════════════╗
║  ZMAI Capability Analysis                                ║
╠══════════════════════════════════════════════════════════╣
║                                                          ║
║  任务: "读取 MySQL 数据库并生成报表"                      ║
║                                                          ║
║  所需能力:                                               ║
║    ✅ db_query       — 可用 (通过 shell + mysql CLI)      ║
║    ✅ file_write     — 可用                              ║
║    ✅ show_output    — 可用                              ║
║    ⚠️ data_format    — 部分可用 (Python脚本)              ║
║                                                          ║
║  替代建议:                                                ║
║    - 确保系统中已安装 mysql CLI 客户端                     ║
║    - 或用 Python 脚本通过 pymysql 连接                    ║
║                                                          ║
║  是否继续? [Y/n]                                         ║
║                                                          ║
╚══════════════════════════════════════════════════════════╝
```

---

## 4. 当前能力清单

### 4.1 原子能力清单

基于当前代码扫描结果：

| 能力名称 | 类别 | 置信度 | 实现方式 | 说明 |
|----------|------|--------|---------|------|
| `file_read` | file_operation | 🟢 完整 | ReadFileTool | 支持行范围、二进制检测、10MB 限制 |
| `file_write` | file_operation | 🟢 完整 | WriteFileTool | 自动创建目录，多种写入 fallback |
| `file_edit` | file_operation | 🟢 完整 | EditTool | 4 种模式：行替换、正则替换、插入、追加 |
| `file_search` | file_operation | 🟢 完整 | GrepTool | 正则搜索，支持 glob 过滤 |
| `code_search` | code_operation | 🟢 完整 | GrepTool | 同上，适用于代码搜索 |
| `shell_exec` | execution | 🟢 完整 | ShellTool | 含 Linux→Windows 命令翻译 |
| `git_operation` | execution | 🟢 完整 | GitTool | 基本 git 命令 |
| `show_output` | delivery | 🟢 完整 | ShowToUserTool | 打印到终端 |
| `open_browser` | delivery | 🟢 完整 | OpenInBrowserTool | 跨平台浏览器打开 |
| `file_output` | delivery | 🟢 完整 | WriteFileTool | 写入到 output/ 目录 |
| `prompt_render` | ai_capability | 🟢 完整 | PromptEngine | 5 种 Prompt 模板 |
| `memory_store` | - | 🟢 完整 | MemoryManager | 工作记忆 + 长期记忆 |
| `workflow_exec` | workflow | 🟢 完整 | WorkflowEngine | 线性/条件分支工作流 |
| `project_detect` | - | 🟢 完整 | Detectors | Python/Node/Rust/Go/Docker/Git/Monorepo |
| `credential_resolve` | - | 🟢 完整 | CredentialResolver | 统一凭据解析 |

### 4.2 部分/间接能力

| 能力名称 | 类别 | 置信度 | 说明 |
|----------|------|--------|------|
| `http_request` | communication | 🟡 间接 | 通过 ShellTool 执行 curl/wget，无专用工具 |
| `db_query` | data_processing | 🟡 间接 | 通过 ShellTool 执行 sqlite3 CLI 或 Python 脚本 |
| `data_analyze` | data_processing | 🟡 间接 | 通过 ShellTool 执行 Python 脚本 |
| `build_test` | execution | 🟡 间接 | 通过 ShellTool 运行 pytest/npm test 等 |
| `code_generate` | code_operation | 🟡 间接 | 通过 WriteFileTool 写入代码，依赖 LLM 生成 |
| `file_upload` | delivery | 🟡 间接 | 通过 ShellTool 调用第三方 CLI |
| `file_download` | delivery | 🟡 间接 | 通过 ShellTool 调用 curl/wget |

### 4.3 当前缺失的能力

| 能力名称 | 类别 | 缺失原因 | 影响场景 |
|----------|------|---------|---------|
| `llm_vision` | ai_capability | Backend 不支持或未启用 | 无法处理图片/图表 |
| `email_send` | communication | 无 SMTP 工具 | 无法发送邮件 |
| `database_connect` | data_processing | 无原生数据库驱动 | 需要用户手动配置 |
| `api_call` | communication | 无 HTTP 客户端工具 | 无法直接调用 REST API |
| `screenshot` | delivery | 无截图工具 | 无法截取页面 |
| `pdf_generate` | data_processing | 无 PDF 生成工具 | 无法生成 PDF 报告 |
| `image_process` | data_processing | 无图像处理工具 | 无法处理图片 |
| `schedule_task` | workflow | 无定时器 | 无法定时执行任务 |
| `notification` | delivery | 无推送工具 | 无法主动通知用户 |

---

## 5. 当前工具清单

### 5.1 正式注册的工具

基于 `swe/tools.py` 扫描：

| 工具名 | 类型 | 参数 | 状态 |
|--------|------|------|------|
| `read_file` | 🔧 `ReadFileTool` | path, start_line, end_line | 🟢 活跃 |
| `write_file` | 🔧 `WriteFileTool` | path, content | 🟢 活跃 |
| `edit` | 🔧 `EditTool` | path, mode, start_line, end_line, old_text, new_text, count, ignore_case | 🟢 活跃 |
| `grep` | 🔧 `GrepTool` | pattern, glob, ignore_case | 🟢 活跃 |
| `shell_exec` | 🔧 `ShellTool` | command, timeout, input | 🟢 活跃 |
| `git` | 🔧 `GitTool` | args, timeout | 🟢 活跃 |
| `show_to_user` | 🔧 `ShowToUserTool` | content, title | 🟢 活跃 |
| `open_in_browser` | 🔧 `OpenInBrowserTool` | path | 🟢 活跃 |

### 5.2 系统内部组件（非 Tool 形式）

| 组件 | 功能 | 是否暴露给 LLM |
|------|------|----------------|
| `Backend` | LLM 调用 (invoke/stream) | ❌ 内部 |
| `ToolRegistry` | 工具注册/发现 | ❌ 内部 |
| `ToolRouter` | 工具调用路由 | ❌ 内部 |
| `MemoryManager` | 工作/长期记忆 | ❌ 内部 |
| `Workspace` | 文件沙箱 | ❌ 内部 |
| `Config` | 配置管理 | ❌ 内部 |
| `StateManager` | 状态持久化 | ❌ 内部 |
| `LifecycleManager` | Agent 生命周期 | ❌ 内部 |
| `PromptEngine` | Prompt 渲染 | ❌ 内部 |
| `WorkflowEngine` | 工作流执行 | ❌ 内部 |
| `CredentialResolver` | 凭据解析 | ❌ 内部 |

### 5.3 工具注册方式

```python
# SWEAgent.initialize() 中的注册
for tool in [
    ReadFileTool(), WriteFileTool(), EditTool(),
    GrepTool(), ShellTool(), GitTool(),
    ShowToUserTool(), OpenInBrowserTool(),
]:
    if tool.name not in existing:
        context.tools.register(tool)
```

---

## 6. Agent 能力映射表

### 6.1 SWEAgent → 能力映射

```
SWEAgent
├── file_read       ← ReadFileTool
├── file_write      ← WriteFileTool
├── file_edit       ← EditTool
├── file_search     ← GrepTool
├── code_search     ← GrepTool (code-specific)
├── shell_exec      ← ShellTool
├── git_operation   ← GitTool
├── show_output     ← ShowToUserTool
├── open_browser    ← OpenInBrowserTool
├── file_output     ← WriteFileTool
│
├── [indirect]
│   ├── http_request   ← ShellTool (curl)
│   ├── db_query       ← ShellTool (sqlite3 CLI / Python)
│   ├── data_analyze   ← ShellTool (Python script)
│   ├── build_test     ← ShellTool (pytest / npm test)
│   └── file_download  ← ShellTool (curl / wget)
│
└── [missing]
    ├── llm_vision
    ├── email_send
    ├── api_call (专用工具)
    ├── screenshot
    └── ...
```

### 6.2 Agent 类型 vs 能力矩阵

| 能力 | SWEAgent | 未来 Agent A | 未来 Agent B |
|------|----------|-------------|-------------|
| file_read | ✅ | — | — |
| file_write | ✅ | — | — |
| file_edit | ✅ | — | — |
| file_search | ✅ | — | — |
| code_search | ✅ | — | — |
| shell_exec | ✅ | — | — |
| git_operation | ✅ | — | — |
| show_output | ✅ | — | — |
| open_browser | ✅ | — | — |
| http_request | 🟡 间接 | — | — |
| db_query | 🟡 间接 | — | — |
| data_analyze | 🟡 间接 | — | — |
| llm_vision | ❌ | — | — |
| email_send | ❌ | — | — |

### 6.3 BackendCapability 映射

| Backend 能力 | Claude | DeepSeek | Gemini |
|-------------|--------|----------|--------|
| STREAMING | ✅ | ✅ | ✅ |
| TOOL_USE | ✅ | ✅ | ✅ |
| SYSTEM_PROMPT | ✅ | ✅ | ✅ |
| MULTI_TURN | ✅ | ✅ | ✅ |
| VISION | ✅ | ❌ | ✅ |
| STRUCTURED_OUTPUT | ✅ | ✅ | ❌ |

当前 `SWEAgent` 未消费 `BackendCapability.VISION` — 即使 Backend 支持，Agent 也没有工具来传递图像数据。

---

## 7. 未来扩展方案

### 7.1 阶段一：能力声明（最小可用）

**文件规划**：`src/zmai/capability/` 包

```
src/zmai/capability/
├── __init__.py         # 导出 Capability 系统公共 API
├── base.py             # Capability 数据类型与枚举
├── registry.py         # CapabilityRegistry — 注册/发现能力
├── analyzer.py         # CapabilityAnalyzer — 任务分析
└── reporter.py         # 用户友好的能力报告输出
```

**实现步骤**：

1. 定义 `Capability` 数据类和分类枚举。
2. 实现 `CapabilityRegistry`（注册能力和查询）。
3. 为每个现有 Tool 注册其提供的能力。
4. 为 SWEAgent 注册 Agent 级能力。
5. 实现 `CapabilityAnalyzer.analyze()` — 关键词规则版。

### 7.2 阶段二：任务前置分析

**集成点**：在 `Runtime.run()` 入口处插入分析步骤。

```python
# 理想中的 Runtime.run() 改动（非侵入式）
async def run(self, ...):
    # [新增] 前置能力分析
    if config.get("capability.check_enabled", True):
        result = await self._capability_analyzer.check(agent, task)
        if result.has_critical_gap:
            return {
                "status": "capability_gap",
                "gaps": result.gaps,
                "summary": result.summary,
            }
    # ... 原有逻辑 ...
```

### 7.3 阶段三：动态能力扩展

- Plugin 可声明新能力
- 运行时动态检测能力变化（如安装新工具后）
- 能力热更新

### 7.4 可能的专用工具提案

基于能力缺口，以下工具可考虑实现：

| 工具 | 提供能力 | 优先级 |
|------|---------|--------|
| `HttpTool` | `http_request`, `api_call` | 🔴 高 |
| `SqliteTool` | `db_query` | 🟡 中 |
| `ImageTool` | `image_process` | 🟢 低 |
| `EmailTool` | `email_send` | 🟢 低 |
| `ScheduleTool` | `schedule_task` | 🟢 低 |

### 7.5 扩展：能力驱动的 Agent 选择

```python
class CapabilityAwareScheduler:
    """根据任务需求选择最合适的 Agent。"""
    
    def select_agent(
        self, task: str, available_agents: list[Agent]
    ) -> tuple[Agent | None, GapAnalysisResult]:
        ...
```

### 7.6 不修改现有代码的原则

所有新增模块放在 `src/zmai/capability/` 包内。

涉及对现有类的扩展，采用以下方式：
- **Agent 基类**：通过混入（Mixin）或组合（Composition），不修改现有 ABC
- **Tool 基类**：通过 `capability` 类属性或注册表关联
- **Runtime**：通过可选的中间件/钩子插入，不修改 `run()` 主逻辑

---

## 8. 附录

### A. 文件扫描清单

以下文件在本次设计中已被完整扫描：

| 文件 | 行数 | 关键内容 |
|------|------|---------|
| `src/zmai/agent/base.py` | 128 | Agent 抽象基类、状态枚举、上下文定义 |
| `src/zmai/tool/base.py` | 189 | Tool 抽象基类、执行上下文、结果定义 |
| `src/zmai/tool/registry.py` | 142 | 线程安全工具注册表 |
| `src/zmai/swe/agent.py` | 261 | SWEAgent 实现（唯一 Agent 实现） |
| `src/zmai/swe/tools.py` | 598 | 8 个 SWE 工具实现 |
| `src/zmai/runtime/runtime.py` | 350 | Runtime 主类编排 |
| `src/zmai/gateway/base.py` | 198 | Backend 基类 + BackendCapability |
| `src/zmai/gateway/tool_router.py` | 118 | 工具调用路由 |
| `src/zmai/gateway/registry.py` | 128 | Backend 注册表 |
| `src/zmai/gateway/plugin.py` | 423 | Backend 插件系统 |
| `src/zmai/workflow/base.py` | 49 | Workflow 抽象基类 |
| `src/zmai/workflow/engine.py` | 138 | 工作流执行引擎 |
| `src/zmai/prompt/types.py` | 51 | Prompt 类型枚举 |
| `src/zmai/prompt/engine.py` | 307 | Prompt 引擎 |
| `src/zmai/memory/base.py` | 71 | 记忆抽象基类 |
| `src/zmai/memory/manager.py` | 71 | 记忆管理器 |
| `src/zmai/runtime/lifecycle.py` | 87 | 生命周期状态机 |
| `src/zmai/runtime/state.py` | 117 | JSON 状态管理 |
| `src/zmai/runtime/preflight.py` | 279 | 启动前健康检查 |
| `src/zmai/workspace/workspace.py` | 1044 | Workspace 沙箱 |
| `src/zmai/config/config.py` | 63 | 配置管理器 |
| `src/zmai/errors/__init__.py` | 130 | 异常类型 |
| `src/zmai/cli/main.py` | 1226 | CLI 主入口 |
| `src/zmai/cli/context.py` | 47 | 项目上下文构建 |
| `src/zmai/auth/resolver.py` | 369 | 统一凭据解析器 |

### B. 设计决策记录

| 决策 | 选项 | 选择 | 理由 |
|------|------|------|------|
| 能力系统位置 | 新包 vs 嵌入现有 | **新包** `capability/` | 不修改现有核心逻辑 |
| 能力声明方式 | 枚举 vs 字符串 | **字符串**（辅以枚举分类） | 灵活、可扩展，Plugin 可声明新能力 |
| 任务分析方式 | 规则 vs LLM vs 混合 | **规则优先**（初始） | 零 API 成本，可靠且快速 |
| Agent 能力来源 | 显式声明 vs 工具推断 | **工具推断为主 + 显式覆盖** | 减少重复，自动同步 |
| 集成 Runtime 方式 | 侵入式 vs 钩子 | **可选钩子** | 不影响现有执行路径 |
| 能力缺失处理 | 硬阻止 vs 软警告 | **严重缺失 → 阻止；部分 → 提示** | 平衡安全与灵活性 |

### C. 关键词 → 能力示例映射

```
# 文件操作
r"(读取|打开|查看|read|view|open).*(文件|file)"             → file_read
r"(写入|创建|生成).*(文件|file)"                              → file_write
r"(修改|编辑|更新|替换|更改).*(文件|代码|file|code)"          → file_edit
r"(搜索|查找|查询|grep|find).*(文件|代码|file|code|text)"     → file_search

# 代码
r"(搜索|查找).*(代码|函数|类|方法|function|class|method)"     → code_search
r"(重构|重写|优化|refactor|optimize)"                          → code_modify

# 执行
r"(运行|执行|执行命令|run|execute|shell|命令)"                → shell_exec
r"(git|commit|push|pull|branch|merge|clone)"                  → git_operation

# 交付
r"(展示|显示|输出|打印|show|display|print|echo)"              → show_output
r"(打开|浏览器|网页|HTML|browser|html|open)"                  → open_browser

# 扩展能力关键词
r"(数据库|SQL|mysql|postgres|sqlite|db|query|查询)"            → db_query
r"(HTTP|API|REST|请求|curl|wget|fetch|调用)"                  → http_request
r"(图片|图像|截图|image|photo|screenshot|vision)"             → llm_vision
r"(邮件|email|send|发送)"                                      → email_send
r"(分析|统计|报表|chart|图表|analyze|statistics)"              → data_analyze
```

---

> **文档结束**
>
> 此文档规划了 ZMAI Agent Capability System 的完整设计。
> 核心原则：不修改现有 Runtime 核心代码，新增 `capability/` 包作为前置分析层。
> 当前 ZMAI 拥有 8 个正式 Tool、约 15 项完整能力、约 6 项间接能力，
> 缺失约 9 项专有能力（通过 Shell + Python 脚本可部分弥补）。
