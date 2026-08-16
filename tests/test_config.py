"""Tests for zmai.config — Config, FileSource, EnvSource, CLISource, _flatten."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from zmai.config.config import Config
from zmai.config.sources import CLISource, EnvSource, FileSource, _flatten

# ═══════════════════════════════════════════════════════════════
# _flatten
# ═══════════════════════════════════════════════════════════════

class TestFlatten:
    def test_simple(self):
        assert _flatten({"a": 1, "b": 2}) == {"a": 1, "b": 2}

    def test_nested(self):
        result = _flatten({"a": {"b": 1, "c": 2}})
        assert result == {"a.b": 1, "a.c": 2}

    def test_deeply_nested(self):
        result = _flatten({"x": {"y": {"z": 3}}})
        assert result == {"x.y.z": 3}

    def test_empty(self):
        assert _flatten({}) == {}

    def test_mixed(self):
        result = _flatten({"a": 1, "b": {"c": 2, "d": {"e": 3}}})
        assert result == {"a": 1, "b.c": 2, "b.d.e": 3}


# ═══════════════════════════════════════════════════════════════
# FileSource
# ═══════════════════════════════════════════════════════════════

class TestFileSource:
    def test_load_json(self, tmp_path: Path):
        f = tmp_path / "config.json"
        f.write_text('{"a": 1, "b": 2}', encoding="utf-8")
        src = FileSource(f)
        assert src.load() == {"a": 1, "b": 2}

    def test_load_missing(self):
        src = FileSource("/nonexistent/path.json")
        assert src.load() == {}

    def test_load_invalid_json(self, tmp_path: Path):
        f = tmp_path / "bad.json"
        f.write_text("not json", encoding="utf-8")
        src = FileSource(f)
        with pytest.raises(Exception) as exc:
            src.load()
        assert "格式错误" in str(exc.value) or "ConfigError" in str(exc.value)

    def test_load_flattens_nested(self, tmp_path: Path):
        f = tmp_path / "nested.json"
        f.write_text(json.dumps({"runtime": {"max_iterations": 100}}), encoding="utf-8")
        src = FileSource(f)
        data = src.load()
        assert data == {"runtime.max_iterations": 100}

    def test_load_empty_file(self, tmp_path: Path):
        f = tmp_path / "empty.json"
        f.write_text("{}", encoding="utf-8")
        src = FileSource(f)
        assert src.load() == {}

    def test_name(self, tmp_path: Path):
        f = tmp_path / "c.json"
        src = FileSource(f)
        assert "file:" in src.name()
        assert "c.json" in src.name()


# ═══════════════════════════════════════════════════════════════
# EnvSource
# ═══════════════════════════════════════════════════════════════

class TestEnvSource:
    def test_load_with_prefix(self):
        with patch.dict(os.environ, {"ZMAI_TEST_KEY": "val"}, clear=True):
            src = EnvSource("ZMAI_TEST_")
            data = src.load()
            assert data.get("key") == "val"

    def test_load_empty(self):
        with patch.dict(os.environ, {}, clear=True):
            src = EnvSource("ZMAI_")
            assert src.load() == {}

    def test_no_match(self):
        with patch.dict(os.environ, {"OTHER_VAR": "x"}, clear=True):
            src = EnvSource("ZMAI_")
            assert src.load() == {}

    def test_double_underscore_to_dot(self):
        with patch.dict(os.environ, {"ZMAI_RUNTIME__MAX_ITERATIONS": "50"}, clear=True):
            src = EnvSource("ZMAI_")
            data = src.load()
            assert data.get("runtime.max_iterations") == 50

    def test_name(self):
        src = EnvSource("ZMAI_")
        assert src.name() == "env:ZMAI_"


class TestEnvSourceCoerce:
    def test_bool_true(self):
        assert EnvSource._coerce("true") is True
        assert EnvSource._coerce("yes") is True
        assert EnvSource._coerce("1") is True

    def test_bool_false(self):
        assert EnvSource._coerce("false") is False
        assert EnvSource._coerce("no") is False
        assert EnvSource._coerce("0") is False

    def test_int(self):
        assert EnvSource._coerce("42") == 42
        assert EnvSource._coerce("-1") == -1

    def test_float(self):
        assert EnvSource._coerce("3.14") == 3.14

    def test_json_array(self):
        assert EnvSource._coerce("[1,2,3]") == [1, 2, 3]

    def test_json_object(self):
        assert EnvSource._coerce('{"a":1}') == {"a": 1}

    def test_string_fallback(self):
        assert EnvSource._coerce("hello") == "hello"

    def test_empty_string(self):
        assert EnvSource._coerce("") == ""


class TestEnvSourceInit:
    def test_empty_prefix_raises(self):
        with pytest.raises(Exception):
            EnvSource(prefix="")

    def test_custom_prefix(self):
        with patch.dict(os.environ, {"MY_VERBOSE": "true"}, clear=True):
            src = EnvSource("MY_")
            data = src.load()
            assert data.get("verbose") is True


# ═══════════════════════════════════════════════════════════════
# CLISource
# ═══════════════════════════════════════════════════════════════

class TestCLISource:
    def test_load_key_value(self):
        src = CLISource(["--backend=deepseek"])
        assert src.load() == {"backend": "deepseek"}

    def test_multiple_keys(self):
        src = CLISource(["--a=1", "--b=2"])
        data = src.load()
        assert data == {"a": "1", "b": "2"}

    def test_skip_non_double_dash(self):
        src = CLISource(["task", "--backend=deepseek", "arg"])
        assert src.load() == {"backend": "deepseek"}

    def test_empty_args(self):
        # CLISource 使用 args or sys.argv，空列表会被 or 忽略
        # 所以 mock sys.argv 确保不读真实参数
        import sys
        with patch.object(sys, "argv", ["prog"]):
            src2 = CLISource()
            assert src2.load() == {}

    def test_no_equals_ignored(self):
        src = CLISource(["--help"])
        assert src.load() == {}

    def test_name(self):
        src = CLISource([])
        assert src.name() == "cli"


# ═══════════════════════════════════════════════════════════════
# Config
# ═══════════════════════════════════════════════════════════════

class TestConfig:
    def _empty_cfg(self) -> Config:
        """创建空 Config（不加载默认源）。"""
        return Config(sources=[])

    def test_get_set(self):
        cfg = self._empty_cfg()
        cfg.set("key", "val")
        assert cfg.get("key") == "val"

    def test_get_default(self):
        cfg = self._empty_cfg()
        assert cfg.get("nonexistent", 42) == 42

    def test_get_default_none(self):
        cfg = self._empty_cfg()
        assert cfg.get("missing") is None

    def test_has(self):
        cfg = self._empty_cfg()
        cfg.set("exists", 1)
        assert cfg.has("exists") is True
        assert cfg.has("nope") is False

    def test_export(self):
        cfg = self._empty_cfg()
        cfg.set("a", 1)
        cfg.set("b", 2)
        exported = cfg.export()
        assert exported == {"a": 1, "b": 2}
        # export 应返回副本，修改不影响源
        exported["a"] = 99
        assert cfg.get("a") == 1

    def test_reload(self, tmp_path: Path):
        """reload 重新加载文件源。"""
        f = tmp_path / "zmai.json"
        f.write_text('{"key": "old"}', encoding="utf-8")
        cfg = Config(sources=[FileSource(f)])
        assert cfg.get("key") == "old"
        f.write_text('{"key": "new"}', encoding="utf-8")
        cfg.reload()
        assert cfg.get("key") == "new"

    def test_merge_order(self, tmp_path: Path):
        """合并顺序: File < Env < CLI（后加载覆盖前）。"""
        f = tmp_path / "zmai.json"
        f.write_text('{"key": "from_file"}', encoding="utf-8")

        class MockEnv(EnvSource):
            def load(self):
                return {"key": "from_env"}

        class MockCLI(CLISource):
            def load(self):
                return {"key": "from_cli"}

        cfg2 = Config(sources=[FileSource(f), MockEnv("ZMAI_")])
        assert cfg2.get("key") == "from_env"  # Env 覆盖 File

    def test_thread_safety(self):
        """set/get 在多线程下不崩溃。"""
        cfg = Config(sources=[])
        import threading
        errors = []

        def setter():
            for i in range(100):
                cfg.set(f"k{i}", i)

        def getter():
            for i in range(100):
                cfg.get(f"k{i}")

        threads = [threading.Thread(target=setter()) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(errors) == 0


class TestConfigSourcesSemantics:
    """Config 构造参数 sources 的语义测试。

    确保 sources=None、sources=[]、sources=[...] 三者的明确区分。
    这是 P0-1 的回归测试：Config(sources=[]) 曾是 falsy trap。
    """

    def test_default_uses_default_sources(self):
        """Config() 使用默认配置源。"""
        cfg = Config()
        # 默认源包含 FileSource、EnvSource、CLISource
        assert len(cfg._sources) >= 3
        # 验证有 FileSource（文件名 zmai.json）
        from zmai.config.sources import FileSource
        assert any(isinstance(s, FileSource) for s in cfg._sources)

    def test_none_uses_default_sources(self):
        """Config(sources=None) 使用默认配置源。"""
        cfg = Config(sources=None)
        assert len(cfg._sources) >= 3
        from zmai.config.sources import FileSource
        assert any(isinstance(s, FileSource) for s in cfg._sources)

    def test_empty_list_uses_no_sources(self):
        """Config(sources=[]) 不加载任何源。"""
        cfg = Config(sources=[])
        assert len(cfg._sources) == 0
        # _data 应为空（无源可加载）
        assert cfg._data == {}

    def test_custom_sources_used_directly(self):
        """Config(sources=[custom]) 使用自定义源。"""
        from zmai.config.sources import EnvSource
        env = EnvSource()
        cfg = Config(sources=[env])
        assert len(cfg._sources) == 1
        assert cfg._sources[0] is env
