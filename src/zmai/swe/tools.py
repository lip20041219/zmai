"""SWE tools — read, write, edit, grep, shell, git, open, show."""

from __future__ import annotations

import logging
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from zmai.tool import Tool, ToolContext, ToolResult

logger = logging.getLogger("zmai.swe.tools")

# 文件操作工具的默认最大写入大小（50MB）
_DEFAULT_MAX_WRITE_SIZE = 50 * 1024 * 1024


# ── 工具日志 ──────────────────────────────────────────

def _emit_tool_result(tool_name: str, context: ToolContext,
                       params: dict[str, Any], result: ToolResult,
                       start_time: float) -> None:
    """输出结构化工具执行日志到 stderr。doctor 模式下不输出。"""
    if context.config.get("_quiet"):
        return
    elapsed = time.time() - start_time
    status = "SUCCESS" if result.success else "FAIL"
    log_lines = [
        f"[{tool_name}]",
        f"  Target:   {params.get('path') or params.get('command') or params.get('args') or params.get('pattern', '')}",  # noqa: E501
        f"  Workspace: {context.workspace_path}",
        f"  Project:  {context.project_path or '(same as workspace)'}",
        f"  Result:   {status} ({elapsed:.2f}s)",
    ]
    if result.error:
        log_lines.append(f"  Reason:   {result.error}")
    if result.output and len(result.output) < 200:
        log_lines.append(f"  Output:   {result.output[:200]}")
    sys.stderr.write("\n".join(log_lines) + "\n")
    sys.stderr.flush()


def _resolve_tool_path(context: ToolContext, user_path: str) -> tuple[bool, Path, str]:
    """解析工具路径。优先写入启动终端目录（project_path）。

    规则：
      1. 绝对路径（/home/... 或 C:\\...）直接使用，检查是否在 project 范围内
      2. 相对路径先从 project_path 解析（启动终端目录）
      3. 回退到 workspace_path（Agent 沙箱）
      4. 拒绝任何路径穿越攻击（../ 逃逸）

    Returns:
        (is_safe, resolved_path, error_message)
    """
    # 绝对路径检测（使用 pathlib，避免手动字符串解析）
    p = Path(user_path)
    if p.is_absolute():
        resolved = p.resolve()
        project_path = context.project_path
        if project_path:
            pp = Path(project_path) if isinstance(project_path, str) else project_path
            try:
                resolved.relative_to(pp.resolve())
                return True, resolved, ""
            except ValueError:
                return False, resolved, f"绝对路径 {user_path} 不在项目目录 {pp} 内"
        # project_path 未设置时，回退到 workspace 检查（不直接放行）
        try:
            resolved.relative_to(context.workspace_path.resolve())
            return True, resolved, ""
        except ValueError:
            return False, resolved, f"绝对路径 {user_path} 不在工作区内"

    # 相对路径：优先从 project_path 解析（启动终端目录）
    project_path = context.project_path
    if project_path:
        pp = Path(project_path) if isinstance(project_path, str) else project_path
        resolved = (pp / p).resolve()
        try:
            resolved.relative_to(pp.resolve())
            return True, resolved, ""
        except ValueError:
            pass  # 逃逸了 project_path，回退到 workspace_path

    # 回退到 workspace_path（Agent 沙箱）
    resolved = (context.workspace_path / p).resolve()
    try:
        resolved.relative_to(context.workspace_path.resolve())
        # symlink 检测日志（仅追踪，不拒绝 — relative_to 已保证安全）
        if resolved != (context.workspace_path / p):
            logger.debug("路径含 symlink: %s → %s", user_path, resolved)
        return True, resolved, ""
    except ValueError:
        return False, resolved, f"路径安全限制: {user_path} 不在工作区内"


