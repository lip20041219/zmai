"""SWE Agent — software engineering agent (delivery-oriented)."""

from __future__ import annotations

import asyncio
import atexit
import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from zmai.agent import Agent, AgentAction, AgentContext, AgentResult, AgentState
from zmai.context.memory import _truncate_head_tail
from zmai.errors import BackendError
from zmai.gateway import Backend
from zmai.gateway.base import BackendRequest, BackendResponse
from zmai.swe._async_utils import run_sync
from zmai.swe.acceptance_guard import MARKER as ACCEPTANCE_GUARD_MARKER
from zmai.swe.completion import CompletionState
from zmai.swe.context import ContextManager
from zmai.swe.graph import GraphRuntime, Node, SWEState
from zmai.swe.loop_guard import LoopGuard
from zmai.swe.models import MAX_REPLANS, Plan, format_plan_summary
from zmai.swe.planner import generate_plan
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
    _is_test_file,
    cli_scope_filter,
    cli_scope_names_exclusions,
    is_test_command,
    test_scope_targets,
)
from zmai.swe.verifier import (
    VerificationResult,
    auto_generate_checks,
    classify_test_progress,
    parse_test_totals,
    verify_test_output,
)
from zmai.tool import ToolCall, ToolContext, ToolResult

logger = logging.getLogger("zmai.swe.agent")

# 语法验证失败后的有限修复重试上限（Phase 3）
# 超限后升级为强制整文件重写，避免原地补丁死循环消耗 benchmark steps。
# 可用 config["edit.repair_attempts"] 覆盖。
MAX_EDIT_REPAIR_ATTEMPTS = 2

# 完成守卫连续拦截上限：反复拦截而模型始终不推进时，明确失败，
# 而不是耗尽 max_steps 后伪装成 timeout。
MAX_COMPLETION_BLOCKS = 3

# 强制修改阶段预算：force_edit 已置位后，允许模型在多少步内产出真正的修改。
# LoopGuard/FixDriving 只负责"要求修改"；若模型在强制期内始终只发被拒绝的调用，
# 状态机已用尽手段 —— 必须在预算内明确失败，而不是空转到 max_steps。
MAX_FORCE_EDIT_STEPS = 8

# 灾难性回退上限：修改让项目无法 import（collection/import error）后，
# Agent 已明确被要求"先恢复再修改"。若仍然继续把项目改坏，说明状态机已无法
# 引导其收敛 —— 必须在预算内明确失败，而不是无限重复"改坏 → 要求恢复 → 再改坏"。
MAX_REGRESSION_RECOVERIES = 2

# edit/write_file 执行失败后的**定向恢复**注入上限（P1: edit failure recovery）。
# 观测（pylint-6506 / 5859 / 7228）：diagnosis → repair plan → force_edit →
# edit failed → 无任何恢复消费者 → 模型漂移去跑 pytest / 写草稿脚本 → 0-byte diff。
# force_edit 只是工具白名单状态，不是"edit 失败"的恢复状态。
# 每次失败注入一条带 target 的恢复提示（强制修改期内额外放行一次对 target 的
# 定向 read）；超过上限后不再注入，交由 MAX_FORCE_EDIT_STEPS / completion
# fail-closed / LoopGuard 收尾 —— 不新增无界循环，也不放宽任何完成门禁。
MAX_EDIT_FAILURE_RECOVERIES = 3

# SWE agent 写入 memory 时使用的 namespace。
# 写入端与读取端（step() 注入 ## Memory Context 处）**必须共用这一个常量**：
# 历史缺陷是写入用 "tools"、读取用 `WorkingMemory.search("")` 的默认
# namespace="default"，两端口径不一致 → `MemoryManager.restore()` 明明恢复了条目，
# `## Memory Context` 却永远为空（跨 run 记忆整个失效）。
_MEMORY_NS_TOOLS = "tools"


def _now_ms() -> int:
    """当前时间戳（毫秒）。"""
    import time
    return int(time.monotonic() * 1000)


def _test_evidence_budget(tc: ToolCall, result: ToolResult,
                          cm: ContextManager) -> int | None:
    """测试失败结果 → evidence 截断预算；其余工具 → None（默认头部截断）。

    P0-4：测试失败的证据（[test summary] + traceback）在 ContextManager 里若按
    默认 500 字符头部截断，模型只会看到 pytest 的 session/collection 头，看不到
    任何失败根因。测试命令的失败结果因此改走证据保留截断（头摘要 + 尾详情）。
    普通工具（read_file/grep/git/普通 shell）与成功的测试运行保持原行为。
    """
    if result.success or tc.name != "shell_exec":
        return None
    if not is_test_command(str((tc.params or {}).get("command", ""))):
        return None
    return cm.test_evidence_chars


def _stats(context: AgentContext, **deltas: int) -> dict:
    """累计修复效率统计（total_steps/reads/duplicate_reads/pytest_calls 等）。

    用于审计 Agent 是否高效闭环，而不只是看 max_steps 是否耗尽。
    存入 context.metadata["swe_stats"]。
    """
    d = context.metadata.setdefault("swe_stats", {})
    for k, v in deltas.items():
        d[k] = d.get(k, 0) + v
    return d


def _fmt_test_totals(t: dict[str, int]) -> str:
    """把 parse_test_totals 的结果格式化成一行可读计数。"""
    s = f"{t.get('passed', 0)} passed, {t.get('failed', 0)} failed"
    if t.get("errors"):
        s += f", {t['errors']} errors"
    return s


# 工作区指纹要跳过的目录：构建产物 / VCS / 缓存 / 依赖。这些目录在每次 pytest
# 或运行脚本后都会变化（__pycache__ / .pytest_cache），若计入指纹，任何一次测试
# 运行都会被误判成"代码修改"，green 状态永远站不住（P0-2/P1-2 会整片回归）。
_WS_IGNORE_DIRS = frozenset({
    ".git", ".hg", ".svn", "__pycache__", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", ".tox", ".venv", "venv", ".eggs", "node_modules",
    "htmlcov", ".idea", ".vscode",
})
_WS_IGNORE_SUFFIXES = (".pyc", ".pyo", ".pyd")
_WS_IGNORE_NAMES = frozenset({".coverage", ".DS_Store"})

# 只读工具按构造不可能改动工作区，跳过指纹遍历（省一次目录树扫描）。
# 未列出的工具——包括以后新增的——一律计算：默认"可能写"，宁可多算不可漏判。
_READ_ONLY_TOOLS = frozenset({"read_file", "grep", "show_to_user", "open_in_browser"})


def _explicit_workspace_root(context: AgentContext) -> Path | None:
    """显式配置的工作区根；未配置时返回 None（此时 CWD 只是兜底，不代表项目范围）。

    "显式" = 调用方在 config 里声明的 project_path。`SWEAgent.initialize()` 会
    用 find_project_root() 从 CWD 推断出仓库根并回写进 config —— 那只是给 agent
    提供上下文，不是本次任务的验收范围（metadata 里 project_root_declared 记了
    区别）。把它当显式 root 会让指纹走全树 os.walk：CWD 是仓库根时单次遍历
    数万文件（ZMAI 自测 9 秒/次，每次工具调用都来一遍）。
    """
    if not context.metadata.get("project_root_declared", True):
        return context.workspace or None
    v = context.config.get("project_path") or context.workspace
    return Path(v) if v else None


def _workspace_root(context: AgentContext) -> Path:
    """工作区根目录 —— 与 ShellTool 的 cwd 保持同一套解析顺序，否则指纹会盯错目录。"""
    return _explicit_workspace_root(context) or Path(".")


def _git_workspace_fingerprint(root: Path) -> dict[str, tuple[int, int]] | None:
    """用 git 索引状态当工作区指纹（快路径）；非 git 仓库返回 None。

    覆盖修改 / 新增 / 删除 / 重命名 / 回退：任一处变化都会改变 porcelain 输出，
    因此与目录树指纹一样能回答"工作区实际有没有变"，但只读索引、不遍历目录树。
    `-uall` 逐个列出未跟踪文件（而非折叠成目录），否则新建目录内的后续改动看不出来；
    被 .gitignore 忽略的产物（__pycache__ 等）本就不该计入，天然被排除。
    值统一为 (0, 0)：本指纹只参与相等比较，含义由 key（状态行）承载。
    """
    try:
        r = subprocess.run(["git", "status", "--porcelain", "-uall"],
                           cwd=str(root), capture_output=True, text=True,
                           timeout=10, encoding="utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:      # 不是 git 仓库 / git 不可用
        return None
    # git 只告诉我们**哪些**文件变了；状态行本身不随内容再变（` M a.py` 改两次
    # 仍是 ` M a.py`，未跟踪文件同理），因此对变更路径再取一次 (mtime_ns, size)。
    # 只 stat 变更文件 → 代价与变更数成正比，而不是与仓库大小成正比。
    fp: dict[str, tuple[int, int]] = {}
    for ln in (r.stdout or "").splitlines():
        if len(ln) < 4:
            continue
        path = ln[3:]
        if " -> " in path:                      # rename/copy：取目标路径
            path = path.split(" -> ", 1)[1]
        path = path.strip().strip('"')
        try:
            st = (root / path).stat()
            fp[f"git:{ln[:2]}:{path}"] = (st.st_mtime_ns, st.st_size)
        except OSError:
            fp[f"git:{ln[:2]}:{path}"] = (-1, -1)   # 删除 / 不可读
    return fp


def _workspace_fingerprint(root: Path, *, prefer_git: bool = False,
                           ) -> dict[str, tuple[int, int]]:
    """工作区代码文件的 (mtime_ns, size) 指纹。

    回答的是"这次工具调用**实际**有没有改动工作区"，而不是猜 shell 命令的意图
    （`python fix.py` 是不是写文件、`sed -i` 是不是改文件都无法从字符串可靠判断）。

    prefer_git：**没有显式配置工作区根**时启用。那种情况下 root 是 `Path(".")`
    —— 它只是一个 cwd 兜底，不代表项目范围：实测 ZMAI 仓库根 30,672 文件、
    单次遍历 13.7 秒，而每次工具调用都要走一遍。此时改用 git 索引快路径，
    非 git 目录再退回目录树（正确性优先，不给 `_ws_changed` 造假值）。

    ponytail: 有显式 root 时仍每次工具调用遍历目录树；SWE-bench 量级是毫秒级，
    真成为瓶颈再对显式 root 也走 git 快路径或增量 stat 缓存。
    """
    if prefer_git:
        git_fp = _git_workspace_fingerprint(root)
        if git_fp is not None:
            return git_fp
    fp: dict[str, tuple[int, int]] = {}
    try:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in _WS_IGNORE_DIRS]
            for fn in filenames:
                if fn.endswith(_WS_IGNORE_SUFFIXES) or fn in _WS_IGNORE_NAMES:
                    continue
                p = Path(dirpath) / fn
                try:
                    st = p.stat()
                except OSError:
                    continue
                fp[str(p.relative_to(root))] = (st.st_mtime_ns, st.st_size)
    except OSError:
        return fp
    return fp


def _is_full_scope_test_command(command: str) -> bool:
    """测试命令是否**可证明**覆盖整个套件（既没有测试目标，也没有缩范围的选项）。

    `python -m pytest -q`                  → True（跑项目配置的全部测试）
    `python -m pytest -q 2>&1 | tail -50`  → True（重定向/管道是输出处理，不是目标）
    `python -m pytest -q test_a.py`        → False（只跑指定目标，是子集）
    `python -m pytest -q --ignore=test_b.py` → False（选项同样能缩小收集范围）
    `python -m pytest -q -k foo`           → False（`-k` 选择器，且 `foo` 是位置目标）

    判定口径是**实际被执行的那条 runner 命令**：由 tools.test_scope_targets()
    剥掉 shell 重定向、按管道/`&&` 切段后，只看 runner 后面有没有非选项参数。
    不这么做的话，`pytest -q 2>&1`（重定向是 shell 语法）会被读成"指定了一个
    名为 `2>&1` 的测试目标"，于是**任何带管道的完整套件运行都拿不到完成资格**。

    只认"完全没有测试目标"这一种证明方式。`-k` / `-m` 这类带参数的选择器因此
    也会被判为子集——它们确实无法证明覆盖完整套件，被提示去跑完整套件是正确方向，
    不是误判。找不到运行器时返回 True（保守：保持既有行为）。

    ── N-1：选项也必须过一遍 ──
    光看位置目标不够：`--ignore=test_bug.py` / `--deselect=...` 这类 **flag=值** 形态
    不产生位置 token，于是被判成"完整范围"，而失败的验收测试根本没被收集 —— baseline
    被锁成缩小后的数量，剩余全绿即 COMPLETED。选项按白名单判定（见
    tools.cli_scope_filter）：只有已知不改变收集范围的选项（`-q` / `-v` / `-s` / `-x`
    / `--tb=...` …）才保留完整范围资格，"没见过的选项"一律 fail-closed。
    """
    targets = test_scope_targets(command)
    if targets is None:
        return True
    if targets:
        return False
    return not cli_scope_filter(command)


def _norm_target_path(p: object) -> str:
    """路径归一化 —— 只用于"是不是同一个目标文件"的比较，不做安全判定。"""
    return os.path.normpath(str(p or "")).replace("\\", "/")


def _fp_key(key: object) -> str:
    """指纹 key → 可解析为路径的形式。

    git 快路径（无显式 project_path 时）的 key 形如 `git: M:tests/test_x.py`，
    目录树路径的 key 就是相对路径本身。两者都要能被还原成路径，否则 CR-1 的
    "测试文件消失"判定会在 git 路径下静默失配。
    """
    k = str(key or "")
    return k.split(":", 2)[2] if k.startswith("git:") else k


# ── CR-3: 验收文件**内容**基线 ────────────────────────────────────
# CR-1 判"路径消失"（滚动指纹的路径集合），CR-3 判"内容变化"（与 run 起始基线的
# 内容摘要比对）。两条判据都落在 workspace evidence 上，因此 shell / git / 一切
# 间接改写路径被同一条规则覆盖，不需要任何命令字符串解析。
_ACCEPTANCE_BASELINE_KEY = "__test_files_baseline__"


def _content_digest(p: Path) -> str | None:
    """文件内容摘要；不存在 / 不可读 → None。"""
    try:
        return hashlib.sha256(p.read_bytes()).hexdigest()
    except OSError:
        return None


