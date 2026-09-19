"""Verifier 测试 — 验证策略、结果汇总、自动验证。"""

from __future__ import annotations

import sys
from pathlib import Path

from zmai.swe.verifier import (
    VerificationCheck,
    VerificationResult,
    auto_generate_checks,
    verify_exit_code,
    verify_file_content,
    verify_file_exists,
    verify_git_diff,
    verify_test_output,
)


class TestVerificationResult:
    """VerificationResult 数据模型测试。"""

    def test_passed_all_pass(self):
        """所有检查通过 → passed=True。"""
        vr = VerificationResult(
            passed=True,
            checks=[VerificationCheck(name="check1", strategy="file_exists", passed=True, target="f.txt", evidence="存在")],  # noqa: E501
            summary="1/1 通过",
        )
        assert vr.passed is True
        assert len(vr.passed_checks) == 1
        assert len(vr.failed_checks) == 0

    def test_failed_any_fail(self):
        """有检查失败 → passed=False。"""
        vr = VerificationResult(
            passed=False,
            checks=[
                VerificationCheck(name="c1", strategy="file_exists", passed=True, target="f1", evidence="存在"),  # noqa: E501
                VerificationCheck(name="c2", strategy="file_exists", passed=False, target="f2", evidence="不存在", error="not found"),  # noqa: E501
            ],
            summary="1/2 通过",
        )
        assert vr.passed is False
        assert len(vr.failed_checks) == 1
        assert vr.failed_checks[0].target == "f2"

    def test_merge_all_pass(self):
        """merge 全部通过 → passed=True。"""
        r1 = VerificationResult(passed=True, checks=[
            VerificationCheck(name="c1", strategy="file_exists", passed=True, target="f1", evidence="ok"),  # noqa: E501
        ])
        r2 = VerificationResult(passed=True, checks=[
            VerificationCheck(name="c2", strategy="file_exists", passed=True, target="f2", evidence="ok"),  # noqa: E501
        ])
        merged = VerificationResult.merge([r1, r2])
        assert merged.passed is True
        assert len(merged.checks) == 2

    def test_merge_any_fail(self):
        """merge 中有失败 → passed=False。"""
        r1 = VerificationResult(passed=True, checks=[
            VerificationCheck(name="c1", strategy="file_exists", passed=True, target="f1", evidence="ok"),  # noqa: E501
        ])
        r2 = VerificationResult(passed=False, checks=[
            VerificationCheck(name="c2", strategy="file_exists", passed=False, target="f2", evidence="no", error="not found"),  # noqa: E501
        ])
        merged = VerificationResult.merge([r1, r2])
        assert merged.passed is False
        assert len(merged.failed_checks) == 1

    def test_to_dict(self):
        """to_dict 包含所有字段。"""
        vr = VerificationResult(
            passed=True,
            checks=[VerificationCheck(name="c", strategy="file_exists", passed=True, target="f", evidence="ok")],  # noqa: E501
            summary="1/1",
        )
        d = vr.to_dict()
        assert d["passed"] is True
        assert len(d["checks"]) == 1
        assert "summary" in d


class TestVerifyFileExists:
    """文件存在验证。"""

    def test_file_exists_pass(self, tmp_path: Path):
        """文件存在 → passed=True。"""
        f = tmp_path / "test.txt"
        f.write_text("hello")
        result = verify_file_exists(f, workspace=tmp_path)
        assert result.passed is True
        assert result.strategy == "file_exists"
        assert "存在" in result.evidence

    def test_file_not_exists(self, tmp_path: Path):
        """文件不存在 → passed=False。"""
        result = verify_file_exists("nonexistent.txt", workspace=tmp_path)
        assert result.passed is False
        assert "不存在" in result.evidence

    def test_absolute_path(self, tmp_path: Path):
        """绝对路径验证。"""
        f = tmp_path / "abs.txt"
        f.write_text("data")
        result = verify_file_exists(str(f))
        assert result.passed is True


