"""SWE Agent — software engineering agent (delivery-oriented)."""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path
from typing import Any

from zmai.agent import Agent, AgentAction, AgentContext, AgentResult, AgentState
from zmai.errors import BackendError
from zmai.gateway import Backend
from zmai.gateway.base import BackendRequest, BackendResponse
from zmai.swe.models import Plan, MAX_REPLANS, format_plan_summary
from zmai.swe.planner import generate_plan
from zmai.swe.completion import CompletionState
from zmai.swe.context import ContextManager
from zmai.swe.loop_guard import LoopGuard, LoopResult
from zmai.swe.scanner import RepositoryInfo, RepositoryScanner
from zmai.swe.tools import (
    EditTool,
    GitTool,
    GrepTool,
    OpenInBrowserTool,
    ReadFileTool,
    ShellTool,
    ShowToUserTool,
    WriteFileTool,
)
from zmai.tool import ToolCall, ToolContext, ToolResult
from zmai.swe.verifier import (
    VerificationCheck,
    VerificationResult,
    auto_generate_checks,
    parse_test_totals,
    verify_file_exists,
    verify_exit_code,
    verify_test_output,
)
from zmai.swe._async_utils import run_sync

logger = logging.getLogger("zmai.swe.agent")


def _now_ms() -> int:
    """当前时间戳（毫秒）。"""
    import time
    return int(time.monotonic() * 1000)


def _stats(context: AgentContext, **deltas: int) -> dict:
    """累计修复效率统计（total_steps/reads/duplicate_reads/pytest_calls 等）。

    用于审计 Agent 是否高效闭环，而不只是看 max_steps 是否耗尽。
    存入 context.metadata["swe_stats"]。
    """
    d = context.metadata.setdefault("swe_stats", {})
    for k, v in deltas.items():
        d[k] = d.get(k, 0) + v
    return d


def _log_stop() -> None:
    """打印任务完成/停止循环的显式日志（自主停止的可审计信号）。"""
    logger.info("[ZMAI] Task completed.")
    logger.info("[ZMAI] Stopping execution loop.")
    logger.info("[ZMAI] No further tool calls allowed.")


def _build_platform_prompt() -> str:
    """Generate platform-specific guidance based on current OS."""
    if sys.platform == "win32":
        return """
## Current OS: Windows (Important!)

You MUST use Windows-compatible commands. Do NOT use Linux/Mac commands:

### Command Mapping (Linux → Windows)
| Linux (DON'T use) | Windows (DO use) |
|---|---|
| ls | dir |
| ls -la | dir /a |
| pwd | cd |
| cat file.txt | type file.txt |
| head -n 5 file.txt | (no direct equivalent; use read_file with line range) |
| grep "pattern" file.py | Use the grep tool (not shell command) |
| touch newfile.txt | echo. > newfile.txt or type nul > newfile.txt |
| rm file.txt | del file.txt |
| rm -rf dir/ | rmdir /s /q dir |
| mv a.txt b.txt | move a.txt b.txt or ren a.txt b.txt |
| cp a.txt b.txt | copy a.txt b.txt |
| chmod +x script.sh | (not needed on Windows) |
| which python | where python |
| find . -name "*.py" | dir /s /b *.py |
| kill -9 PID | taskkill /f /pid PID |
| mkdir -p a/b/c | mkdir a\\b\\c |
| uname -a | ver |
| wc -l file.txt | find /c /v "" file.txt |
| sort file.txt | sort file.txt |
| echo $VAR | echo %VAR% |

### Path Rules
- Forward slash / or backslash \\ both work
- Use %VAR% for environment variables, not $VAR
- Windows is case-insensitive

### Key Tips
- **Do NOT use `ls`** — use `dir`
- **Do NOT use `cat`** — use `type` or the read_file tool
- **Do NOT use `pwd`** — use `cd`
- **Do NOT use `grep`** — use the grep tool (more powerful)
- Do NOT run interactive commands (they will hang)
"""

    return """
## Current OS: Linux/Mac

- Standard Unix commands work (ls, cat, grep, pwd, etc.)
- Use forward slash / for paths
- Do NOT run interactive commands (they will hang)
"""


_BASE_SYSTEM_PROMPT = """You are a Software Engineering Agent (SWE Agent). Your job doesn't end when code is written — you must deliver results the user can see.

## Available Tools

### Read
- read_file: Read file content with optional line range

### Write
- write_file: Write/overwrite file content (use for new files)
- edit: Line-level editing (replace_lines, regex_replace, insert, append)

### Search
- grep: Search text or regex in files (do NOT use shell grep — use this tool)

### Execute
- shell_exec: Execute shell commands in workspace directory
- git: Execute git commands

### Deliver
- show_to_user: Print content to terminal for the user to see
- open_in_browser: Open HTML file in browser

## SWE Workflow (strict — mandatory for code fix tasks)

For CODE FIX tasks you MUST follow this exact 5-phase workflow IN ORDER.
Do NOT skip phases. Do NOT reorder phases.

### Phase 1: Discover
- Read the task description carefully
- List project files (use dir or equivalent)
- Identify relevant source files and test files

### Phase 2: Run Tests First ⚠️ (CRITICAL)
- Run tests via `python -m pytest` (never a bare `pytest`; on Windows prefer `.venv/Scripts/python -m pytest`)
- See which tests FAIL before reading source code
- Capture the failure output — this tells you what to fix
- ⚠️ DO NOT read source files before running tests

### Phase 3: Analyze Failures
- Review test failure output carefully
- Read ONLY source files related to the failures
- Diagnose the root cause of each failure

### Phase 4: Modify Code
- Write/edit files to fix the root causes
- Only modify files that need changes
- Make minimal, targeted changes

### Phase 5: Verify
- Re-run tests to confirm the fix works
- If tests still fail → return to Phase 3 (Analyze)
- If all tests pass → deliver results with show_to_user

## Critical Rules
1. RUN TESTS FIRST — always run the test command before reading source files
2. DO NOT read source files indefinitely without running tests first
3. If you have read more than 8 source files without running tests, STOP and run tests now
4. Do NOT use shell for file/text search — use the grep tool
5. Do not repeatedly ls/dir without purpose
6. A "Requirements:" / numbered-list section in the user's task is a set of CONSTRAINTS, NOT separate tasks. Lines like "run tests" or "stop after success" tell you HOW to work on your single objective — never spin them into independent sub-tasks, and never keep working after your objective is met.
7. NEVER run a bare `pytest`. Always run tests via `python -m pytest` (or `.venv/Scripts/python -m pytest` on Windows).
8. AFTER A TEST FAILURE YOU MUST FIX IT — once you have run tests and seen failures, you are in the FIX PHASE. Read a few related source files to diagnose, THEN make the change with the `edit` or `write_file` tool. Do NOT keep reading file after file without modifying.
9. READ-TO-FIX LIMIT — after a test failure, you may read at most a few (default 3) files before you MUST emit an `edit` or `write_file` call to fix the code. If you have not modified anything after reading 3 files post-failure, STOP reading and make your best fix now.
10. NEVER loop: test-fail → read → test-fail → read with no modification. Each pass must move toward an edit. If your read is not advancing you toward a concrete fix, make the fix.
11. FIX-DRIVEN TOOL CADENCE (mandatory when tests FAIL) — the ONLY acceptable tool sequence is: `shell_exec` (run pytest) → at most 3 `read_file`/`grep` to diagnose → an `edit` or `write_file` that changes code → `shell_exec` (rerun pytest). If you are in the fix phase and you call `read_file` or `grep` without having made a code change, you are failing the task.
12. The runtime reports a "LIVE REPAIR STATUS" section each step. If it says tests are FAILING, your very next write tool call (`edit` or `write_file`) is MANDATORY — stop reading and make the edit now, even if you are not fully certain of the fix. A verifiable edit is strictly better than endless reading."""

