"""Doctor — 安装与运行环境诊断工具。

运行 `zmai doctor` 检查 Backend、API Key、Workspace、Memory、Tool、Config。
不发送任何 HTTP 请求。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from zmai.cli.formatters import Theme, print_json
from zmai.errors import CredentialError


@dataclass
class CheckResult:
    """单个检查项的结果。"""

    name: str
    status: bool = True
    detail: str = ""
    category: str = ""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Doctor:
    """运行 5 类诊断检查并输出结果。不调用外部 API。"""

    def __init__(
        self,
        theme: Theme | None = None,
        json_output: bool = False,
    ) -> None:
        self.theme = theme or Theme.dark()
        self.json_output = json_output
        self.results: list[CheckResult] = []
        self._project_root: Path | None = None

    # ── 辅助方法 ───────────────────────────────────────────────

    def _add(self, name: str, status: bool, detail: str = "",
             category: str = "") -> None:
        self.results.append(CheckResult(
            name=name, status=status,
            detail=detail, category=category,
        ))

    def _pass(self, name: str, category: str = "") -> None:
        self._add(name, True, "PASS", category)

    def _fail(self, name: str, detail: str = "", category: str = "") -> None:
        self._add(name, False, detail or "FAIL", category)

    # ═══════════════════════════════════════════════════════════
    # 检查方法
    # ═══════════════════════════════════════════════════════════

    # ── 1. Backend ──────────────────────────────────────────

    def _check_backend(self) -> None:
        """检查每个 Backend 的 API Key 状态，不发送 HTTP 请求。"""
        cat = "Backend"

        try:
            from zmai.auth.resolver import CredentialResolver
            from zmai.auth.status import source_label
            from zmai.gateway.plugin import PluginRegistry

            reg = PluginRegistry()
            plugins = reg.list_plugins()

            if not plugins:
                self._fail("No backends registered", category=cat)
                return

            # 收集所有冲突警告，最后统一输出
            warnings: list[str] = []

            for plugin in plugins:
                try:
                    status = CredentialResolver().get_status(plugin.name)
                    label = plugin.label or plugin.name.title()

                    if status.configured:
                        src = source_label(status.source)
                        self._add(f"{label}", True, f"PASS (source: {src})", cat)

                        if status.conflict:
                            for detail in status.conflict_details:
                                if detail.source != status.source:
                                    warnings.append(
                                        f"{label}: {detail.label} "
                                        f"has a different key. "
                                        f"Currently using {source_label(status.source)}."
                                    )
                    else:
                        self._fail(f"{label}", "Missing Key", cat)

                except CredentialError as e:
                    self._fail(f"{plugin.label}", e.reason or "Credential Error", cat)

            # 输出冲突警告
            for w in warnings:
                self._add(f"⚠ {w}", False, "Conflict", cat)

        except CredentialError as e:
            self._fail("Credential Store", str(e)[:80], cat)
        except Exception as e:
            self._fail("Backend discovery", str(e)[:60], cat)

    @staticmethod
    def _find_key(name: str, env_key_name: str = "") -> str:
        """使用 CredentialResolver 统一查找 API Key。

        所有凭据读取必须走此路径，禁止自行读取 os.environ 或 AuthStore。
        env_key_name 仅用于向后兼容，实际由 resolver 自动解析。
        """
        from zmai.auth.resolver import CredentialResolver
        status = CredentialResolver().get_status(name)
        return status.api_key

    # ── 2. Config ───────────────────────────────────────────

    def _check_config(self) -> None:
        """检查配置文件是否存在。"""
        cat = "Config"

        try:
            root = self._project_root or Path.cwd()
            # zmai.json
            zmai = root / "zmai.json"
            self._add("zmai.json", zmai.exists(),
                       "PASS" if zmai.exists() else "Missing", cat)

            # ~/.zmai/config.json
            home = Path.home() / ".zmai"
            global_cfg = home / "config.json"
            self._add("~/.zmai/config.json", global_cfg.exists(),
                       "PASS" if global_cfg.exists() else "Missing", cat)

            # ~/.zmai/credentials
            creds = home / "credentials"
            self._add("~/.zmai/credentials", creds.exists(),
                       "PASS" if creds.exists() else "Missing", cat)

        except Exception as e:
            self._fail("Config check", str(e)[:60], cat)

    # ── 3. Workspace ────────────────────────────────────────

    def _check_workspace(self) -> None:
        """测试工作区读写。"""
        cat = "Workspace"

        try:
            root = self._project_root or Path.cwd()
            ws = root / "workspace"
            ws.mkdir(parents=True, exist_ok=True)
            test_file = ws / ".zmai_doctor_test"
            test_file.write_text("ok")
            test_file.unlink()
            self._pass("read/write", cat)
        except Exception as e:
            self._fail("read/write", str(e)[:60], cat)

    # ── 4. Memory ───────────────────────────────────────────

    def _check_memory(self) -> None:
        """测试记忆存储读写。"""
        cat = "Memory"

        try:
            from zmai.memory.manager import MemoryManager
            mgr = MemoryManager()
            wm = mgr.working("__zmai_doctor__")
            wm.store("health", "ok", "doctor")
            val = wm.read("health", "doctor")
            wm.delete("health", "doctor")
            mgr.cleanup("__zmai_doctor__")
            if val == "ok":
                self._pass("read/write", cat)
            else:
                self._fail("read/write", "write/read mismatch", cat)
        except Exception as e:
            self._fail("read/write", str(e)[:60], cat)

    # ── 5. Tool ─────────────────────────────────────────────

    def _check_tool(self) -> None:
        """检查工具注册。"""
        cat = "Tool"

        try:
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
            from zmai.tool.registry import ToolRegistry

            tool_classes = [
                ShowToUserTool, OpenInBrowserTool, ReadFileTool,
                WriteFileTool, EditTool, GrepTool, ShellTool, GitTool,
            ]

            reg = ToolRegistry()
            for cls in tool_classes:
                try:
                    reg.register(cls())
                except Exception:
                    pass

            count = len(reg.list())
            if count == 8:
                self._add("8/8 tools", True, "8/8", cat)
            else:
                self._fail(f"{count}/8 tools", f"missing {8 - count} tool(s)", cat)

        except Exception as e:
            self._fail("tools", str(e)[:60], cat)

    # ═══════════════════════════════════════════════════════════
    # 输出方法
    # ═══════════════════════════════════════════════════════════

    def _report(self) -> None:
        """彩色终端输出。"""
        t = self.theme
        current_cat = ""
        for r in self.results:
            if r.category and r.category != current_cat:
                current_cat = r.category
                print(f"\n  {t.info(current_cat)}:")

            if r.status:
                print(f"    {r.name:20s}  {t.success(r.detail or 'PASS')}")
            else:
                status_text = r.detail or "FAIL"
                print(f"    {r.name:20s}  {t.error(status_text)}")

        passed = sum(1 for r in self.results if r.status)
        total = len(self.results)
        print(f"\n  {t.dim(f'{passed}/{total} checks passed')}\n")

    def _report_json(self) -> None:
        """JSON 格式输出到 stdout。"""
        data: dict[str, Any] = {
            "timestamp": _now(),
            "summary": {
                "passed": sum(1 for r in self.results if r.status),
                "total": len(self.results),
            },
            "checks": [
                {"name": r.name, "status": r.status,
                 "detail": r.detail, "category": r.category}
                for r in self.results
            ],
        }
        print_json(data)

    def _write_doctor_md(self) -> None:
        """写入 DOCTOR.md 到项目根目录。"""
        root = self._project_root or Path.cwd()
        md_path = root / "DOCTOR.md"
        passed = sum(1 for r in self.results if r.status)
        total = len(self.results)

        lines = [
            "# ZMAI Doctor Report",
            "",
            f"**Generated**: {_now()}",
            "",
            f"**Result**: {passed}/{total} checks passed",
            "",
            "---",
            "",
        ]

        current_cat = ""
        for r in self.results:
            if r.category and r.category != current_cat:
                current_cat = r.category
                lines.append(f"## {current_cat}")
                lines.append("")

            icon = "PASS" if r.status else "FAIL"
            detail_str = f" — {r.detail}" if r.detail else ""
            lines.append(f"- **{icon}** {r.name}{detail_str}")

        lines.extend(["", "---", "", "*Report generated by `zmai doctor`*", ""])
        md_path.write_text("\n".join(lines), encoding="utf-8")

    # ═══════════════════════════════════════════════════════════
    # 主入口
    # ═══════════════════════════════════════════════════════════

    def run(self) -> list[CheckResult]:
        """执行全部检查并输出结果。不发送任何 HTTP 请求。"""
        self.results = []
        self._check_backend()
        self._check_config()
        self._check_workspace()
        self._check_memory()
        self._check_tool()

        if self.json_output:
            self._report_json()
        else:
            self._report()

        self._write_doctor_md()
        return self.results
