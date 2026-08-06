"""Auth CLI — zmai auth <setup|status|list|switch|update|remove|test>.

Also includes first-run wizard and auth debug functions used by main().
"""

from __future__ import annotations

import json as _json
import os
import sys
import urllib.error as _urlerror
import urllib.request as _urlrequest
from pathlib import Path
from typing import Any

from zmai.cli.formatters import Theme, print_error, print_table, print_warning
from zmai.config import Config
from zmai.errors import CredentialError


# ═══════════════════════════════════════════════════════════════
# 首次配置向导
# ═══════════════════════════════════════════════════════════════


def should_show_wizard() -> bool:
    """是否需要显示首次配置向导。"""
    from zmai.gateway.backends import get_available_backends
    for _name, info in get_available_backends().items():
        if os.environ.get(info["env_api_key"]):
            return False
    try:
        from zmai.auth import AuthStore
        if AuthStore().list_backends():
            return False
    except CredentialError as e:
        print_error(str(e))
        print_warning("请运行 `zmai auth update <backend>` 重新配置凭据。")
        return False
    except Exception:
        pass
    return sys.stdin.isatty() and sys.stderr.isatty()


def first_run_wizard() -> bool:
    """首次配置向导。返回 True 表示配置成功。"""
    from zmai.gateway.backends import BACKEND_DEFAULT_CONFIG, get_available_backends

    all_backends = get_available_backends()
    backend_list = [
        (name, info["label"], info["default_model"])
        for name, info in all_backends.items()
    ]

    sep = "━" * 50

    # ── Step 1: 选择模型 ──────────────────────────
    print(f"\n  {sep}", file=sys.stderr)
    print(f"  ZMAI — 首次配置向导", file=sys.stderr)
    print(f"", file=sys.stderr)
    print(f"  请选择默认模型：\n", file=sys.stderr)
    for i, (name, label, model) in enumerate(backend_list, 1):
        default_mark = "（默认）" if i == 1 else ""
        print(f"    [{i}] {label:12s} — {model}{default_mark}", file=sys.stderr)

    print(f"", file=sys.stderr)
    print(f"  请输入编号 [1-{len(backend_list)}]，直接回车选择 {backend_list[0][1]}：", file=sys.stderr)
    print(f"  {sep}", file=sys.stderr)
    try:
        sel = input("  ").strip()
    except (EOFError, KeyboardInterrupt):
        sel = ""
    print(file=sys.stderr)

    idx = 0
    if sel:
        try:
            idx = max(0, min(int(sel) - 1, len(backend_list) - 1))
        except ValueError:
            idx = 0

    name, label, default_model = backend_list[idx]
    defaults = BACKEND_DEFAULT_CONFIG.get(name, {})

    # ── Step 2: 输入 API Key ───────────────────────
    print(f"  请输入 {label} API Key", file=sys.stderr)
    print(f"  (支持粘贴，回车确认)", file=sys.stderr)
    try:
        key = input("  ").strip()
    except (EOFError, KeyboardInterrupt):
        key = ""
    print(file=sys.stderr)

    if not key:
        print(f"  API Key 为空，配置取消。\n", file=sys.stderr)
        return False

    # ── Step 3: 保存配置 ────────────────────────────
    from zmai.auth import AuthStore
    from zmai.auth.store import CREDENTIALS_FILE as _CRED_FILE

    try:
        store = AuthStore()
    except CredentialError as e:
        print(f"  ⚠ 凭据文件存在问题: {e.reason}", file=sys.stderr)
        print(f"  正在重置凭据文件...", file=sys.stderr)
        try:
            _CRED_FILE.unlink()
        except Exception:
            pass
        store = AuthStore()

    store.set_backend(
        name, key,
        model=default_model,
        base_url=defaults.get("base_url", ""),
        timeout=int(defaults.get("timeout", 0)),
        max_tokens=int(defaults.get("max_tokens", 0)),
        temperature=float(defaults.get("temperature", 0.0)),
        make_active=True,
    )

    # ── 检测环境变量冲突 ────────────────────────────
    from zmai.auth.resolver import CredentialResolver, _resolve_env_names
    env_key, _ = _resolve_env_names(name)
    env_val = os.environ.get(env_key, "")
    conflict = bool(env_val and env_val != key)

    # ── 完成 ──────────────────────────────────────────
    print(f"  {sep}", file=sys.stderr)
    if not conflict:
        print(f"  ✓ 配置完成", file=sys.stderr)
        print(f"", file=sys.stderr)
        print(f"  当前默认：{label} ({default_model})", file=sys.stderr)
        print(f"  配置文件：~/.zmai/credentials", file=sys.stderr)
        print(f"", file=sys.stderr)
        print(f"  现在可以开始使用了：", file=sys.stderr)
        print(f"    zmai \"你的任务描述\"", file=sys.stderr)
    else:
        print(f"  ✓ 已保存到凭据文件", file=sys.stderr)
        print(f"", file=sys.stderr)
        print(f"  ⚠ 检测到环境变量 {env_key} 使用不同的 Key。", file=sys.stderr)
        print(f"", file=sys.stderr)
        print(f"  运行时优先使用环境变量。", file=sys.stderr)
        print(f"  要使用刚保存的 Key，请先清除环境变量：", file=sys.stderr)
        print(f"", file=sys.stderr)
        print(f"    PowerShell:  $env:{env_key}=\"\"", file=sys.stderr)
        print(f"    CMD:         set {env_key}=", file=sys.stderr)
        print(f"    Bash:        unset {env_key}", file=sys.stderr)
        print(f"", file=sys.stderr)
        print(f"  然后重新运行 zmai。", file=sys.stderr)
    print(f"  {sep}\n", file=sys.stderr)

    return True


