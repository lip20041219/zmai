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

import hashlib
import json
import os
import time

#: 中止时打印的前缀 —— Runtime 据此把本次运行判为"无判定证据"（见 agent.step）。
MARKER = "[AcceptanceGuard]"

_MANIFEST_NAME = "manifest.json"

#: 运行凭据：本会话确实加载过守卫的证明（与 guard 模块同目录）。
_RECEIPT_NAME = "receipt"


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


def _stamp_receipt() -> None:
    """留下"守卫确实在本 pytest 进程里加载过"的凭据。

    CR-4 残余：模型可以在 shell 命令里清空/覆盖 PYTEST_ADDOPTS，把 Runtime 注入的
    `-p zmai_acceptance_guard` 抹掉 —— 守卫于是**静默消失**，而端状态判据看不到
    任何东西（测试文件已被还原）。Runtime 侧的要求因此是：本次调用只要**真的跑了
    pytest**，就必须留下新凭据；没有凭据 = 守卫没加载 = 本次运行 fail-closed。
    写不进去（目录只读等）同样没有凭据，同样 fail-closed。
    """
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               _RECEIPT_NAME), "a", encoding="utf-8") as f:
            f.write(f"{os.getpid()} {time.time()}\n")
    except OSError:
        pass


def pytest_configure(config):  # noqa: ARG001  —— pytest 钩子签名
    """会话启动（任何测试被收集/导入之前）：验收文件必须 == run 起始内容。"""
    _stamp_receipt()
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