_PLAN_EXECUTION_PROMPT = """
## Execution Plan (mandatory)

Below is the plan you must follow when executing this task.
Proceed step by step in the specified order. After each step, explicitly mark "Step X complete".
If a step cannot be executed as planned, explain why and provide an alternative.
After completing all steps, summarize the results."""


def _build_fix_state_directive(context: AgentContext) -> str:
    """每次 step 把当前修复状态注入 system prompt，让模型"看到"自己的只读进展。

    对真实 LLM 而言，静态提示词（"你必须修改"）远不如动态状态来得有效：
    模型每个 step 都会读到"测试已失败 / 已读 N 个文件 / 尚未修改"，从而被
    明确逼入修改阶段。这是 FixDriving 循环兜底的补充（消息注入是异步的，这里
    是每次调用都强制出现在模型上下文里）。
    """
    phase = context.metadata.get("repair_phase", "idle")
    if phase == "idle":
        return ""  # 尚未进入修复态，不注入噪音
    test_failed = context.metadata.get("test_failed", False)
    reads_after_fail = context.metadata.get("reads_after_fail", 0)
    cfg = context.config or {}
    limit = int(cfg.get("fix.read_limit", 3))
    lines = ["\n## LIVE REPAIR STATUS (dynamic, act on this now)"]
    lines.append(f"- repair phase: {phase}")
    if test_failed:
        lines.append(
            f"- tests are FAILING → you MUST emit `edit` or `write_file` to fix "
            f"(you have used {reads_after_fail}/{limit} reads since the failure)"
        )
        lines.append(
            "- DO NOT call read_file/grep again without a code change. "
            "Your next write tool call is required."
        )
    else:
        lines.append("- tests are passing or not yet run; keep verifying.")
    return "\n".join(lines) + "\n"


def _build_system_prompt(backend: Backend | None = None) -> str:
    """Build the full system prompt (base instructions + platform guide + backend identity)."""
    # Backend identity — read dynamically from backend instance
    identity_parts = []
    if backend:
        bn = getattr(backend, "name", "") or ""
        bm = getattr(backend, "model", "") or ""
        bp = getattr(backend, "provider", "") or ""
        if bn:
            identity_parts.append(f"## Your Identity")
            identity_parts.append(f"You are running on {bp.upper() if bp else bn} Backend.")
            if bm:
                identity_parts.append(f"Current model: {bm}.")
        identity_parts.append("")

    platform_prompt = _build_platform_prompt()
    return "\n".join(identity_parts) + _BASE_SYSTEM_PROMPT + platform_prompt