def offer_auth_fix(theme: Theme) -> None:
    """API Key 错误时，交互式询问用户是否要修复。"""
    if not (sys.stdin.isatty() and sys.stderr.isatty()):
        return
    try:
        sys.stderr.write(f"\n  {theme.dim('是否现在配置 API Key？')} [Y/n] ")
        ans = input().strip().lower()
    except (EOFError, KeyboardInterrupt):
        ans = ""
    if ans and ans not in ("y", "yes", ""):
        return
    first_run_wizard()


# ═══════════════════════════════════════════════════════════════
# Auth 子命令 (zmai auth ...)
# ═══════════════════════════════════════════════════════════════


def _run_auth_status() -> None:
    """显示所有 Backend 的状态。"""
    from zmai.auth.resolver import CredentialResolver
    from zmai.auth.status import source_label
    from zmai.gateway.plugin import discover_plugins

    resolver = CredentialResolver()

    print("  Authentication Status")
    print("  ────────────────────")
    print()
    found = False
    for plugin in discover_plugins():
        found = True
        status = resolver.get_status(plugin.name)
        label = plugin.label or plugin.name.title()

        print(f"  {label}")
        if status.configured:
            src = source_label(status.source)
            print(f"    Configured : Yes")
            print(f"    Source     : {src}")

            if status.conflict:
                for detail in status.conflict_details:
                    if detail.source != status.source:
                        print(f"    Warning    : {detail.label} has a different key.")
                print(f"    Note       : Currently using {source_label(status.source)}. "
                      f"To use the key from credentials file, "
                      f"unset the environment variable.")
        else:
            file_st = status.credential_store_status
            if file_st in ("corrupted", "key_mismatch", "empty"):
                print(f"    Configured : No")
                print(f"    Status     : {file_st} (credentials file)")
            else:
                print(f"    Configured : No")
                print(f"    Source     : missing")
        print()

    if not found:
        print("  (no backends registered)")
        print()


