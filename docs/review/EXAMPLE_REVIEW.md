# Examples Review

> 审查日期: 2026-07-17
> 目标: 新人 5 分钟跑通
> 范围: Example、Demo、Tutorial

---

## 一、执行摘要

**当前状态: 无任何可运行的示例。**

```
examples/
  __init__.py    ← 空文件，仅 "Examples for ZMAI."
docs/
  .gitkeep       ← 空目录
```

新人从 `git clone` 到看到效果需要自己探索的步骤：

```
1. git clone
2. pip install -e .         ← README 没说这步
3. 猜测要设 API Key         ← README 没说
4. 猜测要运行 zmai          ← README 没展示 CLI
5. zmai doctor              ← 才能验证安装成功
6. zmai "hello"             ← 才能看到 Agent 工作
```

**至少 10 分钟，且每一步都需要猜测。**

---

## 二、新人 5 分钟路径设计

### 2.1 理想路径

```
第 1 分钟: git clone + pip install
第 2 分钟: 配置 API Key
第 3 分钟: 运行第一个示例（CLI）
第 4 分钟: 运行第一个示例（Python API）
第 5 分钟: 探索更多
```

### 2.2 当前路径 vs 理想路径

| 步骤 | 当前 | 理想 | 差距 |
|------|------|------|------|
| 安装 | `pip install zmai`（不可用） | `git clone + pip install -e .` | 无源码安装说明 |
| 配置 Key | 无说明 | `echo DEEPSEEK_API_KEY=sk-... > .env` | 完全缺失 |
| 验证 | 无 | `zmai doctor` | 无验证方法 |
| Hello World | 无 | `zmai "1+1=?"` | 无 CLI 示例 |
| Python API | 无 | `examples/01_hello_agent.py` | 无编程示例 |
| 后续 | 无 | `examples/02_*.py` 等 | 无进阶示例 |

---

## 三、缺失示例清单

### 3.1 P0 — 必须有的（新人第一个 5 分钟）

| 示例 | 文件 | 预期用时 | 说明 |
|------|------|---------|------|
| CLI Hello | `zmai "1+1=?"` | 30s | 最简验证安装成功 |
| CLI REPL | `zmai` → 输入任务 | 30s | 交互模式体验 |
| CLI Doctor | `zmai doctor` | 10s | 诊断环境 |
| Python Hello | `examples/01_hello_agent.py` | 60s | 编程方式运行 Agent |
| Python Quick Config | `examples/02_quick_config.py` | 30s | 配置管理 |

### 3.2 P1 — 应该有的（新人 5-15 分钟）

| 示例 | 文件 | 说明 |
|------|------|------|
| Custom Tool | `examples/03_custom_tool.py` | 注册自定义工具 |
| Multi Backend | `examples/04_multi_backend.py` | 切换 DeepSeek/Claude |
| Workspace | `examples/05_workspace.py` | 文件沙箱操作 |
| Memory | `examples/06_memory.py` | 记忆存储与读取 |
| Streaming | `examples/07_streaming.py` | 流式输出 |

### 3.3 P2 — 锦上添花（进阶用户）

| 示例 | 文件 | 说明 |
|------|------|------|
| Workflow | `examples/08_workflow.py` | 多步骤工作流 |
| Prompt Engine | `examples/09_prompt.py` | 模板引擎 |
| MCP Client | `examples/10_mcp.py` | 外部工具集成 |

---

## 四、P0 示例详细设计

### 4.1 `examples/01_hello_agent.py` — Hello World （新人第 1 个示例）

目标：30 秒内看到 Agent 回复。

```python
"""01_hello_agent.py — 最简 Agent 调用。

前提:
  1. 已安装 ZMAI: pip install -e .
  2. 已设置环境变量:
     - DEEPSEEK_API_KEY=sk-xxx（DeepSeek）
     或 - ANTHROPIC_API_KEY=sk-xxx（Claude）

运行:
  python examples/01_hello_agent.py
"""

import asyncio
from zmai.runtime import Runtime
from zmai.config import Config


async def main():
    # 1. 创建 Runtime（自动检测 API Key 和 Backend）
    runtime = Runtime(config=Config())

    # 2. 运行一个简单任务
    result = await runtime.run(
        agent_id="hello_agent",
        task="用一句话回答: 1+1=?",
    )

    # 3. 输出结果
    print(f"状态: {result['status']}")
    print(f"回复: {result.get('output', '')}")


if __name__ == "__main__":
    asyncio.run(main())
```

### 4.2 `examples/02_cli_quickstart.sh` — CLI 快速入门

```bash
#!/bin/bash
# 02_cli_quickstart.sh — CLI 模式快速体验
# 运行: bash examples/02_cli_quickstart.sh

set -e

echo "=== 1. 诊断环境 ==="
zmai doctor

echo ""
echo "=== 2. 查看当前配置 ==="
zmai config list

echo ""
echo "=== 3. 运行一次性任务 ==="
zmai "列出当前目录的 Python 文件"

echo ""
echo "=== 4. 进入 REPL 模式 ==="
echo "请运行: zmai"
echo "然后在提示符下输入: 用中文介绍你自己"
```

### 4.3 `examples/03_custom_tool.py` — 自定义工具（P1）

