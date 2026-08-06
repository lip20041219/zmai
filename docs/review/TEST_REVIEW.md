# Test Review

> 审查日期: 2026-07-17
> 范围: CLI、Runtime、Gateway、Memory、Tool、Workspace、Backend、覆盖分析

---

## 一、执行摘要

**总测试数: 283 个函数，15 个文件，4663 行**

| 组件 | 测试文件 | 测试数 | 覆盖评估 |
|------|---------|--------|---------|
| CLI | `test_cli.py` | 19 | ⚠️ 17% |
| Runtime | `test_integration.py` | 9 (间接) | ⚠️ 21% |
| Gateway | `test_gateway.py` | 30 | ✅ 90% (base/registry/router), ❌ DeepSeek 0% |
| Memory | `test_memory.py` | 26 | ✅ 90% |
| Tool | `test_tool.py` + `test_swe.py` | 37 | ✅ 90% (ABC/registry) ⚠️ 60% (SWE tools) |
| Workspace | `test_workspace.py` | 40 | ✅ 85% |
| Backend | `test_gateway.py` | 部分 | ⚠️ Claude 70%, DeepSeek 0% |
| **整体** | **15 文件** | **283** | **~50%** |

**综合评分: 5/10** — Workspace、Memory、Tool 覆盖好；Auth、Config、Runtime 核心完全缺测试。

---

## 二、逐模块分析

### 2.1 CLI — `test_cli.py`（19 测试）

**已测：**
```
✅ Theme（dark/light/plain/colorize）
✅ print_* 输出函数（json/success/error/table/info/warning）
✅ argparse 解析（--json/--no-color/--backend/--version）
✅ _get_theme() 从配置加载
✅ _run_config() 子命令（get/set/list）
```

**未测（14 个函数，~83%）：**
```
❌ _save_session / _load_latest_session  ← session 持久化
❌ _cleanup_old_workspaces               ← workspace 清理
❌ _should_show_init_wizard              ← 向导检测逻辑
❌ _run_init_wizard                      ← 4 步交互向导
❌ _inject_auth_credentials              ← 凭证注入
❌ _print_help                           ← 帮助文本
❌ _repl_run / _oneshot_run              ← 任务执行
❌ _setup_readline                       ← readline 配置
❌ _cmd_interactive                      ← REPL 主循环
❌ _run_auth                             ← auth 子命令
❌ main()                                ← 主入口
```

**风险**: 整个 CLI 入口流程（main() → 子命令分发 → REPL/oneshot → session 管理）无测试覆盖。

### 2.2 Runtime — `test_integration.py`（9 测试，间接）

**已测：**
```
✅ Runtime.__init__()（创建）
✅ Runtime.get_info()（基本信息）
✅ Runtime.list_agents()（列表）
```

**未测（~79%）：**
```
❌ Runtime.run()                  ← 核心方法，完全未测
❌ Runtime._execute_task()        ← Agent 执行循环（死代码？）
❌ Runtime.pause/resume/cancel    ← 生命周期管理
❌ Runtime.shutdown()             ← 关闭
❌ Runtime.default_backend        ← Backend 选择
❌ _register_available_backends() ← Backend 自动注册
❌ _auto_select_default_backend() ← 自动选择
❌ _build_system_prompt()         ← 系统提示词构建
❌ Scheduler                      ← 完全未测
❌ LifecycleManager               ← 完全未测
❌ StateManager                   ← 完全未测
```

**风险**: Runtime 的 run() 方法是整个系统的核心入口，无任何测试。任何重构都可能破坏核心流程。

### 2.3 Gateway — `test_gateway.py`（30 测试）

**已测：**
```
✅ Backend ABC（抽象约束）
✅ BackendRegistry（注册/获取/默认/列表/缓存）
✅ ToolRouter（执行/定义/超时/耗时）
✅ ClaudeBackend（请求构建/响应解析/tool_def）
✅ BackendRequest/Response/Event/TokenUsage 数据类
✅ MCPClient（初始化）
```

