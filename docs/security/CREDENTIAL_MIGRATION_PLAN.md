# Credential Migration Plan

> 从当前系统迁移到开源架构的完整计划
> 日期：2026-07-18

---

## 目录

1. [当前状态 vs 目标状态](#1-当前状态-vs-目标状态)
2. [迁移阶段](#2-迁移阶段)
3. [Phase 1 — 核心 Resolver 重构](#3-phase-1--核心-resolver-重构)
4. [Phase 2 — CLI 行为修正](#4-phase-2--cli-行为修正)
5. [Phase 3 — 开源安全扫描](#5-phase-3--开源安全扫描)
6. [Phase 4 — Backend 统一错误处理](#6-phase-4--backend-统一错误处理)
7. [Phase 5 — 测试体系](#7-phase-5--测试体系)
8. [Phase 6 — 文档与示例](#8-phase-6--文档与示例)
9. [向后兼容](#9-向后兼容)

---

## 1. 当前状态 vs 目标状态

### 1a. 当前状态（迁移前）

```
已完成的修复:
  ✅ 文件式稳定密钥 (~/.zmai/credentials.key)
  ✅ 解密失败抛出 CredentialError（不再静默）
  ✅ CredentialBundle 增强（per-source 字段 + 冲突检测）
  ✅ 所有消费者统一使用 CredentialResolver
  ✅ zmai auth 显示状态（不弹向导）
  ✅ zmai auth update 同步进程环境变量
  ✅ 未知子命令显示帮助

已知问题:
  ❌ zmai auth update 修改进程环境变量（违反"不修改 Shell"原则）
  ❌ 没有 zmai auth test（即时验证）
  ❌ 没有 zmai auth setup（专有命令）
  ❌ Backend HTTP 错误仍然可能暴露原始消息
  ❌ 没有开源安全检查
  ❌ 没有国际化消息
  ❌ 缺少测试覆盖
```

### 1b. 目标状态（迁移后）

```
预期完成:
  ✅ CredentialResolver 是唯一凭据入口
  ✅ zmai auth update 只写文件，不修改环境变量
  ✅ zmai auth test 即时验证 Key
  ✅ zmai auth setup 专用配置向导
  ✅ 冲突检测显式警告
  ✅ 所有 HTTP 错误映射为用户可理解消息
  ✅ 开源安全检查通过（无 Key 泄露）
  ✅ 测试覆盖 10+ 场景
  ✅ 中英文消息结构就绪
```

---

## 2. 迁移阶段

| Phase | 名称 | 工作量 | 风险 | 依赖 |
|-------|------|--------|------|------|
| 1 | 核心 Resolver 重构 | 中 | 低 | 无 |
| 2 | CLI 行为修正 | 小 | 低 | Phase 1 |
| 3 | 开源安全扫描 | 小 | 中 | 无 |
| 4 | Backend 统一错误处理 | 中 | 低 | Phase 1 |
| 5 | 测试体系 | 中 | 低 | Phase 1-4 |
| 6 | 文档与示例 | 小 | 低 | Phase 1-5 |

### 2a. 依赖图

```
Phase 1 (Resolver)
  ├── Phase 2 (CLI)
  ├── Phase 4 (Backend Errors)
  └── Phase 5 (Tests)

Phase 3 (Security) → Phase 6 (Docs)

Phase 2 → Phase 4
Phase 4 → Phase 5
Phase 5 → Phase 6
```

### 2b. 总工作量估算

| Phase | 文件 | 新增代码 | 修改代码 | 估算工时 |
|-------|------|---------|---------|---------|
| 1 | 3 | 50 | 30 | 2h |
| 2 | 1 | 30 | 20 | 1h |
| 3 | 2 | 20 | 10 | 1h |
| 4 | 2 | 40 | 20 | 1.5h |
| 5 | 2 | 100 | 0 | 2h |
| 6 | 3 | 60 | 0 | 1h |
| **合计** | **13** | **300** | **80** | **~8h** |

---

## 3. Phase 1 — 核心 Resolver 重构

### 3a. 改动文件

| 文件 | 改动内容 |
|------|---------|
| `src/zmai/auth/status.py` | 字段命名统一（`credential_store_status` / `key_mask` → `****`） |
| `src/zmai/auth/resolver.py` | 移除 `config` 参数支持；移除 `resolve()` 弃用方法；`inject_to_env()` 精简；新增 `msg()` 国际化函数 |
| `src/zmai/auth/__init__.py` | 导出更新 |

### 3b. status.py 字段对齐

```python
# 当前字段                                # 目标字段
credentials_file_status                   credential_store_status
file_key                                  (保留内部使用)
env_var_name                              env_var_name (不变)
key_preview                               key_mask (改名前缀)
Verification: 无                          verification: "unknown" | "valid" | "invalid"
```

### 3c. resolver.py 清理

```python
# 删除:
class CredentialResolver:
    def __init__(self, config=None):  # config 参数已弃用
        ...
    def resolve(self, name):          # 已弃用
        ...

# 保留:
class CredentialResolver:
    def __init__(self):               # 无参数
        ...
    def get_status(self, name):       # 唯一入口
        ...
    def inject_to_env(self, name=None):  # 后台注入
        ...
```

### 3d. 变更 `zmai auth update` — 不修改环境变量

```python
# 当前（阶段 2 已实现的临时方案）:
store.set_backend(name, key, ...)
os.environ[env_key] = key  # ← 违反"不修改 Shell"原则

# 目标（Phase 1 最终）:
store.set_backend(name, key, ...)
# 不修改环境变量
# 输出提示让用户自己决定
print(f"{name} saved to credential store.")
print(f"To use this key, unset the environment variable:")
print(f"  unset {env_key}")
```

---

## 4. Phase 2 — CLI 行为修正

### 4a. 改动文件

| 文件 | 改动内容 |
|------|---------|
| `src/zmai/cli/main.py` | `zmai auth update` 不设 env var；`zmai auth setup` 完善；`zmai auth test` 实现；显示状态时使用 `CredentialStatus` |

### 4b. `zmai auth update` 最终行为

```python
elif sub == "update":
    name = argv[1]
    key = argv[2] if len(argv) > 2 else input("API Key: ").strip()
    if not key:
        sys.exit(1)

    store.set_backend(name, key, ..., make_active=True)

    # 不修改环境变量！
    print(f"{name} saved to credential store.")

    # 检测是否有冲突
    status = CredentialResolver().get_status(name)
    if status.conflict:
        print()
        print(f"  ⚠ 环境变量 {status.env_var_name} 使用不同的 Key。")
        print(f"  当前运行时使用: {status.env_var_name}")
        print(f"  如需使用刚保存的 Key:")
        print(f"    unset {status.env_var_name}")
```

### 4c. `zmai auth test` 实现

```python
elif sub == "test":
    name = argv[1] if len(argv) > 1 else error("test <backend>")
    _run_auth_test(name)
```

```python
def _run_auth_test(name: str) -> None:
    """测试指定 Backend 的 API Key 有效性。"""
    from zmai.auth.resolver import CredentialResolver
    from zmai.auth.status import mask_key

    status = CredentialResolver().get_status(name)

    print(f"  Testing {name}...")
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

    print(f"    Source : {_source_display(status)}")
    print(f"    Key    : {mask_key(status.api_key)}")
    print()

    # 发送最小 API 请求
    plugin = _find_plugin(name)
    if not plugin:
        print(f"  Unknown backend: {name}")
        return

    verify_url = plugin.verify_url or ""
    if not verify_url:
        print(f"  No verification endpoint configured for {name}.")
        return

    print(f"  Sending API request...")
    print(f"    URL    : {verify_url}")
    print(f"    Method : {plugin.verify_method or 'GET'}")
    print()

    try:
        req = _build_verify_request(verify_url, plugin, status.api_key)
        resp = urllib.request.urlopen(req, timeout=15)
        print(f"  Result : ✅ PASS")
        print(f"  Model  : {status.model or plugin.default_model or '-'}")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:200]
        print(f"  Result : ❌ FAIL")
        print(f"  Status : {e.code} {_http_reason(e.code)}")
        if e.code == 401:
            print(f"  Reason : API Key is invalid.")
        elif e.code == 403:
            print(f"  Reason : API Key may be expired.")
        elif e.code == 429:
            print(f"  Reason : Rate limited.")
        else:
            print(f"  Reason : {_extract_error_message(body)}")
        print()
        print(f"  Run `zmai auth update {name}` with a valid key.")
    except urllib.error.URLError as e:
        print(f"  Result : ❌ FAIL")
        print(f"  Reason : Cannot connect to API server.")
        print(f"  Detail : {e.reason}")
    except Exception as e:
        print(f"  Result : ❌ FAIL")
        print(f"  Reason : {str(e)[:100]}")
```

---

## 5. Phase 3 — 开源安全扫描

### 5a. Pre-commit Hook

```yaml
# .pre-commit-config.yaml

repos:
  - repo: local
    hooks:
      - id: check-api-keys
        name: Check for API keys in code
        entry: >
          bash -c '
          if grep -rn "sk-" --include="*.py" --include="*.md" --include="*.json" --include="*.yaml" --include="*.yml" --include="*.toml" --include="*.cfg" --include="*.ini" --include="*.env" --include="*.example" --include="*.sample" --include="*.txt" src/ docs/ examples/ tests/ | grep -v "__pycache__" | grep -v "\.git" | grep -v "sk-.*placeholder\|sk-your\|sk-test\|YOUR_API_KEY\|replace_me"; then
          echo "ERROR: Found potential API keys in source code!"
          exit 1; fi'
        language: system
        stages: [commit]
```

### 5b. CI Check

```yaml
# .github/workflows/security.yml

name: Security Check
on: [push, pull_request]

jobs:
  check-api-keys:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Check for API keys
        run: |
          set +e
          FOUND=$(grep -rn "sk-" \
            --include="*.py" --include="*.md" --include="*.json" \
            --include="*.yaml" --include="*.yml" --include="*.toml" \
            --include="*.txt" --include="*.cfg" --include="*.ini" \
            --include="*.env" --include="*.example" --include="*.sample" \
            --exclude-dir="__pycache__" --exclude-dir=".git" \
            --exclude-dir="node_modules" --exclude-dir=".venv" \
            --exclude-dir=".claude" \
            . | grep -v "__pycache__" | grep -v "\.git" | \
            grep -v "sk-your\|sk-test\|YOUR_API_KEY\|replace_me\|placeholder")
          if [ -n "$FOUND" ]; then
            echo "ERROR: Potential API keys found:"
            echo "$FOUND"
            exit 1
          fi
          echo "No API keys found in source code."
```

### 5c. 扫描范围

| 路径 | 包含 | 排除 |
|------|------|------|
| `src/` | `.py` | `__pycache__` |
| `docs/` | `.md` | - |
| `examples/` | `.py`, `.md` | - |
| `tests/` | `.py`, `.json` | 测试夹具中的占位符 |
| `README.md` | `.md` | - |
| `pyproject.toml` | `.toml` | - |
| `.github/` | `.yml` | - |

### 5d. 扫描内容

```
模式:
  sk-               (DeepSeek / Claude / OpenAI Key 前缀)
  ANTHROPIC_API_KEY=(环境变量赋值)
  DEEPSEEK_API_KEY= (环境变量赋值)
  OPENAI_API_KEY=   (环境变量赋值)
  GEMINI_API_KEY=   (环境变量赋值)
  AIza              (Gemini Key 前缀)

排除:
  sk-your    (示例)
  sk-test    (测试)
  YOUR_API_KEY (文档占位符)
  replace_me    (文档占位符)
  placeholder   (测试占位符)
```

### 5e. `MANIFEST.in`

```
# 确保 PyPI 包不包含凭据文件
include README.md
include LICENSE
recursive-include src/ *.py
recursive-exclude * credentials
recursive-exclude * credentials.key
recursive-exclude * .zmai
global-exclude .gitignore
```

---

## 6. Phase 4 — Backend 统一错误处理

### 6a. 改动文件

| 文件 | 改动内容 |
|------|---------|
| `src/zmai/gateway/errors.py` | 完善 `friendly_http_error()`，增加国际化消息 |
| `src/zmai/gateway/backends/*.py` | 使用统一的错误处理，不拼接原始错误消息 |
| `src/zmai/runtime/preflight.py` | 使用 `CredentialStatus` 显示友好错误 |

### 6b. `friendly_http_error` 增强

```python
def friendly_http_error(
    http_code: int,
    provider: str,
    model: str,
    env_key: str,
    raw_body: str = "",
) -> str:
    """将 HTTP 错误映射为用户可理解的错误消息。

    绝不输出原始 API 响应体。
    绝不输出完整 API Key。
    """
    if http_code == 401:
        return (
            f"[KEY_INVALID] {provider}: API Key 无效。\n"
            f"请运行 `zmai auth update {provider}` 更新 Key，\n"
            f"或者设置环境变量 {env_key}。"
        )
    if http_code == 403:
        body_lower = raw_body.lower()
        if any(kw in body_lower for kw in ("expired", "disabled", "deactivated")):
            return (
                f"[KEY_EXPIRED] {provider}: API Key 已过期或已被禁用。\n"
                f"请在 {provider} 官网重新生成 Key。"
            )
        return (
            f"[KEY_INVALID] {provider}: API Key 权限不足 (HTTP 403)。\n"
            f"请检查 Key 是否有足够权限。"
        )
    if http_code == 429:
        return (
            f"[RATE_LIMITED] {provider}: 请求过于频繁 (HTTP 429)。\n"
            f"请稍后重试，或降低请求频率。"
        )
    if http_code >= 500:
        return (
            f"[SERVER_ERROR] {provider}: 服务器内部错误 (HTTP {http_code})。\n"
            f"请稍后重试。"
        )
    return (
        f"[BACKEND_ERROR] {provider}: 返回错误 (HTTP {http_code})。\n"
        f"请稍后重试。"
    )
```

### 6c. Backend 实现示例

```python
# src/zmai/gateway/backends/deepseek.py

def invoke(self, request: BackendRequest) -> BackendResponse:
    # ... 构造请求 ...
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        logger.debug("%s API HTTP %d", self.name, e.code)  # 仅日志级别，不包含 body
        raise BackendError(
            friendly_http_error(e.code, self.name, self._model, self._env_key, err_body),
            status_code=e.code,
        )
    except urllib.error.URLError as e:
        raise BackendError(
            f"[NETWORK_ERROR] {self.name}: 无法连接到 API 服务器。\n"
            f"请检查网络连接和代理设置。"
        )
```

---

## 7. Phase 5 — 测试体系

### 7a. 新增测试文件

```
tests/test_auth/
  ├── __init__.py
  ├── test_credential_resolver.py    # 统一凭据解析
  ├── test_auth_store.py             # 加密文件 I/O
  ├── test_credential_bundle.py      # 数据结构
  ├── test_cli.py                    # CLI 命令行为
  └── fixtures/
       ├── credentials_valid.json    # 有效凭据文件（加密）
       ├── credentials_corrupted.txt # 损坏的凭据文件
       └── config_valid.json         # 有效 Config 文件
```

### 7b. 测试场景矩阵

| # | 测试场景 | 预期结果 | 文件 |
|---|---------|---------|------|
| 1 | 用户 A 和用户 B 凭据互相隔离 | A 看不到 B 的 Key | `test_auth_store.py` |
| 2 | 环境变量优先级高于凭据文件 | active_source=environment | `test_credential_resolver.py` |
| 3 | 凭据文件 Key（无环境变量） | active_source=credential_store | `test_credential_resolver.py` |
| 4 | 冲突检测：file=sk-A, env=sk-B | conflict=True | `test_credential_resolver.py` |
| 5 | 冲突检测：file=sk-A, env=sk-A | conflict=False (同 Key) | `test_credential_resolver.py` |
| 6 | Key 缺失 | configured=False, source=missing | `test_credential_resolver.py` |
| 7 | 凭据文件损坏 | credential_store_status=corrupted | `test_auth_store.py` |
| 8 | 解密失败（密钥不匹配） | CredentialError raised | `test_auth_store.py` |
| 9 | Key 格式验证（sk- 前缀） | key_valid_format=True | `test_credential_bundle.py` |
| 10 | API Key 不在日志中 | grep 日志无 sk- 模式 | `test_cli.py` |
| 11 | `zmai auth` 无参数时显示状态 | 显示配置列表 | `test_cli.py` |
| 12 | `zmai auth setup` 进入向导 | 显示提供商选择 | `test_cli.py` |
| 13 | `zmai auth update` 只写文件 | 环境变量不变 | `test_cli.py` |
| 14 | `zmai auth test` 验证 Key | 显示 PASS/FAIL | `test_cli.py` |
| 15 | `zmai auth` 未知子命令 | 显示帮助 | `test_cli.py` |
| 16 | 后台注入不覆盖已设环境变量 | env var 不变 | `test_credential_resolver.py` |

### 7c. 测试隔离方案

```python
# 所有测试使用临时目录，不修改真实 ~/.zmai

@pytest.fixture
def isolated_zmai_dir(tmp_path):
    """创建一个隔离的 ~/.zmai 目录。"""
    home = tmp_path / "home"
    home.mkdir()
    zmai_dir = home / ".zmai"
    zmai_dir.mkdir()

    # 使用 monkeypatch 重定向 Path.home()
    with monkeypatch.context() as m:
        m.setattr(Path, "home", lambda: home)
        yield zmai_dir
```

---

## 8. Phase 6 — 文档与示例

### 8a. 新增文档

```
docs/
  ├── CONFIGURATION.md       # 如何配置 API Key
  ├── SECURITY.md             # 安全策略
  └── TROUBLESHOOTING.md      # 常见问题
```

### 8b. CONFIGURATION.md 概要

```markdown
# Configuring API Keys

ZMAI never bundles API keys.

## Quick Start

```bash
zmai auth setup
# Follow the prompts
```

## Manual Configuration

```bash
zmai auth update deepseek
# Enter your key when prompted
```

Or via environment variables:

```bash
export DEEPSEEK_API_KEY="sk-..."
zmai "your task"
```

## Checking Status

```bash
zmai auth status
```
```

### 8c. SECURITY.md 概要

```markdown
# Security

## API Key Safety

- ZMAI never stores keys in source code
- ZMAI never sends keys to any remote service
- ZMAI never displays full keys in the terminal
- Keys are stored encrypted at `~/.zmai/credentials`

## Reporting a Vulnerability

...
```

---

## 9. 向后兼容

### 9a. 用户数据兼容

| 数据 | 兼容策略 |
|------|---------|
| `~/.zmai/credentials` | 格式不变，version 字段可选升级到 v2 |
| `~/.zmai/credentials.key` | 已有文件直接使用，无需迁移 |
| `~/.zmai/config.json` | 格式不变 |
| `zmai.json` | 格式不变 |

### 9b. CLI 命令兼容

| 命令 | 兼容策略 |
|------|---------|
| `zmai auth` | 行为已改为显示状态（不弹向导） |
| `zmai auth setup` | 新增 |
| `zmai auth status` | 不变 |
| `zmai auth list` | 不变 |
| `zmai auth update` | 行为不变，但不修改环境变量 |
| `zmai auth remove` | 不变 |
| `zmai auth switch` | 不变 |
| `zmai auth test` | 不变（可增强） |
| `zmai auth doctor` | 保留，标记弃用 |
| `zmai doctor` | 不变 |

### 9c. Python API 兼容

| API | 兼容策略 |
|-----|---------|
| `CredentialResolver()` | 无参数构造（config 参数已移除） |
| `CredentialResolver.get_status()` | 新增主要方法 |
| `CredentialResolver.resolve()` | 已移除（使用 get_status） |
| `AuthStore` | 不变 |
| `CredentialBundle` | 不变（内部使用） |
| `CredentialStatus` | 新增 |

### 9d. 主要破坏性变更

| 变更 | 影响 | 应对 |
|------|------|------|
| `CredentialResolver(config=...)` 已移除 | 如果外部代码传入 config | 改为无参数调用 |
| `zmai auth update` 不设环境变量 | 更新后当前进程 Key 不生效 | 用户手动 `unset` 或开新终端 |
| `zmai auth` 显示状态而非向导 | 用户习惯改变 | 使用 `zmai auth setup` |

---

*本文件定义了从当前系统到开源架构的完整迁移路径。*
*实现按 Phase 顺序执行，每个 Phase 完成后可独立发布。*