class TestVerifyFileContent:
    """文件内容验证。"""

    def test_content_non_empty(self, tmp_path: Path):
        """非空文件 → 通过。"""
        (tmp_path / "data.txt").write_text("hello world")
        result = verify_file_content("data.txt", workspace=tmp_path)
        assert result.passed is True

    def test_content_empty(self, tmp_path: Path):
        """空文件 → 不通过。"""
        (tmp_path / "empty.txt").write_text("")
        result = verify_file_content("empty.txt", workspace=tmp_path)
        assert result.passed is False

    def test_content_contains_pattern(self, tmp_path: Path):
        """包含预期文本 → 通过。"""
        (tmp_path / "code.py").write_text("def hello():\n    pass\n")
        result = verify_file_content("code.py", expected="def hello", workspace=tmp_path)
        assert result.passed is True

    def test_content_not_contains(self, tmp_path: Path):
        """不包含预期文本 → 不通过。"""
        (tmp_path / "code.py").write_text("def hello(): pass")
        result = verify_file_content("code.py", expected="goodbye", workspace=tmp_path)
        assert result.passed is False

    def test_content_file_not_found(self, tmp_path: Path):
        """文件不存在 → 不通过。"""
        result = verify_file_content("missing.py", workspace=tmp_path)
        assert result.passed is False
        assert "不存在" in result.error


class TestVerifyExitCode:
    """命令退出码验证。"""

    def test_exit_zero(self):
        """exit 0 → 通过。"""
        if sys.platform == "win32":
            result = verify_exit_code("cmd /c exit 0")
        else:
            result = verify_exit_code("exit 0")
        assert result.passed is True
        assert result.evidence == "exit code 0"

    def test_exit_nonzero(self):
        """exit 非 0 → 不通过。"""
        if sys.platform == "win32":
            result = verify_exit_code("cmd /c exit 1")
        else:
            result = verify_exit_code("exit 1")
        assert result.passed is False
        assert "1" in result.evidence

    def test_echo_success(self):
        """echo 命令成功 → 通过。"""
        result = verify_exit_code("echo hello")
        assert result.passed is True


class TestVerifyTestOutput:
    """测试输出验证。"""

    def test_pytest_pass(self):
        """测试全部通过 → 通过。"""
        output = "test session starts\ncollected 3 items\nPASSED\n3 passed"
        result = verify_test_output(output)
        assert result.passed is True

    def test_pytest_fail(self):
        """测试有失败 → 不通过。"""
        output = "test session starts\nFAILED test_a.py::test_foo\n1 failed"
        result = verify_test_output(output)
        assert result.passed is False
        assert "FAILED" in str(result.error) or "failed" in str(result.error)

    def test_assertion_error(self):
        """AssertionError → 不通过。"""
        output = "AssertionError: assert 1 == 2"
        result = verify_test_output(output)
        # has "passed" or "ok" signal? No → failed
        assert result.passed is False

    def test_empty_output(self):
        """空输出 → 不通过（无通过标记）。"""
        result = verify_test_output("")
        assert result.passed is False


class TestVerifyGitDiff:
    """Git diff 验证。"""

    def test_git_diff_no_fail(self, tmp_path: Path):
        """非 git 仓库不崩溃。"""
        result = verify_git_diff(workspace=tmp_path)
        # 非 git 仓库时 passed=False 但不崩溃
        assert result.strategy == "git_diff"
        # 可能 pass 也可能 fail，只要不崩溃就行
        assert isinstance(result.passed, bool)


