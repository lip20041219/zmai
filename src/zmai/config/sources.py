"""配置源 — 文件、环境变量、CLI 参数三种加载策略。"""

from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any


def _flatten(d: dict, parent: str = "") -> dict[str, Any]:
    """嵌套 dict → 扁平 dict (点号分隔键)。"""
    items: dict[str, Any] = {}
    for k, v in d.items():
        key = f"{parent}.{k}" if parent else k
        if isinstance(v, dict):
            items.update(_flatten(v, key))
        else:
            items[key] = v
    return items


class ConfigSource(ABC):
    """配置源抽象基类。"""

    @abstractmethod
    def load(self) -> dict[str, Any]:
        """加载全部键值对，返回扁平 dict。"""
        ...

    @abstractmethod
    def name(self) -> str:
        """配置源名称。"""
        ...


class FileSource(ConfigSource):
    """JSON 文件配置源。"""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def load(self) -> dict[str, Any]:
        if not self._path.exists():
            return {}
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            return _flatten(raw) if isinstance(raw, dict) else {}
        except json.JSONDecodeError as e:
            from zmai.errors import ConfigError
            raise ConfigError(f"配置文件格式错误: {e}", key=str(self._path))

    def name(self) -> str:
        return f"file:{self._path}"


class EnvSource(ConfigSource):
    """环境变量配置源。"""

    def __init__(self, prefix: str = "ZMAI_") -> None:
        if not prefix:
            from zmai.errors import ConfigError
            raise ConfigError("环境变量前缀长度不能为 0")
        self._prefix = prefix

    def load(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, val in os.environ.items():
            if key.startswith(self._prefix):
                # ZMAI_RUNTIME__MAX_ITERATIONS → runtime.max_iterations
                flat_key = key[len(self._prefix):].lower().replace("__", ".")
                result[flat_key] = self._coerce(val)
        return result

    @staticmethod
    def _coerce(val: str) -> Any:
        # 尝试解析数字、bool、JSON
        if val.lower() in ("true", "yes", "1"):
            return True
        if val.lower() in ("false", "no", "0"):
            return False
        try:
            return int(val)
        except ValueError:
            pass
        try:
            return float(val)
        except ValueError:
            pass
        if val.startswith(("[", "{")):
            try:
                return json.loads(val)
            except json.JSONDecodeError:
                pass
        return val

    def name(self) -> str:
        return f"env:{self._prefix}"


class CLISource(ConfigSource):
    """CLI 参数配置源 (--key=value)。"""

    def __init__(self, args: list[str] | None = None) -> None:
        import sys
        self._args = args or sys.argv[1:]

    def load(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for arg in self._args:
            if arg.startswith("--"):
                rest = arg[2:]
                if "=" in rest:
                    key, val = rest.split("=", 1)
                    result[key] = val
        return result

    def name(self) -> str:
        return "cli"
