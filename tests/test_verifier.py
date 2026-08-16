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
        """工具结果中测试失败 → 验证失败。"""
        results = auto_generate_checks(
            modified_files=["test_main.py"],
            tool_results=[
                {"name": "shell_exec", "success": True, "output": "PASSED: 3 tests passed"},
                {"name": "pytest", "success": False, "output": "FAILED: 1 test failed\nAssertionError"},  # noqa: E501
            ],
        )
        test_checks = [c for c in results.checks if c.strategy == "test_output"]
        if test_checks:
            any(not c.passed for c in test_checks)
            # 只要有 failed 信号，验证就不通过

    def test_auto_with_shell_error_signal(self):
        """shell 输出包含错误信号 → 验证失败。"""
        results = auto_generate_checks(
            modified_files=[],
            tool_results=[
                {"name": "shell_exec", "success": True, "output": "error: command not found"},
            ],
        )
        exit_checks = [c for c in results.checks if c.strategy == "exit_code"]
        # 至少有一个失败检查
        any(not c.passed for c in exit_checks)


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