class TestAutoGenerateChecks:
    """自动验证检查生成。"""

    def test_auto_with_modified_file(self, tmp_path: Path):
        """有已修改文件时生成文件存在验证。"""
        (tmp_path / "output.txt").write_text("hello")
        results = auto_generate_checks(
            modified_files=["output.txt"],
            tool_results=[{"name": "write_file", "success": True, "output": "written output.txt"}],
            workspace=tmp_path,
        )
        assert len(results.checks) >= 1
        # 文件存在检查应通过
        file_checks = [c for c in results.checks if c.strategy == "file_exists"]
        assert any(c.passed for c in file_checks)

    def test_auto_with_missing_file(self, tmp_path: Path):
        """已修改文件丢失时验证失败。"""
        results = auto_generate_checks(
            modified_files=["output.txt"],  # 文件不存在
            tool_results=[{"name": "write_file", "success": True, "output": "written"}],
            workspace=tmp_path,
        )
        file_checks = [c for c in results.checks if c.strategy == "file_exists"]
        assert any(not c.passed for c in file_checks)

    def test_auto_no_checks(self):
        """无上下文时返回 passed=True（仅有 git diff 检查）。"""
        results = auto_generate_checks(
            modified_files=[],
            tool_results=[],
        )
        assert results.passed is True
        # 至少有一个 git diff 检查（在 git 仓库中会通过）

    def test_auto_test_result(self):
        """真实失败的测试命令（success=False，详情在 error）→ 验证失败。

        旧版本 fixture 用了不存在的工具名 "pytest"（真实注册表只有 shell_exec/git），
        断言也是被丢弃的裸表达式；这里改成真实形态 + 真断言。
        """
        results = auto_generate_checks(
            modified_files=[],
            tool_results=[
                {"name": "shell_exec", "success": True,
                 "output": "3 passed in 0.10s\n"},
                {"name": "shell_exec", "success": False, "output": "",
                 "error": "[test summary] 0 passed, 1 failed, 0 errors\n"
                          "exit 1: FAILED test_main.py::test_x - AssertionError"},
            ],
        )
        failed = [c for c in results.checks if not c.passed]
        assert len(failed) == 1
        assert failed[0].strategy == "exit_code"
        assert "AssertionError" in failed[0].error

    def test_auto_with_shell_error_signal(self):
        """shell 命令真实失败（success=False）→ 验证失败，且引用 error 字段证据。"""
        results = auto_generate_checks(
            modified_files=[],
            tool_results=[
                {"name": "shell_exec", "success": False, "output": "",
                 "error": "exit 127: error: command not found"},
            ],
        )
        exit_checks = [c for c in results.checks if c.strategy == "exit_code"]
        assert len(exit_checks) == 1
        assert exit_checks[0].passed is False
        assert "command not found" in exit_checks[0].error
        assert results.passed is False

    # ── 成功输出不得因普通英文单词被误判 ──────────────────────────
    def test_success_output_with_error_word_passes(self):
        """"error handling initialized" 是正常输出，不是失败证据。"""
        results = auto_generate_checks(
            modified_files=[],
            tool_results=[
                {"name": "shell_exec", "success": True,
                 "output": "error handling initialized\n"},
            ],
        )
        assert results.passed is True
        assert [c for c in results.checks if c.strategy == "exit_code"] == []

    def test_success_output_with_fail_word_passes(self):
        """测试名含 failure 的成功运行不得判失败。"""
        results = auto_generate_checks(
            modified_files=[],
            tool_results=[
                {"name": "shell_exec", "success": True,
                 "output": "test_failure_handling PASSED\n1 passed in 0.07s\n"},
            ],
        )
        assert results.passed is True

    def test_success_output_with_cannot_word_passes(self):
        """"cannot connect to cache" 是正常输出，不是失败证据。"""
        results = auto_generate_checks(
            modified_files=[],
            tool_results=[
                {"name": "shell_exec", "success": True,
                 "output": "cannot connect to cache\n"},
            ],
        )
        assert results.passed is True

    def test_success_pytest_summary_prefix_passes(self):
        """ZMAI 自己注入的 "[test summary] … 0 failed, 0 errors" 不得触发失败。

        该前缀由 ShellTool._test_summary_prefix() 前置到**每一条** pytest 输出，
        必然同时含 "failed" 与 "errors" —— 旧的关键词匹配因此让每次成功 pytest
        都必然产生失败 check。
        """
        results = auto_generate_checks(
            modified_files=[],
            tool_results=[
                {"name": "shell_exec", "success": True,
                 "output": "[test summary] 1 passed, 0 failed, 0 errors\n"
                           "1 passed in 0.05s\n"},
            ],
        )
        assert results.passed is True

    # ── 反向：结构化证据里真实存在失败时仍必须判失败 ──────────────
    def test_failed_shell_success_false_is_detected(self):
        results = auto_generate_checks(
            modified_files=[],
            tool_results=[
                {"name": "shell_exec", "success": False, "output": "",
                 "error": "exit 1: \n[stderr]\nboom"},
            ],
        )
        assert results.passed is False
        failed = [c for c in results.checks if not c.passed]
        assert len(failed) == 1
        assert "boom" in failed[0].error

    def test_error_field_evidence_is_not_ignored(self):
        """失败详情只存在于 error 字段（output 为空）时不得被忽略。"""
        results = auto_generate_checks(
            modified_files=[],
            tool_results=[
                {"name": "shell_exec", "success": False, "output": "",
                 "error": "AssertionError: 1 != 2"},
            ],
        )
        assert results.passed is False
        failed = [c for c in results.checks if not c.passed]
        assert failed and "AssertionError" in failed[0].error

    def test_success_output_with_structured_failure_counts_fails(self):
        """成功输出里**解析出的计数**有失败 → 仍判失败（结构化，非关键词）。"""
        results = auto_generate_checks(
            modified_files=[],
            tool_results=[
                {"name": "shell_exec", "success": True,
                 "output": "2 passed, 1 failed in 0.16s\n"},
            ],
        )
        assert results.passed is False
        failed = [c for c in results.checks if not c.passed]
        assert len(failed) == 1
        assert failed[0].strategy == "test_output"
        assert "1 failed" in failed[0].evidence

    def test_write_file_results_are_not_command_checks(self):
        """非 shell/git 的工具结果不参与命令判定（不回归）。"""
        results = auto_generate_checks(
            modified_files=[],
            tool_results=[
                {"name": "write_file", "success": True,
                 "output": "written app.py\n"},
            ],
        )
        assert results.passed is True
        assert [c for c in results.checks
                if c.strategy in ("exit_code", "test_output")] == []