class SWEAgent(Agent):
    """Software Engineering Agent — code reading, modification, execution, and delivery."""

    name = "swe_agent"
    description = "Software Engineering Agent with delivery"

    async def initialize(self, context: AgentContext) -> None:
        logger.info("SWEAgent initializing: %s", context.agent_id)
        # Initialize ContextManager
        if "cm" not in context.metadata:
            context.metadata["cm"] = ContextManager(config=context.config)

        # ── Repository discovery: find and scan user project root ──
        # Distinguish: user project root vs agent runtime workspace vs internal state
        project_root = context.config.get("project_path")
        if project_root:
            project_root = Path(project_root).resolve()
        else:
            detected = RepositoryScanner.find_project_root()
            if detected:
                project_root = detected
                context.config["project_path"] = str(project_root)
                logger.info("Auto-detected project root: %s", project_root)

        if project_root and "repo_info" not in context.metadata:
            try:
                repo_info = RepositoryScanner.scan(project_root)
                context.metadata["repo_info"] = repo_info
                logger.info(
                    "Repository scanned: %s (%d source files, %d test files)",
                    project_root, len(repo_info.source_files), len(repo_info.test_files),
                )
            except Exception as e:
                logger.warning("Repository scan failed: %s", e)

        # ── LoopGuard — 循环检测 ────────────────────────────────
        if "loop_guard" not in context.metadata:
            threshold = int(context.config.get("loop_guard.threshold", 5))
            context.metadata["loop_guard"] = LoopGuard(threshold=threshold)
            logger.info("LoopGuard initialized (threshold=%d)", threshold)

        # ── CompletionState — 跨轮累积完成判定 ────────────────
        if "completion" not in context.metadata:
            context.metadata["completion"] = CompletionState()
            logger.info("CompletionState initialized")

        # ── Workflow phase tracking ────────────────────────────
        if "has_run_test" not in context.metadata:
            context.metadata["has_run_test"] = False
            context.metadata["reads_without_test"] = 0
            context.metadata["workflow_phase"] = "discover"
            logger.info("Workflow phase initialized: discover")

        # ── Repair phase state machine (test fail → diagnose → plan → edit → verify) ──
        # 测试失败后，Agent 必须走完"诊断→计划→修改→验证"的修复闭环，而不是只读不修。
        # 这是对 LoopGuard 的补充：LoopGuard 负责"检测停滞"，repair_phase 负责"驱动进展"。
        if "repair_phase" not in context.metadata:
            context.metadata["repair_phase"] = "idle"  # idle|diagnose|plan|edit|verify|done
            context.metadata["repair_plan_injected"] = False
            context.metadata["repair_cycle"] = 0

        # State is managed by Runtime's LifecycleManager; agent does not maintain independent state
        if context.tools:
            existing = {t.name for t in context.tools.list()}
            for tool in [
                ReadFileTool(), WriteFileTool(), EditTool(),
                GrepTool(), ShellTool(), GitTool(),
                ShowToUserTool(), OpenInBrowserTool(),
            ]:
                if tool.name not in existing:
                    context.tools.register(tool)

    async def plan(self, context: AgentContext) -> Plan:
        """Plan Mode: Generate a structured Plan in read-only mode.

        Uses PlanAgent internally. Does not modify any files.
        Returns a Plan that can be shown to the user for confirmation before execution.
        """
        from zmai.swe.plan_agent import PlanAgent

        if not context.backend:
            raise RuntimeError("No available Backend, cannot generate Plan")

        agent = PlanAgent(
            agent_id=context.agent_id,
            backend=context.backend,
            tools=context.tools,
            config=context.config,
        )
        plan = await agent.create_plan(context.task, context)
        # Store plan for later confirmation and execution
        context.metadata["execution_plan"] = plan
        return plan

    async def step(self, context: AgentContext) -> AgentAction:
        logger.debug("SWEAgent step %d/%d", context.step_count, context.max_steps)

        if not context.backend:
            _l = context.metadata.get("__log__")
            if _l:
                try:
                    _l.record_step(phase="error", action="no_backend",
                                   success=False, error="No available Backend")
                except Exception:
                    pass
            return AgentAction.fail("No available Backend. Please configure an API Key.")

        context.step_count += 1
        cm: ContextManager | None = context.metadata.get("cm")
        if cm is None:
            cm = ContextManager(config=context.config)
            context.metadata["cm"] = cm

        # Initialize task into context manager
        cm.set_task(context.task)

        # ── CompletionState — 惰性初始化（防御 initialize 未共享 ctx）──
        completion: CompletionState | None = context.metadata.get("completion")
        if completion is None:
            completion = CompletionState()
            context.metadata["completion"] = completion
        # ── 硬终止（最高优先级，进入本步即先判）：完成状态已满足 → 立即 return，不再调用 backend ──
        _green_once = context.metadata.get("test_success_count", 0) >= 1
        if completion and (completion.should_complete() or _green_once):
            logger.info(
                "CompletionState satisfied — entering DONE (%s, step %d): %s",
                context.agent_id, context.step_count, completion.summary(),
            )
            _log_stop()
            _l = context.metadata.get("__log__")
            if _l:
                try:
                    _l.record_step(phase="complete", action="completion_state",
                                   success=True,
                                   metadata={"step": context.step_count,
                                             "reason": completion.summary()})
                except Exception:
                    pass
            context.metadata["messages"] = cm.get_context()
            return AgentAction.complete(
                output=f"Task completed — objective met and tests green "
                       f"(step {completion.last_pass_step}). No further work needed."
            )

        # ── Auto-plan phase ──────────────────────────────────
        auto_plan = context.config.get("auto_plan", False)
        plan: Plan | None = context.metadata.get("execution_plan")

        if auto_plan and plan is None:
            on_progress = context.metadata.get("on_progress")
            if on_progress:
                on_progress("info", "Generating execution plan...")
            try:
                plan = await run_sync(generate_plan, context.task, context.backend, context.config)
                context.metadata["execution_plan"] = plan
                # Update context manager with the plan
                cm._recent.clear()
                cm.add_message("user",
                    f"{context.task}\n\n"
                    f"Plan generated with {len(plan.steps)} steps. Execute in order."
                )
                logger.info("Plan generated: %s (%d steps)", plan.goal, len(plan.steps))
                if on_progress:
                    on_progress("info", f"Plan generated: {plan.goal} ({len(plan.steps)} steps)")
                # ── ExecutionLog: plan ──────────────────────
                _l = context.metadata.get("__log__")
                if _l:
                    try:
                        _l.record_step(phase="plan", action="generate_plan",
                                       success=True,
                                       metadata={"goal": plan.goal[:200], "steps": len(plan.steps)})
                    except Exception:
                        pass
            except Exception as e:
                logger.error("Plan generation failed: %s", e)
                _l = context.metadata.get("__log__")
                if _l:
                    try:
                        _l.record_step(phase="plan", action="generate_plan",
                                       success=False, error=str(e)[:500])
                    except Exception:
                        pass
                return AgentAction.fail(f"Plan generation failed: {e}")

        tool_defs = context.tools.definitions() if context.tools else []

        # Inject memory context if available
        memory_context = ""
        if context.memory:
            wm = context.memory.working(context.agent_id)
            mem_items = wm.search("")  # all entries
            if mem_items:
                mem_lines = []
                for e in mem_items[:10]:  # max 10 entries
                    val_str = str(e.value)[:120]
                    mem_lines.append(f"- {e.key}: {val_str}")
                memory_context = "\n## Memory Context\n" + "\n".join(mem_lines) + "\n"

        bc = context.backend.config if hasattr(context.backend, "config") else {}
        system_prompt = _build_system_prompt(backend=context.backend) + memory_context
        # 注入当前修复状态：让模型每个 step 都看到"测试失败/已读N文件/必须修改"
        system_prompt += _build_fix_state_directive(context)

        # ── Inject repository structure into system prompt ────────
        repo_info: RepositoryInfo | None = context.metadata.get("repo_info")
        if repo_info and repo_info.file_count > 0:
            system_prompt += "\n\n" + RepositoryScanner.format_compact(repo_info)
            system_prompt += (
                "\n\n## Workspace Rules\n"
                "The project files listed above are in the project root. "
                "Your workspace sandbox (./workspace/) is for temporary output. "
                "DO NOT scan the workspace/ or .state/ directories for project source code."
            )

        # Inject execution plan into system prompt
        plan = context.metadata.get("execution_plan")
        if plan:
            system_prompt += _PLAN_EXECUTION_PROMPT + "\n" + format_plan_summary(plan)
            cm.set_plan(f"{plan.goal} ({len(plan.steps)} steps)")

        # Get messages from ContextManager
        ctx_messages = cm.get_context()
        request = BackendRequest(
            messages=ctx_messages,
            tools=tool_defs or None,
            system_prompt=system_prompt,
            max_tokens=bc.get("max_tokens", 4096),
            temperature=bc.get("temperature", 0.7),
        )

        # ── Backend invocation (with auto-retry) ──────────────
        # For non-BackendError transient failures (network issues, 503, etc.),
        # retry with exponential backoff (1s, 2s, 4s…), up to max_retries.
        # BackendError (401/400/model-not-found) is propagated immediately, no retry.
        max_retries = int(context.config.get("retry.max_attempts", 3))

        last_error: Exception | None = None
        response: BackendResponse | None = None

        for attempt in range(max_retries):
            try:
                response = await run_sync(context.backend.invoke, request)
                last_error = None
                break
            except BackendError:
                raise  # BackendError propagates immediately, no retry
            except Exception as e:
                last_error = e
                if attempt < max_retries - 1:
                    wait = 2 ** attempt
                    logger.info(
                        "Backend call failed (attempt %d/%d), waiting %.1fs: %s",
                        attempt + 1, max_retries, wait, e,
                    )
                    await asyncio.sleep(wait)
                else:
                    logger.error(
                        "Backend call permanently failed (attempt %d/%d): %s",
                        attempt + 1, max_retries, e,
                    )

        if last_error or response is None:
            _l = context.metadata.get("__log__")
            if _l:
                try:
                    _l.record_step(phase="error", action="backend_failure",
                                   success=False,
                                   error=str(last_error or "Backend produced no response"))
                except Exception:
                    pass
            return AgentAction.fail(str(last_error or "Backend produced no response"))

        if response.content:
            cm.add_message("assistant", response.content)

        if response.tool_calls:
            on_progress = context.metadata.get("on_progress")
            # Count tool call success/failure for this round
            step_tool_ok = 0
            step_tool_fail = 0
            guard: LoopGuard | None = context.metadata.get("loop_guard")
            had_modification = False
            round_tests_passed = False
            # ── Read-limit tracking ──────────────────────────
            reads_without_test = context.metadata.get("reads_without_test", 0)
            has_run_test = context.metadata.get("has_run_test", False)
            # ── Fix-driving tracking (test failed → must modify) ──
            # 测试失败后：允许有限读取分析，但达到阈值仍无修改 → 强制注入修改提示。
            fix_read_limit = int(context.config.get("fix.read_limit", 3))
            test_failed = context.metadata.get("test_failed", False)
            reads_after_fail = context.metadata.get("reads_after_fail", 0)
            # ── Repair phase 状态机（跨步持久化）──
            # 驱动：idle→diagnose(测试失败)→plan(注入修复计划)→edit(修改成功)→verify(测试通过)→done
            repair_phase = context.metadata.get("repair_phase", "idle")
            repair_plan_injected = context.metadata.get("repair_plan_injected", False)
            for tc in response.tool_calls:
                if on_progress:
                    on_progress("tool", tc.name)
                tctx = ToolContext(
                    agent_id=context.agent_id,
                    workspace_path=context.workspace or Path("."),
                    project_path=context.config.get("project_path"),
                    config=context.config,
                    timeout=context.config.get("timeout", 30),
                )
                _ts = _now_ms()
                # 用 execute_tool 容错分发：LLM 幻觉出不存在的工具名时返回
                # 结构化 tool_not_found 错误并写入日志，Agent 据此重新规划。
                # ── FixDriving 硬拦截：强制修改阶段禁止继续读取 ──
                # 测试失败且已达到读取阈值后，read_file/grep 只会让 agent 继续空转。
                # 结构性阻断读取，逼 agent 下一步只能是 edit/write_file（或重跑 pytest）。
                _force_edit = context.metadata.get("force_edit", False)
                # FixDriving 强制修改阶段：拦截所有非写、非展示工具。
                # 若只拦 read_file/grep，执着的 LLM 可反复用 shell_exec 重跑失败的
                # pytest 来"逃逸"——每次失败重跑都会把 reads_after_fail 清零，
                # 永远到不了 edit（无限 read → LoopGuard → 超时）。
                # 因此这里拦截 read_file/grep/shell_exec/git，让唯一可推进的动作
                # 就是 edit/write_file；force_edit 仅在一次成功的写操作后清除。
                if _force_edit and tc.name not in ("edit", "write_file", "show_to_user"):
                    result = ToolResult.err(
                        "[FixDriving] 强制修改阶段：测试已失败且你已读取足够多文件。"
                        "禁止再次 read_file/grep/shell_exec/git —— 立即用 edit 或 "
                        "write_file 修改代码修复失败的测试。"
                    )
                else:
                    result = context.tools.execute_tool(tc.name, tc.params, tctx)
                _dur = _now_ms() - _ts
                if result.success:
                    step_tool_ok += 1
                    if tc.name in ("write_file", "edit", "git"):
                        had_modification = True
                        # 修改成功 → 退出修复态（已产生进展），进入"修改"阶段
                        test_failed = False
                        reads_after_fail = 0
                        context.metadata["force_edit"] = False
                        repair_phase = "edit"
                        # ── CompletionState: 任何修改使旧测试结果失效 ──
                        if completion:
                            completion.record_modification(step=context.step_count)
                else:
                    step_tool_fail += 1
                # ── 测试运行检测（无论成败）──
                # 关键：pytest 失败时 ShellTool 走 error 分支（result.success=False），
                # 必须依然检测，否则 agent 永远不知道"测试失败"、无法进入修复阶段。
                # 因此该检测放在 result.success 分支之外，pytest 成败都执行。
                if tc.name == "shell_exec":
                    _cmd_l = str(tc.params.get("command", "")).lower()
                    if "pytest" in _cmd_l or "python -m pytest" in _cmd_l:
                        has_run_test = True
                        reads_without_test = 0
                if tc.name in ("shell_exec", "git"):
                    _cmd_l = str(tc.params.get("command", "")).lower()
                    if ("pytest" in _cmd_l or "unittest" in _cmd_l
                            or "nosetests" in _cmd_l):
                        exit_code = int(
                            (result.metadata or {}).get("exit_code", 0)
                        )
                        # 失败时 output 为空、错误在 error 里；合并供 verify_test_output 判定
                        test_out = (result.output or "") + (result.error or "")
                        passed = (exit_code == 0) and verify_test_output(test_out).passed
                        # ── P0 基线测试数回退防护（防伪造成功）──
                        # 首次运行记录应运行的总测试数；后续"绿色"运行若实际执行
                        # 总数低于基线（测试被反选/删除/忽略，如 pyproject addopts
                        # 反选或 shell 通配符删除），即使 exit 0 + passed 也视为
                        # 未真正验证业务代码，不得计入完成。
                        _totals = parse_test_totals(test_out)
                        _total_tests = (_totals["passed"] + _totals["failed"]
                                        + _totals["errors"])
                        _baseline = context.metadata.get("baseline_test_count")
                        # partial_green：子集全绿但未覆盖完整基线。
                        # 它不是 failed，但也不能算 full_green / complete。
                        _scope_complete = True
                        if _baseline is None:
                            if _total_tests > 0:
                                context.metadata["baseline_test_count"] = _total_tests
                        elif passed and _baseline > 0 and _total_tests < _baseline:
                            logger.warning(
                                "TestGuard: test count %d < baseline %d — "
                                "partial green, forcing full-suite re-run",
                                _total_tests, _baseline,
                            )
                            _scope_complete = False
                        if completion:
                            completion.record_test_result(
                                exit_code=exit_code,
                                passed=passed,
                                step=context.step_count,
                                scope_complete=_scope_complete,
                            )
                        if passed:
                            round_tests_passed = True
                            # 测试通过 → 退出修复态，清空失败后读取计数，进入"验证"阶段
                            test_failed = False
                            reads_after_fail = 0
                            repair_phase = "verify"
                            # 立即持久化：后续可能因全绿硬终止提前 return，避免阶段停留在 edit
                            context.metadata["repair_phase"] = "verify"
                            if _scope_complete:
                                # ── full_green：达到基线 + exit 0 + verify 通过 ──
                                # 累计"测试全绿"次数；一旦 ≥1 即具备硬终止资格，
                                # 防止 success → success → success 无限循环。
                                prev_green = context.metadata.get("test_success_count", 0)
                                context.metadata["test_success_count"] = prev_green + 1
                                # 清空子集未覆盖标记（已覆盖完整基线）。
                                context.metadata["test_scope_incomplete"] = False
                                context.metadata["required_next_action"] = ""
                            else:
                                # ── partial_green：不 complete、不累计 success ──
                                # 明确告知模型：本次通过但未覆盖基线，必须运行完整套件。
                                # 结构化 recovery 状态：注入下一轮 Agent 上下文，让
                                # 模型"看到"它只验证了子集，下一步必须跑完整套件，
                                # 而不是继续 read/edit 或重复跑同一个子集。
                                context.metadata["test_scope_incomplete"] = True
                                context.metadata["tests_passed"] = False
                                context.metadata["required_next_action"] = "run_full_test_suite"
                                cm.add_message("user",
                                    "[TEST_SCOPE_INCOMPLETE] 当前测试运行通过，但只执行了 "
                                    f"{_total_tests}/{_baseline} 个测试（未覆盖完整基线套件）。\n"
                                    "本次结果不能作为最终完成验证。\n"
                                    "不要继续随机读取、修改文件或重复运行同一个子集。\n"
                                    "下一步必须运行完整测试套件：python -m pytest -q\n"
                                    "只有完整测试数量达到基线且全部通过后才能完成任务。"
                                )
                        else:
                            # 测试失败 → 进入修复态：强制后续进入修改阶段
                            if not test_failed:
                                # 首次进入修复态才清零读数。若每次失败 pytest 都清零，
                                # agent 会用 read→pytest→read 无限交替逃逸 FixDriving
                                # （读数永远攒不满 fix.read_limit，force_edit 永不激活），
                                # 直到耗尽 max_steps 提前终止。
                                reads_after_fail = 0
                            test_failed = True
                            # 一旦测试曾失败，则只有"全绿重测"才能判定完成（粘性标记）
                            context.metadata["tests_ever_failed"] = True
                            if repair_phase != "edit":
                                repair_phase = "diagnose"
                            # ── 首次失败注入具体修复计划（让 Agent 制定修改方案而非只分析）──
                            if not repair_plan_injected:
                                repair_plan_injected = True
                                repair_phase = "plan"
                                _fail_text = (result.output or "") + (result.error or "")
                                # ── P2: 语义化失败解析 ──
                                # ── P1: 基于语义失败生成有序修复计划 ──
                                # 整个解析/生成块用 try 兜底：即使模块缺失或解析失败，
                                # 也不能让 Agent step 崩溃（Repair Plan 是增强而非硬依赖）。
                                _plan_msg = ""
                                try:
                                    from zmai.swe.failure import (
                                        format_failure, parse_test_failure,
                                    )
                                    from zmai.swe.fix_planner import (
                                        format_plan, generate_fix_plan,
                                    )
                                    _issue = parse_test_failure(
                                        _fail_text,
                                        context.config.get("project_path"),
                                    )
                                    if _issue is not None:
                                        _plan = generate_fix_plan(_issue)
                                        context.metadata["last_failure_issue"] = _issue
                                        _plan_msg = "\n" + format_failure(_issue) \
                                                    + "\n" + format_plan(_plan)
                                except Exception:
                                    _plan_msg = ""
                                cm.add_message("user",
                                    "[Repair Plan] 测试失败。请按以下闭环立即修复（不要只读不修）：\n"
                                    "1. 诊断：从上面的失败信息找出根因（必要时只读取最相关的 1-2 个源码文件）\n"
                                    "2. 计划：明确要修改哪个文件、添加或改动什么代码\n"
                                    "3. 修改：用 `edit` 或 `write_file` 工具实施修改\n"
                                    "4. 验证：重新运行 `python -m pytest`，直到通过\n"
                                    f"\n失败分析：\n{_fail_text[:800]}\n{_plan_msg}"
                                )
                                _l = context.metadata.get("__log__")
                                if _l:
                                    try:
                                        _l.record_step(phase="repair", action="inject_plan",
                                                       success=False,
                                                       metadata={"cycle": context.metadata.get("repair_cycle", 0)})
                                    except Exception:
                                        pass
                # ── Track reads before test ─────────────
                if tc.name == "read_file" and not has_run_test:
                    reads_without_test += 1
                # ── Fix-driving: 测试失败后只读不修，累计读取计数 ──
                if tc.name == "read_file" and test_failed and not had_modification:
                    reads_after_fail += 1
                # ── LoopGuard: record every tool call ─────
                if guard:
                    guard.record_tool_call(
                        name=tc.name,
                        params=tc.params,
                        success=result.success,
                        output=result.output or "",
                        error=result.error,
                    )
                # ── 修复效率统计（供审计，不改变行为）──
                _stats(context, total_calls=1)
                if tc.name == "read_file":
                    _stats(context, read_calls=1)
                    _rp = (tc.params or {}).get("path", "")
                    _read_seen = context.metadata.setdefault("_read_files_seen", set())
                    if _rp not in _read_seen:
                        _read_seen.add(_rp)
                        _stats(context, unique_read_files=1)
                    elif (result.metadata or {}).get("cached"):
                        _stats(context, duplicate_reads=1)
                if tc.name == "shell_exec":
                    _cmd = str((tc.params or {}).get("command", "")).lower()
                    if "pytest" in _cmd:
                        _stats(context, pytest_calls=1)
                        # 无修改重跑：距上次修改的 pytest 且结果相同 → 无进展重跑
                        if not had_modification:
                            _stats(context, unchanged_pytest_calls=1)
                if tc.name in ("edit", "write_file"):
                    _stats(context, edit_calls=1)
                # ── ExecutionLog: tool_call + tool_result ──
                _l = context.metadata.get("__log__")
                if _l:
                    try:
                        _l.record_step(phase="tool_call", action=tc.name,
                                       tool_name=tc.name, tool_input=tc.params,
                                       success=True, duration_ms=_dur)
                        _l.record_step(phase="tool_result", action=tc.name,
                                       tool_name=tc.name,
                                       success=result.success,
                                       tool_output=result.output,
                                       error=result.error,
                                       duration_ms=_dur)
                    except Exception:
                        pass
                if on_progress:
                    tag = "OK" if result.success else "FAIL"
                    brief = (result.output or result.error or "")[:80]
                    on_progress("result", f"{tag}: {brief}")
                # Auto-save key tool results to memory
                if context.memory:
                    wm = context.memory.working(context.agent_id)
                    wm.store(f"tool:{tc.name}", {
                        "success": result.success,
                        "output": (result.output or "")[:200],
                        "error": result.error,
                    }, namespace="tools")
                # Add tool results via ContextManager
                cm.add_tool_result(
                    name=tc.name,
                    success=result.success,
                    output=result.output or "",
                    error=result.error,
                )
            # ── LoopGuard: track no-modification steps ──
            if guard and not had_modification:
                guard.record_no_modification()

            # Accumulate tool execution stats to metadata for finalize
            prev_ok = context.metadata.get("tool_calls_ok", 0)
            prev_fail = context.metadata.get("tool_calls_fail", 0)
            context.metadata["tool_calls_ok"] = prev_ok + step_tool_ok
            context.metadata["tool_calls_fail"] = prev_fail + step_tool_fail

            # ── 硬终止条件（最高优先级）：测试通过 → 任务完成，立即结束执行循环 ──
            # 一旦成立必须立即 return complete，禁止继续进入下一轮 shell/read/test。
            # 双重判定：
            #   1) CompletionState.should_complete()（全绿 + exit0 + 无后续修改）
            #   2) 防御机制：test_success_count >= 1 —— 已有一次全绿即强制停止，
            #      杜绝 success → success → success 无限循环。
            _green_once = context.metadata.get("test_success_count", 0) >= 1
            if completion and (completion.should_complete() or _green_once):
                context.metadata["tests_passed"] = True
                context.metadata["messages"] = cm.get_context()
                logger.info(
                    "CompletionState satisfied — terminating (%s, step %d): %s",
                    context.agent_id, context.step_count, completion.summary(),
                )
                _log_stop()
                _l = context.metadata.get("__log__")
                if _l:
                    try:
                        _l.record_step(phase="complete", action="tests_passed",
                                       success=True,
                                       metadata={"step": context.step_count,
                                                 "reason": completion.summary()})
                    except Exception:
                        pass
                return AgentAction.complete(
                    output=f"Tests passed. Task completed in {context.step_count} step(s)."
                )

            # ── Read-limit enforcement ─────────────────────────
            context.metadata["reads_without_test"] = reads_without_test
            context.metadata["has_run_test"] = has_run_test
            context.metadata["test_failed"] = test_failed
            context.metadata["reads_after_fail"] = reads_after_fail
            # ── Repair phase 状态机持久化（跨步驱动诊断→计划→修改→验证）──
            context.metadata["repair_phase"] = repair_phase
            context.metadata["repair_plan_injected"] = repair_plan_injected
            read_limit = int(context.config.get("workflow.read_limit", 8))
            if not has_run_test and reads_without_test >= read_limit:
                cm.add_message("user",
                    f"[Workflow] 你已读取 {reads_without_test} 个文件但尚未运行测试。\n"
                    f"请立即运行 `python -m pytest`（或项目的测试命令），根据测试失败信息修复代码。\n"
                    f"在运行测试之前不要再读取更多文件。"
                )
                # Reset counter to avoid repeated messages
                context.metadata["reads_without_test"] = 0
                context.metadata["messages"] = cm.get_context()
                return AgentAction.cont(
                    output=f"Read limit reached ({reads_without_test} reads, no tests run yet)"
                )

            # ── Fix-driving enforcement ────────────────────────
            # 测试失败后只读不修达到阈值 → 强制进入修改阶段（读取永远无法让测试通过）。
            if test_failed and reads_after_fail >= fix_read_limit:
                logger.warning(
                    "FixDriving: test failed, %d reads after failure without modification — forcing fix phase",
                    reads_after_fail,
                )
                _stats(context, fixdriving_activations=1)
                if guard:
                    guard.reset()  # 给予全新方向，避免 no_progress 误伤分析阶段
                repair_phase = "plan"  # 强制回到"计划→修改"，阻断只读停滞
                # 结构性拦截：下一轮起禁止 read_file/grep，直到 agent 产生一次修改
                context.metadata["force_edit"] = True
                cm.add_message("user",
                    f"[FixDriving] 测试已失败，但你已读取 {reads_after_fail} 个相关文件仍未修改代码（当前阶段：{repair_phase}）。\n"
                    f"读取不会让测试通过——你必须做出修改。\n"
                    f"请停止读取，立即用 `edit` 或 `write_file` 工具修改代码来修复失败的测试。\n"
                    f"先做出你认为正确的修改，然后重新运行 `python -m pytest` 验证。"
                )
                context.metadata["reads_after_fail"] = 0
                context.metadata["messages"] = cm.get_context()
                # ── ExecutionLog: fix_driving ─────────
                _l = context.metadata.get("__log__")
                if _l:
                    try:
                        _l.record_step(phase="fix_driving", action="force_modify",
                                       success=False,
                                       metadata={"reads_after_fail": reads_after_fail,
                                                 "limit": fix_read_limit})
                    except Exception:
                        pass
                return AgentAction.cont(
                    output=f"Test failed, forced into fix phase after {reads_after_fail} reads"
                )

            # ── LoopGuard: check for loops ────────────────────
            if guard:
                loop_result = guard.check()
                if loop_result.blocked:
                    logger.warning(
                        "LoopGuard blocked: reason=%s, details=%s",
                        loop_result.reason, loop_result.details,
                    )
                    # Reset guard so next step starts fresh
                    guard.reset()
                    # ── 修复效率统计 ──
                    _stats(context, loopguard_blocks=1)
                    # ── 结构化恢复信号：列出最近的无效动作 + 规定下一步策略 ──
                    recent = [
                        f"- {c['name']} {str(c.get('signature','')).split(':',1)[-1][:80]}"
                        for c in guard.get_recent_calls(6)
                    ]
                    recent_txt = "\n".join(recent) if recent else "- (无)"
                    # 升级计数器：同一失败反复被阻断 → 不再重复相同动作，转入强制定向修改
                    recoveries = context.metadata.get("loop_recovery_count", 0) + 1
                    context.metadata["loop_recovery_count"] = recoveries
                    recover_limit = int(context.config.get("loop_guard.recover_limit", 2))
                    if recoveries >= recover_limit:
                        context.metadata["force_edit"] = True
                        context.metadata["repair_phase"] = "plan"
                    recovery_msg = (
                        f"[LoopGuard][LoopRecovery] 检测到重复的无进展动作（第 {recoveries} 次恢复）。\n"
                        f"原因: {loop_result.reason}（{loop_result.suggestion}）\n"
                        f"之前的无效动作:\n{recent_txt}\n"
                        f"下一步必须:\n"
                        f"- 不要重复上述任何 read/shell 动作。\n"
                        f"- 使用当前失败证据定位一个具体修复目标。\n"
                        f"- 若根因证据已足够，直接用 `edit`/`write_file` 修改业务文件。\n"
                        f"- 不要修改测试文件。\n"
                        f"- 若证据不足，只允许一次新的定向读取（或重跑一次 pytest）。"
                    )
                    if recoveries >= recover_limit:
                        recovery_msg += (
                            f"\n\n已连续 {recoveries} 次循环恢复仍未推进——进入修复升级："
                            f"你现在必须直接修改代码，禁止再读取无关文件。"
                        )
                    # ── scope-aware 恢复：测试子集未覆盖完整基线时的循环，强制跑完整套件 ──
                    if context.metadata.get("test_scope_incomplete"):
                        recovery_msg += (
                            "\n\n[TestScope] 检测到你仍在重复执行测试子集，未覆盖完整基线套件。"
                            "不要再重复运行同一个子集或继续 read/edit。"
                            "下一步只能运行完整测试套件：python -m pytest -q"
                        )
                        context.metadata["required_next_action"] = "run_full_test_suite"
                    cm.add_message("user", recovery_msg)
                    # ── ExecutionLog: loop_guard ─────────
                    _l = context.metadata.get("__log__")
                    if _l:
                        try:
                            _l.record_step(phase="loop_guard", action="blocked",
                                           success=False,
                                           metadata=loop_result.details)
                        except Exception:
                            pass
                    context.metadata["messages"] = cm.get_context()
                    return AgentAction.cont(
                        output=f"LoopGuard blocked: {loop_result.reason} (recovery {recoveries})"
                    )

            # Context compaction
            cm.compact()

            # Backward compat: sync to metadata["messages"]
            context.metadata["messages"] = cm.get_context()

            return AgentAction.cont(output=f"Executed {len(response.tool_calls)} tools")

        # ── Pre-completion check: is the Plan fully executed? ──
        plan = context.metadata.get("execution_plan")
        if plan and not plan.is_finished:
            replan_count = context.metadata.get("replan_count", 0)
            if replan_count < MAX_REPLANS:
                context.metadata["replan_count"] = replan_count + 1
                logger.info(
                    "Plan not finished, replanning (attempt %d/%d)",
                    replan_count + 1, MAX_REPLANS,
                )
                # Clear old plan, next step will auto-regenerate
                context.metadata.pop("execution_plan", None)
                cm.add_message("user",
                    f"Plan not fully executed ({plan.completed_steps}/{len(plan.steps)} steps done). "
                    f"Generate a new execution plan for the remaining work."
                )
                context.metadata["messages"] = cm.get_context()
                # ── ExecutionLog: replan ───────────────────
                _l = context.metadata.get("__log__")
                if _l:
                    try:
                        _l.record_step(phase="replan", action="replan",
                                       success=True,
                                       metadata={"attempt": replan_count + 1,
                                                  "max": MAX_REPLANS,
                                                  "completed": plan.completed_steps,
                                                  "total": len(plan.steps)})
                    except Exception:
                        pass
                return AgentAction.cont(
                    output=f"Replanning (attempt {replan_count + 1}/{MAX_REPLANS})"
                )
            else:
                logger.warning(
                    "Max replan attempts (%d) exceeded, Plan partially complete: %d/%d",
                    MAX_REPLANS, plan.completed_steps, len(plan.steps),
                )

        # Context compaction
        cm.compact()

        # ── Objective verification ────────────────────────────
        # Agent must not claim completion based solely on tool call success.
        vresult = self._auto_verify(context)
        if vresult is not None:
            context.metadata["verification"] = vresult
            # ── ExecutionLog: verification ─────────────────
            _l = context.metadata.get("__log__")
            if _l:
                try:
                    _l.record_step(phase="verification", action="auto_verify",
                                   success=vresult.passed,
                                   tool_output=vresult.summary[:2000],
                                   metadata={
                                       "checks": len(vresult.checks),
                                       "passed_checks": len(vresult.passed_checks),
                                       "failed_checks": len(vresult.failed_checks),
                                   })
                except Exception:
                    pass

            if not vresult.passed:
                logger.info("Verification failed: %s", vresult.summary)
                cm.add_message("user",
                    f"[Verification Results]\n{vresult.summary}\n"
                    f"{len(vresult.failed_checks)} check(s) failed. Please fix and retry."
                )
                # Inject failure info for the LLM to attempt fixing
                for fc in vresult.failed_checks[:3]:
                    cm.add_message("user",
                        f"[Check: {fc.name}]\n"
                        f"Strategy: {fc.strategy}\n"
                        f"Evidence: {fc.evidence[:200]}\n"
                        f"Error: {fc.error or 'none'}"
                    )
                context.metadata["messages"] = cm.get_context()
                return AgentAction.cont(
                    output=f"Verification failed: {vresult.summary}"
                )

            # ── CompletionState: 客观验证通过 → 标记任务目标已达成 ──
            if completion:
                completion.mark_objective_met()

        # Backward compat: sync to metadata["messages"]
        context.metadata["messages"] = cm.get_context()

        # ── ExecutionLog: step complete ──────────────────
        _l = context.metadata.get("__log__")
        if _l:
            try:
                _l.record_step(phase="complete", action="step_complete",
                               success=True,
                               metadata={"step": context.step_count})
            except Exception:
                pass

        # ── 修复审计：修改后未全绿重测且测试曾失败 → 不得判定 completed ──
        # 防止"edit 后 end_turn 却仅凭 file_exists/git_diff 被误判完成"：
        # 一旦本任务测试曾失败，就必须先有一次覆盖完整基线套件的全绿重测
        # （completion.tests_complete）才能完成。否则强制进入重测，绝不带着
        # 未通过的测试 claim 完成。tests_complete=False 同时覆盖两种场景：
        #   a) 从未全绿（tests_passed=False）
        #   b) partial_green：子集通过但未达基线（tests_passed=True, tests_complete=False）
        _tests_failed_ever = context.metadata.get("tests_ever_failed", False)
        if _tests_failed_ever and completion and not completion.tests_complete:
            logger.warning(
                "Completion blocked: tests failed earlier but no full-scope green "
                "re-run; forcing full-suite pytest re-test instead of completing"
            )
            cm.add_message("user",
                "[Workflow] 你修改了代码，但自上次失败后还没有一次覆盖完整测试套件的"
                "全绿运行（部分测试通过不能作为完成验证）。\n"
                "不要停止——请运行完整测试套件 `python -m pytest -q` 验证你的修改。\n"
                "只有完整测试全部通过（达到基线数量）才算完成。"
            )
            context.metadata["messages"] = cm.get_context()
            _l = context.metadata.get("__log__")
            if _l:
                try:
                    _l.record_step(phase="completion_guard", action="block_unverified",
                                   success=False,
                                   metadata={"tests_failed_ever": True,
                                             "tests_passed": completion.tests_passed,
                                             "tests_complete": completion.tests_complete})
                except Exception:
                    pass
            return AgentAction.cont(output="Modified but not re-tested; forcing pytest re-run")

        return AgentAction.complete(output=response.content or "")

    def _auto_verify(self, context: AgentContext) -> VerificationResult | None:
        """Automatically generate and run objective verification.

        Selects appropriate verification strategies based on context (modified files, tool results).
        Returns None if no checks are available (treated as passed).
        """
        cm: ContextManager | None = context.metadata.get("cm")

        modified_files = []
        tool_results = []

        if cm:
            modified_files = cm._modified_files
            tool_results = cm._tool_results

        # Supplement from metadata tool stats
        if not tool_results:
            ok = context.metadata.get("tool_calls_ok", 0)
            fail = context.metadata.get("tool_calls_fail", 0)
            if ok == 0 and fail == 0:
                return None  # No tools executed, skip verification

        if not modified_files and not tool_results:
            return None  # Nothing to check

        ws_path = context.workspace
        result = auto_generate_checks(modified_files, tool_results, ws_path)
        logger.info("Verification complete: %s (%d/%d)", result.summary,
                     sum(1 for c in result.checks if c.passed), len(result.checks))
        return result

    async def finalize(self, context: AgentContext) -> AgentResult:
        """Finalize agent execution.

        Determines actual status by priority (highest first):
          1. timed_out → TIMEOUT (max_steps exhausted)
          2. step_failed → FAILED (step() returned fail with error)
          3. replan exhausted + Plan incomplete → FAILED
          4. All tools failed → FAILED
          5. Verification failed → FAILED
          6. Everything else → COMPLETED
        """
        timed_out = context.metadata.get("timed_out", False)
        step_failed = context.metadata.get("step_failed", False)
        tool_ok = context.metadata.get("tool_calls_ok", 0)
        tool_fail = context.metadata.get("tool_calls_fail", 0)
        replan_count = context.metadata.get("replan_count", 0)
        plan = context.metadata.get("execution_plan")

        error: str | None = None

        if timed_out:
            status = AgentState.TIMEOUT
            logger.info(
                "SWEAgent timeout: %s (%d steps)",
                context.agent_id, context.step_count,
            )
        elif step_failed:
            status = AgentState.FAILED
            error = str(step_failed) if step_failed is not True else None
            logger.info(
                "SWEAgent step failed: %s (%s)",
                context.agent_id, error or "unknown error",
            )
        elif replan_count >= MAX_REPLANS and plan and not plan.is_finished:
            status = AgentState.FAILED
            logger.info(
                "SWEAgent Plan incomplete with replan exhausted: %s (%d/%d steps, %d replans)",
                context.agent_id, plan.completed_steps, len(plan.steps), replan_count,
            )
        # 测试曾全绿通过是"目标已达成"的决定性信号。
        # 修复过程中必然会出现多次失败 pytest（tool_fail 累积），
        # 若最终测试已通过，不得再被 tool_fail 比例误判为 FAILED。
        elif (
            not (
                context.metadata.get("tests_passed", False)
                or context.metadata.get("test_success_count", 0) >= 1
            )
            and tool_fail > 0
            and (tool_ok == 0 or tool_fail >= tool_ok)
        ):
            status = AgentState.FAILED
            logger.info(
                "SWEAgent tool calls failed: %s (%d steps, %d/%d tool calls failed)",
                context.agent_id, context.step_count, tool_fail, tool_fail + tool_ok,
            )
        elif (
            not (
                context.metadata.get("tests_passed", False)
                or context.metadata.get("test_success_count", 0) >= 1
            )
            and (vresult := context.metadata.get("verification")) is not None
            and not vresult.passed
        ):
            # Check verification — failed verification must not result in COMPLETED.
            # 但"测试曾全绿通过"是目标已达成的决定性信号（同上 tool_fail 分支）：
            # 修复过程中 mid-run 的 auto_verify 可能留下一次过期的 failed 结果，
            # 不得用它覆盖一次合法的全绿完成。
            status = AgentState.FAILED
            logger.info(
                "SWEAgent verification failed: %s (%s)",
                context.agent_id, vresult.summary,
            )
        else:
            status = AgentState.COMPLETED

        result = AgentResult(
            agent_id=self.agent_id,
            status=status,
            output=context.metadata.get("output", ""),
            steps=context.step_count,
            error=error,
            metadata={"swe_stats": context.metadata.get("swe_stats", {})},
        )
        logger.info(
            "SWEAgent finished: %s (%d steps, status=%s)",
            self.agent_id, result.steps, status.value,
        )
        return result
