"""Tests for zmai.prompt module."""

from __future__ import annotations

import pytest

from zmai.prompt import PromptEngine, PromptRole, PromptType
from zmai.prompt.base import PromptTemplate, TemplateEngine
from zmai.prompt.templates import DEFAULT_TEMPLATES

# ── 测试: PromptType ─────────────────────────────────────────


class TestPromptType:
    def test_enum_values(self) -> None:
        assert PromptType.SYSTEM.value == "system"
        assert PromptType.PLANNER.value == "planner"
        assert PromptType.EXECUTOR.value == "executor"
        assert PromptType.VERIFIER.value == "verifier"
        assert PromptType.REPORT.value == "report"

    def test_label(self) -> None:
        assert "System" in PromptType.SYSTEM.label
        assert "Planner" in PromptType.PLANNER.label
        assert "Executor" in PromptType.EXECUTOR.label
        assert "Verifier" in PromptType.VERIFIER.label
        assert "Report" in PromptType.REPORT.label

    def test_list(self) -> None:
        types = PromptType.list()
        assert "system" in types
        assert "planner" in types
        assert "executor" in types
        assert "verifier" in types
        assert "report" in types
        assert len(types) == 5

    def test_prompt_role_values(self) -> None:
        assert PromptRole.SYSTEM.value == "system"
        assert PromptRole.USER.value == "user"
        assert PromptRole.ASSISTANT.value == "assistant"
        assert PromptRole.TOOL.value == "tool"


# ── 测试: TemplateEngine ─────────────────────────────────────


class TestTemplateEngine:
    def test_simple_variable(self) -> None:
        engine = TemplateEngine()
        result = engine.render("Hello $name!", {"name": "ZMAI"})
        assert result == "Hello ZMAI!"

    def test_braced_variable(self) -> None:
        engine = TemplateEngine()
        result = engine.render("Hello ${name}!", {"name": "World"})
        assert result == "Hello World!"

    def test_multiple_variables(self) -> None:
        engine = TemplateEngine()
        result = engine.render("$a and $b", {"a": "1", "b": "2"})
        assert result == "1 and 2"

    def test_missing_variable_keeps_placeholder(self) -> None:
        engine = TemplateEngine()
        result = engine.render("Hello $name!", {})
        assert result == "Hello $name!"

    def test_conditional_if_true(self) -> None:
        engine = TemplateEngine()
        template = "{% if show %}VISIBLE{% endif %}"
        result = engine.render(template, {"show": True})
        assert result == "VISIBLE"

    def test_conditional_if_false(self) -> None:
        engine = TemplateEngine()
        template = "{% if show %}VISIBLE{% endif %}"
        result = engine.render(template, {"show": False})
        assert result == ""

    def test_conditional_if_else(self) -> None:
        engine = TemplateEngine()
        template = "{% if ok %}YES{% else %}NO{% endif %}"
        result = engine.render(template, {"ok": True})
        assert result == "YES"
        result = engine.render(template, {"ok": False})
        assert result == "NO"

    def test_conditional_with_variable(self) -> None:
        engine = TemplateEngine()
        template = "{% if extra}$extra info{% endif %}"
        result = engine.render(template, {"extra": "extra", "extra info": " EXTRA"})
        # 条件 True，然后替换 $extra
        assert "extra" in result

    def test_for_loop(self) -> None:
        engine = TemplateEngine()
        template = "{% for item in items %}- $item\n{% endfor %}"
        result = engine.render(template, {"items": ["a", "b", "c"]})
        assert result == "- a\n- b\n- c\n"

    def test_empty_for_loop(self) -> None:
        engine = TemplateEngine()
        template = "{% for item in items %}- $item\n{% endfor %}"
        result = engine.render(template, {"items": []})
        assert result == ""

    def test_nested_conditional_and_variable(self) -> None:
        engine = TemplateEngine()
        template = "{% if detail %}Detail: $info{% endif %}"
        result = engine.render(template, {"detail": True, "info": "important"})
        assert result == "Detail: important"

    def test_engine_default_stdlib(self) -> None:
        engine = TemplateEngine()
        # 默认应该用 stdlib（不依赖 Jinja2）
        result = engine.render("Hello $name", {"name": "test"})
        assert result == "Hello test"