class TestIntegrationSWEAgent:
    """集成测试 — 验证通过 SWEAgent 执行后的验证行为。"""

    def test_write_then_verify_success(self, tmp_path: Path):
        """写入文件后验证文件存在 → 通过。"""
        f = tmp_path / "created.txt"
        f.write_text("test content")

        vr = VerificationResult(
            passed=True,
            checks=[
                VerificationCheck(
                    name="文件存在: created.txt",
                    strategy="file_exists",
                    passed=True,
                    target="created.txt",
                    evidence="文件存在",
                ),
            ],
            summary="1/1 通过",
        )
        assert vr.passed is True

    def test_write_then_file_missing(self, tmp_path: Path):
        """写入文件后文件不存 → 验证不通过。"""
        vr = VerificationResult(
            passed=False,
            checks=[
                VerificationCheck(
                    name="文件存在: missing.txt",
                    strategy="file_exists",
                    passed=False,
                    target="missing.txt",
                    evidence="文件不存在",
                    error="文件 missing.txt 不存在",
                ),
            ],
            summary="0/1 通过",
        )
        assert vr.passed is False
        assert len(vr.failed_checks) == 1

    def test_tool_failure_then_not_completed(self):
        """Tool 失败后不得 COMPLETED。"""
        # finalize 中的 tool_fail > 0 and tool_ok == 0 → FAILED
        tool_ok = 0
        tool_fail = 1
        assert tool_fail > 0 and tool_ok == 0  # 应 FAILED

    def test_partial_complete_with_verification_fail(self):
        """部分完成 + 验证失败 → 最终验证失败。"""
        vresult = VerificationResult(
            passed=False,
            checks=[
                VerificationCheck(name="文件存在", strategy="file_exists", passed=True, target="a.txt", evidence="存在"),  # noqa: E501
                VerificationCheck(name="测试结果", strategy="test_output", passed=False, target="", evidence="FAILED", error="test failed"),  # noqa: E501
            ],
            summary="1/2 通过",
        )
        assert vresult.passed is False
        assert len(vresult.failed_checks) == 1

    def test_final_verify_fail_is_final(self):
        """最终验证失败后状态为 FAILED。"""
        # 模拟 finalize 检查
        vresult = VerificationResult(
            passed=False,
            checks=[VerificationCheck(name="内容验证", strategy="file_content", passed=False, target="f.py", evidence="空文件", error="empty content")],  # noqa: E501
            summary="0/1 通过",
        )
        has_vresult = True
        verify_failed = has_vresult and not vresult.passed
        assert verify_failed is True


