"""CLI tests - theme, color, arg parsing."""

from __future__ import annotations

import io
import sys

import pytest

from zmai.cli.formatters import Theme, print_error, print_info, print_json, print_success, print_table, print_warning


class TestTheme:
    def test_dark_theme_created(self):
        t = Theme.dark()
        assert t.enabled is True
        assert "success" in t.colors

    def test_light_theme(self):
        t = Theme.light()
        assert t.enabled is True

    def test_plain_theme_disabled(self):
        t = Theme.plain()
        assert t.enabled is False

    def test_colorize_returns_colored_when_enabled(self):
        t = Theme.dark()
        result = t.colorize("hello", "success")
        assert "\033[" in result
        assert "hello" in result
        assert "\033[0m" in result

    def test_colorize_returns_plain_when_disabled(self):
        t = Theme.plain()
        result = t.colorize("hello", "success")
        assert result == "hello"

    def test_colorize_unknown_style(self):
        t = Theme.dark()
        result = t.colorize("test", "nonexistent")
        assert result == "test"

    def test_convenience_methods(self):
        t = Theme.dark()
        assert "\033[" in t.success("ok")
        assert "\033[" in t.error("fail")
        assert "\033[" in t.warning("warn")
        assert "\033[" in t.info("info")
        assert "\033[" in t.dim("dim")
        assert "\033[" in t.highlight("hl")


class TestOutputFunctions:
    def test_print_json(self):
        out = io.StringIO()
        sys.stdout, old = out, sys.stdout
        try:
            print_json({"a": 1})
        finally:
            sys.stdout = old
        assert '"a": 1' in out.getvalue()

    def test_print_success(self):
        out = io.StringIO()
        sys.stdout, old = out, sys.stdout
        try:
            print_success("done", Theme.plain())
        finally:
            sys.stdout = old
        assert "done" in out.getvalue()

    def test_print_error(self):
        err = io.StringIO()
        sys.stderr, old = err, sys.stderr
        try:
            print_error("fail", Theme.plain())
        finally:
            sys.stderr = old
        assert "fail" in err.getvalue()

    def test_print_table(self):
        out = io.StringIO()
        sys.stdout, old = out, sys.stdout
        try:
            print_table(["A", "B"], [["1", "2"], ["3", "4"]], Theme.plain())
        finally:
            sys.stdout = old
        r = out.getvalue()
        assert "A" in r and "1" in r and "3" in r

    def test_print_table_empty_rows(self):
        out = io.StringIO()
        sys.stdout, old = out, sys.stdout
        try:
            print_table(["A"], [], Theme.plain())
        finally:
            sys.stdout = old
        assert out.getvalue() == ""

    def test_print_info(self):
        out = io.StringIO()
        sys.stdout, old = out, sys.stdout
        try:
            print_info("info msg", Theme.plain())
        finally:
            sys.stdout = old
        assert "info msg" in out.getvalue()

    def test_print_warning(self):
        out = io.StringIO()
        sys.stdout, old = out, sys.stdout
        try:
            print_warning("warn", Theme.plain())
        finally:
            sys.stdout = old
        assert "warn" in out.getvalue()


class TestCLIParsing:
    def test_parser_builds(self):
        from zmai.cli.main import _build_parser
        p = _build_parser()
        assert p is not None

    def test_help(self):
        from zmai.cli.main import _build_parser
        p = _build_parser()
        h = p.format_help()
        assert "--json" in h
        assert "--no-color" in h
        assert "--backend" in h

    def test_positional_task(self):
        from zmai.cli.main import _build_parser
        p = _build_parser()
        args = p.parse_args(["hello world"])
        assert args.task == ["hello world"]

    def test_no_color_flag(self):
        from zmai.cli.main import _build_parser
        p = _build_parser()
        args = p.parse_args(["--no-color", "task"])
        assert args.no_color is True
        assert args.task == ["task"]

    def test_json_flag(self):
        from zmai.cli.main import _build_parser
        p = _build_parser()
        args = p.parse_args(["--json", "task"])
        assert args.json is True
        assert args.task == ["task"]

    def test_version_flag(self):
        from zmai.cli.main import _build_parser
        p = _build_parser()
        with pytest.raises(SystemExit) as e:
            p.parse_args(["--version"])
        assert e.value.code == 0


class TestThemeFromConfig:
    def test_light_from_config(self):
        from zmai.cli.main import _get_theme
        from zmai.config import Config
        import argparse
        c = Config()
        c.set("cli.theme", "light")
        t = _get_theme(argparse.Namespace(no_color=False), c)
        assert isinstance(t, Theme)

    def test_plain_from_config(self):
        from zmai.cli.main import _get_theme
        from zmai.config import Config
        import argparse
        c = Config()
        c.set("cli.theme", "plain")
        t = _get_theme(argparse.Namespace(no_color=False), c)
        assert t.enabled is False

    def test_dark_default(self):
        from zmai.cli.main import _get_theme
        from zmai.config import Config
        import argparse
        t = _get_theme(argparse.Namespace(no_color=False), Config())
        assert t.enabled is True

    def test_no_color_disables(self):
        from zmai.cli.main import _get_theme
        from zmai.config import Config
        import argparse
        t = _get_theme(argparse.Namespace(no_color=True), Config())
        assert t.enabled is False


class TestConfigSubcommand:
    def test_config_list(self):
        from zmai.cli.main import _run_config
        out = io.StringIO()
        sys.stdout, old = out, sys.stdout
        try:
            _run_config(["list"])
        finally:
            sys.stdout = old
        assert "Key" in out.getvalue() or "(" in out.getvalue()

    def test_config_get(self):
        from zmai.cli.main import _run_config
        out = io.StringIO()
        sys.stdout, old = out, sys.stdout
        try:
            _run_config(["get", "runtime.max_iterations"])
        finally:
            sys.stdout = old
        assert "100" in out.getvalue() or "max_iterations" in out.getvalue()

    def test_config_bad_subcommand(self):
        from zmai.cli.main import _run_config
        with pytest.raises(SystemExit):
            _run_config(["invalid"])

    def test_config_set_no_value(self):
        from zmai.cli.main import _run_config
        with pytest.raises(SystemExit):
            _run_config(["set", "key"])
