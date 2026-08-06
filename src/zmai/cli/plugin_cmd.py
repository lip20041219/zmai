"""Plugin CLI — zmai plugin <list|install|remove>."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import Any

from zmai.cli.formatters import Theme


PLUGINS_HOME = Path.home() / ".zmai" / "backends"


def run_plugin(argv: list[str]) -> None:
    """管理 Plugin Backend。"""
    PLUGINS_HOME.mkdir(parents=True, exist_ok=True)

    if not argv:
        argv = ["list"]
    sub = argv[0]

    if sub == "list":
        files = sorted(PLUGINS_HOME.glob("*.py"))
        if not files:
            print("no plugins installed.")
            print(f"  use: zmai plugin install <file.py>")
            return
        theme = Theme.dark()
        print(f"  {theme.dim('Plugin directory:')}")
        print(f"    {PLUGINS_HOME}")
        print()
        for f in files:
            print(f"  {f.stem}")
        print()
        print(f"  {theme.dim(f'{len(files)} plugin(s)')}")

    elif sub == "install":
        if len(argv) < 2:
            print("usage: zmai plugin install <file.py> [--name <name>]", file=sys.stderr)
            sys.exit(1)
        src = Path(argv[1])
        if not src.exists():
            print(f"file not found: {src}", file=sys.stderr)
            sys.exit(1)
        dst = PLUGINS_HOME / src.name
        shutil.copy2(src, dst)
        print(f"installed: {dst}")

    elif sub == "remove":
        if len(argv) < 2:
            print("usage: zmai plugin remove <name>", file=sys.stderr)
            sys.exit(1)
        target = PLUGINS_HOME / f"{argv[1]}.py"
        if target.exists():
            target.unlink()
            print(f"removed: {argv[1]}")
        else:
            print(f"plugin not found: {argv[1]}", file=sys.stderr)
            sys.exit(1)

    else:
        print(f"unknown plugin subcommand: {sub}", file=sys.stderr)
        print("usage: zmai plugin <list|install|remove>", file=sys.stderr)
        sys.exit(1)
