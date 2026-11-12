"""Runtime — orchestrates the full Agent lifecycle."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from zmai.config import Config
from zmai.gateway import Backend, PluginRegistry, ToolRouter
from zmai.memory import MemoryManager
from zmai.runtime.lifecycle import LifecycleManager
from zmai.runtime.scheduler import Scheduler
from zmai.runtime.state import StateManager
from zmai.tool import ToolRegistry
from zmai.tool.base import remaining_wall_seconds
from zmai.workspace import Workspace

logger = logging.getLogger("zmai.runtime")


def _redact_error(error: BaseException) -> str:
    message = str(error)
    for name, value in os.environ.items():
        if (any(part in name.upper()
                for part in ("KEY", "TOKEN", "SECRET", "PASSWORD"))
                and len(value) >= 8):
            message = message.replace(value, "[REDACTED]")
    return re.sub(
        r"(?i)(?:sk-[A-Za-z0-9_-]{12,}|"
        r"(?:api[_-]?key|token|secret|password)\s*[:=]\s*\S+)",
        "[REDACTED]", message)

ProgressCB = Callable[[str, str], None] | None


def _persist_trajectory(ws_path: Path | None, agent_id: str, step: int,
                        ctx: Any) -> None:
    """把最近一次模型请求快照写入 workspace/.state/trajectory.json。

    记录的是 SWEAgent.step() 里模型适配器实际收到的那份输入（system_prompt
    独立字段 + messages），不是 metadata["messages"] 的那个子集。

    先写同目录 .tmp 再 os.replace：进程在写入中途被硬杀时，磁盘上要么是
    完整的旧快照，要么是完整的新快照，不会留下半截 JSON。写入失败只告警，
    不影响 Agent 执行。
    """
    try:
        snap = ctx.metadata.get("__last_request__")
        if ws_path is None or not snap:
            return
        state_dir = Path(ws_path) / ".state"
        state_dir.mkdir(parents=True, exist_ok=True)
        target = state_dir / "trajectory.json"
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_text(
            json.dumps(
                {
                    "step": step,
                    "agent_id": agent_id,
                    "system_prompt": snap["system_prompt"],
                    "messages": snap["messages"],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        os.replace(tmp, target)
    except Exception as exc:
        logger.warning("轨迹持久化失败 (忽略): %s", exc)


@dataclass
class RuntimeInfo:
    running_agents: int = 0
    completed_agents: int = 0
    total_agents: int = 0
    uptime_seconds: float = 0.0
    memory_usage: dict[str, Any] = field(default_factory=dict)
    config: dict[str, Any] = field(default_factory=dict)


def _build_system_prompt(backend: Backend) -> str:
    """构建系统提示词，包含 Backend 身份和 SWE Agent 行为指令。"""
    backend_model = backend.model or ""
    name = backend.name or "unknown"
    platform_name = sys.platform
    identity = f"你运行在 {name}"
    if backend_model:
        identity += f" (模型: {backend_model})"
    identity += f"上。当前操作系统: {platform_name}。"

    if platform_name == "win32":
        identity += """

注意: 当前是 Windows 系统。
- 列出文件用 dir 而不是 ls
- 文件路径用反斜杠 \\ 或正斜杠 /
- 执行 .exe 文件不需要前缀
- 打开文件用 start 命令"""

    return identity + """

你是 SWE Agent。你的工作不是写完代码就结束，而是要让用户看到结果。

可用工具:
- read_file: 读取文件
- write_file: 写入文件
- edit: 行级编辑（替换/正则/插入/追加）
- grep: 搜索文本
- shell_exec: 执行命令
- git: git 操作
- show_to_user: 打印内容让用户看到
- open_in_browser: 在浏览器打开 HTML

工作流程（必须遵守）:
1. 理解：搜索代码，阅读文件
2. 执行：写代码、改文件、跑命令
3. 交付：生成网页则打开浏览器，改完代码则展示结果
4. 确认：告知用户已完成，展示成果