def _git_tracked_paths(root: Path) -> set[str]:
    """git 仓库里全部已跟踪文件（相对路径）；非仓库返回空集。

    只在**没有显式工作区根**时用：那时 `_workspace_fingerprint` 走 git 快路径，
    指纹里只有**变更**文件 —— 拿它当验收文件的枚举来源会得到近乎空的基线，
    "改写一个本来干净的测试文件"就没有前值可比。本函数只在建立基线时调一次。
    """
    try:
        r = subprocess.run(["git", "ls-files", "-z"], cwd=str(root),
                           capture_output=True, text=True, timeout=10,
                           encoding="utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError):
        return set()
    if r.returncode != 0:
        return set()
    return {p for p in (r.stdout or "").split("\0") if p}


def _capture_acceptance_baseline(context: AgentContext) -> dict[str, str]:
    """**拍下**当前磁盘上的验收文件内容身份：{相对路径: 内容摘要}。

    名字刻意是"拍一次"而不是"读基线"：它每次都按当前磁盘重算，只有
    `initialize()` 里那一次调用得到的才是合法基线，结果存进 metadata 后
    **永不刷新** —— 这是 CR-3 的全部要害。
    滚动指纹（`__ws_fingerprint__`）不能兼任这个角色：它每一步都被覆写，
    模型"先改坏测试 → 再跑 pytest"就会把改坏后的状态当成新常态，漏洞原样存在。
    基线只能来自"模型还没动过任何东西"的那一刻。

    ponytail: 每个验收文件读一遍全文；SWE-bench 量级是毫秒级。真成为瓶颈再加
    mtime 未变的增量复用 —— 但那必须同时处理 mtime 伪造，别为性能牺牲正确性。
    """
    root = _explicit_workspace_root(context) or Path(".")
    # 验收文件的枚举来源：显式根下是目录树指纹的全部 key；无显式根时指纹只含
    # 变更文件，另行用 git 索引补齐（见 _git_tracked_paths）。
    cands = {_norm_target_path(_fp_key(k))
             for k in (context.metadata.get("__ws_fingerprint__") or {})}
    if _explicit_workspace_root(context) is None:
        cands |= {_norm_target_path(p) for p in _git_tracked_paths(root)}
    out: dict[str, str] = {}
    for rel in sorted(cands):
        if not _is_test_file(root / rel, root):
            continue
        d = _content_digest(root / rel)
        if d is not None:                   # 建立基线时就不存在的 → 不入基线
            out[rel] = d
    return out


def _acceptance_files_changed(context: AgentContext) -> list[str]:
    """当前内容 ≠ run 起始基线的验收文件（**含已消失的**，消失记 inode 读不到）。

    身份用**内容摘要**而非 (mtime_ns, size)：`os.utime` 还原 mtime + 等长替换
    可以让滚动指纹完全看不出变化，而内容摘要描述的是模型控制不了的事实。

    消失也在这里报，而不是只靠 CR-1：CR-1 判据是**滚动**指纹的**路径集合**变化，
    在 git 快路径（无显式工作区根）下，一个 run 起始时干净的文件被删除只会让
    git 多出一行 ` D path`，没有 key 消失 —— 那条路径上 CR-1 静默失效。基线是
    真正的状态判据，顺带把这一格补上（恢复判据也随之统一为"内容回到 run 起始"）。
    """
    baseline = context.metadata.get(_ACCEPTANCE_BASELINE_KEY) or {}
    root = _explicit_workspace_root(context) or Path(".")
    out = []
    for rel, want in baseline.items():
        if _content_digest(root / rel) != want:   # None（消失）同样满足
            out.append(rel)
    return sorted(out)


def _acceptance_violation(context: AgentContext) -> tuple[list[str], list[str]]:
    """当前处于违规态的验收文件：(被删除的, 内容被改写的)。

    CR-1（路径消失）与 CR-3（内容变化）两条判据互补，合成一个入口给完成门禁用。
    """
    return (sorted(context.metadata.get("test_files_removed") or ()),
            sorted(context.metadata.get("test_files_modified") or ()))


# ── CR-5: pytest scope 配置基线 ────────────────────────────────────
# `baseline_test_count` 由"首次完整范围运行"建立，而"完整范围"由 pytest 的配置
# 定义：addopts / testpaths / python_files / python_classes / python_functions /
# norecursedirs / collect_ignore。这些配置**不在**验收文件集合里（`_is_test_file`
# 只认 tests/、test_*.py、conftest.py、pytest.ini、tox.ini、.coveragerc），于是
# 模型可以在首次 pytest 之前写 `addopts = "--ignore=<失败的测试>"`，用**缩小后**的
# 套件跑出全绿，把缩小后的规模锁成 baseline —— 数量判据在这里失效：它只能证明
# "这次跑到的数量 == 之前跑到的数量"，从来没有证明过"完整"。
#
# 判据因此落在配置本身：run 起始把**定义 pytest scope 的那一段**取一次内容摘要，
# 之后每次测试结果判定都重新比对，不一致 → 本次运行不产生任何判定、不得建立
# baseline、不得 complete（fail-closed）。只取那一段、不取整个文件：pyproject.toml /
# setup.cfg 的大部分内容是普通项目配置（依赖、打包、格式化），"加个依赖"这样的
# 合法修改不得被当成验收标准被改。
_SCOPE_CONFIG_KEY = "__pytest_scope_baseline__"
#: 文件 → 定义 pytest scope 的段（pytest 自己的读法：pyproject 用点号表名，
#: 其余 ini 用段名）。tox.ini / pytest.ini 另有 CR-3 整文件保护，这里按同样的
#: 段口径纳入 scope 基线，顺带覆盖"运行期间**新增**一个配置文件"的形态。
_SCOPE_CONFIG_SECTIONS = {
    "pyproject.toml": ("tool", "pytest", "ini_options"),
    "setup.cfg": ("tool:pytest",),
    "pytest.ini": ("pytest",),
    "tox.ini": ("pytest",),
}
#: 文件不存在 / 没有该段 —— 同一件事（"后来才出现"同样算变化）
_SCOPE_ABSENT = "absent"


def _ini_section(text: str, header: str) -> str | None:
    """取 ini 段原文（`[header]` 到下一个段头）；没有该段 → None。

    行扫描而非 configparser：不引入插值 / 重复段 / 续行带来的语义分歧，段内容
    逐字节可比 —— 正好是"这段配置有没有被改"要的东西。
    """
    want = header.strip().lower()
    body: list[str] = []
    inside = False
    for ln in text.splitlines():
        s = ln.strip()
        if s.startswith("[") and s.endswith("]"):
            if inside:
                break
            inside = s.lower() == want
            continue
        if inside:
            body.append(ln)
    return "\n".join(body) if inside else None


def _pyproject_scope_section(raw: bytes) -> str | None:
    """pyproject.toml 里 `[tool.pytest.ini_options]` 表的规范化文本。

    用 tomllib（与 pytest 的读法一致）：`[tool.pytest]` + `ini_options = {...}`
    这类等价写法也能认出来，行扫描会漏。py<3.11 没有 tomllib、或文件不是合法
    TOML → 返回整份文本：判据退化为"整个文件的摘要"，仍确定、仍 fail-closed。
    """
    text = raw.decode("utf-8", "replace")
    try:
        import tomllib
    except ImportError:
        return text
    try:
        node: object = tomllib.loads(text)
    except Exception:
        return text
    for key in _SCOPE_CONFIG_SECTIONS["pyproject.toml"]:
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    return json.dumps(node, sort_keys=True, default=str)


def _scope_config_identity(path: Path) -> str:
    """一个配置文件对 pytest scope 的贡献（内容摘要）。

    文件不存在、或没有 pytest 段 → `_SCOPE_ABSENT`（固定值，与任何摘要都不同）。
    """
    try:
        raw = path.read_bytes()
    except OSError:
        return _SCOPE_ABSENT
    if path.name.lower() == "pyproject.toml":
        section = _pyproject_scope_section(raw)
    else:
        section = _ini_section(raw.decode("utf-8", "replace"),
                               f"[{_SCOPE_CONFIG_SECTIONS[path.name.lower()][0]}]")
    if section is None:
        return _SCOPE_ABSENT
    return hashlib.sha256(section.encode("utf-8")).hexdigest()


def _capture_scope_baseline(context: AgentContext) -> dict[str, str]:
    """run 起始拍一次 scope 配置身份；**永不刷新**（与 CR-3 基线同理）。"""
    root = _explicit_workspace_root(context) or Path(".")
    return {name: _scope_config_identity(root / name) for name in _SCOPE_CONFIG_SECTIONS}


def _pytest_scope_drift(context: AgentContext) -> list[str]:
    """当前 scope 配置身份 ≠ run 起始的文件（含"运行期间才出现的段"）。"""
    baseline = context.metadata.get(_SCOPE_CONFIG_KEY)
    if not baseline:
        return []
    root = _explicit_workspace_root(context) or Path(".")
    return sorted(name for name, want in baseline.items()
                  if _scope_config_identity(root / name) != want)


def _cli_scope_msg(options: list[str]) -> str:
    """命令行显式缩范围时的指令（N-1）。"""
    return "\n".join([
        "[TestGuard] 本次测试命令**显式缩小了收集范围**：",
        *(f"- {o}" for o in options),
        "被这些选项排除的测试没有运行，因此这次全绿不能证明完整套件通过 —— "
        "本结果既不是通过证据，也不是失败证据，不得据此建立基线或判定完成。",
        "请用未指定目标、不带过滤选项的完整套件命令重新验证：python -m pytest -q",
    ])


def _scope_config_msg(files: list[str]) -> str:
    """scope 配置偏离时的指令（测试结果判定与完成门禁共用同一段话）。"""
    return "\n".join([
        "[TestGuard] 定义 pytest 收集范围的配置与本次运行开始时不一致：",
        *(f"- {p}" for p in files),
        "pytest 跑哪些测试由这些配置决定（addopts / testpaths / python_files / "
        "norecursedirs ...）。改掉它们之后，\"这一轮收集到多少测试\"就不再能证明"
        "覆盖了完整验收范围 —— 本次结果既不是通过证据，也不是失败证据。",
        "在恢复之前任务不得判定完成，也不得据此建立测试基线。",
        "请把它们恢复到本次运行开始时的内容（例如 `git restore <path>`）。"
        "与 pytest 范围无关的普通项目配置不受此限制。",
    ])


# ── CR-4: 测试窗口内的验收文件守卫 ────────────────────────────────
# CR-1/CR-3 的判据都取自**工具调用返回之后**的端状态。一次调用内部完成
# "改写验收文件 → 跑 pytest → 还原原内容"（A → B → pytest → A）时，端状态与 run
# 起始逐字节相同，中间态没有任何观测点，而被污染的 green 已经产生 —— 判定点晚于
# 证据产生点，事后追证据追不回来。修复把判定点移进**测试进程内部**（见
# acceptance_guard 模块）：pytest 会话启动时（改写已发生、还原未发生）核对验收
# 文件清单，不一致就中止会话（非 0 退出、不产出任何计数）。这里只负责三件事 ——
# 把 run 起始清单交给守卫、把守卫注入测试命令的子进程环境、把守卫的判定接进
# 测试证据判定。
_GUARD_METADATA_KEY = "__acceptance_guard__"

#: 守卫目录里自动加载的 sitecustomize（标准库-only，见 acceptance_guard 模块头）。
#: 解释器启动时由 `site` 自动 import —— 此刻 shell 里的 `set "PYTEST_ADDOPTS="`
#: 早已生效完毕，所以这里写回的环境变量是模型**覆盖不掉**的那一份。
_GUARD_SITECUSTOMIZE = '''\
"""把验收窗口守卫强制注入本解释器启动的 pytest 会话（由 Runtime 生成）。

模型可以在 shell 命令里覆盖/清空 PYTEST_ADDOPTS，从而抹掉 Runtime 注入的 `-p`；
本模块由解释器在启动时自动加载（守卫目录在 PYTHONPATH 上），此时不再有 shell
的环境变量操作，注入因此不可被命令改写。
"""
import os

_FLAG = "-p zmai_acceptance_guard"
_addopts = os.environ.get("PYTEST_ADDOPTS", "").strip()
if _FLAG not in _addopts:
    os.environ["PYTEST_ADDOPTS"] = (_addopts + " " if _addopts else "") + _FLAG
'''


def _install_acceptance_guard(context: AgentContext) -> None:
    """把 run 起始的验收文件清单（= CR-3 基线）交给测试窗口守卫。

    守卫目录放在**工作区之外**：它只是被测解释器要 import 的一个模块 + 一份清单
    ——放进工作区会被计入工作区指纹，也会污染 agent diff。清单与 CR-3 基线同一
    时刻产生、同样**永不刷新**：守卫比对的必须是"run 起始"，不是"上一次"。
    """
    baseline = context.metadata.get(_ACCEPTANCE_BASELINE_KEY) or {}
    if not baseline:
        return                          # 没有验收文件 → 无可保护对象，不注入
    root = (_explicit_workspace_root(context) or Path(".")).resolve()
    guard_dir = Path(tempfile.mkdtemp(prefix="zmai-acceptance-guard-"))
    # 进程退出时清理；长驻进程（harness）在 run 期间保留，退出时统一回收。
    atexit.register(shutil.rmtree, str(guard_dir), ignore_errors=True)
    shutil.copy2(Path(__file__).with_name("acceptance_guard.py"),
                 guard_dir / "zmai_acceptance_guard.py")
    # ── CR-4 残余：注入不得被命令内的环境变量改写 ────────────────────
    # `-p zmai_acceptance_guard` 走 PYTEST_ADDOPTS，而模型可以在 shell 命令里
    # `set "PYTEST_ADDOPTS="` 把它整车抹掉 —— 守卫静默消失。这里补三层：
    #   1) sitecustomize.py：解释器启动时（shell 的环境变量操作**已经结束**）把
    #      `-p` 重新写回 os.environ，模型无法在命令里覆盖它；
    #   2) receipt：守卫加载时留凭据，Runtime 事后核对（见 _guard_receipt_advanced）
    #      —— PYTHONPATH 连 sitecustomize 一起被清掉时，本次运行 fail-closed；
    #   3) 守卫产物摘要（见下面的 module_digest / sitecustomize_digest）：改写守卫
    #      本体同样 fail-closed。
    (guard_dir / "sitecustomize.py").write_text(_GUARD_SITECUSTOMIZE, encoding="utf-8")
    manifest = guard_dir / "manifest.json"
    manifest.write_text(json.dumps({"root": str(root), "files": baseline}),
                        encoding="utf-8")
    receipt = guard_dir / "receipt"
    receipt.write_text("", encoding="utf-8")
    context.metadata[_GUARD_METADATA_KEY] = {
        "dir": str(guard_dir),
        "manifest": str(manifest),
        "digest": _content_digest(manifest),
        "receipt": str(receipt),
        "receipt_seen": "",
        # 守卫自身的代码摘要：目录在工作区之外，但模型仍可能顺着 PYTHONPATH 找到
        # 并改写它（改成空壳 + 照写凭据）。与清单同理：摘要不符 → 本次运行不作数。
        "module_digest": _content_digest(guard_dir / "zmai_acceptance_guard.py"),
        "sitecustomize_digest": _content_digest(guard_dir / "sitecustomize.py"),
    }
    logger.info("Acceptance window guard installed: %d file(s) (%s)",
                len(baseline), guard_dir)


def _acceptance_guard_env(context: AgentContext) -> dict[str, str]:
    """测试命令子进程的环境增量：把守卫模块注入被测解释器。

    只"追加"不覆盖：PYTHONPATH 前置守卫目录、PYTEST_ADDOPTS 追加 `-p`，项目自己
    的取值原样保留（漏掉这条会让合法运行的 pytest 行为被改变）。
    """
    g = context.metadata.get(_GUARD_METADATA_KEY)
    if not g:
        return {}
    pypath = os.environ.get("PYTHONPATH", "")
    addopts = os.environ.get("PYTEST_ADDOPTS", "").strip()
    return {
        "PYTHONPATH": g["dir"] + (os.pathsep + pypath if pypath else ""),
        "PYTEST_ADDOPTS": ((addopts + " ") if addopts else "") + "-p zmai_acceptance_guard",
    }


def _guard_receipt_advanced(context: AgentContext, g: dict[str, Any]) -> bool:
    """守卫是否真的在本次 pytest 进程里加载过（CR-4 残余）。

    守卫每次加载都会往 receipt 追加一行；Runtime 记住上一次的内容，要求**本次调用
    之后必须变化**。命令里清掉 PYTEST_ADDOPTS（或连 PYTHONPATH 一起清）会让守卫
    静默消失、receipt 原地不动 —— 这正是要 fail-closed 的形态。
    """
    try:
        raw = Path(g["receipt"]).read_text(encoding="utf-8")
    except OSError:
        return False
    if raw == g.get("receipt_seen", ""):
        return False
    g["receipt_seen"] = raw
    return True


def _acceptance_guard_broken(context: AgentContext, test_out: str, cmd: str = "",
                             *, counts: int = 0) -> bool:
    """本次测试运行是否被守卫否掉（或守卫自身已不可信）。

    * 输出里有守卫的中止标记 → 会话在验收文件偏离时被中止：本次运行不产生任何
      判定（既非通过也非失败）；
    * 清单文件与 run 起始记录不一致（被删/被改）→ 守卫的判据不再可信，本次结果
      一律不作为证据（fail-closed：不给"改掉清单就把守卫变成摆设"留静默通道）；
    * 命令**确实调用了 pytest**，却没有留下守卫的运行凭据 → 注入被命令内的环境变量
      覆盖/清除，守卫根本没加载：同样不产生任何判定（fail-closed，见
      `_guard_receipt_advanced`）。非 pytest 运行器（unittest / nose）不要求凭据 ——
      守卫本来就只注入 pytest。

    ── 凭据只在本次运行**产出结构化计数**时是判据 ──
    零计数（`--collect-only` / 超时 / 被中止）的运行本来就不产生任何判定
    （见 step 里的 `_no_verdict`）：凭据缺失不改变结论，却会把消息分支从
    `[TEST_TIMEOUT]` / `[NO_TEST_EVIDENCE]` 抢走 —— 超时的有界预算（按
    `completion_block_count` 收敛）因此永不计数。凭据核对照旧无条件执行
    （`receipt_seen` 必须跟上），只是**缺失**仅在 counts > 0 时才算"守卫被覆盖"。
    """
    g = context.metadata.get(_GUARD_METADATA_KEY)
    if not g:
        return False
    if ACCEPTANCE_GUARD_MARKER in test_out:
        return True
    if _content_digest(Path(g["manifest"])) != g["digest"]:
        return True
    guard_dir = Path(g["dir"])
    if (_content_digest(guard_dir / "zmai_acceptance_guard.py") != g["module_digest"]
            or _content_digest(guard_dir / "sitecustomize.py") != g["sitecustomize_digest"]):
        logger.warning("Acceptance guard module was modified during the run (%s)",
                       context.agent_id)
        return True
    _cmd = (cmd or "").lower()
    _advanced = _guard_receipt_advanced(context, g)
    if ("pytest" in _cmd or "py.test" in _cmd) and counts > 0 and not _advanced:
        logger.warning(
            "Acceptance guard receipt not advanced — guard injection was overridden "
            "(%s): %s", context.agent_id, (cmd or "")[:120],
        )
        return True
    return False


def _acceptance_guard_msg() -> str:
    """守卫否掉本次运行时的指令（测试结果与完成门禁共用同一段话）。"""
    return "\n".join([
        "[TestGuard] 本次测试运行没有产生有效的守卫判定，两种原因之一：",
        "1) 测试执行期间验收/测试文件与本次运行开始时的内容不一致（守卫已中止会话）；",
        "2) 守卫根本没有加载 —— 命令改写了 PYTHONPATH / PYTEST_ADDOPTS，"
        "把 Runtime 注入的 pytest 插件覆盖或清掉了。",
        "该运行的结果既不是通过证据，也不是失败证据 —— 它描述的不是项目的"
        "验收标准，不得作为完成依据。",
        "请先（1）把测试文件恢复到本次运行开始时的原始内容"
        "（例如 `git restore <path>` / `git checkout -- <path>`），并"
        "（2）不要用 shell 命令覆盖 PYTHONPATH / PYTEST_ADDOPTS，"
        "再重新运行完整测试套件：python -m pytest -q",
    ])


def _reset_edit_failure_recovery(context: AgentContext) -> None:
    """真实修改已落地 → 清零 edit-failure 恢复状态 + 完成守卫的 block 计数。

    调用点是全仓唯一的**进展边界**：两个调用方都是"工作区指纹真的变了"的分支
    （修改成功，或执行失败但真实改写了工作区）。因此这里统一重置所有
    "按'连续无进展'度量"的状态。

    清零对象：
      * edit-failure 计数 + 两种一次性额度（定向 read / 目标发现 grep）——
        历史 edit 失败不得污染后续 repair cycle，也不得在下一次进入强制
        修改期时留下可用的额度；
      * `completion_block_count` —— 该守卫的语义是"**连续**没有取得有效进展的
        completion block 累计到 N 次即明确失败"（见 agent.py 完成守卫处的
        "blocked Nx without progress" 文案）。计数若只增不减，就变成"整个 run
        历史累计 N 次"，于是模型每做几次真实修改、又几次过早收尾，就会被判
        FAILED —— 而它其实一直在推进，与本函数的调用点直接矛盾。
        在进展边界归零，"N 次"才等价于"N 次连续无进展"。
        判据落在工作区证据（指纹变化）上，而不是 `ever_modified`：后者只证明
        历史上改过代码，不证明最近一次 block 之后取得了进展。

    判据由调用方按工作区证据（had_modification / ever_modified）给出，而不是
    "工具返回 success"。
    """
    context.metadata["edit_failure_recovery_attempts"] = 0
    context.metadata["edit_recovery_read_allowance"] = 0
    context.metadata["completion_block_count"] = 0
    context.metadata["edit_recovery_grep_allowance"] = 0


def _take_edit_recovery_read(context: AgentContext, tc: ToolCall) -> bool:
    """edit 失败恢复期内，放行**一次**对上一次失败目标文件的定向 read。

    只放行 read_file、只放行目标文件本身、只放行一次、消耗即失效。不解除整段
    force_edit（其余 read/grep/git/非测试 shell 仍被拒绝），也不给别的文件开口子。
    """
    left = int(context.metadata.get("edit_recovery_read_allowance", 0) or 0)
    if left <= 0 or tc.name != "read_file":
        return False
    target = str(context.metadata.get("edit_failure_target") or "")
    if not target:
        return False
    if _norm_target_path((tc.params or {}).get("path", "")) != _norm_target_path(target):
        return False
    context.metadata["edit_recovery_read_allowance"] = left - 1
    return True


def _take_edit_recovery_grep(context: AgentContext, tc: ToolCall) -> bool:
    """目标未知的 edit 失败恢复期内，放行**一次** grep 用于发现源码目标。

    与 `_take_edit_recovery_read` 分工互补，二者互斥：
      * 目标已知 → 定向 read（重读真实内容后再改）；
      * 目标未知 → 本函数，一次 grep 找到"该改哪个文件"。

    TestGuard 拒绝时若从未记录过源码目标，`edit_failure_target` 与
    `last_failure_issue` 皆空，恢复提示会要求模型"先用 grep 定位"——但在
    force_edit 下 grep 与 read_file 一并被结构性拦截，这条指令不可执行，
    模型只能在预算内反复盲试 edit/write_file（pylint-6506/5859/7228 的
    0-byte diff 形态）。额度只认 grep、只发一次、不解除 force_edit，
    也不给 read_file/git/普通 shell 开口子。
    """
    if tc.name != "grep":
        return False
    if str(context.metadata.get("edit_failure_target") or ""):
        return False  # 目标已知 → 走定向 read 那条路，不重复开口子
    left = int(context.metadata.get("edit_recovery_grep_allowance", 0) or 0)
    if left <= 0:
        return False
    context.metadata["edit_recovery_grep_allowance"] = left - 1
    return True


def _handle_edit_failure(context: AgentContext, cm: ContextManager,
                         tc: ToolCall, result: ToolResult) -> None:
    """目标源码文件的 edit/write_file 执行失败 → 有界、定向的恢复注入。

    只处理"调用真的执行了、但对目标文件的修改没有落地"这一种失败
    （空 diff / 正则错误 / 截断 / 写入失败 / TestGuard 拒绝）。

    不处理 EDIT_VALIDATION_FAILED —— 那条已有专用路径（[EDIT_REPAIR]），
    这里若也消费会让同一失败被两套机制重复计数。

    TestGuard 拒绝是个特例：那次尝试的目标本身非法（测试/验收文件只读），
    绝不能把它当成"恢复目标"写进提示，否则等于持续把模型推向测试文件。
    """
    error = result.error or ""
    if "EDIT_VALIDATION_FAILED" in error:       # 语法错误：专用路径，不重复消费
        return
    attempted = str((tc.params or {}).get("path", "") or "")
    _test_rejection = "TestGuard" in error
    if _test_rejection:
        # 保持上一次的**源码**目标（若有），而不是这次被拒的测试文件
        target = str(context.metadata.get("edit_failure_target") or "")
        if not target:                          # 还没记录过源码目标 → 用诊断落点兜底
            target = str(getattr(
                context.metadata.get("last_failure_issue"), "file", "") or "")
            # 兜底目标取自 traceback 落点，而**测试失败**的落点就是测试文件本身
            # （FailureIssue.file = 断言失败的那个 test_*.py）。不过滤就等于把
            # "恢复目标"指向只读验收文件：定向 read 额度发给它，提示还会要求
            # "让 target 真实发生修改"——与本函数下方"target 必须是业务源码文件"
            # 直接矛盾，并把模型持续推回它刚被拒绝的动作（pylint-5859/7228）。
            # 判据复用 TestGuard 自己的 `_is_test_file`，保证两边口径完全一致：
            # 凡 TestGuard 拒绝写入的文件，都不得被当作恢复目标。
            if target:
                _root = Path((context.config or {}).get("project_path")
                             or context.workspace or ".")
                _tp = Path(target)
                if _is_test_file(_tp if _tp.is_absolute() else _root / _tp, _root):
                    target = ""
    else:
        target = attempted
        if attempted:
            context.metadata["edit_failure_target"] = attempted

    attempts = int(context.metadata.get("edit_failure_recovery_attempts", 0) or 0) + 1
    context.metadata["edit_failure_recovery_attempts"] = attempts
    _stats(context, edit_failures=1)
    first_line = (error.strip().splitlines() or ["(no error message)"])[0]
    logger.warning("[EDIT_FAILURE] file=%s attempt=%d/%d error=%s",
                   attempted or "(unknown)", attempts,
                   MAX_EDIT_FAILURE_RECOVERIES, first_line)

    if attempts > MAX_EDIT_FAILURE_RECOVERIES:
        # 有界：不再注入。强制期预算 / completion fail-closed / LoopGuard 照旧收尾。
        logger.warning(
            "Edit-failure recovery budget exhausted (%d) — no further recovery (%s)",
            attempts, context.agent_id,
        )
        return
    _stats(context, edit_failure_recoveries=1)

    # 定向 read：只在"读取本来就被结构性禁用"的强制修改期内才需要放行一次。
    # 判据落在 **target**，而不是"这次被拒的文件"：TestGuard 拒绝时 target 保留的
    # 是上一次的源码目标（见上方分支），沿用 `not _test_rejection` 会把源码 target
    # 的读取额度一并关掉，让下面那句 "read target first" 变成空头支票。
    # 额度只放行 `read_file`（见 _take_edit_recovery_read），不触及 edit/write_file，
    # TestGuard 对测试文件的写保护语义不受影响。
    _force_edit_active = bool(context.metadata.get("force_edit"))
    _arm_read = bool(target) and _force_edit_active
    if _arm_read:
        context.metadata["edit_recovery_read_allowance"] = 1
    # 目标未知（TestGuard 拒绝且从未记录过源码目标）→ 发一次 grep 发现额度。
    # 此时若不放行 grep，恢复提示里"先用 grep 定位"在 force_edit 下不可执行：
    # 两个工具都被结构性禁用，模型拿不到任何靶子，只能盲试直到预算耗尽。
    _arm_grep = not target and _force_edit_active
    if _arm_grep:
        context.metadata["edit_recovery_grep_allowance"] = 1

    lines = [
        "[EDIT_FAILURE_RECOVERY] 上一次 edit/write_file 执行失败——修改没有落地。",
        f"target: {target or '(未知——尚无合法的业务源码目标，需要先定位)'}",
        f"error: {first_line}",
    ]
    if _test_rejection:
        lines.append(
            "注意：失败原因是目标文件属于测试/验收文件（TestGuard 只读保护）。"
            "测试文件永远不得修改——target 必须是业务源码文件。"
        )
        if _arm_grep:
            # 兜底目标（诊断落点）恰好是测试文件、已被过滤掉时，必须说清
            # "为什么这次没有 target"，否则模型会以为只是暂时拿不到。
            lines.append(
                "上一次的失败目标属于测试/验收文件，已被排除，**不得**作为恢复目标"
                "（它只读，改它等于伪造验收）。本次恢复没有已知 target。"
            )
    if _arm_grep:
        # 目标未知时，恢复提示不能只喊"去用 grep"却把它拦死。明确说明这是
        # **本次恢复专属的一次性额度**，避免模型把它当成通用读取权限而滥用。
        lines.append(
            "本次 recovery 只允许一次 `grep` 用于发现业务源码目标"
            "（force_edit 下这是唯一一次读取类调用，且不会因此开放 read_file）。\n"
            "步骤：① 用这次 grep 定位真正需要修改的业务源码文件；"
            "② 重新规划改法；③ 再用 `edit` / `write_file` 修改该源码文件。\n"
            "目标确定之前，不要重复尝试修改测试文件——测试文件永远不得修改。"
        )
    lines += [
        "required:",
        "- read target file first: 先 `read_file` 读 target 的**真实**内容与行号"
        + ("（读取工具当前被禁用，仅本文件放行一次）" if _arm_read else
           "（目标未知 → 先用上面那唯一一次 `grep` 定位业务源码文件，再读它）"
           if _arm_grep else ""),
        "- re-plan the edit: 按读到的真实内容重新确定改法（old_text / 行号 / 新内容）",
        "- retry modification: " + (
            "目标确定后，下一次用 `edit` 或 `write_file` 让该源码文件真实发生修改"
            if _arm_grep else
            "下一次必须用 `edit` 或 `write_file` 让 target 真实发生修改"
        ),
        "- do not modify tests or unrelated files: 不得改测试文件、草稿文件或无关路径",
        "不要用重跑 pytest、写草稿脚本或探索无关路径来替代这次修改——"
        "先把这次失败的修改在真正的源码目标上做成。",
    ]
    cm.add_message("user", "\n".join(lines))


def _acceptance_files_msg(removed: list[str], rewritten: list[str]) -> str:
    """CR-1/CR-3: 验收文件违规期间的统一指令（检测点 / 测试结果 / 完成门禁三处共用）。

    删除（CR-1）与改写（CR-3）共用一段文字：两者的处置完全一致 —— 都不是修复、
    都不产生任何验证证据、都必须先恢复到 run 起始状态再重跑完整套件。
    """
    lines = ["[TestGuard] 验收/测试文件处于违规状态："]
    lines += [f"- {p}（已从工作区删除）" for p in removed]
    lines += [f"- {p}（内容与本次运行开始时不一致）"
              for p in rewritten if p not in removed]
    lines += [
        "测试文件是只读验收标准，删除或改写它都不构成修复，也不能作为完成依据。",
        "在恢复之前：测试结果既不是通过证据也不是失败证据，任务不得判定完成，"
        "也不得据此建立测试基线。",
        "请先把这些文件恢复到**本次运行开始时**的原始内容"
        "（例如 `git restore <path>` / `git checkout -- <path>`；"
        "若起始状态与 git 中的版本不同，则写回该原始内容），"
        "恢复后再运行完整测试套件验证。",
    ]
    return "\n".join(lines)


def _degenerate_response_reason(response: BackendResponse) -> str | None:
    """退化响应判定：退化时返回原因字符串，正常响应返回 None（P1-B）。

    观测（pylint-5859 / 7228）：连续 4 次 `content="" tool_calls=[]` 被当成正常回合
    消费 —— 完成门禁累计 3 次后 fail，全程没有任何机制重试或标注它。Runtime 无法
    区分"模型什么都没说"与"模型主动收尾"。

    两类退化：
      1. 截断且**无** tool_calls：stop_reason=length/max_tokens —— 文本不完整，
         不能当作结束（deepseek 透传 OpenAI 的 "length"；claude/gemini 归一化为
         "max_tokens"）。带 tool_calls 的截断回合不在此列：重试复用的是同一个请求
         （max_tokens 不变），截断是确定性的，重试只会烧完预算再 fail-closed，
         反而把一次可继续的回合变成硬失败。
      2. 空响应：content 与 tool_calls 同时为空。

    判据刻意保守：**content 非空即视为正常**。因此 `content="done" + tool_calls=[]`
    这类合法纯文本收尾不受影响，也不影响任何带 tool_calls 的回合。
    """
    if response.stop_reason in ("length", "max_tokens") and not response.tool_calls:
        return f"truncated response (stop_reason={response.stop_reason})"
    if not response.content and not response.tool_calls:
        return "empty response (no content, no tool calls)"
    return None


def _log_stop() -> None:
    """打印任务完成/停止循环的显式日志（自主停止的可审计信号）。"""
    logger.info("[ZMAI] Task completed.")
    logger.info("[ZMAI] Stopping execution loop.")
    logger.info("[ZMAI] No further tool calls allowed.")


def _graph_enter(context: AgentContext, node: Node, reason: str = "") -> None:
    """Graph Runtime 接入点：只记录控制流**实际到达**的节点。

    纯观察层 —— 不做任何判定、不改变任何分支、不写除 graph_trace /
    graph_violations 之外的 metadata。默认 enforce=False，非法转移只记录。
    """
    graph: GraphRuntime | None = context.metadata.get("graph")
    if graph is None:
        graph = GraphRuntime(SWEState(context.metadata))
        context.metadata["graph"] = graph
    graph.enter(node, reason, step=context.step_count)


def _has_full_scope_green_evidence(context: AgentContext) -> bool:
    """是否已有"足以作为完成证据"的绿色运行 —— 唯一判据是**完整范围全绿**。

    `completion.tests_complete` = 有一次达到完整范围基线、且未被后续修改作废的
    全绿运行（record_modification / 失败 / partial_green 都会把它置 False）。

    ── N-2：这里曾经还有第二条分支（"未建立基线 ⇒ 子集全绿也算证据"）──
    它与 partial_green 里那条例外同源，放行的是"模型自选范围的全绿"：没建立基线
    不等于套件只有这么大，只等于"还没跑过完整套件"。零修改要完成，只能靠一次
    可证明覆盖完整范围的运行（裸 `pytest -q`）—— 那条路径会锁定 baseline，
    `tests_complete` 为真，走正常分支即可，不需要旁路。
    """
    completion = context.metadata.get("completion")
    return bool(completion and completion.tests_passed and completion.tests_complete)


def _eval_blocking_completion(context: AgentContext,
                              *, at_completion_point: bool = False) -> bool:
    """SWE-bench eval 模式下，未做任何代码修改时拦截完成判定。

    SWE-bench 的 FAIL_TO_PASS 测试只存在于 test_patch，base_commit 上并不存在。
    agent 在 base 仓库跑现有测试必然全绿 → CompletionState 会把 objective_met
    置 True → 零修改即宣布完成（如 requests-3362 2 步 no_change）。

    本守卫：当 config.eval.require_code_change=true 且 agent 从未成功产生
    edit/write_file 时，即使测试全绿也返回 True（需注入提示并 return cont，
    强制 agent 先做出修改）。

    at_completion_point=True 用于"模型返回纯文本、即将直接 complete"的路径：
    那里不必再问"是否本就该完成"——已经站在完成点上，没改过代码就必须拦。
    """
    cfg = context.config or {}
    if str(cfg.get("eval.require_code_change", "false")).lower() != "true":
        return False
    if context.metadata.get("ever_modified", False):
        return False
    if at_completion_point:
        return True
    completion = context.metadata.get("completion")
    green_once = context.metadata.get("test_success_count", 0) >= 1
    if not (completion and (completion.should_complete() or green_once)):
        return False
    return True


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
- shell_exec: Execute shell commands in the project source directory (cwd = project root)
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
12. The runtime reports a "LIVE REPAIR STATUS" section each step. If it says tests are FAILING, your very next write tool call (`edit` or `write_file`) is MANDATORY — stop reading and make the edit now, even if you are not fully certain of the fix. A verifiable edit is strictly better than endless reading."""  # noqa: E501

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
    force_edit = context.metadata.get("force_edit", False)
    # force_edit 是"读取已被硬拒绝"的硬状态，优先级高于"不注入噪音"：
    # 有一处置位点（regression 恢复）不改 repair_phase，若此时 phase 恰为 idle，
    # 早早 return "" 会让模型在完全禁读的状态下收不到任何状态块。
    if phase == "idle" and not force_edit:
        return ""  # 尚未进入修复态，不注入噪音
    test_failed = context.metadata.get("test_failed", False)
    reads_after_fail = context.metadata.get("reads_after_fail", 0)
    cfg = context.config or {}
    limit = int(cfg.get("fix.read_limit", 3))
    lines = ["\n## LIVE REPAIR STATUS (dynamic, act on this now)"]
    lines.append(f"- repair phase: {phase}")
    if force_edit:
        # 强制修改期：读取工具已被运行时结构性拒绝，且 reads_after_fail 已被刻意
        # 清零（防止"拦截→喂数→再触发"的自激）。若此处照旧报读取预算，模型每步
        # 都读到 "0/3 reads used"，会认为诊断额度尚存而反复重试同一个 read/shell
        # —— 拦截是硬性的，但模型看到的状态不是，于是空转到 Force-edit 预算耗尽。
        lines.append(
            "- READ TOOLS ARE DISABLED: read_file / grep / git / non-test "
            "shell_exec are being REJECTED by the runtime right now."
        )
        lines.append(
            "- Retrying them cannot succeed — they are blocked, not failing. "
            "No new information will come back."
        )
        lines.append(
            "- The ONLY calls that will execute: `edit`, `write_file`, "
            "`show_to_user`, and `shell_exec` running pytest."
        )
        lines.append("- Your next tool call MUST be `edit` or `write_file`.")
        # edit 失败恢复放行了**一次**对目标文件的定向 read。状态块若继续宣称读取
        # 全禁用，模型会当成矛盾指令而不敢重读，恢复提示里的 "read target first"
        # 就成了空头支票（与 LoopRecovery 处同一条教训）。
        _allow = int(context.metadata.get("edit_recovery_read_allowance", 0) or 0)
        _rt = str(context.metadata.get("edit_failure_target") or "")
        if _allow > 0 and _rt:
            lines.append(
                f"- EXCEPTION (上一次 edit 失败): ONE `read_file` of `{_rt}` is allowed "
                "now so you can re-read its real content before retrying the edit. "
                "After that single read, reads are rejected again."
            )
        # 目标未知时发的是 grep 发现额度。状态块若不声明它，"读取全禁用"就会
        # 与恢复提示里的一次性 grep 互相矛盾（同 `_rt` 那条教训）。
        _gallow = int(context.metadata.get("edit_recovery_grep_allowance", 0) or 0)
        if _gallow > 0:
            lines.append(
                "- EXCEPTION (上一次 edit 失败且目标未知): ONE `grep` is allowed now "
                "to locate the source file that must be modified. After that single "
                "grep, reads are rejected again."
            )
    elif test_failed:
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
            identity_parts.append("## Your Identity")
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
        # 记录项目根的**来源**（在下面的推断回写之前取）：调用方在 config
        # 里声明（CLI / benchmark / eval harness 都这么做）= 这个 run 就是
        # 针对该项目的任务；仅从 cwd 推断出来 = 只是给 agent 提供上下文。
        # 完成门禁据此决定"该项目的测试套件是不是本次验收标准"——
        # 推断出来的项目根不得让一个 "say hello" 任务变成必须出示完整套件
        # 全绿的任务。setdefault：调用方（或上游）可显式预置。
        context.metadata.setdefault("project_root_declared", bool(project_root))
        if project_root:
            project_root = Path(project_root).resolve()
        else:
            detected = RepositoryScanner.find_project_root()
            if detected:
                project_root = detected
                context.config["project_path"] = str(project_root)
                logger.info("Auto-detected project root: %s", project_root)

        # ── P2-1: 显式记录"测试发现状态"，区分 NO_TESTS 与 SCAN_UNKNOWN ──
        # 修复前 scan() 异常只记一条 warning，repo_info 不写入，完成门禁那条
        #   `_has_tests = bool(getattr(repo_info, "test_files", None))`
        # 就把"无法确定有没有测试"静默读成了"没有测试" —— 于是"改过一点代码 +
        # 扫描失败 + 测试从未失败"可以零验证证据直接 COMPLETED。
        # 这里把结果记成显式两态，供完成门禁区分处理（未知 → fail-closed）。
        if project_root and "repo_info" not in context.metadata:
            try:
                repo_info = RepositoryScanner.scan(project_root)
                context.metadata["repo_info"] = repo_info
                context.metadata["test_discovery"] = "known"
                logger.info(
                    "Repository scanned: %s (%d source files, %d test files)",
                    project_root, len(repo_info.source_files), len(repo_info.test_files),
                )
            except Exception as e:
                context.metadata["test_discovery"] = "unknown"
                logger.warning(
                    "Repository scan failed — test discovery UNKNOWN "
                    "(completion gate will fail closed): %s", e,
                )
        # 覆盖剩余分支：没有 project_root（无法扫描）、或调用方已预置 repo_info。
        # 前者无法确定 → unknown；后者是显式提供 → 视为 known。
        if "test_discovery" not in context.metadata:
            context.metadata["test_discovery"] = (
                "known" if context.metadata.get("repo_info") is not None else "unknown"
            )

        # ── 工作区指纹基线（P1-3）────────────────────────────────
        # 必须在任何工具执行**之前**取基线，否则第一次工具调用造成的修改会被
        # 当成"没有前值可比较"而漏掉（`python fix.py` 只改一次就再没变化）。
        if "__ws_fingerprint__" not in context.metadata:
            _explicit_root = _explicit_workspace_root(context)
            context.metadata["__ws_fingerprint__"] = _workspace_fingerprint(
                _explicit_root or Path("."), prefer_git=_explicit_root is None,
            )

        # ── CR-3: 验收文件内容基线 ───────────────────────────────
        # 同样必须在任何工具执行**之前**建立，且**只建立一次、永不刷新**：
        # 它唯一的合法来源就是"模型还没动过任何东西"的那一刻（见基线函数注释）。
        # 依赖上面的 `__ws_fingerprint__`（枚举来源），故顺序固定。
        if _ACCEPTANCE_BASELINE_KEY not in context.metadata:
            context.metadata[_ACCEPTANCE_BASELINE_KEY] = _capture_acceptance_baseline(context)
            logger.info(
                "Acceptance-file content baseline: %d file(s)",
                len(context.metadata[_ACCEPTANCE_BASELINE_KEY]),
            )

        # ── CR-5: pytest scope 配置基线 ───────────────────────────
        # 同样在任何工具执行之前、同样只建立一次：它定义的是"run 起始的收集范围"。
        if _SCOPE_CONFIG_KEY not in context.metadata:
            context.metadata[_SCOPE_CONFIG_KEY] = _capture_scope_baseline(context)

        # ── CR-4: 测试窗口内的验收文件守卫 ────────────────────────
        # 清单来源就是上面的 CR-3 基线：同一时刻（模型还没动过任何东西）、
        # 同样只建立一次。守卫在测试进程内部核对它（见 acceptance_guard）。
        if _GUARD_METADATA_KEY not in context.metadata:
            _install_acceptance_guard(context)

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
            _graph_enter(context, Node.FAILED, "no_backend")
            _l = context.metadata.get("__log__")
            if _l:
                try:
                    _l.record_step(phase="error", action="no_backend",
                                   success=False, error="No available Backend")
                except Exception:
                    pass
            return AgentAction.fail("No available Backend. Please configure an API Key.")

        context.step_count += 1
        _graph_enter(context, Node.ENTRY, f"step {context.step_count}")
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
        # eval 守卫：SWE-bench 模式下未修改代码不得因"现有测试全绿"完成。
        # P0-2A：这里只**抑制完成**，不再直接 return cont —— 在 backend 之前拦截会让
        # 模型永远拿不到决策机会（ever_modified 永远是 False → 条件自锁 → 空转到
        # max_steps 后被报成 timeout）。拦截提示由 post-tool EvalGuard 与 completion
        # guard 注入，那两处都在模型已经被咨询过之后。
        _eval_blocks_completion = _eval_blocking_completion(context)
        if _eval_blocks_completion:
            logger.info(
                "[EvalGuard] Completion suppressed at step entry: no code modification yet "
                "(%s, step %d) — continuing so the backend can act",
                context.agent_id, context.step_count,
            )

        _green_once = context.metadata.get("test_success_count", 0) >= 1
        if (not _eval_blocks_completion and completion
                and (completion.should_complete() or _green_once)):
            logger.info(
                "CompletionState satisfied — entering DONE (%s, step %d): %s",
                context.agent_id, context.step_count, completion.summary(),
            )
            _log_stop()
            _graph_enter(context, Node.DONE, f"completion_state: {completion.summary()}")
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
            _graph_enter(context, Node.PLAN, "auto_plan")
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
                _graph_enter(context, Node.FAILED, "plan_generation_failed")
                return AgentAction.fail(f"Plan generation failed: {e}")

        tool_defs = context.tools.definitions() if context.tools else []

        # Inject memory context if available
        memory_context = ""
        if context.memory:
            wm = context.memory.working(context.agent_id)
            # namespace 必须与写入端（见 _MEMORY_NS_TOOLS）一致：search 的默认
            # namespace 是 "default"，与写入用的 "tools" 不同，读回来永远是空。
            mem_items = wm.search("", namespace=_MEMORY_NS_TOOLS)  # all entries
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

        _graph_enter(context, Node.BACKEND, "invoke")
        for attempt in range(max_retries):
            try:
                response = await run_sync(context.backend.invoke, request)
                last_error = None
            except BackendError:
                raise  # BackendError propagates immediately, no retry
            except Exception as e:
                last_error = e
                response = None
            # ── P1-B: 退化响应不进入正常回合 ──────────────────
            # HTTP 200 + 空/截断响应走同一条 retry budget 与退避：它与网络抖动
            # 一样属于"这一次调用没拿到可用输出"，不该被当成一次模型决策消费。
            # 复用既有预算，不新增状态机；耗尽后由下方 fail-closed 分支收尾。
            if last_error is None and response is not None:
                _degenerate = _degenerate_response_reason(response)
                if _degenerate is not None:
                    _stats(context, degenerate_responses=1)
                    logger.warning("[DegenerateResponse] attempt %d/%d: %s (%s)",
                                   attempt + 1, max_retries, _degenerate, context.agent_id)
                    last_error = RuntimeError(f"[DEGENERATE_RESPONSE] {_degenerate}")
                    response = None
            if last_error is None:
                break
            if attempt < max_retries - 1:
                wait = 2 ** attempt
                logger.info(
                    "Backend call failed (attempt %d/%d), waiting %.1fs: %s",
                    attempt + 1, max_retries, wait, last_error,
                )
                await asyncio.sleep(wait)
            else:
                logger.error(
                    "Backend call permanently failed (attempt %d/%d): %s",
                    attempt + 1, max_retries, last_error,
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
            _graph_enter(context, Node.FAILED, "backend_call_failed")
            return AgentAction.fail(str(last_error or "Backend produced no response"))

        # ── Token 用量累积（G1）──────────────────────────
        # Backend 已解析 usage（input/output/cache），逐轮累加进 metadata。
        # finalize() 将其透出到 AgentResult，Runtime.run() 再透出给 Runner，
        # 供 result.json 统计整体 token 消耗。
        if response.usage:
            _tok = context.metadata.setdefault("token_usage", {
                "input_tokens": 0, "output_tokens": 0,
                "cache_read_tokens": 0, "cache_write_tokens": 0,
            })
            _tok["input_tokens"] += response.usage.input_tokens
            _tok["output_tokens"] += response.usage.output_tokens
            _tok["cache_read_tokens"] += response.usage.cache_read_tokens
            _tok["cache_write_tokens"] += response.usage.cache_write_tokens

        if response.content:
            cm.add_message("assistant", response.content)

        if response.tool_calls:
            _graph_enter(context, Node.TOOL, f"{len(response.tool_calls)} tool call(s)")
            on_progress = context.metadata.get("on_progress")
            # Count tool call success/failure for this round
            step_tool_ok = 0
            step_tool_fail = 0
            guard: LoopGuard | None = context.metadata.get("loop_guard")
            had_modification = False
            # B-1: 本 step 是否发生过"测试命令超时"。超时既非通过也非失败证据，
            # 但必须能被计数（见下方"连续超时预算"），否则同一条命令会无限重跑。
            _step_timed_out = False
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
                    # P1-1: 把"这份内容是否仍在模型可见窗口"的判据交给 ContextManager。
                    # 工具只拿探针，不持有 cm 引用。
                    read_visible=cm.is_read_visible,
                    # CR-4: 测试命令的子进程环境（守卫注入）。非测试命令不使用。
                    env=_acceptance_guard_env(context),
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
                #
                # 例外：测试验证命令放行。完成守卫要求"修改后重跑完整套件确认全绿"，
                # 若 force_edit 把 pytest 一并拦死，两条指令在结构上不可同时满足
                # （模型被告知去验证，却没有任何工具能验证）。测试命令本身不修改
                # 工作区，不构成对修改阶段的绕过；其余 shell/git/read 仍一律拦截。
                _test_cmd_exempt = (
                    tc.name == "shell_exec"
                    and is_test_command(str(tc.params.get("command", "")))
                )
                # 例外：edit 失败恢复期内，对**上一次失败的目标文件**放行一次定向
                # read（消耗式）。不解除 force_edit —— 其余 read/grep/git/非测试
                # shell 照旧被拒绝，其它文件也拿不到这个额度。
                # 目标未知时改发一次 grep 发现额度：两个取用函数互斥（read 只认
                # read_file、grep 只认 grep 且要求 target 为空），不会双重消耗。
                _recovery_read = _force_edit and (
                    _take_edit_recovery_read(context, tc)
                    or _take_edit_recovery_grep(context, tc)
                )
                if (_force_edit and not _test_cmd_exempt and not _recovery_read
                        and tc.name not in ("edit", "write_file", "show_to_user")):
                    # 恢复态优先：上一次修改已让项目无法 import 时，正确的下一步是
                    # "撤回那次修改"，而不是继续叠加新修改（P1-D）。
                    if context.metadata.get("needs_revert"):
                        _reject_msg = (
                            "[FixDriving][Recovery] 强制恢复阶段（READ TOOLS DISABLED）："
                            "上一次修改已让项目无法 import（测试收集不起来）。\n"
                            "read_file/grep/git 已被运行时结构性禁用，重复调用不会成功"
                            "（这不是临时错误）。\n"
                            "先恢复：用 `write_file` 写回修改前的正确内容，或用 `edit` "
                            "删除你刚追加/改错的代码。\n"
                            "不要继续添加新代码（仅允许重跑 pytest 验证恢复情况）。"
                        )
                    else:
                        _reject_msg = (
                            "[FixDriving] 强制修改阶段（READ TOOLS DISABLED）：测试已失败"
                            "且你已读取足够多文件。\n"
                            "read_file/grep/git/非测试 shell_exec 已被运行时结构性禁用，"
                            "重复调用不会成功，也不会返回新信息（这不是临时错误）。\n"
                            "唯一会执行的调用：`edit`、`write_file`、`show_to_user`、"
                            "以及运行 pytest 的 `shell_exec`。\n"
                            "立即用 `edit` 或 `write_file` 修改代码修复失败的测试。"
                        )
                    result = ToolResult.err(_reject_msg)
                    _intercepted = True
                else:
                    result = context.tools.execute_tool(tc.name, tc.params, tctx)
                    _intercepted = False
                _dur = _now_ms() - _ts
                # ── P1-3: 用工作区真实状态判断"是否发生修改" ──
                # 只认工具名（write_file/edit）会漏掉一切经 shell 的修改：
                # `python fix.py`、`sed -i`、`echo ... > app.py`、
                # `git checkout/restore` 都真实改写了工作区，却被当成"未修改"，
                # 于是 eval 模式 ever_modified 恒为 False（EvalGuard 永久阻塞合法
                # 完成），且修改前的 green state 不会被失效。
                # 这里改为比对工作区指纹 —— 判断"实际有没有变"，而不是猜命令意图。
                _ws_changed = False
                if not _intercepted and tc.name not in _READ_ONLY_TOOLS:
                    _explicit_root = _explicit_workspace_root(context)
                    _fp = _workspace_fingerprint(
                        _explicit_root or Path("."),
                        prefer_git=_explicit_root is None,
                    )
                    _prev_fp = context.metadata.get("__ws_fingerprint__")
                    # ── CR-1: 测试文件从工作区消失 = TestGuard 违规 ──────────
                    # 判据放在**证据层**（指纹的路径集合），不放在命令形态上：
                    # 于是 `git rm` / `git mv` / `git checkout <ref> -- tests/` /
                    # `git clean` / `git stash` / `python -c "os.remove(...)"` 等
                    # 一切间接路径被同一条规则覆盖，GitTool 不需要任何命令解析。
                    # 测试文件是只读验收标准：它消失既不是"进展"，也不是合法的
                    # 验证范围变更 —— 收缩后的套件不得被锁成新 baseline。
                    _root = _explicit_root or Path(".")
                    _gone = sorted(
                        p for p in (_prev_fp or {})
                        if p not in _fp
                        and _is_test_file(_root / _norm_target_path(_fp_key(p)), _root)
                    )
                    # ── CR-3: 验收文件**内容**被改写 = TestGuard 违规 ────────
                    # 判据同样是 workspace evidence：与 run 起始的内容摘要基线比对。
                    # shell 改写 / `git checkout <ref> -- tests/...`（回退旧版本）/
                    # sed / 等长替换 + mtime 还原 —— 全部被这一条覆盖：文件还在、
                    # 测试数量也没变，但验收标准的内容已经不是 run 起始的那份了。
                    # 必须用**内容摘要**：等长替换 + `os.utime` 还原后，滚动指纹
                    # (mtime_ns, size) 连"变过"都看不出来。
                    _rewritten = _acceptance_files_changed(context)
                    if _gone or _rewritten:
                        if _gone:
                            context.metadata["test_files_removed"] = sorted(
                                set(context.metadata.get("test_files_removed", ())) | set(_gone))
                        if _rewritten:
                            context.metadata["test_files_modified"] = _rewritten
                        else:
                            # 同一步内已恢复原内容 → 违规态不必建立
                            context.metadata.pop("test_files_modified", None)
                        # 违规态下的旧 green 立即失效：它描述的是一份已被删掉/改写的
                        # 验收标准（防 subset green 后删测试，在下一步 step 入口靠
                        # _green_once 完成；也防同一步内"先全绿再动测试"靠
                        # CompletionState 完成）。只作废旧证据，不记 ever_modified。
                        context.metadata["test_success_count"] = 0
                        if completion:
                            completion.record_modification(step=context.step_count)
                        # 立刻把恢复指令交给模型（step 末随上下文进入下一轮）
                        cm.add_message("user", _acceptance_files_msg(
                            list(context.metadata.get("test_files_removed") or ()),
                            _rewritten))
                        logger.warning(
                            "[TestGuard] acceptance file(s) tampered — removed=%s "
                            "rewritten=%s (%s)",
                            _gone or "-", _rewritten or "-", context.agent_id,
                        )
                    else:
                        if context.metadata.get("test_files_removed"):
                            # 全部恢复原状 → 解除违规态（恢复是唯一合法出口）
                            if all((_root / _norm_target_path(_fp_key(p))).exists()
                                   for p in context.metadata["test_files_removed"]):
                                context.metadata.pop("test_files_removed", None)
                        # `_rewritten` 为空 = 每个基线文件都**在**且内容 == run 起始状态
                        # （消失也算 changed，所以这里连"文件还没回来"都排除了）。
                        context.metadata.pop("test_files_modified", None)
                    _ws_changed = _prev_fp is not None and _fp != _prev_fp
                    # ── CR-1/CR-3: 动验收文件不算"进展" ──────────────────
                    # 该次调用不得计入 had_modification / ever_modified，也不得
                    # 向 LoopGuard 提供 progress（_ws_changed 同源）。违规态下
                    # 整次调用都不算进展，即使同一命令里也改了源码 —— 违规主导。
                    _ws_changed = _ws_changed and not (_gone or _rewritten)
                    context.metadata["__ws_fingerprint__"] = _fp
                # ── 修改证据独立于工具 success ──
                # 判据是"工作区**实际**有没有变"（工作区指纹），而不是"命令是否返回 0"。
                # `git stash pop` 冲突、`git checkout` 部分失败、shell 改完文件才返回
                # 非零 —— 都是执行失败但真实推进了工作区。若把修改证据挂在 success 上：
                #   * 旧 green 不会失效（P0-2 语义被绕过）；
                #   * 步末仍会 guard.record_no_modification() 累加无修改计数，
                #     把已经发生的进展当成停滞（LoopGuard 侧记录了也没用）。
                # 失败的调用不解除 FixDriving 的强制修改要求（force_edit 等仍按成功处理）。
                if _ws_changed and not result.success:
                    had_modification = True
                    context.metadata["ever_modified"] = True
                    if completion:
                        completion.record_modification(step=context.step_count)
                    context.metadata["test_success_count"] = 0
                    # 工作区真的变了 → 这次 edit-failure cycle 结束，状态清零
                    _reset_edit_failure_recovery(context)
                if result.success:
                    step_tool_ok += 1
                    # 只有真正改写工作区代码的调用才算"修改证据"。git status/diff/log
                    # 是只读的：曾把它们计入修改，使一条只读 git 命令即可置
                    # ever_modified=True 并解除 force_edit，同时清空 test_failed ——
                    # 既是 no_change 逃逸口，也让 FixDriving 被无声解除。
                    # 指纹比对天然区分二者：只读 git 不改变工作区 → _ws_changed False。
                    if tc.name in ("write_file", "edit") or _ws_changed:
                        had_modification = True
                        # 记录真实代码修改：eval 守卫据此放行完成判定
                        context.metadata["ever_modified"] = True
                        # 修改成功 → 退出修复态（已产生进展），进入"修改"阶段
                        test_failed = False
                        reads_after_fail = 0
                        context.metadata["force_edit"] = False
                        repair_phase = "edit"
                        # ── CompletionState: 任何修改使旧测试结果失效 ──
                        if completion:
                            completion.record_modification(step=context.step_count)
                        # ── P0-2B: green 计数必须与 CompletionState 同步失效 ──
                        # test_success_count 是 metadata 里的第二个真相源。不清零时，
                        # `or _green_once` 旁路会让"全绿之后又改了代码"仍然判完成——
                        # completion.should_complete() 已是 False，metadata["tests_passed"]
                        # 却仍为 True，两个真相源互相矛盾。
                        context.metadata["test_success_count"] = 0
                        # 目标源码真实发生修改 → edit-failure 恢复计数清零，
                        # 不让历史失败污染后续 repair cycle（P1: edit failure recovery）。
                        _reset_edit_failure_recovery(context)
                else:
                    step_tool_fail += 1
                # ── 测试运行检测（无论成败）──
                # 关键：pytest 失败时 ShellTool 走 error 分支（result.success=False），
                # 必须依然检测，否则 agent 永远不知道"测试失败"、无法进入修复阶段。
                # 因此该检测放在 result.success 分支之外，pytest 成败都执行。
                if tc.name == "shell_exec" and not _intercepted:
                    _cmd_l = str(tc.params.get("command", "")).lower()
                    if "pytest" in _cmd_l or "python -m pytest" in _cmd_l:
                        has_run_test = True
                        reads_without_test = 0
                # 只有真正执行的调用才能作为测试结果证据：被 force_edit 拒绝的调用
                # 没有 exit_code（result.metadata 为空 → 默认 0），若参与判定会被
                # 当成"测试通过"，反而给 test_success_count 记一次全绿。
                if tc.name in ("shell_exec", "git") and not _intercepted:
                    _cmd_l = str(tc.params.get("command", "")).lower()
                    # ── P1: 只认真正的 runner 调用，不认含 "pytest" 字样的命令 ──
                    # 裸子串匹配会把 `type pytest.log`（重放旧的全绿日志）/
                    # `cat pytest.ini` / `pip install pytest` / `echo pytest` /
                    # `python -c "...pytest..."` / `# pytest` 都当成测试运行，
                    # 从而用一条没跑测试的命令取得 tests_complete。
                    if is_test_command(str(tc.params.get("command", ""))):
                        exit_code = int(
                            (result.metadata or {}).get("exit_code", 0)
                        )
                        # 失败时 output 为空、错误在 error 里；合并供 verify_test_output 判定
                        test_out = (result.output or "") + (result.error or "")
                        # ── CR-4: 测试进程内部守卫的判定 ──────────────────
                        # 守卫在测试进程里核对 run 起始清单：被改写的会话会中止并打印
                        # 标记（无任何计数），因此这里的"本次运行不作数"是守卫**在运行
                        # 期间**给出的判定，而不是事后从端状态推断 —— A → B → pytest →
                        # A 的端状态与起始完全相同，端状态判据看不到它。
                        # 判定延后到计数解析之后（凭据判据需要"本次是否产出计数"）。
                        # ── CR-5: pytest 收集范围配置偏离 run 起始 ──────────
                        # "完整范围"是配置定义的，不是数量定义的：模型在首次 pytest
                        # 之前改掉 addopts / testpaths / python_files 等，就能让**缩小
                        # 后**的套件跑出全绿，把缩小后的规模锁成 baseline。
                        _scope_drift = _pytest_scope_drift(context)
                        # 真实退出码交给 verify_test_output 作权威判据：
                        # exit 0 + 结构化汇总无失败 = 通过，输出里的普通文本
                        # （测试名含失败词、被测代码打印的 traceback）不得推翻它。
                        passed = verify_test_output(test_out, exit_code=exit_code).passed
                        # ── P0 基线测试数回退防护（防伪造成功）──
                        # 首次运行记录应运行的总测试数；后续"绿色"运行若实际执行
                        # 总数低于基线（测试被反选/删除/忽略，如 pyproject addopts
                        # 反选或 shell 通配符删除），即使 exit 0 + passed 也视为
                        # 未真正验证业务代码，不得计入完成。
                        _totals = parse_test_totals(test_out)
                        _total_tests = (_totals["passed"] + _totals["failed"]
                                        + _totals["errors"])
                        _guard_broken = _acceptance_guard_broken(
                            context, test_out, _cmd_l, counts=_total_tests)
                        # ── Regression Detection（本轮 vs 上一轮）──
                        # 2 passed,2 failed → 0 passed,4 failed 不能被当作普通失败：
                        # Agent 必须知道"刚才的修改让测试状态变差了"，否则会继续沿
                        # 错误方向改。
                        # 无计数的运行（collection/import error）同样参与比较，只是
                        # 不刷新基线 —— "上一轮还能跑出 1 passed/3 failed，本轮一个测试
                        # 都没跑起来"是最严重的退化，漏掉它等于对灾难性回退保持沉默。
                        # ── P1-1: totals 只在同一测试命令（同一测试范围）内可比 ──
                        # 计数下降只有在"跑的还是同一套测试"时才等于退化。换了一套
                        # 测试再比总数，比较的是两个不同的测试集合。反例：模型未做
                        # 任何修改，`pytest test_fail.py`（0 passed, 1 failed）之后跑
                        # `pytest broken/` 撞上 collection error，被读成"测试整个消失"
                        # → 假 regression + [Recovery] + needs_revert/force_edit。
                        # ponytail: 用命令的 token 集合做 scope 身份，免解析、够用；
                        # 若要容忍 -q 增删或 pytest vs python -m pytest 的改写，
                        # 再提取 pytest 的目标参数作为 key。
                        _scope = " ".join(sorted(_cmd_l.split()))
                        _prev_totals = (
                            context.metadata.get("last_test_totals")
                            if context.metadata.get("last_test_scope") == _scope
                            else None
                        )
                        _verdict = classify_test_progress(_prev_totals, _totals)
                        if _totals["passed"] + _totals["failed"] > 0:
                            context.metadata["last_test_totals"] = dict(_totals)
                            context.metadata["last_test_scope"] = _scope
                            # 测试重新被收集起来 → 已从"改坏"状态恢复
                            context.metadata.pop("needs_revert", None)
                        # 计数正常的运行照旧记录（含首次 "first"）；无计数的运行只在
                        # 判定出退化时记录，避免用 collection error 覆盖正常判定。
                        if _totals["passed"] + _totals["failed"] > 0 or _verdict != "first":
                            context.metadata["test_progress"] = _verdict
                        if _verdict == "regression":
                            # 本轮是否连测试都收集不起来（import / collection error）
                            _module_broken = (
                                _totals["passed"] + _totals["failed"] == 0
                                and _totals["errors"] > 0
                            )
                            _stats(context, regression_detected=1)
                            _graph_enter(context, Node.REGRESSION,
                                         f"{_fmt_test_totals(_prev_totals)} → "
                                         f"{_fmt_test_totals(_totals)}"
                                         + (" (module_broken)" if _module_broken else ""))
                            logger.warning(
                                "[Regression] %s step %d: %s → %s%s",
                                context.agent_id, context.step_count,
                                _fmt_test_totals(_prev_totals),
                                _fmt_test_totals(_totals),
                                " (project no longer importable)" if _module_broken else "",
                            )
                            cm.add_message("user",
                                "[Regression]\n"
                                f"previous test result: {_fmt_test_totals(_prev_totals)}\n"
                                f"current test result: {_fmt_test_totals(_totals)}\n\n"
                                "Regression detected: the latest modification made the "
                                "test state worse. Reconsider the latest change before "
                                "continuing."
                            )
                            if _module_broken:
                                # ── 回退/恢复路径（P0-B / P1-D）──
                                # 语法合法的 edit 也可能是破坏性的：它让项目无法 import，
                                # 测试从"有计数"直接掉到"收集不起来"。这不是进度，必须
                                # 判为退化并强制先恢复，而不是继续叠加新修改。
                                _recoveries = context.metadata.get(
                                    "regression_recoveries", 0) + 1
                                context.metadata["regression_recoveries"] = _recoveries
                                context.metadata["needs_revert"] = True
                                # 重新进入保护态：唯一合法的推进是 edit/write_file
                                # （恢复动作本身）或重跑 pytest 验证。
                                context.metadata["force_edit"] = True
                                _stats(context, module_broken_regressions=1)
                                cm.add_message("user",
                                    "[Recovery] 刚才的修改让项目无法再被 import —— 测试已经"
                                    "收集不起来（collection/import error）。这不是失败变少，"
                                    "而是把项目改坏了。\n"
                                    "必须先恢复，再谈修复：\n"
                                    "1. 用 `write_file` 把刚才改动的文件写回修改前的正确内容，"
                                    "或用 `edit` 删除你刚追加/改错的代码；\n"
                                    "2. 重新运行 `python -m pytest` 确认测试重新被收集"
                                    "（输出里重新出现 passed/failed 计数）；\n"
                                    "3. 恢复之后重新诊断原问题，再动手修改。\n"
                                    "在测试恢复可收集之前，不要继续添加新代码。"
                                )
                        _baseline = context.metadata.get("baseline_test_count")
                        # partial_green：子集全绿但未覆盖完整基线。
                        # 它不是 failed，但也不能算 full_green / complete。
                        # ── P1-2 / P2-2: baseline 只能由"可证明覆盖完整套件"的运行建立 ──
                        # baseline 是"完整套件有多少测试"的断言。唯一能证明这件事的
                        # 命令形式是未指定测试目标（裸 `pytest -q`）——见
                        # `_is_full_scope_test_command`。
                        # P1-2 已堵住"首次子集**全绿**自证 baseline"；P2-2 补上对称的
                        # 那一半："首次子集**失败**"同样不得自证。
                        # 失败只暴露**这次跑了多少个**测试，不暴露套件规模：`pytest
                        # test_x.py` 失败只能证明 test_x.py 里有 1 个测试。旧条件里的
                        # `not passed` 让失败的子集运行获得了绿运行永远拿不到的授权 ——
                        # baseline 被锁成子集规模后，同一子集再跑绿即满足
                        # `_total_tests >= _baseline`，于是"只验证了子集"被当成
                        # "完整套件全绿"，携带未验证范围判定完成（fail-open）。
                        _full_scope_cmd = _is_full_scope_test_command(_cmd_l)
                        # ── N-1: 命令行缩范围 ────────────────────────────
                        # 与 CR-5（配置文件改范围）是同一件事的两种载体。两档判据：
                        #   1) 白名单之外的一切选项 → 本次不作**完整范围证据**（不锁
                        #      baseline）。`-k` / `--collect-only` / 未知插件选项都在内，
                        #      走既有的 partial_green / [NO_TEST_EVIDENCE] 语义。
                        #   2) 命令**点名**了跳过谁 / 换了哪份配置（`--ignore` /
                        #      `--deselect` / `--override-ini` …）→ 连"本 run 自己的
                        #      全绿证据"也不成立：命令本身就证明了它没跑完整套件。
                        _cli_scope_options = cli_scope_filter(_cmd_l)
                        _scope_declared = bool(_cli_scope_options) and \
                            cli_scope_names_exclusions(_cmd_l)
                        _scope_complete = True
                        # ── CR-1/CR-3: 验收文件违规期间不得建立/刷新 baseline ──
                        # 否则"删掉失败测试 → 首次裸 pytest"会把**收缩后**的套件
                        # 规模锁成 baseline，剩余测试全绿即拿到 tests_complete；
                        # 同理"改写失败测试 → 首次裸 pytest"会把**被掏空后**的
                        # 验收标准锁成新常态，两条路径合起来就是 CR-3 的 baseline
                        # 绕过（基线只能来自 run 起始，不得被攻击后的状态污染）。
                        # CR-4: 守卫否掉的运行同样不得建立/刷新 baseline —— 被中止的
                        # 会话没有"这个套件有多大"的任何证据。
                        # CR-5: scope 配置偏离期间同样不得建立/刷新 baseline ——
                        # "完整范围"正是由这份配置定义的，它被改过，"完整"就没有依据。
                        _acceptance_files_intact = (not any(_acceptance_violation(context))
                                                    and not _guard_broken
                                                    and not _scope_drift)
                        if _baseline is None:
                            if _total_tests > 0 and _full_scope_cmd and _acceptance_files_intact:
                                context.metadata["baseline_test_count"] = _total_tests
                            elif _total_tests > 0:
                                # 非完整范围（无论成败）：不锁定 baseline，本次也不算
                                # full_green。下一轮由 partial_green 分支提示跑完整套件，
                                # 届时再建立基线。
                                _scope_complete = False
                        elif passed and _baseline > 0 and _total_tests < _baseline:
                            logger.warning(
                                "TestGuard: test count %d < baseline %d — "
                                "partial green, forcing full-suite re-run",
                                _total_tests, _baseline,
                            )
                            _stats(context, test_guard_triggered=1)
                            _scope_complete = False
                        if not _acceptance_files_intact:
                            _scope_complete = False
                        # ── P0: 零测试计数 = 无证据，不得作为完成依据 ──
                        # verifier 的"exit 0 + 通过信号"对**没跑测试**的命令同样成立：
                        # `pytest --collect-only` 打印 session 头；`pytest --help` /
                        # `pip install pytest` 的输出含裸子串 "ok"。三者 exit 0、计数为 0。
                        # 照旧走 green 会让 tests_passed/tests_complete 同时置真 —— 用一条
                        # 没跑测试的命令换到"完整套件全绿"资格，绕过 P0 门禁。
                        # 零计数既不是 green（没验证任何东西）也不是 failure（没有失败证据），
                        # 因此两个分支都不进，只要求补一次能产出计数的完整套件运行。
                        # ── B-1: 超时 ≠ 零测试证据 ─────────────────────────
                        # 测试命令超时（ShellTool: exit 124 + error "timeout (Ns)"）时
                        # 计数同样为 0，但它与"根本没跑测试"是两件事：命令跑了、只是没跑完。
                        # 归进 [NO_TEST_EVIDENCE] 会让 Loop 指令模型重跑**同一条必然再次
                        # 超时**的完整套件命令，而那条路径既不置 test_failed、也不进
                        # FixDriving，没有任何超时语义能把模型拉出来（只能烧墙钟到
                        # max_steps / force_edit 预算）。超时照旧不产生任何判定
                        # （既非通过证据也非失败证据，fail-closed —— 见下面 _no_verdict），
                        # 只把指令换成可执行的方向，并交给连续超时预算收尾。
                        _timeout_evidence = exit_code == 124 or "timeout (" in test_out
                        _no_test_evidence = _total_tests == 0 and not _timeout_evidence
                        # ── CR-1: 测试文件缺失期间的测试结果不作为任何判定证据 ──
                        # 与超时同理：它既不能证明通过，也不能证明失败。测试文件
                        # 已从工作区消失时，"剩下全绿"描述的是一个被收缩过的验收
                        # 标准 —— 不得据此完成、不得据此锁定 baseline。
                        _files_missing = any(_acceptance_violation(context))
                        # ── CR-4: 守卫在测试运行期间否掉的运行也不产生任何判定 ──
                        # 与"验收文件当前处于违规态"同源但成因不同：这里文件可能已经
                        # 被还原（端状态合法），偏离只发生在**运行期间** —— 只有守卫
                        # 看得到。既不得据此完成，也不得据此建立 baseline。
                        # ── CR-5: scope 配置偏离期间的测试结果同样不作数 ──────
                        # 这一条不能退化成数量比较：数量只能证明"这次跑到的 == 上次
                        # 跑到的"，证明不了"完整"。偏离的是定义范围的配置，范围本身
                        # 就有问题，本次运行既不是通过证据也不是失败证据。
                        # ── N-1: 命令点名跳过了测试 → 本次运行不作任何判定 ────
                        # 必须堵得比"不能建 baseline"更早：否则"零修改 + 无基线"的
                        # 两处放行分支（本文件的 I2′ 例外与完成门禁的
                        # _has_completion_evidence）会各自把它当成"本 run 自己的
                        # 证据"，绕过 baseline 直接完成。
                        # 无判定：零证据 / 超时 / 验收文件缺失 / 守卫中止 / scope
                        # 配置偏离 / 命令点名排除 —— 都不得写进 CompletionState、
                        # 不得进 green 计数、不得当失败处理。
                        _no_verdict = (_no_test_evidence or _timeout_evidence
                                       or _files_missing or _guard_broken
                                       or bool(_scope_drift) or _scope_declared)
                        if _files_missing:
                            cm.add_message("user", _acceptance_files_msg(
                                *_acceptance_violation(context)))
                        # 超时判定优先于其余"无判定"原因：它是本次运行的**事实**
                        # （命令没跑完），其余判据（守卫凭据 / scope 偏离）都只是
                        # 结论层面不可信 —— 两者都不产生证据，但只有超时分支会置
                        # `_step_timed_out`，进而驱动连续超时的有界终止。被抢占的
                        # 话超时预算永不计数，模型只能烧到 max_steps。
                        elif _timeout_evidence:
                            _step_timed_out = True
                            context.metadata["required_next_action"] = "narrow_test_scope"
                            cm.add_message("user",
                                "[TEST_TIMEOUT] 测试命令在时限内没有跑完 —— 它既不构成"
                                "通过证据，也不构成失败证据，不要据此判断修改是否生效。\n"
                                "不要重复运行同一条命令（它还会超时）。下一步二选一：\n"
                                "1) 缩小范围：先运行与本次修改最相关的单个测试文件或用例；\n"
                                "2) 显式提高时限：在 shell_exec 参数里传 `timeout`，"
                                "或调大 config 的 timeout.test。\n"
                                "在取得结构化测试计数（passed/failed）之前，任务不得判定完成。"
                            )
                        elif _guard_broken:
                            cm.add_message("user", _acceptance_guard_msg())
                        elif _scope_drift:
                            logger.warning(
                                "TestGuard: pytest scope config drifted from run start "
                                "(%s): %s", context.agent_id, ", ".join(_scope_drift),
                            )
                            _stats(context, test_guard_triggered=1)
                            cm.add_message("user", _scope_config_msg(_scope_drift))
                        elif _scope_declared:
                            logger.warning(
                                "TestGuard: test command declared exclusions "
                                "(%s): %s", context.agent_id, " ".join(_cli_scope_options),
                            )
                            _stats(context, test_guard_triggered=1)
                            context.metadata["test_scope_incomplete"] = True
                            context.metadata["required_next_action"] = "run_full_test_suite"
                            cm.add_message("user", _cli_scope_msg(_cli_scope_options))
                        elif _no_test_evidence:
                            context.metadata["test_scope_incomplete"] = True
                            context.metadata["required_next_action"] = "run_full_test_suite"
                            cm.add_message("user",
                                "[NO_TEST_EVIDENCE] 本次测试运行没有执行任何测试"
                                "（解析不出 passed/failed/error 计数）。exit 0、输出里出现"
                                "通过字样，都不构成测试通过的证据。\n"
                                "下一步必须运行完整测试套件取得结构化计数："
                                "python -m pytest -q"
                            )
                        if completion and not _no_verdict:
                            completion.record_test_result(
                                exit_code=exit_code,
                                passed=passed,
                                step=context.step_count,
                                scope_complete=_scope_complete,
                            )
                        # ── P0-2B: green 计数只在 full_green 时累计，其余结果一律清零 ──
                        # 失败 / partial_green 同样使"历史 green"失效，否则 `or _green_once`
                        # 旁路可以在当前 verification 无效时宣布完成。
                        if not _no_verdict and not (passed and _scope_complete):
                            context.metadata["test_success_count"] = 0
                        if passed and not _no_verdict:
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
                                # ── N-2: 这里**没有例外** ────────────────────
                                # 曾经有一条放行：`_baseline is None`（首次运行就是子集）
                                # + 零修改 + 验收文件完好 ⇒ 把这唯一一次子集全绿记成
                                # "本 run 自己的完成证据"（为了 autostop / "跑一次测试，
                                # 通过就停"）。它同时是一条绕过口：模型**不改任何源码**、
                                # 自己挑一个测试子集跑绿，就能宣布整个任务完成 ——
                                # 既没有完整 scope 的证据，也没有被验证的改动，
                                # 剩下的测试（可能正是失败的）从来没被看过。
                                #
                                # 现在零修改要完成，只能靠**可证明的完整范围运行**全绿：
                                # 未指定目标、不带缩范围选项、配置与 run 起始一致（裸
                                # `python -m pytest -q`）—— 那条路径会锁定 baseline 并直接
                                # 走 full_green，不需要例外。子集全绿只作为"本次运行通过"
                                # 反馈，不作完成证据；模型收到的指令是去跑完整套件。
                                context.metadata["test_scope_incomplete"] = True
                                context.metadata["tests_passed"] = False
                                context.metadata["required_next_action"] = "run_full_test_suite"
                                cm.add_message("user",
                                    "[TEST_SCOPE_INCOMPLETE] 当前测试运行通过，但"
                                    + (
                                        f"只执行了 {_total_tests}/{_baseline} 个测试"
                                        "（未覆盖完整基线套件）。\n"
                                        if _baseline else
                                        f"只执行了 {_total_tests} 个测试，且尚未建立完整"
                                        "基线——首次运行就是子集，无法证明已覆盖整个测试"
                                        "套件。\n"
                                    )
                                    + "本次结果不能作为最终完成验证。\n"
                                    "不要继续随机读取、修改文件或重复运行同一个子集。\n"
                                    "下一步必须运行完整测试套件：python -m pytest -q\n"
                                    "只有完整测试数量达到基线且全部通过后才能完成任务。"
                                )
                        elif not _no_verdict:
                            # 测试失败 → 进入修复态：强制后续进入修改阶段
                            if not test_failed:
                                # 首次进入修复态才清零读数。若每次失败 pytest 都清零，
                                # agent 会用 read→pytest→read 无限交替逃逸 FixDriving
                                # （读数永远攒不满 fix.read_limit，force_edit 永不激活），
                                # 直到耗尽 max_steps 提前终止。
                                reads_after_fail = 0
                                # ── P0-1: 新失败解除计划闩锁 ──
                                # 首次失败 / 修复后再次失败都算"新失败"，必须重新进入
                                # 诊断→规划。否则首个失败会把后续所有失败的根因分析
                                # 永久锁死：多 bug 任务只能拿到第一条失败的 file:line
                                # 与源码上下文，其余失败退化成裸 traceback。
                                # 同一失败连续重跑不计（test_failed 已为 True，不进本分支）。
                                repair_plan_injected = False
                            test_failed = True
                            # 一旦测试曾失败，则只有"全绿重测"才能判定完成（粘性标记）
                            context.metadata["tests_ever_failed"] = True
                            if repair_phase != "edit":
                                repair_phase = "diagnose"
                            # ── 失败注入具体修复计划（让 Agent 制定修改方案而非只分析）──
                            # P0-1: 闩锁只在**诊断成功**后置位（见下方 _issue is not None）。
                            # 在解析前置位会让一次解析异常永久烧掉闩锁——既不重试，也
                            # 伪造出"诊断已完成"的状态，把 Agent 直接推入修改阶段。
                            if not repair_plan_injected:
                                _fail_text = (result.output or "") + (result.error or "")
                                # ── P2: 语义化失败解析 ──
                                # ── P1: 基于语义失败生成有序修复计划 ──
                                # 整个解析/生成块用 try 兜底：即使模块缺失或解析失败，
                                # 也不能让 Agent step 崩溃（Repair Plan 是增强而非硬依赖）。
                                _plan_msg = ""
                                try:
                                    from zmai.swe.failure import (
                                        format_failure,
                                        parse_test_failure,
                                    )
                                    from zmai.swe.fix_planner import (
                                        format_plan,
                                        generate_fix_plan,
                                    )
                                    _issue = parse_test_failure(
                                        _fail_text,
                                        context.config.get("project_path"),
                                    )
                                    if _issue is not None:
                                        _stats(context, failure_parser_used=1)
                                        _plan = generate_fix_plan(_issue)
                                        context.metadata["last_failure_issue"] = _issue
                                        _plan_msg = "\n" + format_failure(_issue) \
                                                    + "\n" + format_plan(_plan)
                                        # 诊断真正产出结果，才宣告"计划已就绪"
                                        repair_plan_injected = True
                                        repair_phase = "plan"
                                except Exception:
                                    logger.warning(
                                        "Failure analysis failed for %s — 保持诊断态并"
                                        "允许下次失败重试解析", context.agent_id,
                                        exc_info=True,
                                    )
                                    _plan_msg = ""
                                cm.add_message("user",
                                    "[Repair Plan] 测试失败。请按以下闭环立即修复（不要只读不修）：\n"  # noqa: E501
                                    "1. 诊断：从上面的失败信息找出根因（必要时只读取最相关的 1-2 个源码文件）\n"  # noqa: E501
                                    "2. 计划：明确要修改哪个文件、添加或改动什么代码\n"
                                    "3. 修改：用 `edit` 或 `write_file` 工具实施修改\n"
                                    "4. 验证：重新运行 `python -m pytest`，直到通过\n"
                                    # P0-1: 头+尾截断。pytest 把 traceback 放在输出尾部，
                                    # 取头部 800 字符只剩 rootdir/collected 噪声，"失败分析"
                                    # 段不含任何根因信息。
                                    f"\n失败分析：\n"
                                    f"{_truncate_head_tail(_fail_text, cm.test_evidence_chars)}"
                                    f"\n{_plan_msg}"
                                )
                                _graph_enter(
                                    context, Node.REPAIR_PLAN,
                                    "plan_injected" if repair_plan_injected
                                    else "analysis_failed")
                                _l = context.metadata.get("__log__")
                                if _l:
                                    try:
                                        _l.record_step(phase="repair", action="inject_plan",
                                                       success=False,
                                                       metadata={"cycle": context.metadata.get("repair_cycle", 0)})  # noqa: E501
                                    except Exception:
                                        pass
                # ── Track reads before test ─────────────
                if tc.name == "read_file" and not has_run_test:
                    reads_without_test += 1
                # ── Fix-driving: 测试失败后只读不修，累计读取计数 ──
                # 只计"真正执行成功"的读取。被 force_edit 拦截的读取也计数时，拦截
                # 反而给触发器喂数：每攒满 fix.read_limit 就重新触发一次 FixDriving，
                # guard.reset() 随之反复清空 LoopGuard 历史 → 升级路径饿死 → 模型在
                # "被拦 → 喂数 → 再触发"的闭环里耗尽 max_steps（pylint-6506：66 次
                # 拦截 / 13 次 FixDriving / 1 次 LoopGuard / 0 次 edit）。
                # ReadCache 命中的重复读取同样不计：它没有给模型任何新信息，却会
                # 消耗诊断预算、把 FixDriving 提前推进到"必须 edit"，反而提高乱改概率。
                if (tc.name == "read_file" and not _intercepted and result.success
                        and not (result.metadata or {}).get("cached")
                        and test_failed and not had_modification):
                    reads_after_fail += 1
                # ── LoopGuard: record every tool call ─────
                if guard:
                    guard.record_tool_call(
                        name=tc.name,
                        params=tc.params,
                        success=result.success,
                        output=result.output or "",
                        error=result.error,
                        # P1: 复用 P1-3 已算出的工作区证据 —— 只看工具名无法区分
                        # git status（只读）与 git checkout（写），也让 shell 的
                        # 真实修改能被 LoopGuard 记录。
                        ws_changed=_ws_changed,
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
                if tc.name == "shell_exec" and not _intercepted:
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
                    }, namespace=_MEMORY_NS_TOOLS)
                # Add tool results via ContextManager
                cm.add_tool_result(
                    name=tc.name,
                    success=result.success,
                    output=result.output or "",
                    error=result.error,
                    truncate=_test_evidence_budget(tc, result, cm),
                    # P1-1: 把工具自带的 read_key 一路带进注入模型的那条消息，
                    # 供 is_read_visible() 后续判断该结果是否还在可见窗口。
                    meta=result.metadata,
                )
                # ── Edit syntax validation feedback + limited repair ──
                # 语法验证失败的编辑：结构化错误已随工具结果进入上下文（含
                # action_required: repair_the_previous_edit），Agent 利用现有 loop
                # 自行修复。这里只做两件事：
                #   1) 计数修复尝试（edit_repair_attempts），供审计与上限判定
                #   2) 预算耗尽（默认 2 次）后升级为强制整文件重写，避免原地补丁死循环
                if tc.name in ("edit", "write_file") and not result.success \
                        and result.error and "EDIT_VALIDATION_FAILED" in result.error:
                    _repair_attempts = context.metadata.get("edit_repair_attempts", 0) + 1
                    context.metadata["edit_repair_attempts"] = _repair_attempts
                    _max_repair = int(context.config.get(
                        "edit.repair_attempts", MAX_EDIT_REPAIR_ATTEMPTS))
                    _stats(context, edit_validation_failures=1)
                    _err_type = next(
                        (ln.split(":", 1)[1].strip() for ln in (result.error or "").splitlines()
                         if ln.startswith("error_type:")),
                        "SyntaxError",
                    )
                    logger.warning(
                        "[EDIT_VALIDATION_FAIL] file=%s attempt=%d/%d error=%s",
                        tc.params.get("path", ""), _repair_attempts, _max_repair, _err_type,
                    )
                    if _repair_attempts >= _max_repair:
                        _graph_enter(context, Node.EDIT_RECOVERY,
                                     f"edit_validation_repair_exhausted "
                                     f"{_repair_attempts}/{_max_repair}")
                        context.metadata["force_edit"] = True
                        context.metadata["repair_phase"] = "plan"
                        cm.add_message("user",
                            f"[EDIT_REPAIR] 语法修复尝试已达上限"
                            f"（{_repair_attempts}/{_max_repair}）。\n"
                            "反复对同一文件打小补丁仍产生语法错误。停止原地补丁——\n"
                            "先用 `read_file` 读完整文件，再用 `write_file` "
                            "一次性重写该文件的正确版本。\n"
                            f"最近一次语法错误 ({_err_type}) 详见上一条工具结果。"
                        )
                # ── Edit failure recovery（P1）──────────────────────────
                # 目标源码的 edit/write_file **执行失败**（空 diff / 正则错误 / 截断 /
                # 写入失败 / TestGuard 拒绝）时，此前只累加 step_tool_fail，没有任何
                # 恢复消费者：模型在 force_edit 下拿不到靶子，于是漂移去跑 pytest、
                # 写草稿文件、探索无关路径，最终 0-byte diff（pylint-6506/5859/7228）。
                # 这里把模型拉回**同一个目标文件**，并（强制修改期内）放行一次定向
                # read，让"重读真实内容 → 重新规划 → 重试修改"这条路径可执行。
                # 有界、不改测试、不放宽完成门禁。
                elif (tc.name in ("edit", "write_file") and not result.success
                        and result.error and not _intercepted):
                    _graph_enter(context, Node.EDIT_RECOVERY,
                                 f"{tc.name} failed: {tc.params.get('path', '')}")
                    _handle_edit_failure(context, cm, tc, result)
            # ── LoopGuard: track no-modification steps ──
            if guard and not had_modification:
                guard.record_no_modification()

            # Accumulate tool execution stats to metadata for finalize
            prev_ok = context.metadata.get("tool_calls_ok", 0)
            prev_fail = context.metadata.get("tool_calls_fail", 0)
            context.metadata["tool_calls_ok"] = prev_ok + step_tool_ok
            context.metadata["tool_calls_fail"] = prev_fail + step_tool_fail

            # ── 连续超时预算（B-1）──────────────────────────────
            # 超时不能变成无限重试：同一条命令反复超时既烧墙钟（每次 timeout.test 秒），
            # 又永远拿不到测试结论。复用 completion_block_count —— 它的语义正是"**连续**
            # 没有取得有效进展"，且在每次真实工作区进展时归零（_reset_edit_failure_
            # recovery，判据是工作区指纹），因此"改一次 → 超时一次"不会耗尽预算，
            # 只有连续超时才收敛到明确失败。
            # 位置必须在下面的完成门禁**之前**：门禁在 eval 模式（未修改源码）会直接
            # return cont，放在门禁之后就永远轮不到计数，超时又变成无限循环。
            if _step_timed_out:
                _timeout_blocks = context.metadata.get("completion_block_count", 0) + 1
                context.metadata["completion_block_count"] = _timeout_blocks
                if _timeout_blocks > MAX_COMPLETION_BLOCKS:
                    logger.warning(
                        "Test command timed out %d times without progress — failing (%s)",
                        _timeout_blocks, context.agent_id,
                    )
                    _graph_enter(context, Node.FAILED, "test_timeout_no_progress")
                    return AgentAction.fail(
                        error=(f"Test command timed out {_timeout_blocks} times without "
                               "progress: no structured test evidence was obtained")
                    )

            # ── 硬终止条件（最高优先级）：测试通过 → 任务完成，立即结束执行循环 ──
            # 一旦成立必须立即 return complete，禁止继续进入下一轮 shell/read/test。
            _graph_enter(context, Node.COMPLETION_GATE, "post_tool")
            # 双重判定：
            #   1) CompletionState.should_complete()（全绿 + exit0 + 无后续修改）
            #   2) 防御机制：test_success_count >= 1 —— 已有一次全绿即强制停止，
            #      杜绝 success → success → success 无限循环。
            # eval 守卫优先：SWE-bench 模式下未修改代码不得因"现有测试全绿"完成。
            if _eval_blocking_completion(context):
                logger.info(
                    "[EvalGuard] Post-tool completion blocked: no code modification yet "
                    "(%s, step %d)",
                    context.agent_id, context.step_count,
                )
                # 已有 force_edit 或 LoopRecovery 时不需要重复注入，否则紧耦合注入
                if not context.metadata.get("force_edit"):
                    cm.add_message("user",
                        "[EvalGuard] 当前仓库的现有测试通过，但这不能证明任务已解决——"
                        "SWE-bench 的验证测试（FAIL_TO_PASS）不在当前仓库中，"
                        "需要通过修改源码实现。\n"
                        "请仔细阅读任务描述定位 bug 根因，然后用 `edit` 或 "
                        "`write_file` 修改源码。\n"
                        "修改后再运行测试验证。未修改任何源码前不得完成任务。"
                    )
                context.metadata["messages"] = cm.get_context()
                return AgentAction.cont(
                    output="EvalGuard: must make a code change before completing"
                )
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
                _graph_enter(context, Node.DONE, "post_tool_complete")
                return AgentAction.complete(
                    output=f"Tests passed. Task completed in {context.step_count} step(s)."
                )

            # ── 强制修改阶段预算（强制期的有界性）──────────────
            # force_edit 置位后若模型持续只发被拒绝的调用，FixDriving/LoopGuard 只会
            # 反复"要求修改"并在每次拦截后 guard.reset()，形成"每 N 步拦一次"的固定点。
            # 这里给强制期一个有界预算：预算内没产出修改即明确失败，不放任到 max_steps。
            # 上升沿在此处惰性记录，无需改动三处 force_edit 置位点；force_edit 一旦
            # 被成功修改清除，预算随之重置。
            if context.metadata.get("force_edit"):
                _fe_since = context.metadata.get("force_edit_since_step")
                if _fe_since is None:
                    context.metadata["force_edit_since_step"] = context.step_count
                elif context.step_count - _fe_since >= MAX_FORCE_EDIT_STEPS:
                    logger.warning(
                        "Force-edit budget exhausted (%d steps without modification) "
                        "— failing (%s)",
                        context.step_count - _fe_since, context.agent_id,
                    )
                    _graph_enter(context, Node.FAILED, "force_edit_budget_exhausted")
                    return AgentAction.fail(
                        error=(f"force_edit armed for {context.step_count - _fe_since} "
                               f"steps without any code modification")
                    )
            else:
                context.metadata.pop("force_edit_since_step", None)

            # ── 灾难性回退预算：已要求"先恢复再修改"仍继续把项目改坏 → 有界失败 ──
            # 与 force_edit 预算同理：不允许在"改坏 → 要求恢复 → 又改坏"的循环里
            # 无限消耗 steps。
            if context.metadata.get("regression_recoveries", 0) >= MAX_REGRESSION_RECOVERIES:
                logger.warning(
                    "Destructive regression repeated %d times without recovery — failing (%s)",
                    context.metadata["regression_recoveries"], context.agent_id,
                )
                _graph_enter(context, Node.FAILED, "regression_recoveries_exhausted")
                return AgentAction.fail(
                    error=(f"{context.metadata['regression_recoveries']} destructive "
                           "regressions left the project uncollectable without recovery")
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
            _graph_enter(context, Node.READ_LIMIT, f"reads_without_test={reads_without_test}")
            if not has_run_test and reads_without_test >= read_limit:
                cm.add_message("user",
                    f"[Workflow] 你已读取 {reads_without_test} 个文件但尚未运行测试。\n"
                    f"请立即运行 `python -m pytest`（或项目的测试命令），根据测试失败信息修复代码。\n"  # noqa: E501
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
            # P0-1: 前置条件加上"诊断已产出结果"。解析异常/解析不出失败时
            # repair_plan_injected 保持 False，此时不得强制修改——否则就是对一条
            # 运行时自己都没能定位的失败强行要求改代码（fail-open）。定位未就绪时
            # 继续允许读取，由 LoopGuard（no_progress）与 max_steps 兜底。
            _graph_enter(context, Node.FIX_DRIVING, f"reads_after_fail={reads_after_fail}")
            if test_failed and repair_plan_injected and reads_after_fail >= fix_read_limit:
                logger.warning(
                    "FixDriving: test failed, %d reads after failure without modification — forcing fix phase",  # noqa: E501
                    reads_after_fail,
                )
                _stats(context, fixdriving_activations=1)
                if guard:
                    guard.reset()  # 给予全新方向，避免 no_progress 误伤分析阶段
                repair_phase = "plan"  # 强制回到"计划→修改"，阻断只读停滞
                # 结构性拦截：下一轮起禁止 read_file/grep，直到 agent 产生一次修改
                context.metadata["force_edit"] = True
                cm.add_message("user",
                    f"[FixDriving] 测试已失败，但你已读取 {reads_after_fail} 个相关文件仍未修改代码（当前阶段：{repair_phase}）。\n"  # noqa: E501
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
                _graph_enter(context, Node.LOOP_GUARD, "check")
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
                    # ── P1: 强制修改必须有"靶子" ──
                    # 升级的设计意图是"用当前失败证据**定向**修改"，前提是确实存在一个
                    # 可定向的目标，或模型自己已经在改代码：
                    #   repair_plan_injected → 运行时已定位根因，定向修改有靶子；
                    #   ever_modified        → 模型已展示过修改路径，此时停滞意味着
                    #                          "改法不奏效"，要求换一个改法是合理的。
                    # 两者皆无（既未定位、也从未修改过）时强制 edit 等于让人凭空改代码
                    # ——与 P0-1 同源的 fail-open。此时保持 diagnose，只注入恢复提示，
                    # 由 workflow.read_limit / max_steps 兜底（不删除 LoopGuard 恢复本身）。
                    _escalate = recoveries >= recover_limit and (
                        repair_plan_injected
                        or bool(context.metadata.get("ever_modified")))
                    if _escalate:
                        context.metadata["force_edit"] = True
                        context.metadata["repair_phase"] = "plan"
                    recovery_msg = (
                        f"[LoopGuard][LoopRecovery] 检测到重复的无进展动作（第 {recoveries} 次恢复）。\n"  # noqa: E501
                        f"原因: {loop_result.reason}（{loop_result.suggestion}）\n"
                        f"之前的无效动作:\n{recent_txt}\n"
                        f"下一步必须:\n"
                        f"- 不要重复上述任何 read/shell 动作。\n"
                        f"- 使用当前失败证据定位一个具体修复目标。\n"
                        f"- 若根因证据已足够，直接用 `edit`/`write_file` 修改业务文件。\n"
                        f"- 不要修改测试文件。\n"
                        # force_edit 已置位时读取会被硬拒绝，"允许一次定向读取"是空头
                        # 支票：模型照做→被拒→再循环。此处必须与实际可执行集合一致。
                        + ("- 读取工具已被运行时禁用：唯一可推进的动作是 "
                           "`edit`/`write_file`（或重跑一次 pytest 验证）。"
                           if context.metadata.get("force_edit") else
                           "- 若证据不足，只允许一次新的定向读取（或重跑一次 pytest）。")
                    )
                    if _escalate:
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
            _graph_enter(context, Node.REPLAN, f"{plan.completed_steps}/{len(plan.steps)} done")
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
                    f"Plan not fully executed ({plan.completed_steps}/{len(plan.steps)} steps done). "  # noqa: E501
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
        _graph_enter(context, Node.AUTO_VERIFY, "text_only")
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
                # ── P1: 接入完成守卫的 text-only 预算 ──
                # 这里原本直接 return cont：既没有局部上界，也不计入任何计数器，
                # 而完成守卫（下方 _needs_retest/_needs_change 处）与 LoopGuard
                # （只挂在 tool_calls 分支）都在这条 return 之后 —— 于是
                #   text-only → 验证失败 → cont → text-only → …
                # 可以一路烧到 max_steps，最终被 Runtime 标成 timeout。
                # 之所以能一直失败：auto_generate_checks 对**成功**命令的输出做裸
                # 关键词匹配（error/fail/traceback/cannot），一条输出里含 "fail"
                # 字样的成功运行（如测试名 test_failure_handling）就产生永久失败
                # 的 check，而 _tool_results 滑窗在纯文本循环里不会推进。
                # 语义与完成守卫一致（模型只回文本、Agent 无法推进），因此共用同一
                # 条预算，不新增平行计数器。
                _blocks = context.metadata.get("completion_block_count", 0) + 1
                context.metadata["completion_block_count"] = _blocks
                if _blocks > MAX_COMPLETION_BLOCKS:
                    # 不无限循环：与完成守卫同样的收敛策略 —— 明确失败，
                    # 而不是耗尽 max_steps 伪装成 timeout。
                    logger.warning(
                        "Auto-verify blocked %d times with no progress — failing (%s)",
                        _blocks - 1, context.agent_id,
                    )
                    _graph_enter(context, Node.FAILED, "auto_verify_blocked")
                    return AgentAction.fail(
                        error=(f"Verification blocked {_blocks - 1}x without progress: "
                               f"{vresult.summary}")
                    )
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
        # ── P1-4: 改过代码就必须有一次"修改之后的有效全绿验证" ──
        # 原判据只认"测试曾经失败"，于是"改过代码但测试从未失败"没有任何门禁：
        # 改完直接回纯文本即可 complete —— 无论是从未跑过测试，还是只跑了子集
        # （partial_green），都不是有效验证。
        # completion.tests_complete 已经准确表达"存在一次未被后续修改作废的完整
        # 套件全绿"（record_modification / 失败 / partial_green 都会把它置 False），
        # 直接复用，不新增第二套 completion 真相源。
        # 作用域限定在**项目本身有测试**的任务（测试即验收标准）；无测试的项目
        # 保持既有行为，不会被这条新门禁永久卡住。
        # ── P0: 有测试的项目必须以"正向证据"为准，而不是"三个门禁都没触发" ──
        # 此前判据是 `_tests_failed_ever or (有测试 and 改过代码)`：只要模型零修改、
        # 零测试，`_needs_retest`/`_needs_change` 全为假、`_auto_verify` 又因无工具
        # 结果返回 None —— 于是"没改、没测、没失败"的纯文本响应直接 complete，
        # Runtime 手上没有任何独立证据（模型说完成即完成）。
        # 项目本身有测试时，唯一的正向证据就是 completion.tests_complete
        # ——"存在一次未被后续修改作废的完整套件全绿"。直接复用它，不新增状态。
        # 无测试的项目不受约束（测试不是它的验收标准）。
        # ── P2-1: "确实没有测试" 与 "无法确定是否有测试" 必须区别对待 ──
        # 只有**显式记录过**的 unknown 才算 fail-closed —— 那是"扫描过、但无法
        # 确定"（扫描抛异常 / 找不到项目根，见 initialize 的 test_discovery 写入点）。
        # 调用方直接构造的 AgentContext（从未 initialize）根本没有发现状态，不得
        # 据此凭空要求测试证据：那不是"发现失败"，只是"没问过"。
        _discovery = context.metadata.get("test_discovery")
        _test_status_unknown = _discovery == "unknown"
        _repo_info = context.metadata.get("repo_info")
        _has_tests = bool(getattr(_repo_info, "test_files", None))
        # 该项目的测试套件是不是**本次任务的验收标准**：项目根由调用方声明
        # （见 initialize 处的 project_root_declared）。从 cwd 推断出来的项目根
        # 只提供上下文，不把无关任务（"say hello" / mock smoke）变成测试任务。
        _tests_are_acceptance = (
            _has_tests and bool(context.metadata.get("project_root_declared")))
        _has_completion_evidence = _has_full_scope_green_evidence(context)
        # 判据的作用域 —— 只阻止"真正需要阻止完成"的情况：
        #   * 测试失败过 → 必须有一次完整套件全绿（失败是负面证据，与是否
        #     改过代码无关）；
        #   * 该项目以测试为验收标准（或发现状态显式未知）且 改过代码 /
        #     没有可用的完成证据 → 必须有一次完整套件全绿；
        #   * 零修改 + 有可用的完成证据（= 完整范围全绿）→ 放行：没有修改就没有
        #     "未验证的改动"，一次真实全绿就是本 run 自己的完成证据（verify-only /
        #     autostop "跑一次测试，通过就停" 属于这一类，**但那次运行必须覆盖完整
        #     范围**：N-2 之前"首次子集全绿"也算证据，那让模型能自选范围完成）。
        #     partial_green 的 [TEST_SCOPE_INCOMPLETE] 提示与 tests_complete=False
        #     都不受影响 —— 一旦修改源码，record_modification 会作废这条绿色证据。
        _needs_retest = bool(
            completion and not completion.tests_complete
            and (
                _tests_failed_ever
                or ((_tests_are_acceptance or _test_status_unknown)
                    and (bool(context.metadata.get("ever_modified"))
                         or not _has_completion_evidence))
            )
        )
        # ── SWE eval 守卫：纯文本响应同样不得在零修改时完成 ──
        # 该守卫原先只在 `if response.tool_calls:` 分支内生效，模型只要只回文本就能
        # 绕过 FixDriving / force_edit / LoopGuard 全部机制直接 complete
        # （requests-1963 实测：5 步 / 0 次 pytest / 0 次修改 / status=completed / diff 空）。
        _needs_change = _eval_blocking_completion(context, at_completion_point=True)
        # ── CR-1: 验收文件缺失 → 无论项目形态/模式，纯文本都不得完成 ──
        # _no_verdict 已让缺失期内的测试结果不产生通过证据，这里再补上纯文本路径
        # （未声明项目根的 run 会走"零修改 + 本 run 证据"的放行分支）。
        _files_missing = any(_acceptance_violation(context))
        # ── CR-5: scope 配置偏离 run 起始 → 纯文本路径同样不得完成 ──
        # 与 CR-1 同理：_no_verdict 已让偏离期内的测试结果不产生证据，这里再补上
        # 纯文本路径（未声明项目根的 run 会走"零修改 + 本 run 证据"的放行分支）。
        _scope_drift = _pytest_scope_drift(context)

        _graph_enter(context, Node.COMPLETION_GATE, "text_only")
        if _needs_retest or _needs_change or _files_missing or _scope_drift:
            _blocks = context.metadata.get("completion_block_count", 0) + 1
            context.metadata["completion_block_count"] = _blocks
            if _blocks > MAX_COMPLETION_BLOCKS:
                # 不无限循环：守卫反复拦截而模型始终不推进 → 明确失败，
                # 既不放行未验证的 complete，也不耗尽 max_steps 伪装成 timeout。
                logger.warning(
                    "Completion guard blocked %d times with no progress — failing (%s)",
                    _blocks - 1, context.agent_id,
                )
                _graph_enter(context, Node.FAILED, "completion_gate_blocked")
                return AgentAction.fail(
                    error=(f"Completion blocked {_blocks - 1}x without progress: "
                           f"ever_modified={bool(context.metadata.get('ever_modified'))}, "
                           f"tests_ever_failed={bool(_tests_failed_ever)}")
                )
            # "你修改了代码 → 去重测" 只在真的改过代码时成立。从未修改时
            # tests_ever_failed 同样让 _needs_retest 为真，此处若照旧说"你修改了
            # 代码"，模型会在强制修改期收到"去跑 pytest"的指令——恰好把它从 edit
            # 推回重跑测试（实测 e2e 的 read/pytest 空转正是这条指令的形态）。
            if _files_missing:
                logger.warning(
                    "Completion blocked: acceptance file(s) removed/rewritten (%s, step %d)",
                    context.agent_id, context.step_count,
                )
                cm.add_message("user", _acceptance_files_msg(
                    *_acceptance_violation(context)))
            elif _scope_drift:
                logger.warning(
                    "Completion blocked: pytest scope config differs from run start "
                    "(%s): %s", context.agent_id, ", ".join(_scope_drift),
                )
                cm.add_message("user", _scope_config_msg(_scope_drift))
            elif _needs_retest and context.metadata.get("ever_modified"):
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
            elif _needs_retest and not _needs_change:
                # ── N-2: 还没改代码、但也没有"完整范围全绿" —— 不能靠自选子集完成 ──
                # 这条以前落在下面的 else 里，会得到"请先修改源码"的指令：对纯验证
                # 类任务那是错误方向（本来就没有要改的东西）。正确方向是跑完整套件。
                # eval 模式（_needs_change）优先走下面的 EvalGuard 指令：那里要求的
                # 正是"先改源码"，与"跑完整套件"是不同的动作，不能混。
                logger.warning(
                    "Completion blocked: no full-scope green evidence yet (%s, step %d)",
                    context.agent_id, context.step_count,
                )
                cm.add_message("user",
                    "[Workflow] 还没有取得『完整测试套件全绿』这一完成证据。\n"
                    "只跑一部分测试（指定文件、`-k`/`-m` 选择器、缩范围选项）即使全绿，"
                    "也不能证明整个套件通过——剩下的测试可能正是失败的。\n"
                    "请运行未指定目标、不带过滤选项的完整套件：`python -m pytest -q`，"
                    "并在其全绿后再结束任务。"
                )
            else:
                logger.warning(
                    "Completion blocked: no code modification yet (%s, step %d)",
                    context.agent_id, context.step_count,
                )
                cm.add_message("user",
                    "[Workflow] 尚未修改任何源码，不能结束任务。\n"
                    "请先定位 bug 根因，用 `edit` 或 `write_file` 修改源码，"
                    "再运行 `python -m pytest` 验证。"
                )
            context.metadata["messages"] = cm.get_context()
            _l = context.metadata.get("__log__")
            if _l:
                try:
                    _l.record_step(phase="completion_guard", action="block_unverified",
                                   success=False,
                                   metadata={"tests_failed_ever": bool(_tests_failed_ever),
                                             "tests_passed": (completion.tests_passed
                                                              if completion else False),
                                             "tests_complete": (completion.tests_complete
                                                                if completion else False),
                                             "block_count": _blocks})
                except Exception:
                    pass
            return AgentAction.cont(
                output=("Modified but not re-tested; forcing pytest re-run"
                        if (_needs_retest and context.metadata.get("ever_modified")) else
                        "No code change yet; completion blocked until source is modified")
            )

        _graph_enter(context, Node.DONE, "text_only_complete")
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

        # ── P1-A: 只用与**当前 workspace**相关的执行证据 ──
        # 修改前产生的失败结果描述的是旧代码：工作区已经变了，那条失败可能早已不存在。
        # 继续拿它生成 passed=False 的 check，会把"已修改、只是还没重测"的正常推进误判
        # 成验证失败，烧掉 completion_block_count 并最终错误 FAILED。
        # 修改**之后**产生的失败/成功结果照常参与判定，因此"改完重测仍失败 → 继续阻断"
        # 与"改完重测通过 → 放行"都不受影响（completion 闸门另有一套证据约束）。
        # ponytail: 只把成功的 edit/write_file 当作修改边界；shell 改文件（sed -i、
        # python fix.py）识别不到 → 边界偏保守（旧失败仍被计入），不会漏放。
        _last_mod = -1
        for _i, _tr in enumerate(tool_results):
            if _tr.get("name") in ("write_file", "edit") and _tr.get("success"):
                _last_mod = _i
        tool_results = tool_results[_last_mod + 1:]

        # ── P1-A1: 检查根目录必须是**项目源码目录**，不是 Agent 工作区 ──
        # context.workspace 是 ZMAI 的临时工作区（只有 input/output/temp），源码在
        # project_path。verifier 的 workspace 参数是"相对路径的解析根"，传工作区会让
        # modified_files 里的每个相对路径都解析到不存在的路径，`文件存在: <file>`
        # 必然 FAIL —— 已修好的改动被判成验证失败，烧 completion_block_count 后
        # 把整个 run 判成 FAILED（SWE-bench smoke: psf__requests-3362 实测）。
        # git 检查同理：工作区不是 git 仓库，diff 必须去项目里跑。
        ws_path = context.config.get("project_path") or context.workspace
        result = auto_generate_checks(
            modified_files, tool_results, Path(ws_path) if ws_path else None)
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
            metadata={
                "swe_stats": context.metadata.get("swe_stats", {}),
                "token_usage": context.metadata.get("token_usage", {}),
                "ever_modified": context.metadata.get("ever_modified", False),
                "test_success_count": context.metadata.get("test_success_count", 0),
                "edit_repair_attempts": context.metadata.get("edit_repair_attempts", 0),
                "edit_failure_recovery_attempts": context.metadata.get(
                    "edit_failure_recovery_attempts", 0),
                "edit_validation_failures": context.metadata.get(
                    "swe_stats", {}
                ).get("edit_validation_failures", 0),
                # Graph Runtime：显式化的转移轨迹（观察层，不影响判定）
                "graph_trace": context.metadata.get("graph_trace", []),
                "graph_violations": context.metadata.get("graph_violations", []),
            },
        )
        logger.info(
            "SWEAgent finished: %s (%d steps, status=%s)",
            self.agent_id, result.steps, status.value,
        )
        return result