# ── 测试文件只读保护 ──────────────────────────────────────────
# 测试文件是"验收标准"：Agent 修改它们（改断言/删测试/跳过测试/加假测试）
# 属于"伪造成功"，必须拦截。tests/ 目录、test_*.py、*_test.py、conftest.py
# 以及纯测试执行配置（pytest.ini/tox.ini/.coveragerc）一律只读。
_TEST_DIR_NAMES = {"tests"}
_TEST_CONFIG_FILES = {"conftest.py", "pytest.ini", "tox.ini", ".coveragerc"}


def _is_test_file(path: Path, root: Path) -> bool:
    """判断文件是否为测试/验收文件（只读，禁止修改）。

    Args:
        path: 已解析的绝对路径。
        root: 项目根（project_path 或 workspace_path）。

    Returns:
        True 表示该文件是测试文件，禁止写操作。
    """
    try:
        rel = path.resolve().relative_to(root.resolve())
    except (ValueError, OSError):
        return False  # 无法判定为项目内测试文件 → 不拦截（保守）
    name = rel.name
    if any(p in _TEST_DIR_NAMES for p in rel.parts[:-1]):
        return True
    if name in _TEST_CONFIG_FILES:
        return True
    if name.endswith(".py"):
        return name.startswith("test_") or name.endswith("_test.py")
    return False


def _test_file_guard_msg(path: Path) -> str:
    """生成测试文件拦截的明确原因，供 Agent 继续分析业务代码。"""
    return (
        f"[TestGuard] 拒绝修改测试/验收文件 {path.name}：测试文件是只读验收标准，"
        f"禁止修改、删除、放宽断言或跳过测试。请分析业务代码并修改它，"
        f"让测试通过，不要改动 tests/ 下的任何文件。"
    )


_DESTRUCTIVE_VERBS = re.compile(r"^(del|rm|move|mv|ren|rmdir|unlink)(\s|$)")
# 测试文件标记：test_*.py / *_test.py / tests/ 目录
_TEST_FILE_TOKEN = re.compile(
    r"test_[a-z0-9_]+\.py\b|_test\.py\b|[/\\]tests[/\\]|(^|[ /\\])tests($|[ /\\])"
)


def _shell_attempts_test_mutation(cmd: str) -> bool:
    """粗略检测 shell 命令是否试图删除/移动/重命名测试文件。

    这是对 EditTool/WriteFileTool 只读保护的补充：失误/恶意的 Agent 可绕过写
    工具，直接用 shell（del/rm/move/ren 等）删除或移走测试文件，使 pytest 以
    "0 passed" 通过——伪造成功。此处做保守拦截，仅当命令以破坏性动词开头且
    目标包含测试文件标记时才拦截，避免误伤正常命令。
    """
    low = (cmd or "").lower().strip()
    if not low:
        return False
    if not _DESTRUCTIVE_VERBS.match(low):
        return False
    return bool(_TEST_FILE_TOKEN.search(low))


class ShowToUserTool(Tool):
    name = "show_to_user"
    description = "Print content to terminal for the user to see."
    parameters = {
        "type": "object",
        "properties": {
            "content": {"type": "string"},
            "title": {"type": "string"},
        },
        "required": ["content"],
    }

    def execute(self, context: ToolContext, params: dict[str, Any]) -> ToolResult:
        _st = time.time()
        content = params.get("content", "")
        title = params.get("title", "")
        header = f"\n{title}\n" if title else ""
        sys.stdout.write(f"{header}{content}\n\n")
        sys.stdout.flush()
        result = ToolResult.ok(output=f"shown ({len(content)} chars)")
        _emit_tool_result(self.name, context, params, result, _st)
        return result


