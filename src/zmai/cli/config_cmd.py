"""Config CLI — zmai config <get|set|list>."""

from __future__ import annotations

import sys
from typing import Any

from zmai.cli.formatters import Theme, print_table
from zmai.config import Config


def run_config(argv: list[str]) -> None:
    """`zmai config <get|set|list>`"""
    config = Config()
    theme = Theme.dark()
    if not argv or argv[0] not in ("get", "set", "list"):
        print("usage: zmai config <get|set|list> [key] [value]")
        sys.exit(1)
    sub = argv[0]
    if sub == "list":
        data = config.export()
        rows = [[k, str(v)] for k, v in sorted(data.items())]
        print_table(["Key", "Value"], rows, theme)
    elif sub == "get":
        key = argv[1] if len(argv) > 1 else None
        if not key:
            print("need key")
            sys.exit(1)
        val = config.get(key)
        if val is not None:
            print(f"{key} = {val}")
        else:
            print(f"not set: {key}")
    elif sub == "set":
        if len(argv) < 3:
            print("need key and value")
            sys.exit(1)
        config.set(argv[1], argv[2])
        print(f"{argv[1]} = {argv[2]}")