# ── 测试: PromptTemplate ─────────────────────────────────────


class TestPromptTemplate:
    def test_render_with_variables(self) -> None:
        tmpl = PromptTemplate(
            prompt_type="system",
            role="system",
            template="You are $role. Task: $task",
        )
        result = tmpl.render({"role": "expert", "task": "fix bugs"})
        assert "expert" in result
        assert "fix bugs" in result

    def test_render_empty_variables(self) -> None:
        tmpl = PromptTemplate(prompt_type="system", template="Hello")
        result = tmpl.render()
        assert result == "Hello"

    def test_to_dict(self) -> None:
        tmpl = PromptTemplate(
            prompt_type="planner",
            role="user",
            template="Plan: $task",
        )
        d = tmpl.to_dict()
        assert d["type"] == "planner"
        assert d["role"] == "user"
        assert d["template"] == "Plan: $task"

    def test_init_with_enum(self) -> None:
        tmpl = PromptTemplate(
            prompt_type="system",
            role="system",
            template="You are $name",
        )
        assert "You are" in tmpl.render({"name": "bot"})


# ── 测试: PromptEngine ───────────────────────────────────────


class TestPromptEngine:
    def test_init_loads_default_templates(self) -> None:
        engine = PromptEngine()
        templates = engine.list_templates()
        assert "system" in templates
        assert "planner" in templates
        assert "executor" in templates
        assert "verifier" in templates
        assert "report" in templates
        assert len(templates) == 5

    def test_render_system(self) -> None:
        engine = PromptEngine()
        result = engine.render(PromptType.SYSTEM, {
            "agent_name": "TestAgent",
            "description": "A test agent",
            "workspace_path": "/ws/test",
            "backend_name": "mock",
            "max_steps": "50",
        })
        assert "TestAgent" in result
        assert "A test agent" in result
        assert "/ws/test" in result
        assert "mock" in result
        assert "50" in result

    def test_render_planner(self) -> None:
        engine = PromptEngine()
        result = engine.render(PromptType.PLANNER, {
            "task": "Build a web app",
            "context": "Python project",
            "max_steps": "5",
        })
        assert "Build a web app" in result
        assert "Python project" in result
        assert "5" in result

    def test_render_executor(self) -> None:
        engine = PromptEngine()
        result = engine.render(PromptType.EXECUTOR, {
            "plan": "1. Setup\n2. Code",
            "step_number": "2",
            "step_description": "Write code",
            "completed_steps": "1",
            "total_steps": "2",
            "tool_descriptions": "read, write",
        })
        assert "Write code" in result
        assert "1" in result or "completed" in result
        assert "read, write" in result

    def test_render_verifier(self) -> None:
        engine = PromptEngine()
        result = engine.render(PromptType.VERIFIER, {
            "step_description": "Write tests",
            "execution_result": "All tests pass",
            "verification_criteria": "Tests cover edge cases",
        })
        assert "Write tests" in result
        assert "All tests pass" in result
        assert "PASS" in result or "Tests" in result

    def test_render_report(self) -> None:
        engine = PromptEngine()
        result = engine.render(PromptType.REPORT, {
            "task": "Fix bug",
            "execution_summary": "Fixed",
            "steps_details": "1. Debug\n2. Fix",
            "success": True,
            "error_info": "",
            "total_steps": "2",
            "completed_steps": "2",
            "failed_steps": "0",
            "total_tokens": "1500",
        })
        assert "Fix bug" in result
        assert "Fixed" in result
        assert "2" in result

    def test_render_report_failure(self) -> None:
        engine = PromptEngine()
        result = engine.render(PromptType.REPORT, {
            "task": "Fix bug",
            "execution_summary": "Failed",
            "steps_details": "1. Debug",
            "success": False,
            "error_info": "Timeout",
            "total_steps": "1",
            "completed_steps": "0",
            "failed_steps": "1",
            "total_tokens": "500",
        })
        assert "Timeout" in result or "issues" in result

    def test_render_invalid_type(self) -> None:
        engine = PromptEngine()
        with pytest.raises(ValueError, match="未知的 Prompt 类型"):
            engine.render("invalid_type", {})