class OpenInBrowserTool(Tool):
    name = "open_in_browser"
    description = "Open HTML file in browser."
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
        },
        "required": ["path"],
    }

    def execute(self, context: ToolContext, params: dict[str, Any]) -> ToolResult:
        _st = time.time()
        path = params.get("path", "")
        if not path:
            result = ToolResult.err("path required")
            _emit_tool_result(self.name, context, params, result, _st)
            return result
        is_safe, full, err_msg = _resolve_tool_path(context, path)
        if not is_safe:
            result = ToolResult.err(err_msg)
            _emit_tool_result(self.name, context, params, result, _st)
            return result
        if not full.exists():
            result = ToolResult.err(f"文件不存在: {full}")
            _emit_tool_result(self.name, context, params, result, _st)
            return result
        abs_path = str(full.resolve())
        try:
            if sys.platform == "win32":
                r = subprocess.run(
                    ["cmd", "/c", "start", "", abs_path],
                    capture_output=True, text=True, timeout=10,
                )
                if r.returncode != 0:
                    detail = r.stderr.strip() or r.stdout.strip() or f"exit {r.returncode}"
                    result = ToolResult.err(f"浏览器打开失败: {detail[:200]}")
                    _emit_tool_result(self.name, context, params, result, _st)
                    return result
            elif sys.platform == "darwin":
                subprocess.run(["open", abs_path], timeout=10)
            else:
                subprocess.run(["xdg-open", abs_path], timeout=10)
            result = ToolResult.ok(output=f"opened: {abs_path}")
        except subprocess.TimeoutExpired:
            result = ToolResult.err("浏览器打开超时")
        except FileNotFoundError:
            result = ToolResult.err("未找到浏览器程序（缺少 open/xdg-open 命令）")
        except Exception as e:
            result = ToolResult.err(f"浏览器打开失败: {type(e).__name__}: {e}")
        _emit_tool_result(self.name, context, params, result, _st)
        return result


class ReadFileTool(Tool):
    name = "read_file"
    description = "Read file content. Supports start_line and end_line. Max 10MB for text."
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "start_line": {"type": "integer"},
            "end_line": {"type": "integer"},
        },
        "required": ["path"],
    }

    _MAX_TEXT_SIZE = 10 * 1024 * 1024  # 10MB

    # 读取缓存：按 (agent_id, 解析后绝对路径, mtime, size) 缓存，同一任务重复读
    # 同一未变化文件时返回"复用提示"而非重新读盘 —— 既省 token 又暴露无意义重复读取。
    # 实例级缓存天然按任务隔离（每个 SWEAgent 注册独立的 ReadFileTool 实例）。
    _read_cache: dict[tuple, tuple[int, int]] = {}
    _read_cache_order: list[tuple] = []

    def execute(self, context: ToolContext, params: dict[str, Any]) -> ToolResult:
        _st = time.time()
        path = params.get("path", "")
        if not path:
            result = ToolResult.err("path required")
            _emit_tool_result(self.name, context, params, result, _st)
            return result
        is_safe, full, err_msg = _resolve_tool_path(context, path)
        if not is_safe:
            result = ToolResult.err(err_msg)
            _emit_tool_result(self.name, context, params, result, _st)
            return result
        if not full.exists():
            result = ToolResult.err(f"not found: {path}")
            _emit_tool_result(self.name, context, params, result, _st)
            return result

        # ── 读取缓存命中判定（文件自上次读取以来未变化）──
        agent = getattr(context, "agent_id", "?")
        try:
            st = full.stat()
            cache_key = (agent, str(full.resolve()), st.st_mtime_ns, st.st_size)
        except OSError:
            cache_key = None
        if cache_key is not None and cache_key in self._read_cache:
            _line_count, _size = self._read_cache[cache_key]
            result = ToolResult.ok(
                output=(
                    f"[ReadCache] {path} 已在当前修复上下文读取过，内容未变化。\n"
                    f"请复用之前的读取结果，不要重复读取 —— 直接基于已读内容分析并修改代码。"
                ),
                metadata={"cached": True, "line_count": _line_count, "size": _size},
            )
            _emit_tool_result(self.name, context, params, result, _st)
            return result

        fsize = full.stat().st_size
        if fsize > self._MAX_TEXT_SIZE:
            result = ToolResult.err(
                f"文件过大 ({fsize} bytes)，超过 10MB 限制。"
                f"如需查看请使用 shell_exec 命令。"
            )
            _emit_tool_result(self.name, context, params, result, _st)
            return result

        # 检测二进制文件：读取前 8KB，如果有 null 字节则判为二进制
        try:
            head = full.read_bytes()[:8192]
            if b"\x00" in head:
                result = ToolResult.err(
                    f"二进制文件，无法以文本方式读取 ({path})"
                )
                _emit_tool_result(self.name, context, params, result, _st)
                return result
        except OSError as e:
            result = ToolResult.err(f"read error: {e}")
            _emit_tool_result(self.name, context, params, result, _st)
            return result

        # 尝试 UTF-8 读取，失败时 fallback 到系统编码
        try:
            text = full.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            try:
                import locale
                enc = locale.getpreferredencoding()
                text = full.read_text(encoding=enc, errors="replace")
            except Exception as e:
                result = ToolResult.err(f"read error: {e}")
                _emit_tool_result(self.name, context, params, result, _st)
                return result

        lines = text.splitlines(keepends=True)
        start = params.get("start_line", 1)
        end = params.get("end_line", len(lines))
        selected = lines[max(0, start - 1):end]
        total = len(lines)
        numbered = "".join(f"{i+1:>4}|{line}" for i, line in enumerate(selected))
        # 记录读取缓存（供后续重复读命中）
        if cache_key is not None:
            self._read_cache[cache_key] = (total, fsize)
            self._read_cache_order.append(cache_key)
            if len(self._read_cache_order) > 400:  # 简单 LRU 上限，防无限增长
                oldest = self._read_cache_order.pop(0)
                self._read_cache.pop(oldest, None)
        result = ToolResult.ok(
            output=f"{path} ({total} lines, {start}-{min(end, total)})\n{numbered}",
            metadata={"line_count": total, "size": fsize},
        )
        _emit_tool_result(self.name, context, params, result, _st)
        return result