禁止：
- 不要写完文件就不管了
- 每个任务结束时必须让用户看到成果"""


class Runtime:
    """ZMAI 运行时主类。"""

    def __init__(self, config: Config | None = None) -> None:
        self._config = config or Config()
        self._lifecycle = LifecycleManager()
        self._state = StateManager()
        self._scheduler = Scheduler(
            max_concurrent=int(self._config.get("runtime.max_concurrent_agents", 10))
        )
        self._workspace = Workspace(
            root=self._config.get("workspace.root", "./workspace"),
        )
        self._tools = ToolRegistry()
        self._gateway = PluginRegistry(config=self._config)
        self._memory = MemoryManager()
        self._tool_router = ToolRouter(self._tools)
        self._tasks: dict[str, asyncio.Task[Any]] = {}
        self._started_at = datetime.now(timezone.utc)
        self._logger = logger

    async def run(
        self,
        agent_id: str,
        task: str,
        backend: str | None = None,
        config: dict[str, Any] | None = None,
        on_progress: ProgressCB = None,
        tool_defs: Any = None,
    ) -> dict[str, Any]:
        """运行 Agent。使用 SWE Agent 执行任务。

        Args:
            agent_id: Agent 唯一标识。
            task: 任务描述。
            backend: Backend 名称（None 使用默认）。
            config: 运行时配置。
            on_progress: 进度回调。
            tool_defs: 工具定义（保留参数）。

        Returns:

        当 config["auto_plan"]=True 时:
          1. 进入 PLANNING 阶段 → 生成 Plan（只读）
          2. 调用 on_plan 回调展示 Plan（须确认）
          3. 确认后进入执行阶段
          4. 拒绝则返回 failed
        """
        from zmai.agent import AgentAction, AgentContext, AgentState
        from zmai.runtime.preflight import check as preflight_check
        from zmai.swe.agent import SWEAgent
        from zmai.swe.evidence import CompletionLedger, decide_terminal, shadow_diff

        run_ledger = CompletionLedger()
        report_run_id = uuid.uuid4().hex
        report_started_at = datetime.now(timezone.utc).isoformat()
        report_started_monotonic = time.monotonic()
        report_project = (config or {}).get("project_path") or Path.cwd()
        fallback_report_workspace = self._workspace._root / ".reports" / report_run_id
        try:
            from zmai.swe.delivery_report import capture_workspace, create_delivery_report

            report_snapshot = capture_workspace(
                report_project, exclude_paths=(fallback_report_workspace,)
            )
        except Exception as exc:
            report_snapshot = None
            logger.warning("交付报告快照初始化失败 (忽略): %s", type(exc).__name__)

        def attach_delivery_report(
            result: dict[str, Any], workspace_path: Path | None = None,
        ) -> dict[str, Any]:
            """Attach report locations without allowing reporting to alter task status."""
            result["run_id"] = report_run_id
            report_workspace = workspace_path or fallback_report_workspace
            try:
                if not report_workspace.resolve().is_relative_to(self._workspace._root):
                    raise ValueError("report workspace escaped configured workspace root")
                report_pointer = create_delivery_report(
                    result=result,
                    task=task,
                    run_id=report_run_id,
                    started_at=report_started_at,
                    elapsed_seconds=time.monotonic() - report_started_monotonic,
                    before=report_snapshot,
                    project_root=report_project,
                    workspace_path=report_workspace,
                )
                report_pointer["workspace_directory"] = report_workspace.relative_to(
                    self._workspace._root
                ).as_posix()
            except Exception as exc:
                logger.warning("交付报告生成失败 (忽略): %s", type(exc).__name__)
                report_pointer = {
                    "status": "failed",
                    "run_id": report_run_id,
                    "reason_code": "report_generation_failed",
                }
            result["delivery_report"] = report_pointer
            if isinstance(result.get("metadata"), dict):
                result["metadata"]["delivery_report"] = report_pointer
            return result

        def record_terminal(status: str, reason: str = "", *, source: str,
                            step: int = -1,
                            observed_kind: str | None = None,
                            decision: Any | None = None) -> dict[str, Any]:
            run_ledger.record_terminal(status, reason, source=source, step=step)
            decision = decision or decide_terminal(run_ledger)
            old_kind = observed_kind or ("failed" if status == "exception" else status)
            diff = shadow_diff(old_kind, decision, point=source, step=step)
            return {
                "evidence_ledger": run_ledger.summary(),
                "evidence_decision": decision.to_dict(),
                "evidence_shadow": [diff],
            }

        # Preflight Check — 在调用 Backend API 之前检查系统状态
        try:
            pf = preflight_check(backend, self._gateway, self._config)
        except Exception as exc:
            error = _redact_error(exc)
            facts = record_terminal("exception", error, source="runtime_preflight")
            return attach_delivery_report({"status": "failed", "agent_id": agent_id,
                                          "error": error, **facts})
        if not pf.passed:
            pf.print()
            facts = record_terminal("failed", "preflight failed", source="runtime_preflight")
            return attach_delivery_report({"status": "failed", "agent_id": agent_id, **facts})

        try:
            self._lifecycle.create(agent_id)
        except Exception as e:
            facts = record_terminal("failed", str(e), source="runtime_lifecycle_create")
            return attach_delivery_report({"status": "failed", "agent_id": agent_id,
                                          "error": str(e), **facts})

        self._state.update(agent_id, status="created", task=task)
        try:
            ws_path = self._workspace.prepare(agent_id)
            backend_inst = self._gateway.get(backend)
        except Exception as exc:
            error = _redact_error(exc)
            facts = record_terminal("exception", error, source="runtime_setup")
            self._lifecycle.fail(agent_id)
            self._state.update(agent_id, status="failed", error=error)
            return attach_delivery_report({"status": "failed", "agent_id": agent_id,
                                          "error": error, **facts})
        run_config = {**(config or {})}
        auto_plan = run_config.get("auto_plan", False)

        # ── Project root：只认调用方显式声明的 ──
        # 不要在这里用 find_project_root() 把 cwd 推断成 project_path：
        # SWEAgent.initialize() 在没有 project_path 时本来就会做同样的推断
        # （并写回 config），预填是纯冗余。代价是真实的：完成门禁要用
        # "config 里有没有 project_path" 区分"这个 run 就是针对该项目的任务"
        # （CLI / benchmark / eval harness 都显式声明）与"只是为了给 agent
        # 提供上下文才推断出的项目根"。预填会把后者洗成前者，于是 "say hello"
        # 这类任务被套上"必须出示完整套件全绿"的门禁
        # （test_runtime / test_mocks / test_agent_lifecycle 实测被卡死）。

        # ── Plan Mode: PLANNING → PLAN_READY → (确认) → EXECUTING ──────
        if auto_plan:
            self._lifecycle.plan(agent_id)
            self._state.update(agent_id, status="planning")
            plan_agent_id = agent_id
            plan_ctx = AgentContext(
                agent_id=plan_agent_id,
                task=task,
                config=run_config,
                backend=backend_inst,
                workspace=ws_path,
                tools=self._tools,
                metadata={"messages": []},
            )
            plan_ctx.metadata["evidence_ledger"] = run_ledger

            from zmai.swe.agent import SWEAgent as _SWEAgent
            _plan_swe = _SWEAgent(plan_agent_id)
            try:
                await _plan_swe.initialize(plan_ctx)
                plan = await _plan_swe.plan(plan_ctx)
            except asyncio.CancelledError:
                facts = record_terminal("cancelled", "Plan generation cancelled",
                                        source="runtime_plan")
                self._lifecycle.cancel(agent_id)
                self._state.update(agent_id, status="cancelled")
                return attach_delivery_report({"status": "cancelled", "agent_id": agent_id,
                                               **facts}, ws_path)
            except Exception as e:
                facts = record_terminal("exception", str(e), source="runtime_plan")
                self._lifecycle.fail(agent_id)
                self._state.update(agent_id, status="failed", error=str(e))
                return attach_delivery_report({"status": "failed", "agent_id": agent_id,
                                               "error": str(e), **facts}, ws_path)

            # Plan 生成成功 → PLAN_READY
            self._lifecycle.plan_ready(agent_id)
            self._state.update(agent_id, status="plan_ready")

            # 通过 on_plan 回调展示 Plan 并等待确认
            on_plan = (config or {}).get("on_plan")
            if on_plan:
                try:
                    approved = on_plan(plan)
                except Exception:
                    approved = False
                if not approved:
                    facts = record_terminal("failed", "Plan 被用户拒绝",
                                            source="runtime_plan_rejected")
                    self._lifecycle.fail(agent_id)
                    self._state.update(agent_id, status="failed",
                                       error="Plan 被用户拒绝")
                    return attach_delivery_report({
                        "status": "failed", "agent_id": agent_id,
                        "error": "Plan 被用户拒绝", **facts,
                    }, ws_path)
            # 自动确认模式（无 on_plan 回调时默认通过）
            self._lifecycle.execute(agent_id)
            self._state.update(agent_id, status="executing")

        else:
            # ── 普通模式：直接执行 ──────────────────────────────────
            self._lifecycle.execute(agent_id)
            self._state.update(agent_id, status="executing")

        try:
            report_snapshot = capture_workspace(report_project, exclude_paths=(ws_path,))
        except Exception as exc:
            report_snapshot = None
            logger.warning("执行前 Git 快照失败 (忽略): %s", type(exc).__name__)

        async def _run_agent() -> dict[str, Any]:
            from zmai.execution import ExecutionLog

            agent = SWEAgent(agent_id)
            ctx = AgentContext(
                agent_id=agent_id,
                task=task,
                config=run_config,
                backend=backend_inst,
                workspace=ws_path,
                tools=self._tools,
                max_steps=int(self._config.get("runtime.max_iterations", 300)),
                metadata={"messages": [], "on_progress": on_progress,
                          "evidence_ledger": run_ledger},
                memory=self._memory,
            )
            if "wall_seconds_limit" in run_config:
                work_seconds = max(0.0, float(run_config["wall_seconds_limit"])
                                   - float(run_config.get("wall_exit_reserve_seconds", 0)))
                run_config["_wall_deadline_monotonic"] = time.monotonic() + work_seconds
            # ── ExecutionLog 初始化 ────────────────────────────
            _log = ExecutionLog(agent_id=agent_id, task=task)
            ctx.metadata["__log__"] = _log
            _log.record_step(phase="init", action="agent_initialize")

            phase = "initialize"
            try:
                await agent.initialize(ctx)
                phase = "memory_restore"
                restored = self._memory.restore(agent_id)
            except asyncio.CancelledError:
                facts = record_terminal(
                    "cancelled", f"Agent cancelled during {phase}",
                    source=f"runtime_{phase}")
                if not self._lifecycle.is_terminal(agent_id):
                    self._lifecycle.cancel(agent_id)
                self._state.update(agent_id, status="cancelled")
                return {"status": facts["evidence_decision"]["kind"],
                        "agent_id": agent_id, "metadata": facts}
            except Exception as exc:
                error_msg = _redact_error(exc)
                facts = record_terminal(
                    "exception", error_msg, source=f"runtime_{phase}")
                _log.record_step(phase="error", action=phase, success=False,
                                 error=error_msg, metadata={"error_type": type(exc).__name__})
                try:
                    _log.persist(str(ws_path / ".state" / "execution_log.json"))
                except Exception:
                    pass
                self._lifecycle.fail(agent_id)
                self._state.update(agent_id, status="failed", error=error_msg)
                return {"status": facts["evidence_decision"]["kind"],
                        "agent_id": agent_id, "steps": 0,
                        "phase": phase, "error_type": type(exc).__name__,
                        "error": error_msg,
                        "metadata": facts}

            # 从 Long-term Memory 恢复到 Working Memory
            if restored > 0 and on_progress:
                on_progress("info", f"restored {restored} memory entries")

            step_count = 0
            output = ""
            action: AgentAction | None = None
            try:
                while step_count < ctx.max_steps:
                    if (run_config.get("_wall_budget_exhausted") or
                            ((remaining := remaining_wall_seconds(run_config)) is not None
                             and remaining <= 0)):
                        ctx.metadata["timed_out"] = True
                        ctx.metadata["wall_budget_exhausted"] = True
                        run_ledger.record_terminal(
                            "timeout", "wall-clock budget exhausted",
                            source="runtime_step_budget", step=step_count)
                        break
                    action = await agent.step(ctx)
                    output = action.output or output
                    step_count += 1
                    self._state.update(agent_id, step_count=step_count)
                    # 每个已完成步骤后立即落盘，下次模型调用前磁盘上已有上一步快照
                    _persist_trajectory(ws_path, agent_id, step_count, ctx)
                    # 外部容器时限会直接杀进程，不能只在 finalize 时保存工具日志。
                    _log.persist(str(ws_path / ".state" / "execution_log.json"))
                    if (run_config.get("_wall_budget_exhausted") or
                            ((remaining := remaining_wall_seconds(run_config)) is not None
                             and remaining <= 0)):
                        ctx.metadata["timed_out"] = True
                        ctx.metadata["wall_budget_exhausted"] = True
                        run_ledger.record_terminal(
                            "timeout", "wall-clock budget exhausted",
                            source="runtime_step_budget", step=step_count)
                        break
                    if action.type in ("complete", "candidate", "fail"):
                        if action.type == "fail":
                            ctx.metadata["step_failed"] = action.error or True
                            run_ledger.record_terminal(
                                "failed", action.error or "AgentAction.fail",
                                source="runtime_agent_action", step=step_count)
                        break

                # max_steps 耗尽（含 max_steps <= 0：一步都没跑）→ 标记超时。
                # action is None 表示循环从未执行过 step；若只按"action 不是
                # complete/fail"判断，finalize 的兜底分支会把零步执行判成
                # COMPLETED —— 那不是任何验证的结果。
                if action is None or action.type not in ("complete", "candidate", "fail"):
                    ctx.metadata["timed_out"] = True
                    run_ledger.record_terminal(
                        "timeout", "maximum step count exhausted",
                        source="runtime_max_steps", step=step_count)

                # ── finalize() 收集结果；Runtime 统一执行终态判定 ──
                result_obj = await agent.finalize(ctx)
                if isinstance(agent, SWEAgent):
                    # finalize() supplies diagnostics only. Runtime is the sole
                    # authority for SWE terminal states and never trusts its proposal.
                    legacy_status = result_obj.status.value
                    decision = decide_terminal(run_ledger)
                    if decision.kind == "continue":
                        terminal_meta = record_terminal(
                            "failed",
                            "terminal evidence insufficient: " + decision.reason,
                            source="runtime_terminal_evidence_fallback",
                            step=step_count, observed_kind=legacy_status)
                    else:
                        terminal_meta = record_terminal(
                            decision.kind, decision.reason,
                            source="runtime_terminal_authority",
                            step=step_count, observed_kind=legacy_status,
                            decision=decision)
                    resolved_kind = terminal_meta["evidence_decision"]["kind"]
                    result_obj.metadata["terminal_legacy_status"] = legacy_status
                    result_obj.status = {
                        "completed": AgentState.COMPLETED,
                        "candidate": AgentState.CANDIDATE,
                        "failed": AgentState.FAILED,
                        "timeout": AgentState.TIMEOUT,
                        "cancelled": AgentState.CANCELLED,
                    }.get(resolved_kind, AgentState.FAILED)
                    if result_obj.status == AgentState.FAILED and not result_obj.error:
                        result_obj.error = terminal_meta["evidence_decision"]["reason"]
                else:
                    terminal_meta = record_terminal(
                        result_obj.status.value, result_obj.error or "",
                        source="runtime_agent_result", step=step_count)
                terminal_meta["evidence_shadow"] = [
                    *result_obj.metadata.get("evidence_shadow", []),
                    *terminal_meta["evidence_shadow"],
                ]
                result_obj.metadata.update(terminal_meta)
                self._memory.persist(agent_id)

                # ── ExecutionLog 持久化到工作区 ──────────────
                try:
                    if ws_path:
                        _log.record_step(phase="finalize", action="agent_finalize",
                                         success=result_obj.status == AgentState.COMPLETED,
                                         metadata={
                                             "status": result_obj.status.value,
                                             "swe_stats": result_obj.metadata.get("swe_stats", {}),
                                             "token_usage": result_obj.metadata.get(
                                                "token_usage", {}
                                            ),
                                         })
                        _log.persist(str(ws_path / ".state" / "execution_log.json"))
                except Exception:
                    pass

                # ── 按 Runtime 的证据判定更新生命周期并返回 ──
                if result_obj.status == AgentState.COMPLETED:
                    self._lifecycle.complete(agent_id)
                    self._state.update(agent_id, status="completed")
                    return {
                        "status": "completed", "agent_id": agent_id,
                        "output": output or (result_obj.output if result_obj else ""),
                        "steps": step_count,
                        "metadata": result_obj.metadata,
                    }
                elif result_obj.status == AgentState.CANDIDATE:
                    self._lifecycle.candidate(agent_id)
                    self._state.update(agent_id, status="candidate")
                    return {"status": "candidate", "agent_id": agent_id,
                            "output": output or result_obj.output,
                            "steps": step_count, "metadata": result_obj.metadata}
                elif result_obj.status == AgentState.TIMEOUT:
                    self._lifecycle.timeout(agent_id)
                    self._state.update(agent_id, status="timeout")
                    return {
                        "status": "timeout", "agent_id": agent_id,
                        "error": ("任务墙钟预算耗尽" if ctx.metadata.get("wall_budget_exhausted")
                                  else f"达到最大执行步数 ({ctx.max_steps})，任务未完成"),
                        "steps": step_count,
                        "metadata": result_obj.metadata,
                    }
                else:  # FAILED 或其他
                    self._lifecycle.fail(agent_id)
                    error_msg = result_obj.error or result_obj.output or "Agent 执行失败"
                    self._state.update(agent_id, status="failed", error=error_msg)
                    return {
                        "status": "failed", "agent_id": agent_id,
                        "error": error_msg,
                        "steps": step_count,
                        "metadata": result_obj.metadata,
                    }

            except asyncio.CancelledError:
                facts = record_terminal(
                    "cancelled", "Agent task cancelled", source="runtime_cancel",
                    step=step_count)
                self._memory.persist(agent_id)
                try:
                    _log.record_step(phase="error", action="cancelled",
                                     success=False, error="Agent 被取消")
                    if ws_path:
                        _log.persist(str(ws_path / ".state" / "execution_log.json"))
                except Exception:
                    pass
                # lifecycle.cancel() 可能在 Runtime.cancel() 中已被调用
                # LifecycleManager.cancel() 在终态会忽略重复调用
                if not self._lifecycle.is_terminal(agent_id):
                    self._lifecycle.cancel(agent_id)
                self._state.update(agent_id, status="cancelled")
                return {"status": facts["evidence_decision"]["kind"],
                        "agent_id": agent_id, "metadata": facts}
            except Exception as e:
                error_msg = _redact_error(e)
                facts = record_terminal(
                    "exception", error_msg, source="runtime_exception",
                    step=step_count)
                self._memory.persist(agent_id)
                try:
                    _log.record_step(phase="error", action="exception",
                                     success=False, error=error_msg)
                    if ws_path:
                        _log.persist(str(ws_path / ".state" / "execution_log.json"))
                except Exception:
                    pass
                self._lifecycle.fail(agent_id)
                self._state.update(agent_id, status="failed", error=error_msg)
                return {"status": facts["evidence_decision"]["kind"],
                        "agent_id": agent_id, "error": error_msg,
                        "metadata": facts}

        agent_coro = _run_agent()
        try:
            task_obj = await self._scheduler.schedule(agent_id, agent_coro)
        except Exception as exc:
            agent_coro.close()
            error = _redact_error(exc)
            facts = record_terminal("exception", error, source="runtime_schedule")
            self._lifecycle.fail(agent_id)
            self._state.update(agent_id, status="failed", error=error)
            return attach_delivery_report({"status": "failed", "agent_id": agent_id,
                                          "error": error, **facts}, ws_path)
        self._tasks[agent_id] = task_obj
        try:
            result = await task_obj
        except asyncio.CancelledError:
            facts = record_terminal("cancelled", "Runtime await cancelled",
                                    source="runtime_await")
            if not self._lifecycle.is_terminal(agent_id):
                self._lifecycle.cancel(agent_id)
            self._state.update(agent_id, status="cancelled")
            result = {"status": "cancelled", "agent_id": agent_id, **facts}
        except Exception as exc:
            error = _redact_error(exc)
            facts = record_terminal("exception", error, source="runtime_await")
            self._lifecycle.fail(agent_id)
            self._state.update(agent_id, status="failed", error=error)
            result = {"status": "failed", "agent_id": agent_id,
                      "error": error, **facts}
        finally:
            self._tasks.pop(agent_id, None)
        return attach_delivery_report(result, ws_path)

    # ── 生命周期管理 ─────────────────────────────────

    async def pause(self, agent_id: str, reason: str | None = None) -> None:
        """Pause 在当前状态模型中不支持。无操作。"""
        logger.warning("Agent %s: pause 在当前状态模型中不可用", agent_id)

    async def resume(self, agent_id: str, input: str | None = None) -> None:
        """Resume 在当前状态模型中不支持。无操作。"""
        logger.warning("Agent %s: resume 在当前状态模型中不可用", agent_id)

    async def cancel(self, agent_id: str, reason: str | None = None) -> None:
        await self._scheduler.cancel(agent_id)
        if agent_id in self._tasks and not self._tasks[agent_id].done():
            self._tasks[agent_id].cancel()
            self._tasks.pop(agent_id, None)
        # LifecycleManager.cancel() 在终态会忽略重复调用
        self._lifecycle.cancel(agent_id)
        self._state.update(agent_id, status="cancelled")

    # ── 状态查询 ─────────────────────────────────────

    def get_status(self, agent_id: str) -> dict[str, Any]:
        state = self._state.get(agent_id)
        if not state:
            return {"agent_id": agent_id, "status": "unknown"}
        return state.to_dict()

    def list_agents(self) -> list[dict[str, Any]]:
        return [s.to_dict() for s in self._state.list()]

    def get_info(self) -> RuntimeInfo:
        states = self._state.list()
        return RuntimeInfo(
            running_agents=self._scheduler.running_count(),
            completed_agents=sum(1 for s in states if s.status == "completed"),
            total_agents=len(states),
            uptime_seconds=(datetime.now(timezone.utc) - self._started_at).total_seconds(),
            config=self._config.export(),
        )

    @property
    def default_backend(self) -> str:
        """当前默认 Backend 名称。"""
        return self._gateway.default_name or ""

    async def shutdown(self) -> None:
        """关闭 Runtime：取消所有活跃 Agent、清理工作区、持久化状态。"""
        # 取消所有活跃任务
        for aid, t in list(self._tasks.items()):
            if not t.done():
                t.cancel()
        self._tasks.clear()

        await self._scheduler.shutdown()

        # 清理所有临时工作区
        for aid in self._state.list():
            try:
                self._workspace.cleanup(aid, keep_output=True)
            except Exception:
                pass

        # 强制刷状态到磁盘
        self._state.flush()