def _run_auth_test(name: str) -> None:
    """测试指定 Backend 的 API Key 有效性。"""
    from zmai.auth.resolver import CredentialResolver
    from zmai.auth.status import mask_key, source_label
    from zmai.gateway.backends import get_backend_info
    from zmai.gateway.plugin import discover_plugins

    plugin = None
    for p in discover_plugins():
        if p.name == name:
            plugin = p
            break

    if plugin is None:
        print(f"  [ERROR] Backend '{name}' 未注册。", file=sys.stderr)
        sys.exit(1)

    label = plugin.label or name.title()
    status = CredentialResolver().get_status(name)

    print(f"  Testing {label}...")
    print(f"  ────────────────────────")
    print()

    print(f"  Resolving credentials...")
    if not status.configured:
        print(f"    Source : None")
        print(f"    Key    : -")
        print()
        print(f"  No credentials configured for {name}.")
        print(f"  Run `zmai auth update {name}` to configure.")
        return

    print(f"    Source : {source_label(status.source)}")
    if status.env_var_status == "ok":
        print(f"             ({status.env_var_name})")
    print(f"    Key    : {mask_key(status.api_key)}")
    if status.conflict:
        conflict_list = ", ".join(
            d.label for d in status.conflict_details if d.source != status.source
        )
        print(f"    Warning: {conflict_list} has a different key.")
    print()

    verify_url = plugin.verify_url or ""
    if not verify_url:
        try:
            info = get_backend_info(name)
            if info:
                verify_url = info.get("verify_url", "")
        except Exception:
            pass

    if not verify_url:
        print(f"  Sending API request...")
        print(f"    URL    : (not configured)")
        print()
        print(f"  Result : SKIP (no verification endpoint)")
        print(f"  Model  : {status.model or plugin.default_model or '-'}")
        return

    print(f"  Sending API request...")
    print(f"    URL    : {verify_url}")
    print(f"    Method : {plugin.verify_method or 'GET'}")
    print()

    method = plugin.verify_method or "GET"

    if verify_url and "generativelanguage.googleapis.com" in verify_url:
        full_url = f"{verify_url}?key={status.api_key}"
        req = _urlrequest.Request(full_url, method=method)
    else:
        full_url = verify_url
        req = _urlrequest.Request(full_url, method=method)
        if "anthropic" in verify_url:
            req.add_header("x-api-key", status.api_key)
            req.add_header("anthropic-version",
                           plugin.verify_headers.get("anthropic-version", "2023-06-01"))
        else:
            req.add_header("Authorization", f"Bearer {status.api_key}")

    for k, v in plugin.verify_headers.items():
        if k.lower() not in ("content-type",):
            req.add_header(k, v)

    try:
        timeout = 15
        resp = _urlrequest.urlopen(req, timeout=timeout)
        http_code = resp.status
        body = resp.read().decode("utf-8", errors="replace")[:500]
    except _urlerror.HTTPError as e:
        http_code = e.code
        try:
            body = e.read().decode("utf-8", errors="replace")[:500]
        except Exception:
            body = ""
    except _urlerror.URLError as e:
        reason = str(e.reason) if e.reason else "Unknown error"
        print(f"  Result : FAIL")
        print(f"  Reason : Network error")
        print(f"  Detail : {reason}")
        print()
        print(f"  Check your network connection and proxy settings.")
        return
    except OSError as e:
        print(f"  Result : FAIL")
        print(f"  Reason : Network error")
        print(f"  Detail : {e}")
        print()
        print(f"  Check your network connection.")
        return

    if http_code == 200 or http_code == 201:
        print(f"  Result : PASS")
        print(f"  Model  : {status.model or plugin.default_model or '-'}")
        try:
            from zmai.auth.store import AuthStore
            store = AuthStore()
            existing = store.get_backend(name)
            if existing:
                store.set_backend(
                    name, existing["api_key"],
                    model=existing.get("model", ""),
                    base_url=existing.get("base_url", ""),
                    timeout=existing.get("timeout", 0),
                    max_tokens=existing.get("max_tokens", 0),
                    temperature=existing.get("temperature", 0.0),
                    make_active=False,
                )
        except Exception:
            pass
    else:
        reason = _classify_http_error(http_code, body)
        print(f"  Result : FAIL")
        print(f"  Status : {http_code} {reason}")
        if http_code == 401:
            print(f"  Reason : API Key is invalid.")
        elif http_code == 403:
            body_lower = body.lower()
            if any(kw in body_lower for kw in ("expired", "disabled", "deactivated")):
                print(f"  Reason : API Key has expired or been disabled.")
            else:
                print(f"  Reason : API Key does not have permission.")
        elif http_code == 429:
            print(f"  Reason : Rate limited.")
        else:
            _safe_print_error_body(body)
        print()
        print(f"  Run `zmai auth update {name}` with a valid key.")