**未测：**
```
❌ DeepSeekBackend              ← 完全未测（invoke/stream/capabilities）
❌ ClaudeBackend.stream()        ← 流式响应未测
❌ ClaudeBackend 重试逻辑        ← retry 机制
❌ MCPClient.call_tool()        ← 工具调用
❌ BACKEND_METADATA             ← 元数据完整性
❌ get_backend_info() 等        ← 辅助函数
```

**风险**: DeepSeek 是默认 Backend 之一，但完全无测试。

### 2.4 Memory — `test_memory.py`（26 测试）

**已测：**
```
✅ MemoryEntry（构造/序列化/TTL）
✅ WorkingMemory（CRUD/search/namespace/LRU）
✅ LongTermMemory（CRUD/persistence/namespace/append-only）
✅ MemoryManager（per agent/cleanup/exists/persist/restore）
```

**未测：**
```
❌ WorkingMemory TTL 过期读取     ← 惰性删除路径
❌ LongTermMemory 并发访问        ← 多线程读写
❌ MemoryManager 默认 ~/.zmai/memory ← 默认路径
```

**覆盖**: ~90%，项目中覆盖最好的模块之一。

### 2.5 Tool — `test_tool.py` + `test_swe.py`（37 测试）

**已测：**
```
✅ Tool ABC（抽象约束/定义）
✅ ToolResult（ok/err/to_dict）
✅ ToolRegistry（注册/获取/注销/列表/执行/线程安全）
✅ 全部 8 个 SWE Tool（init + 基本执行）
```

**未测：**
```
❌ _resolve_tool_path()           ← 路径解析核心逻辑
❌ _emit_tool_result()            ← 日志输出
❌ _translate_cmd()              ← Linux→Windows 命令翻译
❌ GitTool.execute()             ← 只测了 init
❌ OpenInBrowserTool 成功路径    ← 只测了文件不存在
```

### 2.6 Workspace — `test_workspace.py`（40 测试）

**已测：**
```
✅ 初始化（4 种方式/自定义配置/不可写目录）
✅ Agent 生命周期（prepare/cleanup/remove/list）
✅ 文件操作（write/read/list/exists/delete）
✅ 路径安全（../穿越/Agent ID 注入/隔离）
✅ 文件大小限制
✅ Manifest（创建/更新/删除/全局）
✅ State（agent/global/持久化）
✅ 目录路径（5 个目录获取）
✅ 文件分类（8 类扩展名 + mime）
✅ 文件类型支持（8 种格式写读）
✅ 并发（10 Agent 并行/50 并发写入）
```

**未测：**
```
❌ _check_disk_space()            ← 磁盘空间检查
❌ max_files 限制                  ← 文件数限制
❌ JSON 文件损坏恢复               ← 异常处理路径
❌ symlink 穿越攻击               ← 符号链接安全
```

**覆盖**: ~85%，项目中覆盖最好的模块。

### 2.7 Backend（广义）

包括 Gateway 中的 Backend ABC 和各实现：

| Backend | 测试状态 | 评估 |
|---------|---------|------|
| Backend ABC | ✅ 完整测试 | ~90% |
| BackendRegistry | ✅ 完整测试 | ~95% |
| ClaudeBackend | ⚠️ 部分测试 | ~70%（缺 stream/retry） |
| DeepSeekBackend | ❌ 完全未测 | 0% |
| BACKEND_METADATA | ❌ 未验证 | 0% |

---

## 三、完全缺失的测试文件

以下源模块没有对应的测试文件：

| 源模块 | 行数 | 需要测试文件 |
|--------|------|-------------|
| `zmai/auth/store.py` | 194 | `tests/test_auth.py` |
| `zmai/config/sources.py` | ~80 | `tests/test_config.py`（可合并） |
| `zmai/config/config.py` | 55 | 同上 |
| `zmai/agent/base.py` | 127 | `tests/test_agent.py` |
| `zmai/runtime/scheduler.py` | ~80 | `tests/test_runtime.py`（可合并） |
| `zmai/runtime/lifecycle.py` | ~60 | 同上 |
| `zmai/runtime/state.py` | ~60 | 同上 |
| `zmai/gateway/backends/deepseek.py` | ~80 | `tests/test_gateway.py`（可追加） |
| `zmai/cli/context.py` | 90 | 间接测试 |
| `zmai/errors/__init__.py` | ~40 | 与各模块测试共同覆盖 |