# ── 测试: PromptEngine 模板管理 ─────────────────────────────


class TestPromptEngineTemplates:
    def test_set_template(self) -> None:
        engine = PromptEngine()
        engine.set_template(PromptType.SYSTEM, "Custom: $name")
        result = engine.render(PromptType.SYSTEM, {"name": "CustomAgent"})
        assert result == "Custom: CustomAgent"

    def test_set_template_with_string_key(self) -> None:
        engine = PromptEngine()
        engine.set_template("planner", "Plan: $task")
        result = engine.render("planner", {"task": "test"})
        assert result == "Plan: test"

    def test_set_template_with_custom_role(self) -> None:
        engine = PromptEngine()
        engine.set_template(PromptType.SYSTEM, "Hello", role="user")
        tmpl = engine.get_template(PromptType.SYSTEM)
        assert tmpl is not None
        assert tmpl.role == "user"

    def test_get_template(self) -> None:
        engine = PromptEngine()
        tmpl = engine.get_template(PromptType.SYSTEM)
        assert tmpl is not None
        assert tmpl.prompt_type == "system"

    def test_get_template_nonexistent(self) -> None:
        engine = PromptEngine()
        tmpl = engine.get_template("nonexistent")
        assert tmpl is None

    def test_get_template_with_string(self) -> None:
        engine = PromptEngine()
        tmpl = engine.get_template("executor")
        assert tmpl is not None
        assert tmpl.prompt_type == "executor"

    def test_reset_template(self) -> None:
        engine = PromptEngine()
        engine.set_template(PromptType.SYSTEM, "Custom")
        engine.reset_template(PromptType.SYSTEM)
        tmpl = engine.get_template(PromptType.SYSTEM)
        assert tmpl is not None
        assert tmpl.template == DEFAULT_TEMPLATES["system"]

    def test_reset_template_nonexistent(self) -> None:
        engine = PromptEngine()
        # 不应抛出异常
        engine.reset_template("nonexistent")

    def test_reset_all(self) -> None:
        engine = PromptEngine()
        engine.set_template(PromptType.SYSTEM, "Custom Sys")
        engine.set_template(PromptType.PLANNER, "Custom Plan")
        engine.reset_all()
        sys_tmpl = engine.get_template(PromptType.SYSTEM)
        plan_tmpl = engine.get_template(PromptType.PLANNER)
        assert sys_tmpl is not None
        assert plan_tmpl is not None
        assert sys_tmpl.template == DEFAULT_TEMPLATES["system"]
        assert plan_tmpl.template == DEFAULT_TEMPLATES["planner"]

    def test_list_templates(self) -> None:
        engine = PromptEngine()
        templates = engine.list_templates()
        assert isinstance(templates, dict)
        assert len(templates) == 5
        for key, val in templates.items():
            assert isinstance(key, str)
            assert isinstance(val, str)


# ── 测试: PromptEngine 便捷方法 ─────────────────────────────


class TestPromptEngineConvenience:
    def test_render_system_convenience(self) -> None:
        engine = PromptEngine()
        result = engine.render_system(
            agent_name="MyAgent",
            description="Does stuff",
            workspace_path="/ws/1",
        )
        assert "MyAgent" in result
        assert "Does stuff" in result

    def test_render_planner_convenience(self) -> None:
        engine = PromptEngine()
        result = engine.render_planner(
            task="Refactor code",
            context="Legacy codebase",
            max_steps=3,
        )
        assert "Refactor code" in result

    def test_render_executor_convenience(self) -> None:
        engine = PromptEngine()
        result = engine.render_executor(
            plan="1. Fix",
            step_number=1,
            step_description="Fix the bug",
            completed_steps=0,
            total_steps=1,
            tool_descriptions="read, write",
        )
        assert "Fix the bug" in result

    def test_render_verifier_convenience(self) -> None:
        engine = PromptEngine()
        result = engine.render_verifier(
            step_description="Test step",
            execution_result="Passed",
            verification_criteria="All green",
        )
        assert "Test step" in result

    def test_render_report_convenience(self) -> None:
        engine = PromptEngine()
        result = engine.render_report(
            task="Build feature",
            execution_summary="Done",
            steps_details="1. Code",
            success=True,
            total_steps=3,
            completed_steps=3,
            total_tokens=2000,
        )
        assert "Build feature" in result
        assert "3" in result

    def test_convenience_with_extra_vars(self) -> None:
        engine = PromptEngine()
        result = engine.render_system(
            agent_name="Agent",
            extra_var="extra_value",
        )
        assert "Agent" in result