def _classify_http_error(http_code: int, body: str) -> str:
    mapping = {400: "Bad Request", 401: "Unauthorized",
               403: "Forbidden", 404: "Not Found", 429: "Too Many Requests"}
    return mapping.get(http_code, "Error")


def _safe_print_error_body(body: str) -> None:
    if not body:
        return
    try:
        data = _json.loads(body)
        msg = (data.get("error", {}).get("message", "")
               or data.get("message", ""))
        if msg:
            safe = str(msg)[:100]
            if "sk-" in safe:
                safe = safe[:safe.index("sk-")] + "(redacted)"
            print(f"  Detail : {safe}")
    except Exception:
        pass


def _print_setup_hint_if_needed() -> None:
    """如果没有任何 Backend 配置，输出设置提示。"""
    from zmai.auth.resolver import CredentialResolver
    from zmai.gateway.plugin import discover_plugins
    resolver = CredentialResolver()
    any_configured = False
    for plugin in discover_plugins():
        if resolver.get_status(plugin.name).configured:
            any_configured = True
            break
    if not any_configured:
        print()
        print("  No backends configured.")
        print("  Use `zmai auth setup` to configure, or set environment variables:")
        print("    ANTHROPIC_API_KEY / DEEPSEEK_API_KEY / GEMINI_API_KEY")


def print_auth_debug(gateway) -> None:
    """打印 Auth 调试信息（不暴露真实 Key）。"""
    from zmai.auth.resolver import CredentialResolver
    from zmai.auth.status import source_label
    from zmai.auth.store import AuthStore
    from zmai.errors import CredentialError
    from zmai.gateway.backends import get_backend_info

    backend_name = gateway.default_name or ""
    if not backend_name:
        try:
            backend_name = AuthStore().get_active_backend()
        except CredentialError:
            pass
        except Exception:
            pass
    if not backend_name:
        return

    info = get_backend_info(backend_name) or {}
    label = info.get("label", backend_name.title())

    status = CredentialResolver().get_status(backend_name)
    active_src = source_label(status.source)

    dim = "\033[2m"
    reset = "\033[0m"

    sys.stderr.write(f"{dim}  Auth ─────────────────────────────────────────────────────────{reset}\n")
    sys.stderr.write(f"{dim}    Backend:         {label}  ({active_src}){reset}\n")
    sys.stderr.write(
        f"{dim}    Credentials File: "
        f"{'Loaded' if status.credential_store_status == 'ok' else 'No key'}{reset}\n"
    )
    sys.stderr.write(
        f"{dim}    Config File:     "
        f"{'Loaded' if status.config_file_status == 'ok' else 'No key'}{reset}\n"
    )
    sys.stderr.write(
        f"{dim}    Environment:     "
        f"{'Loaded' if status.env_var_status == 'ok' else 'No key'}{reset}\n"
    )

    if status.conflict:
        conflict_list = ", ".join(
            d.label for d in status.conflict_details
        )
        sys.stderr.write(
            f"{dim}    ⚠ {conflict_list} have different keys. "
            f"Using {active_src}.{reset}\n"
        )

    sys.stderr.write(f"{dim}  ────────────────────────────────────────────────────────────────{reset}\n")


def _find_auth_key(name: str, env_key_name: str = "") -> str:
    """使用 CredentialResolver 统一查找 API Key。"""
    from zmai.auth.resolver import CredentialResolver
    status = CredentialResolver().get_status(name)
    return status.api_key


# ═══════════════════════════════════════════════════════════════
# 主路由
# ═══════════════════════════════════════════════════════════════