---

## 四、测试质量评估

### 4.1 优点

| 优点 | 说明 |
|------|------|
| Workspace 测试全面 | 40 个测试覆盖 85% 公开 API，包括安全测试 |
| 回归测试有针对性 | `test_swe_regression.py` 明确标注已知 Bug 和修复 |
| 无外部依赖 | 所有测试使用 `tmp_path` + 内联 Mock 类，无需网络 |
| 并发测试存在 | 多 Agent 和并发写入场景有覆盖 |
| 路径安全测试完善 | 遍历攻击有 Workspace 和 SWE 两级防御 |

### 4.2 缺点

| 缺点 | 严重度 | 说明 |
|------|--------|------|
| Auth 零测试 | **高** | 凭证加密/解密/存储/检测全部无覆盖 |
| Runtime 核心零测试 | **高** | `run()` 方法——整个系统的核心无测试 |
| Config 零测试 | **高** | 三层配置源的合并逻辑无测试 |
| DeepSeek Backend 零测试 | **高** | 默认 Backend 之一完全无覆盖 |
| Agent ABC 零测试 | **中** | AgentState/AgentAction/AgentResult 无独立测试 |
| Python logging 未测试 | **中** | 只有 `caplog` 在 test_tool.py 中使用过一次 |
| `main()` 未测试 | **中** | CLI 入口流程完全无覆盖 |
| 覆盖率工具未配置 | **低** | 无法量化覆盖率变化 |

### 4.3 测试密度对比

| 组件 | 源码行数 | 测试函数 | 密度（函数/百行） |
|------|---------|---------|-----------------|
| Workspace | ~500 | 40 | 8.0 |
| Memory | ~200 | 26 | 13.0 |
| Tool/SWE | ~400 | 37 | 9.3 |
| Gateway | ~300 | 30 | 10.0 |
| CLI | ~500 | 19 | 3.8 |
| Runtime | ~300 | 0（直接） | 0 |
| Auth | ~200 | 0 | 0 |
| Config | ~150 | 0 | 0 |
| Agent | ~130 | 0 | 0 |

---

## 五、建议

### Phase 1（填补核心缺口，1-2 天）

| 优先级 | 测试 | 文件 | 说明 |
|--------|------|------|------|
| P0 | AuthStore CRUD | `test_auth.py` | 凭证加密/解密/存储/环境检测 |
| P0 | Runtime.run() | `test_runtime.py` | MockBackend + SWEAgent 全流程 |
| P1 | DeepSeekBackend | `test_gateway.py` | invoke/stream/capabilities |
| P1 | Config 三层合并 | `test_config.py` | FileSource/EnvSource/CLISource |

### Phase 2（完善覆盖，2-3 天）

| 优先级 | 测试 | 说明 |
|--------|------|------|
| P1 | AgentState/AgentAction | Agent ABC 数据类 |
| P1 | CLI 子命令（auth/doctor） | 验证 `_run_auth`/`_run_doctor` |
| P2 | Scheduler/LifecycleManager | Runtime 内部组件 |
| P2 | `_resolve_tool_path` | SWE 工具路径解析 |
| P2 | `_translate_cmd` | 跨平台命令翻译 |

### Phase 3（基础设施，1 天）

| 优先级 | 措施 | 说明 |
|--------|------|------|
| P2 | 配置 pytest-cov | `pip install pytest-cov` + `pyproject.toml` 配置 |
| P2 | 添加 CI workflow | `.github/workflows/test.yml` |
| P3 | 覆盖率门禁 | PR 覆盖率不降级 |

---

*Report generated by `claude` — 基于 15 个测试文件 + 25+ 源模块审计*
