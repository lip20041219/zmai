"""CR-4：测试窗口内的验收文件完整性守卫（pytest 插件）。

问题（CR-4）：CR-1/CR-3 的判据都取自**工具调用返回之后**的工作区端状态。一次
shell 调用内部完成"改写验收文件 → 跑 pytest → 还原原内容"时，端状态与 run 起始
逐字节相同 —— 中间态没有任何观测点，而被污染的 green 已经产生：

    A ──改写──▶ B ──pytest 全绿──▶ A     （端状态 == run 起始状态）

这些结果既不是失败的子集问题，也不是"事后追不追回"的问题：**问题的判定点晚于
证据的产生点**。因此这里把判定点移进测试进程内部 —— pytest 会话启动时，同一
命令里的改写已经发生、还原还没发生，此刻核对验收文件内容摘要：

* 不一致 → 中止会话（非 0 退出、不产出任何 passed/failed 计数）；
* 于是"测试执行期间验收文件是否处于合法状态"成为**测试证据的一部分**，
  而不是事后从端状态推断：被污染的会话不可能产出 green；
* 会话结束时再核一次，覆盖"测试运行期间被改写"的形态；
* 一切异常 fail-closed：清单读不到、插件自身出错 → 非 0 退出，同样不产出 green。

注入方式（见 `SWEAgent._acceptance_guard_env` 与 `ShellTool.execute`）：guard 目录
前置到 PYTHONPATH，PYTEST_ADDOPTS 追加 `-p zmai_acceptance_guard`；清单
`manifest.json` 与本文件同目录，由 Runtime 在 run 起始（与 CR-3 基线同一时刻）
写出，之后不再刷新。

本模块**只依赖标准库**：它运行在被测项目的解释器里，那里通常没有安装 zmai。
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
import time

#: 中止时打印的前缀 —— Runtime 据此把本次运行判为"无判定证据"（见 agent.step）。
MARKER = "[AcceptanceGuard]"

#: P0-2：Runtime 每次测试执行发放的 nonce 回显前缀。守卫在会话结束时打印本次
#: 实际收到的 nonce；Runtime 只接受"输出与 receipt 同时带有本 run 的 nonce"的结果。
#: 与中止 MARKER 刻意不同（`[AcceptanceGuard:nonce]` 不包含 `[AcceptanceGuard]`）。
NONCE_MARKER = "[AcceptanceGuard:nonce]"

#: Runtime 注入 nonce 的环境变量名（与 agent._NONCE_ENV 一致）。
NONCE_ENV = "ZMAI_ACCEPTANCE_NONCE"

_MANIFEST_NAME = "manifest.json"

#: 运行凭据：本会话确实加载过守卫的证明（与 guard 模块同目录）。
_RECEIPT_NAME = "receipt"

#: 本 pytest 进程收到的 nonce（由 pytest_configure 从环境变量读取）。
_nonce = ""

#: scope 配置快照：pytest 启动瞬间四份配置的原文（base64）。
#: Runtime 事后用与 run 起始基线相同的算法重算身份，覆盖 A→B→A 的瞬时改配置。
_SCOPE_SNAPSHOT_NAME = "scope_snapshot.json"

#: P0-3：测试环境身份快照（conftest / .pth / sitecustomize / 已加载插件）。
#: Runtime 把它与证据记录绑定，并在回放后比对；环境变化 → 证据作废。
ENV_SNAPSHOT_NAME = "env_snapshot.json"


def _manifest_path() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), _MANIFEST_NAME)


def _load_manifest() -> dict:
    with open(_manifest_path(), encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("manifest is not an object")
    return data


def _deviated() -> list[str]:
    """当前内容 ≠ 清单的验收文件（**含已消失的**：读不到同样算偏离）。"""
    m = _load_manifest()
    root = m.get("root") or "."
    out: list[str] = []
    for rel, want in (m.get("files") or {}).items():
        try:
            with open(os.path.join(root, rel), "rb") as f:
                if hashlib.sha256(f.read()).hexdigest() != want:
                    out.append(rel)
        except OSError:
            out.append(rel)
    return sorted(out)


def _abort(lines: list[str]) -> None:
    """打印原因并让本次会话以非 0 退出（没有任何测试计数 → 不可能被当作 green）。

    用 pytest 自己的 UsageError 退出：pytest 只打印 `ERROR: <原因>`、不展开
    traceback，模型看到的是"为什么这次运行不算数"，而不是插件内部的栈。
    """
    msg = "\n".join(f"{MARKER} {ln}" for ln in lines)
    try:
        import pytest
    except ImportError:                 # 不可能：本模块只在 pytest 进程里加载
        print(msg, flush=True)
        raise SystemExit(3)
    raise pytest.UsageError(msg)


def _stamp_receipt(nonce: str) -> None:
    """留下"守卫确实在本 pytest 进程里加载过"的凭据。

    CR-4 残余：模型可以在 shell 命令里清空/覆盖 PYTEST_ADDOPTS，把 Runtime 注入的
    `-p zmai_acceptance_guard` 抹掉 —— 守卫于是**静默消失**，而端状态判据看不到
    任何东西（测试文件已被还原）。Runtime 侧的要求因此是：本次调用只要**真的跑了
    pytest**，就必须留下新凭据；没有凭据 = 守卫没加载 = 本次运行 fail-closed。
    写不进去（目录只读等）同样没有凭据，同样 fail-closed。

    P0-2：凭据同时记录本次 run 的 nonce —— 只写"加载过"无法区分新旧运行，
    记录 nonce 后才能与 Runtime 本次发放的值绑定。
    """
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               _RECEIPT_NAME), "a", encoding="utf-8") as f:
            f.write(f"{os.getpid()} {time.time()} {nonce}\n")
    except OSError:
        pass


def _snapshot_scope() -> None:
    """把 pytest 启动瞬间的 scope 配置原文写进守卫目录。

    A→B→A（改配置 → 跑 pytest → 还原）的端状态与起始相同，Runtime 侧事后
    比对看不到中间态。守卫在这里只负责取证（原样保存字节），不解析 TOML——
    解析由 Runtime 用**同一个**解析器完成，避免被测解释器与 Agent 解释器的
    能力差异造成误判。
    """
    try:
        m = _load_manifest()
        root = m.get("root") or "."
        files: dict[str, str | None] = {}
        for name in m.get("scope_files") or []:
            try:
                with open(os.path.join(root, name), "rb") as f:
                    files[name] = base64.b64encode(f.read()).decode("ascii")
            except OSError:
                files[name] = None
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               _SCOPE_SNAPSHOT_NAME), "w", encoding="utf-8") as f:
            json.dump({"files": files}, f)
    except Exception:
        pass


#: 扫描 conftest / .pth 时跳过的目录（构建产物、VCS、缓存、依赖）。
_ENV_SKIP_DIRS = frozenset({
    ".git", ".hg", ".svn", "__pycache__", ".venv", "venv", ".tox", ".eggs",
    "node_modules", ".pytest_cache", ".mypy_cache", ".ruff_cache", "htmlcov",
})


def _file_digest(path: str) -> str | None:
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return None


def _snapshot_environment(config) -> None:
    """P0-3：记录影响测试执行的环境身份，供 Runtime 与证据绑定。

    覆盖 conftest.py、sys.path 上的 .pth、sitecustomize/usercustomize、本会话
    实际加载的 pytest 插件。只取证、不判定；读不到的文件记为 None（同样改变身份）。
    同一命令回放时会加载同一组 conftest/插件，因此前后快照可比。
    """
    try:
        manifest = _load_manifest()
        root = manifest.get("root") or "."
        entries: dict[str, str | None] = {}
        count = 0
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in _ENV_SKIP_DIRS]
            for name in filenames:
                if name != "conftest.py":
                    continue
                path = os.path.join(dirpath, name)
                entries[f"conftest:{os.path.relpath(path, root)}"] = _file_digest(path)
                count += 1
            if count >= 500:       # 有界：异常深的仓库不拖垮测试启动
                break
        seen: set[str] = set()
        for entry in sys.path:
            if not entry:
                continue
            directory = os.path.abspath(entry)
            if directory in seen:
                continue
            seen.add(directory)
            try:
                names = sorted(n for n in os.listdir(directory) if n.endswith(".pth"))
            except OSError:
                continue
            for name in names:
                path = os.path.join(directory, name)
                entries[f"pth:{path}"] = _file_digest(path)
        for name in ("sitecustomize", "usercustomize"):
            module = sys.modules.get(name)
            path = getattr(module, "__file__", None) if module else None
            if not path:
                for entry in sys.path:
                    candidate = os.path.join(entry, name + ".py")
                    if os.path.isfile(candidate):
                        path = candidate
                        break
            if path:
                entries[f"module:{name}"] = _file_digest(path)
        try:
            plugins = list(getattr(config.pluginmanager, "_name2plugin", {}).values())
        except Exception:
            plugins = []
        for plugin in plugins:
            path = getattr(plugin, "__file__", None)
            if path:
                entries[f"plugin:{path}"] = _file_digest(path)
        payload = {"entries": entries, "python": sys.executable,
                   "version": sys.version.split()[0]}
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               ENV_SNAPSHOT_NAME), "w", encoding="utf-8") as f:
            json.dump(payload, f, sort_keys=True)
    except Exception:
        pass                       # 快照失败 → Runtime 侧按"环境不可证明"降级


def pytest_configure(config):  # noqa: ARG001  —— pytest 钩子签名
    """会话启动（任何测试被收集/导入之前）：验收文件必须 == run 起始内容。"""
    global _nonce
    _nonce = os.environ.get(NONCE_ENV, "").strip()
    _snapshot_scope()
    _snapshot_environment(config)
    _stamp_receipt(_nonce)
    try:
        deviated = _deviated()
    except Exception as e:  # 清单缺失/损坏 → fail-closed
        _abort([f"验收文件守卫无法核对清单（{e}）：本次测试运行不作为任何证据。",
                "请先恢复本次运行开始时的测试文件，再重新运行测试。"])
    if deviated:
        _abort([
            "验收/测试文件在本次测试**运行期间**与本次运行开始时的内容不一致：",
            *(f"- {p}" for p in deviated),
            "测试文件是只读验收标准：本会话已中止，它的结果既不是通过证据，"
            "也不是失败证据，不得作为完成依据。",
            "请把这些文件恢复到本次运行开始时的原始内容"
            "（例如 `git restore <path>` / `git checkout -- <path>`），"
            "再重新运行完整测试套件。",
        ])


def pytest_sessionfinish(session, exitstatus):  # noqa: ARG001  —— pytest 钩子签名
    """会话结束时再核一次：测试**运行期间**被改写的运行不得算通过。"""
    # P0-2：回显本次运行收到的 nonce，供 Runtime 与输出/receipt 双向核对。
    # 它只是防"旧输出/伪造输出被复用"的绑定值，不是密钥，也不承诺绝对防篡改
    # （被测代码与测试进程共享信任边界）。
    if _nonce:
        print(f"{NONCE_MARKER} {_nonce}", flush=True)
    # 会话结束时再拍一次：捕获收集/运行阶段才加载的插件。
    _snapshot_environment(session.config)
    try:
        deviated = _deviated()
    except Exception:
        deviated = [_MANIFEST_NAME]
    if not deviated:
        return
    print(f"{MARKER} 测试运行期间验收文件被改写（{', '.join(deviated)}）："
          "本次结果不作为通过证据。", flush=True)
    if session.exitstatus == 0:
        session.exitstatus = 1