def run_auth(argv: list[str]) -> None:
    """`zmai auth <setup|status|list|switch|update|remove|test>`"""
    from zmai.auth import AuthStore
    try:
        store = AuthStore()
    except CredentialError as e:
        print_error(str(e))
        sys.exit(1)
    theme = Theme.dark()

    if not argv:
        _run_auth_status()
        _print_setup_hint_if_needed()
        return

    sub = argv[0]
    if sub == "setup":
        configured = first_run_wizard()
        if not configured:
            sys.exit(1)

    elif sub == "status":
        _run_auth_status()

    elif sub == "list":
        rows = []
        for b in store.list_backends():
            active = "*" if b["active"] else " "
            model_s = b.get("model", "") or "-"
            timeout_s = str(b.get("timeout", "")) or "-"
            temp_s = str(b.get("temperature", "")) or "-"
            rows.append([f"{active} {b['name']}", model_s, b["key_preview"],
                          timeout_s, temp_s, b["verified_at"][:10] or "-"])
        if rows:
            print_table(["Backend", "Model", "Key", "Timeout", "Temp", "Verified"], rows, theme)
        else:
            print("no backends configured. run zmai auth.", file=sys.stderr)
    elif sub == "switch":
        if len(argv) < 2:
            print("usage: zmai auth switch <backend>", file=sys.stderr)
            sys.exit(1)
        name = argv[1]
        if store.set_active_backend(name):
            info = store.get_backend(name)
            m = f" ({info.get('model', '')})" if info and info.get('model') else ""
            print(f"switched to {name}{m}")
        else:
            print(f"backend '{name}' not configured", file=sys.stderr)
            sys.exit(1)
    elif sub == "update":
        if len(argv) < 2:
            print("usage: zmai auth update <backend> [api_key] [model] [base_url] [timeout] [max_tokens] [temperature]", file=sys.stderr)
            sys.exit(1)
        name = argv[1]
        existing = store.get_backend(name) or {}
        key = argv[2] if len(argv) > 2 else ""
        model_input = argv[3] if len(argv) > 3 else ""
        base_url = argv[4] if len(argv) > 4 else ""
        timeout = int(argv[5]) if len(argv) > 5 and argv[5].isdigit() else 0
        max_tokens = int(argv[6]) if len(argv) > 6 and argv[6].isdigit() else 0
        temperature = float(argv[7]) if len(argv) > 7 else 0.0
        if not key:
            sys.stderr.write(f"  API Key ({name})")
            if existing.get("api_key"):
                sys.stderr.write(f" [current: {existing['api_key'][:7]}...]")
            sys.stderr.write(": ")
            key = input().strip()
        if not key:
            print("API Key required", file=sys.stderr)
            sys.exit(1)
        store.set_backend(name, key, model=model_input, base_url=base_url,
                          timeout=timeout, max_tokens=max_tokens,
                          temperature=temperature, make_active=True)

        print(f"{name} saved to credential store.")
        print()

        from zmai.auth.resolver import CredentialResolver, _resolve_env_names
        env_key, _ = _resolve_env_names(name)
        status = CredentialResolver().get_status(name)
        if status.conflict:
            print(f"  ⚠ 环境变量 {env_key} 使用不同的 Key。")
            print(f"  当前运行时使用环境变量的 Key。")
            print(f"  如需使用刚保存的 Key：")
            print(f"    unset {env_key}")
    elif sub == "remove":
        if len(argv) < 2:
            print("usage: zmai auth remove <backend>", file=sys.stderr)
            sys.exit(1)
        if store.remove_backend(argv[1]):
            print(f"removed {argv[1]}")
        else:
            print(f"backend '{argv[1]}' not found", file=sys.stderr)
    elif sub == "doctor":
        from zmai.gateway.plugin import PluginRegistry

        reg = PluginRegistry()
        theme = Theme.dark()
        found = False

        for plugin in reg.list_plugins():
            api_key = _find_auth_key(plugin.name, plugin.env_api_key)
            if api_key:
                icon = theme.success("✓")
                status = theme.success("Configured")
            else:
                icon = theme.error("✗")
                status = theme.error("Missing")

            suffix = ""
            if not plugin.builtin:
                suffix = theme.dim(" (plugin)")

            print(f"  {plugin.label:20s} {icon}  {status}{suffix}")
            found = True

        if not found:
            print("  no backends registered")

    elif sub == "test":
        if len(argv) < 2:
            print("usage: zmai auth test <backend>", file=sys.stderr)
            sys.exit(1)
        _run_auth_test(argv[1])

    else:
        print(f"  Unknown subcommand: '{sub}'", file=sys.stderr)
        print(f"  Usage: zmai auth <setup|status|list|update|switch|remove|test>", file=sys.stderr)
        sys.exit(1)