class WriteFileTool(Tool):
    name = "write_file"
    description = "Write content to file (overwrite). Creates parent dirs. Max 50MB."
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "content": {"type": "string"},
        },
        "required": ["path", "content"],
    }

    _MAX_WRITE_SIZE = _DEFAULT_MAX_WRITE_SIZE  # 50MB

    def execute(self, context: ToolContext, params: dict[str, Any]) -> ToolResult:
        _st = time.time()
        path = params.get("path", "")
        content = params.get("content", "")
        if not path:
            result = ToolResult.err("path required")
            _emit_tool_result(self.name, context, params, result, _st)
            return result
        is_safe, full, err_msg = _resolve_tool_path(context, path)
        if not is_safe:
            result = ToolResult.err(err_msg)
            _emit_tool_result(self.name, context, params, result, _st)
            return result

        # 测试文件只读保护：禁止写入/覆盖测试文件（含新建 tests/ 下文件）
        if _is_test_file(full, Path(context.project_path or context.workspace_path)):
            result = ToolResult.err(_test_file_guard_msg(full))
            _emit_tool_result(self.name, context, params, result, _st)
            return result

        # 检查文件大小
        if len(content) > self._MAX_WRITE_SIZE:
            result = ToolResult.err(
                f"写入内容过大 ({len(content)} bytes)，超过 {self._MAX_WRITE_SIZE // (1024*1024)}MB 限制"  # noqa: E501
            )
            _emit_tool_result(self.name, context, params, result, _st)
            return result

        # 检查 symlink 逃逸: 如果目标已是 symlink 且指向外部，拒绝写入
        if full.is_symlink():
            resolved_link = full.resolve()
            try:
                resolved_link.relative_to(
                    (context.project_path or context.workspace_path).resolve()
                )
            except ValueError:
                result = ToolResult.err(
                    f"拒绝写入: {path} 是指向 workspace 外部的 symlink ({resolved_link})"
                )
                _emit_tool_result(self.name, context, params, result, _st)
                return result

        # 确保父目录存在
        try:
            full.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            result = ToolResult.err(f"无法创建目录 {full.parent} (OS error {e.errno}): {e.strerror}")  # noqa: E501
            _emit_tool_result(self.name, context, params, result, _st)
            return result

        errors = []

        # ── Attempt 1: Path.write_text() ──
        try:
            full.write_text(content, encoding="utf-8")
            result = ToolResult.ok(output=f"written {path} ({len(content)} chars)")
            _emit_tool_result(self.name, context, params, result, _st)
            return result
        except Exception as e:
            errors.append(f"Attempt 1 (Path.write_text): {type(e).__name__}: {e}")

        # ── Attempt 2: Python open() with explicit encoding ──
        try:
            with open(str(full), "w", encoding="utf-8", errors="strict") as f:
                f.write(content)
            result = ToolResult.ok(output=f"written (open) {path} ({len(content)} chars)")
            _emit_tool_result(self.name, context, params, result, _st)
            return result
        except Exception as e3:
            errors.append(f"Attempt 2 (open): {type(e3).__name__}: {e3}")

        # ── All attempts failed ──
        detail = "\n".join(errors)
        result = ToolResult.err(f"所有写入方式均失败:\n{detail}")
        _emit_tool_result(self.name, context, params, result, _st)
        return result