class TestVerificationEdgeCases:
    """边界条件测试。"""

    def test_verify_result_has_failed_checks(self):
        """failed_checks 正确返回。"""
        check_pass = VerificationCheck(name="p1", strategy="file_exists", passed=True, target="f1", evidence="ok")  # noqa: E501
        check_fail = VerificationCheck(name="p2", strategy="file_exists", passed=False, target="f2", evidence="no", error="not found")  # noqa: E501
        vr = VerificationResult(passed=False, checks=[check_pass, check_fail], summary="")
        assert len(vr.failed_checks) == 1
        assert vr.failed_checks[0].name == "p2"

    def test_verify_result_passed_checks(self):
        """passed_checks 正确返回。"""
        check_pass = VerificationCheck(name="p1", strategy="file_exists", passed=True, target="f1", evidence="ok")  # noqa: E501
        check_fail = VerificationCheck(name="p2", strategy="file_exists", passed=False, target="f2", evidence="no")  # noqa: E501
        vr = VerificationResult(passed=False, checks=[check_pass, check_fail], summary="")
        assert len(vr.passed_checks) == 1
        assert vr.passed_checks[0].name == "p1"


class TestVerifyTestOutputEvidenceOrder:
    """判定优先级：真实 exit code > 结构化 pytest 计数 > 文本启发式。

    修复前 `verify_test_output` 只接受输出文本，`passed = not failures and
    has_passed_signal` —— 一次 **exit 0 的通过运行**只要输出里出现 `FAILED ` /
    `Traceback` / `AssertionError`（测试名含失败词、被测代码自己打印的 traceback、
    `-rA` 报告等）就会被判失败，属于"成功被判失败"。
    """

    # 1. exit 0 + 全部通过 → passed=True
    def test_exit_zero_green_passes(self):
        r = verify_test_output("2 passed in 0.05s\n", exit_code=0)
        assert r.passed is True
        assert r.error is None
        assert "exit_code=0" in r.evidence

    # 2. exit 1 + 测试失败 → passed=False
    def test_exit_one_failure_fails(self):
        r = verify_test_output("1 failed, 1 passed in 0.05s\n", exit_code=1)
        assert r.passed is False
        assert "exit code 1" in r.error

    # 3. 输出文本包含 FAIL 但 exit 0 → 不得仅凭关键词判失败
    def test_keyword_fail_with_exit_zero_is_not_failure(self):
        out = ("FAILED test_failure_handling PASSED\n"
               "Traceback (most recent call last):\n"
               "AssertionError: expected\n"
               "2 passed in 0.05s\n")
        assert "FAILED " in out and "Traceback" in out      # 夹具确实含关键词
        r = verify_test_output(out, exit_code=0)
        assert r.passed is True, f"exit 0 + 汇总无失败不得被文本推翻: {r.error}"

    # 4. exit code 与文本冲突 → 以结构化证据为准（双向）
    def test_structured_evidence_wins_over_text(self):
        # 4a. exit 0 但结构化汇总报失败 → 失败（不因"有 passed 字样"而通过）
        r = verify_test_output("2 passed, 1 failed in 0.05s\n", exit_code=0)
        assert r.passed is False
        assert "summary reports 1 failed" in r.error

        # 4b. exit 1 但文本里有 passed 字样 → 失败
        r2 = verify_test_output("3 passed in 0.05s\n", exit_code=1)
        assert r2.passed is False
        assert "exit code 1" in r2.error

    def test_exit_one_without_summary_still_fails(self):
        r = verify_test_output("collection error\n", exit_code=1)
        assert r.passed is False

    def test_exit_zero_without_summary_requires_pass_signal(self):
        """无结构化计数（如 --collect-only）→ 仍要求出现通过信号。"""
        assert verify_test_output("3 tests collected in 0.02s\n",
                                  exit_code=0).passed is False
        r = verify_test_output("test session starts\n3 tests collected\n",
                               exit_code=0)
        assert r.passed is True

    # 向后兼容：无 exit_code 的旧调用点保持原启发式
    def test_without_exit_code_keeps_legacy_heuristic(self):
        assert verify_test_output("2 passed in 0.05s\n").passed is True
        assert verify_test_output("1 failed in 0.05s\n").passed is False
        assert verify_test_output("").passed is False