# ── 测试: PromptEngine to_message ───────────────────────────


class TestPromptEngineToMessage:
    def test_to_message(self) -> None:
        engine = PromptEngine()
        msg = engine.to_message(PromptType.SYSTEM, {
            "agent_name": "Bot",
            "description": "",
            "workspace_path": "/ws",
            "backend_name": "c",
            "max_steps": "10",
        })
        assert msg["role"] == "system"
        assert "Bot" in msg["content"]

    def test_to_message_with_string_type(self) -> None:
        engine = PromptEngine()
        msg = engine.to_message("planner", {"task": "test"})
        assert msg["role"] == "system"
        assert "test" in msg["content"]

    def test_to_messages(self) -> None:
        engine = PromptEngine()
        msgs = engine.to_messages(
            [PromptType.SYSTEM, PromptType.PLANNER],
            base_variables={
                "agent_name": "Bot",
                "description": "",
                "workspace_path": "/ws",
                "backend_name": "c",
                "max_steps": "10",
                "task": "test task",
                "context": "ctx",
            },
        )
        assert len(msgs) == 2
        assert msgs[0]["role"] == "system"
        assert msgs[1]["role"] == "system"

    def test_to_message_preserves_custom_role(self) -> None:
        engine = PromptEngine()
        engine.set_template(PromptType.SYSTEM, "You are $name", role="user")
        msg = engine.to_message(PromptType.SYSTEM, {"name": "Bot"})
        assert msg["role"] == "user"


# ── 测试: 默认模板内容完整性 ─────────────────────────────────


class TestDefaultTemplates:
    def test_default_templates_have_required_variables(self) -> None:
        """验证所有默认模板包含了必要的变量占位符。"""
        required_vars: dict[str, list[str]] = {
            "system": ["agent_name", "description", "workspace_path", "backend_name", "max_steps"],
            "planner": ["task", "context", "max_steps"],
            "executor": ["plan", "step_number", "step_description", "total_steps", "tool_descriptions"],  # noqa: E501
            "verifier": ["step_description", "execution_result", "verification_criteria"],
            "report": ["task", "execution_summary", "steps_details", "total_steps", "completed_steps"],  # noqa: E501
        }
        for ptype, vars in required_vars.items():
            template = DEFAULT_TEMPLATES.get(ptype, "")
            for var in vars:
                assert f"${var}" in template, f"{ptype} 模板缺少变量 ${var}"

    def test_system_template_structure(self) -> None:
        tmpl = DEFAULT_TEMPLATES["system"]
        assert "ZMAI" in tmpl
        assert "Core Capabilities" in tmpl
        assert "Guidelines" in tmpl

    def test_planner_template_structure(self) -> None:
        tmpl = DEFAULT_TEMPLATES["planner"]
        assert "task planner" in tmpl.lower()
        assert "Instructions" in tmpl
        assert "Output Format" in tmpl

    def test_executor_template_structure(self) -> None:
        tmpl = DEFAULT_TEMPLATES["executor"]
        assert "executor" in tmpl.lower()
        assert "Current Step" in tmpl

    def test_verifier_template_structure(self) -> None:
        tmpl = DEFAULT_TEMPLATES["verifier"]
        assert "verifier" in tmpl.lower()
        assert "PASS" in tmpl

    def test_report_template_structure(self) -> None:
        tmpl = DEFAULT_TEMPLATES["report"]
        assert "summary" in tmpl.lower()
        assert "Output Format" in tmpl