def _normalize_new_lines(new_text: str) -> list[str]:
    """规范化 edit 的 new_text 为带换行的行列表。

    LLM 经常不提供结尾换行（如 new_text=\"return a * b\"），
    若直接替换且下一行存在，会造成 `return a * bdef divide(...)`
    这类行拼接损坏。规则：
      - 空内容 → 返回空列表（删除行）
      - 拆分后每行必须保留换行；最后一项若缺失换行则补 \\n，
        避免与后续行拼接（Python 文件末尾多一个换行无害）。
    """
    if not new_text:
        return []
    new_items = new_text.splitlines(keepends=True)
    if new_items and not new_items[-1].endswith(("\n", "\r")):
        new_items[-1] += "\n"
    return new_items


class EditTool(Tool):
    name = "edit"
    description = "Line-level editing: replace_lines, regex_replace, insert, append."
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "mode": {"type": "string", "enum": ["replace_lines", "regex_replace", "insert", "append"]},  # noqa: E501
            "start_line": {"type": "integer"},
            "end_line": {"type": "integer"},
            "old_text": {"type": "string"},
            "new_text": {"type": "string"},
            "count": {"type": "integer"},
            "ignore_case": {"type": "boolean"},
        },
        "required": ["path", "mode", "new_text"],
    }

    _MAX_EDIT_SIZE = _DEFAULT_MAX_WRITE_SIZE  # 50MB

    def execute(self, context: ToolContext, params: dict[str, Any]) -> ToolResult:
        _st = time.time()
        path = params.get("path", "")
        mode = params.get("mode", "")
        new_text = params.get("new_text", "")
        if not path:
            result = ToolResult.err("path required")
            _emit_tool_result(self.name, context, params, result, _st)
            return result
        is_safe, full, err_msg = _resolve_tool_path(context, path)
        if not is_safe:
            result = ToolResult.err(err_msg)
            _emit_tool_result(self.name, context, params, result, _st)
            return result

        # 测试文件只读保护：禁止编辑/删除/放宽断言/跳过测试
        if _is_test_file(full, Path(context.project_path or context.workspace_path)):
            result = ToolResult.err(_test_file_guard_msg(full))
            _emit_tool_result(self.name, context, params, result, _st)
            return result

        # 文件大小限制：禁止写入超限内容（与 WriteFileTool 一致）
        if len(new_text) > self._MAX_EDIT_SIZE:
            result = ToolResult.err(
                f"编辑内容过大 ({len(new_text)} bytes)，超过 {self._MAX_EDIT_SIZE // (1024*1024)}MB 限制"  # noqa: E501
            )
            _emit_tool_result(self.name, context, params, result, _st)
            return result
        if not full.exists() and mode != "append":
            result = ToolResult.err(f"not found: {path}")
            _emit_tool_result(self.name, context, params, result, _st)
            return result

        # 备份原始内容（用于编辑失败时恢复）
        original_content = None
        if full.exists() and mode != "append":
            try:
                original_content = full.read_bytes()
            except Exception:
                pass

        def _restore() -> None:
            if original_content is not None and full.exists():
                try:
                    full.write_bytes(original_content)
                except Exception:
                    pass

        try:
            if mode == "append":
                full.parent.mkdir(parents=True, exist_ok=True)
                with full.open("a", encoding="utf-8") as f:
                    f.write(new_text)
                result = ToolResult.ok(output=f"appended {path}")
                _emit_tool_result(self.name, context, params, result, _st)
                return result

            lines = full.read_text(encoding="utf-8").splitlines(keepends=True)

            if mode == "replace_lines":
                start = params.get("start_line", 1)
                end = params.get("end_line", len(lines))
                if start < 1 or start > len(lines):
                    result = ToolResult.err(f"start_line {start} out of range (1-{len(lines)})")
                    _emit_tool_result(self.name, context, params, result, _st)
                    return result
                if end < start:
                    result = ToolResult.err(f"end_line {end} < start_line {start}")
                    _emit_tool_result(self.name, context, params, result, _st)
                    return result
                new_items = _normalize_new_lines(new_text)
                lines[start-1:end] = new_items
                full.write_text("".join(lines), encoding="utf-8")
                result = ToolResult.ok(output=f"replaced {path}:{start}-{end}")
                _emit_tool_result(self.name, context, params, result, _st)
                return result

            if mode == "regex_replace":
                old = params.get("old_text", "")
                if not old:
                    result = ToolResult.err("old_text required")
                    _emit_tool_result(self.name, context, params, result, _st)
                    return result
                flags = re.IGNORECASE if params.get("ignore_case") else 0
                count = params.get("count", 0) or 0
                try:
                    new_content, n = re.subn(old, new_text, "".join(lines), count=count, flags=flags)  # noqa: E501
                except re.error as e:
                    result = ToolResult.err(f"regex error: {e}")
                    _emit_tool_result(self.name, context, params, result, _st)
                    return result
                full.write_text(new_content, encoding="utf-8")
                result = ToolResult.ok(output=f"replaced {n} matches in {path}")
                _emit_tool_result(self.name, context, params, result, _st)
                return result

            if mode == "insert":
                ln = params.get("start_line", 1)
                ins = _normalize_new_lines(new_text)
                lines[ln-1:ln-1] = ins
                full.write_text("".join(lines), encoding="utf-8")
                result = ToolResult.ok(output=f"inserted at {path}:{ln}")
                _emit_tool_result(self.name, context, params, result, _st)
                return result

            result = ToolResult.err(f"unknown mode: {mode}")
        except Exception as e:
            _restore()
            result = ToolResult.err(f"edit error: {e}")
        _emit_tool_result(self.name, context, params, result, _st)
        return result