```python
"""03_custom_tool.py — 注册并使用自定义工具。

运行:
  python examples/03_custom_tool.py
"""

import asyncio
from zmai.tool import Tool, ToolContext, ToolResult
from zmai.runtime import Runtime
from zmai.config import Config


class CurrentTimeTool(Tool):
    """返回当前时间的工具。"""
    name = "current_time"
    description = "获取当前日期和时间"
    parameters = {
        "type": "object",
        "properties": {
            "format": {
                "type": "string",
                "enum": ["short", "full"],
                "description": "时间格式",
            }
        },
    }

    def execute(self, context: ToolContext, params: dict) -> ToolResult:
        from datetime import datetime
        fmt = params.get("format", "short")
        now = datetime.now()
        if fmt == "full":
            return ToolResult.ok(now.strftime("%Y-%m-%d %H:%M:%S"))
        return ToolResult.ok(now.strftime("%H:%M"))


async def main():
    runtime = Runtime(config=Config())

    # 注册自定义工具
    runtime._tools.register(CurrentTimeTool())

    result = await runtime.run(
        agent_id="custom_tool_demo",
        task="现在几点了？用 current_time 工具获取时间",
    )
    print(f"结果: {result.get('output', '')[:200]}")


if __name__ == "__main__":
    asyncio.run(main())
```

### 4.4 `examples/04_multi_backend.py` — 多 Backend 切换（P1）

```python
"""04_multi_backend.py — 切换不同 Backend。

运行:
  python examples/04_multi_backend.py
"""

import asyncio
from zmai.runtime import Runtime
from zmai.config import Config
from zmai.auth import AuthStore


async def run_with_backend(backend_name: str, task: str) -> dict:
    """使用指定 Backend 运行任务。"""
    config = Config()
    config.set("gateway.default_backend", backend_name)
    runtime = Runtime(config=config)
    return await runtime.run(agent_id=f"demo_{backend_name}", task=task)


async def main():
    backends = ["deepseek", "claude"]
    task = "用一句话说'你好'"

    for name in backends:
        print(f"\n--- 使用 Backend: {name} ---")
        try:
            result = await run_with_backend(name, task)
            print(f"回复: {result.get('output', '')[:200]}")
        except Exception as e:
            print(f"失败: {e}（可能未配置 {name} 的 API Key）")


if __name__ == "__main__":
    asyncio.run(main())
```

---

## 五、Tutorial 设计

### 5.1 当前状态：无

没有 Tutorial 目录或文档。

### 5.2 建议教程序列

| # | 教程 | 内容 | 示例文件 |
|---|------|------|---------|
| 1 | **安装与环境** | 安装、API Key 配置、`zmai doctor` 验证 | `02_cli_quickstart.sh` |
| 2 | **第一个 Agent** | CLI REPL + Python API 调用 | `01_hello_agent.py` |
| 3 | **理解 Workspace** | 文件读写、Agent 隔离、安全特性 | `05_workspace.py` |
| 4 | **配置管理** | zmai.json、config get/set/list、--global | `02_quick_config.py` |
| 5 | **自定义工具** | 继承 Tool、注册、使用 | `03_custom_tool.py` |
| 6 | **多 Backend** | 切换模型、比较结果 | `04_multi_backend.py` |
| 7 | **记忆系统** | WorkingMemory、LongTermMemory、persist/restore | `06_memory.py` |

### 5.3 教程格式

每个教程在同一文件中包含：
1. 顶部注释说明前提和预期输出
2. 可直接运行的 Python 代码
3. 关键步骤的中文注释

---

## 六、Demo 设计

### 6.1 当前状态：无

没有可演示的 Demo 脚本。

### 6.2 建议 Demo 脚本

| 脚本 | 适用场景 | 内容 |
|------|---------|------|
| `demo_full.sh` | 全面展示（5 分钟） | doctor → config → auth → 任务 → REPL |
| `demo_agent.sh` | Agent 能力（2 分钟） | 阅读代码 → 修改 → 运行测试 |
| `demo_workspace.sh` | Workspace 演示（1 分钟） | prepare → write → list → read → cleanup |

---

## 七、总结

### 7.1 当前评分

| 维度 | 评分 | 说明 |
|------|------|------|
| Example | ☆☆☆☆☆ | 无任何 .py 示例文件 |
| Demo | ☆☆☆☆☆ | 无任何演示脚本 |
| Tutorial | ☆☆☆☆☆ | 无任何教程文档 |
| 新人 5 分钟路径 | ☆☆☆☆☆ | 根本无法在 5 分钟内跑通 |

**综合: 0/5** — 示例系统完全不存在。

### 7.2 实施建议

```
Phase 1 (1 小时):
├── examples/01_hello_agent.py          ← P0
├── examples/02_cli_quickstart.sh       ← P0
├── README.md 更新: 安装说明 + 快速开始 ← P0

Phase 2 (2 小时):
├── examples/03_custom_tool.py          ← P1
├── examples/04_multi_backend.py        ← P1
├── examples/05_workspace.py            ← P1
├── examples/06_memory.py               ← P1

Phase 3 (1 小时):
├── demo_full.sh                        ← P2
├── docs/tutorial/01_install.md         ← P2
└── docs/tutorial/02_first_agent.md     ← P2
```

---

*Report generated by `claude` — 基于 `examples/` 目录 + 项目 API 审计*