# ── 测试: 边界情况 ───────────────────────────────────────────


class TestPromptEdgeCases:
    def test_empty_template_renders_empty(self) -> None:
        engine = PromptEngine()
        engine.set_template(PromptType.SYSTEM, "")
        result = engine.render(PromptType.SYSTEM, {"key": "val"})
        assert result == ""

    def test_template_with_no_variables(self) -> None:
        engine = PromptEngine()
        engine.set_template(PromptType.SYSTEM, "Static text")
        result = engine.render(PromptType.SYSTEM, {"unused": "var"})
        assert result == "Static text"

    def test_dollar_sign_in_template(self) -> None:
        """$ 符号在模板中应正确处理。"""
        engine = PromptEngine()
        engine.set_template(PromptType.SYSTEM, "Price: $100")
        result = engine.render(PromptType.SYSTEM, {})
        # $100 中的 $1 不是合法变量名（数字开头），应保留
        assert result == "Price: $100"

    def test_variable_with_numbers(self) -> None:
        engine = PromptEngine()
        engine.set_template(PromptType.SYSTEM, "Value: $val123")
        result = engine.render(PromptType.SYSTEM, {"val123": "ok"})
        assert result == "Value: ok"

    def test_unicode_variables(self) -> None:
        engine = PromptEngine()
        result = engine.render_system(
            agent_name="测试助手",
            description="中文描述",
            workspace_path="/路径",
            backend_name="克劳德",
            max_steps=10,
        )
        assert "测试助手" in result
        assert "中文描述" in result

    def test_very_long_variable_value(self) -> None:
        engine = PromptEngine()
        long_str = "x" * 10000
        result = engine.render_system(
            agent_name="Agent",
            description=long_str,
            workspace_path="/ws",
            backend_name="c",
            max_steps=1,
        )
        assert long_str in result

    def test_xss_in_variable(self) -> None:
        """变量中的特殊字符不应破坏模板结构。"""
        engine = PromptEngine()
        result = engine.render_system(
            agent_name="<script>alert('xss')</script>",
            description="",
            workspace_path="/ws",
            backend_name="c",
            max_steps=1,
        )
        assert "<script>" in result  # 内容不应被转义

    def test_template_with_newlines(self) -> None:
        engine = PromptEngine()
        engine.set_template(PromptType.SYSTEM, "Line1\nLine2\n$var")
        result = engine.render(PromptType.SYSTEM, {"var": "Line3"})
        assert result == "Line1\nLine2\nLine3"

    def test_render_all_types_with_defaults(self) -> None:
        """所有 5 种类型用默认模板渲染不应抛异常。"""
        engine = PromptEngine()
        base_vars = {
            "agent_name": "A", "description": "D", "workspace_path": "/w",
            "backend_name": "c", "max_steps": "10", "task": "T",
            "context": "C", "plan": "P", "step_number": "1",
            "step_description": "SD", "completed_steps": "0", "total_steps": "1",
            "tool_descriptions": "T", "execution_result": "ER",
            "verification_criteria": "VC", "execution_summary": "ES",
            "steps_details": "SD", "success": True, "error_info": "",
            "failed_steps": "0", "total_tokens": "0",
        }
        for pt in PromptType:
            result = engine.render(pt, base_vars)
            assert isinstance(result, str)


# ── 测试: 自定义模板工厂 ─────────────────────────────────────


class TestCustomTemplates:
    def test_custom_templates_on_init(self) -> None:
        custom = {
            "system": "Custom system: $name",
            "planner": "Custom planner: $task",
        }
        engine = PromptEngine(templates=custom)
        assert "Custom system" in engine.render(PromptType.SYSTEM, {"name": "Bot"})
        assert "Custom planner" in engine.render(PromptType.PLANNER, {"task": "test"})
        # 未自定义的类型应使用默认模板
        assert "executor" in engine.get_template(PromptType.EXECUTOR).template

    def test_custom_template_overrides_default(self) -> None:
        custom = {"system": "Only custom"}
        engine = PromptEngine(templates=custom)
        assert engine.render(PromptType.SYSTEM, {}) == "Only custom"