class GrepTool(Tool):
    name = "grep"
    description = "Search text in workspace with regex."
    parameters = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string"},
            "glob": {"type": "string"},
            "ignore_case": {"type": "boolean"},
        },
        "required": ["pattern"],
    }

    def execute(self, context: ToolContext, params: dict[str, Any]) -> ToolResult:
        _st = time.time()
        pattern = params.get("pattern", "")
        if not pattern:
            result = ToolResult.err("pattern required")
            _emit_tool_result(self.name, context, params, result, _st)
            return result
        flags = re.IGNORECASE if params.get("ignore_case") else 0
        try:
            regex = re.compile(pattern, flags)
        except re.error as e:
            result = ToolResult.err(f"regex error: {e}")
            _emit_tool_result(self.name, context, params, result, _st)
            return result

        # project_path/workspace_path 可能是 str，统一转 Path（否则 root.glob 崩溃）
        root = Path(context.project_path or context.workspace_path)
        results = []
        total = 0
        _ignore_dirs = {".git", "node_modules", "__pycache__",
                        ".venv", "venv", ".tox", "build", "dist",
                        ".egg-info", ".mypy_cache", ".pytest_cache"}

        # 如果 LLM 传入了 glob 模式，用它缩小搜索范围
        glob_pattern = params.get("glob") or "**/*"
        for f in sorted(root.glob(glob_pattern)):
            if not f.is_file():
                continue
            rel = f.relative_to(root).as_posix()
            # 检查所有路径段，包括根目录级
            parts = rel.split("/")
            # 检查文件路径中所有目录段，防止 .git/config 等被忽略
            if any(part in _ignore_dirs for part in parts[:-1]):
                continue
            try:
                for i, line in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1):  # noqa: E501
                    if regex.search(line):
                        results.append(f"{rel}:{i}:{line.rstrip()[:200]}")
                        total += 1
            except Exception:
                pass
        if not results:
            result = ToolResult.ok(output=f"no matches: {pattern}")
        else:
            summary = f"found {total}:\n" + "\n".join(results[:100])
            if len(results) > 100:
                summary += f"\n... +{len(results)-100} more"
            result = ToolResult.ok(output=summary, metadata={"matches": total})
        _emit_tool_result(self.name, context, params, result, _st)
        return result


def _translate_cmd(cmd: str) -> str:
    """将常见 Linux 命令翻译为 Windows 等价命令。"""
    if sys.platform != "win32":
        return cmd
    stripped = cmd.strip()
    if not stripped:
        return cmd

    lowered = stripped.lower()

    # 完整命令替换
    full_replacements = {
        "ls": "dir",
        "pwd": "cd",
    }
    if lowered in full_replacements:
        prefix = full_replacements[lowered]
        return prefix

    # 前缀匹配（命令后有参数）
    for linux_cmd, win_cmd in [
        ("ls ", "dir "),
        ("pwd ", "cd "),
        ("cat ", "type "),
        ("rm -rf ", "rmdir /s /q "),
        ("rm -r ", "rmdir /s /q "),
        ("rm -f ", "del /q "),
        ("rm ", "del "),
        ("mv ", "move "),
        ("cp -r ", "xcopy /e /i "),
        ("cp ", "copy "),
        ("which ", "where "),
        ("uname", "ver"),
        ("sort ", "sort "),  # sort 在 Windows 上也存在
        ("head -n ", "cmd /c \"type "),  # 不完美但可工作
        ("grep -r", "findstr /s"),  # 部分等价
        ("grep ", "findstr "),  # 部分等价
        ("chmod ", "echo "),  # Windows 不需要 chmod，回显提醒
        ("wc -l ", "find /c /v \"\" "),
    ]:
        if lowered.startswith(linux_cmd):
            return win_cmd + stripped[len(linux_cmd):]

    return cmd


def _cap_shell_output(output: str, command: str, limit: int = 10000,
                      tail_keep: int = 3000) -> str:
    """截断 shell 输出，但对 pytest 命令保留尾部。

    大型 pytest 输出的最终 summary 行（如 "1302 passed, 0 failed"）在输出
    末尾。若按头截断会被切掉，导致 parse_test_totals() 解析为 0，误触发
    baseline 回退。因此对 pytest/python -m pytest 命令：保留头部（失败
    traceback）+ 尾部（summary），中间省略。普通 shell 命令维持原有限制。
    """
    if len(output) <= limit:
        return output
    if re.search(r"\bpytest\b|python\s+-m\s+pytest", command, re.IGNORECASE):
        head = output[: max(0, limit - tail_keep)]
        tail = output[-tail_keep:]
        return f"{head}\n...[output truncated, {len(output)} chars]...\n{tail}"
    return output[:limit]


class ShellTool(Tool):
    name = "shell_exec"
    description = (
        "Execute shell command in workspace directory. "
        "On Windows: use dir instead of ls, type instead of cat, cd instead of pwd."
    )
    parameters = {
        "type": "object",
        "properties": {
            "command": {"type": "string"},
            "timeout": {"type": "integer"},
        },
        "required": ["command"],
    }

    def execute(self, context: ToolContext, params: dict[str, Any]) -> ToolResult:
        _st = time.time()
        cmd = params.get("command", "")
        if not cmd:
            result = ToolResult.err("command required")
            _emit_tool_result(self.name, context, params, result, _st)
            return result
        cmd = _translate_cmd(cmd)
        # 测试文件只读保护：拦截 shell 对测试文件的删除/移动/重命名（绕过写工具）
        if _shell_attempts_test_mutation(cmd):
            result = ToolResult.err(
                "[TestGuard] 拒绝通过 shell 删除/移动/重命名测试文件："
                "测试文件是只读验收标准，禁止删除、移走或跳过测试。"
                "请修改业务代码让测试通过。"
            )
            _emit_tool_result(self.name, context, params, result, _st)
            return result
        confirm_fn = context.config.get("on_confirm")
        if confirm_fn and not confirm_fn("shell_exec", cmd):
            result = ToolResult.ok(output="cancelled by user")
            _emit_tool_result(self.name, context, params, result, _st)
            return result
        timeout = params.get("timeout", context.timeout or 30)
        stdin_input = params.get("input")
        try:
            cwd = str(context.project_path or context.workspace_path)
            r = subprocess.run(cmd, shell=True, cwd=cwd,
                               input=stdin_input,
                               capture_output=True, text=True, timeout=timeout,
                               encoding="utf-8", errors="replace")
            output = r.stdout or ""
            if r.stderr:
                output += f"\n[stderr]\n{r.stderr}"
            if r.returncode != 0:
                result = ToolResult.err(
                    error=f"exit {r.returncode}: {_cap_shell_output(output, cmd, 5000)}",
                    metadata={"exit_code": r.returncode})
            else:
                result = ToolResult.ok(output=_cap_shell_output(output, cmd, 10000),
                                       metadata={"exit_code": r.returncode})
        except subprocess.TimeoutExpired:
            result = ToolResult.err(f"timeout ({timeout}s)")
        except Exception as e:
            result = ToolResult.err(f"failed: {e}")
        _emit_tool_result(self.name, context, params, result, _st)
        return result


class GitTool(Tool):
    name = "git"
    description = "Execute git command in workspace directory."
    parameters = {
        "type": "object",
        "properties": {
            "args": {"type": "string"},
            "timeout": {"type": "integer"},
        },
        "required": ["args"],
    }

    def execute(self, context: ToolContext, params: dict[str, Any]) -> ToolResult:
        _st = time.time()
        args = params.get("args", "")
        if not args:
            result = ToolResult.err("args required")
            _emit_tool_result(self.name, context, params, result, _st)
            return result
        confirm_fn = context.config.get("on_confirm")
        if confirm_fn and not confirm_fn("git", args):
            result = ToolResult.ok(output="cancelled by user")
            _emit_tool_result(self.name, context, params, result, _st)
            return result
        timeout = params.get("timeout", context.timeout or 30)
        try:
            # 安全: 使用 list args 而非 shell=True + 字符串拼接，
            # 防止 git "status; rm -rf /" 注入攻击
            cmd_parts = ["git"] + shlex.split(args)
            cwd = str(context.project_path or context.workspace_path)
            r = subprocess.run(cmd_parts, shell=False, cwd=cwd,
                               capture_output=True, text=True, timeout=timeout,
                               encoding="utf-8", errors="replace")
            output = r.stdout or ""
            if r.stderr:
                output += f"\n[stderr]\n{r.stderr}"
            result = ToolResult.ok(output=output[:5000], metadata={"exit_code": r.returncode})
        except subprocess.TimeoutExpired:
            result = ToolResult.err(f"git timeout ({timeout}s)")
        except Exception as e:
            result = ToolResult.err(f"git error: {e}")
        _emit_tool_result(self.name, context, params, result, _st)
        return result
