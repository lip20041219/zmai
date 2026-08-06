# ZMAI Auth System Design v2.0

Version: 2.0
Date: 2026-07-16

> **重新设计凭证管理系统。** 目标：用户在终端输入 `zmai` 即可完成初始化，零手动配置。
>
> **不修改 Runtime / Agent / Gateway / Workflow / Workspace / Memory 模块。**
>
> **仅优化认证流程、存储层、CLI 交互。**

---

## 目录

1. [现状与问题](#1-现状与问题)
2. [设计原则](#2-设计原则)
3. [系统架构](#3-系统架构)
4. [存储层设计](#4-存储层设计)
5. [自动初始化流程](#5-自动初始化流程)
6. [命令设计](#6-命令设计)
7. [Backend 支持矩阵](#7-backend-支持矩阵)
8. [密钥验证机制](#8-密钥验证机制)
9. [安全设计](#9-安全设计)
10. [迁移方案](#10-迁移方案)
11. [文件清单与实现计划](#11-文件清单与实现计划)

---

## 1. 现状与问题

### 1.1 当前实现

现有 `src/zmai/auth/store.py` 的存储方案：

```
凭证 → XOR 加密 → ~/.zmai/credentials（本地文件）
         ↑
   机器标识派生密钥（MachineGuid / /etc/machine-id）
```

### 1.2 存在问题

| 问题 | 严重程度 | 说明 |
|------|---------|------|
| XOR 加密本质是**混淆** | 🔴 | 异或加密可被几行 Python 破解，不提供真正安全 |
| 机器密钥可预测 | 🔴 | MachineGuid 和 machine-id 都是固定值，非随机 |
| 密钥在文件系统中明文可读 | 🟡 | 加密文件虽然不可直接读，但解密密钥可从本地推导 |
| 无真实密钥验证 | 🟡 | 仅验证 HTTP 连接，未验证 token 有效性 |
| 无 macOS / Linux 安全存储 | 🟡 | 仅使用文件存储，未利用 OS 级密钥链 |
| 缺失 Gemini 支持 | 🟡 | 不支持 Google Gemini API Key 管理 |
| 缺少 `auth verify` 命令 | 🟡 | 用户无法手动验证密钥是否有效 |
| 初始化向导在 main.py 中 | 🔴 | 认证逻辑与 CLI 入口耦合，非独立模块 |

### 1.3 根因

认证模块 (`auth/store.py`) 设计时以"文件加密"为核心，而非以"操作系统安全存储"为核心。

**改变方向：**
- OS 安全存储（Windows Credential Manager / macOS Keychain / Linux Secret Service）作为主力
- 加密文件作为降级方案
- 环境变量作为兼容覆盖（只读，不写入）

---

## 2. 设计原则

### 2.1 零手动配置

```
用户输入: $ zmai
                   ↓
自动检测环境变量 → 自动检测 OS Keychain → 自动检测旧凭证文件
          ↓
     都未找到 → 启动交互式初始化向导
          ↓
 用户输入一次 API Key → 验证成功 → 存入 OS Keychain → 立即进入 REPL
```

**用户永远不需要手动设置环境变量或编辑配置文件。**

### 2.2 OS 安全存储优先

```
存储层级（按优先级）：
 ① 环境变量（只读，不写）—— 最高优先级覆盖，兼容 CI/CD
 ② OS Credential Manager —— 主力存储，每条 Backend 一个条目
 ③ 加密文件 —— 降级方案（当 OS Keychain 不可用时）
 ④ 明文文件 —— 最终降级（带警告）
```

### 2.3 一次配置，全局可用

```
凭证位置：~/.zmai/          ← 用户主目录（所有项目共享）
项目位置：./zmai.json       ← 配置 Backend 名称/模型（非敏感）

换项目 → 不需要重新输入 Key
换终端 → 不需要重新设置环境变量
换机器 → 仅需重新配一次
```

### 2.4 不修改下游

```
修改范围：
  src/zmai/auth/*           ← 认证模块
  src/zmai/cli/main.py      ← 集成点（注入 + 命令注册）
  src/zmai/cli/formatters.py ← 可复用

不修改：
  src/zmai/runtime/*        ✗
  src/zmai/gateway/*        ✗
  src/zmai/agent/*          ✗
  src/zmai/workspace/*      ✗
  src/zmai/memory/*         ✗
  src/zmai/workflow/*       ✗
  src/zmai/swe/*            ✗
```

---

## 3. 系统架构

### 3.1 整体架构

```
┌─────────────────────────────────────────────────────────┐
│                      CLI 层                               │
│                                                          │
│  main.py                                                 │
│    ├── _auto_detect_auth()     ← 启动时自动检测             │
│    ├── _run_init_wizard()      ← 交互式初始化向导           │
│    └── zmai auth <sub>         ← auth 子命令               │
│                                                          │
│  auth/                                                    │
│    ├── store.py                ← AuthStore 统一入口        │
│    ├── keychain.py             ← OS 安全存储适配器          │
│    │   ├── WinCredManager      ← Windows Credential Mgr    │
│    │   ├── MacKeychain         ← macOS Keychain            │
│    │   └── LinuxSecretService  ← libsecret / secret-tool   │
│    ├── verify.py               ← API Key 验证              │
│    └── detect.py               ← 自动检测可用凭证           │
│                                                          │
└───────────┬─────────────────────────────────────────────┘
            │ 通过 env 注入 API Key
            ▼
┌─────────────────────────────────────────────────────────┐
│                    Gateway 层（不变）                     │
│                                                          │
│  ClaudeBackend    ← 读取 ANTHROPIC_API_KEY               │
│  DeepSeekBackend  ← 读取 DEEPSEEK_API_KEY                │
│  OpenAIBackend    ← 读取 OPENAI_API_KEY                  │
│  GeminiBackend    ← 读取 GEMINI_API_KEY                  │
│                                                          │
└─────────────────────────────────────────────────────────┘
```

### 3.2 数据流

```
写入流程：
  CLI 输入 API Key → 验证 (verify.py) → 存入 OS Keychain (keychain.py) → 确认

读取流程：
  CLI 启动 → detect.py 检测 →
    ① 环境变量存在？→ 直接用（跳过存储）
    ② OS Keychain 有？→ 取出 → 注入环境变量 → Gateway 使用
    ③ 旧加密文件有？→ 迁移到 OS Keychain → 注入

验证流程：
  zmai auth verify [backend]
    → 从存储读取 API Key
    → 调用各 Backend 的 ListModels / 简单 chat 接口
    → 返回验证结果（成功/失败/错误详情）
```

### 3.3 与 Gateway 的适配（关键）

Gateway 层的 Backend 实现（`ClaudeBackend`、`DeepSeekBackend`）当前从**环境变量**读取 API Key：

```python
# claude.py 第 53 行
self._api_key = self._config.get("api_key", os.environ.get("ANTHROPIC_API_KEY", ""))
```

**设计中不修改 Gateway 层。** 适配方案：

```python
# auth/detect.py
def inject_credentials():
    """从安全存储读取凭证，注入环境变量。
    
    Gateway 层无感知 — 它读取的是环境变量。
    用户无感知 — 自动完成。
    """
    store = AuthStore()
    for name, var, default_model in [
        ("anthropic", "ANTHROPIC_API_KEY", "claude-sonnet-4-6"),
        ("deepseek",  "DEEPSEEK_API_KEY",  "deepseek-chat"),
        ("openai",    "OPENAI_API_KEY",    "gpt-4o"),
        ("gemini",    "GEMINI_API_KEY",    "gemini-2.0-flash"),
    ]:
        if os.environ.get(var):
            continue  # 环境变量已存在，优先（CI/CD 场景）
        info = store.get_backend(name)
        if info and info.get("api_key"):
            os.environ[var] = info["api_key"]
            if info.get("model"):
                os.environ[f"{name.upper()}_MODEL"] = info["model"]
```

**这保持了所有 Gateway 层的不变性。** Gateway 只感知环境变量。

---

## 4. 存储层设计

### 4.1 层级结构与降级逻辑

```
AuthStore.get_backend(name)
    │
    ├── 检查 ENV 变量 → 有？→ 直接返回（只读覆盖）
    │
    ├── 检查 OS Keychain → 有？→ 返回
    │   ├── Windows: Credential Manager (Win32 API)
    │   ├── macOS: Keychain (security CLI)
    │   └── Linux: Secret Service (secret-tool CLI)
    │
    ├── 检查加密文件 → 有？→ 迁移到 OS Keychain → 返回
    │   └── ~/.zmai/credentials (AES-GCM, 降级方案)
    │
    └── 检查明文文件 → 有？→ 迁移并警告 → 返回
        └── ~/.zmai/credentials.plain (最终降级)
```

### 4.2 OS Keychain 适配器

```python
# auth/keychain.py
import os
import sys
import subprocess
import ctypes
from abc import ABC, abstractmethod
from typing import Any

SERVICE_NAME = "zmai"


class KeychainBackend(ABC):
    """OS 安全存储适配器基类。"""

    @abstractmethod
    def get(self, name: str) -> str | None:
        """读取凭证。返回 API Key 或 None。"""
        ...

    @abstractmethod
    def set(self, name: str, api_key: str) -> None:
        """保存凭证。"""
        ...

    @abstractmethod
    def delete(self, name: str) -> bool:
        """删除凭证。"""
        ...

    @staticmethod
    def available() -> bool:
        """当前平台是否支持此 Keychain。"""
        ...


class WinCredManager(KeychainBackend):
    """Windows Credential Manager (Win32 API)。"""

    # 使用 ctypes 调用 advapi32.CredWriteW / CredReadW / CredDeleteW
    # Credential 目标格式: "zmai:anthropic" / "zmai:deepseek"

    @staticmethod
    def available() -> bool:
        return sys.platform == "win32"

    def get(self, name: str) -> str | None:
        try:
            # CredReadW → 解密 → 返回
            ...
        except Exception:
            return None

    def set(self, name: str, api_key: str) -> None:
        # CredWriteW → CRED_TYPE_GENERIC
        ...

    def delete(self, name: str) -> bool:
        # CredDeleteW
        ...


class MacKeychain(KeychainBackend):
    """macOS Keychain (通过 security CLI)。"""

    @staticmethod
    def available() -> bool:
        return sys.platform == "darwin"

    def get(self, name: str) -> str | None:
        try:
            result = subprocess.run(
                ["security", "find-generic-password",
                 "-s", SERVICE_NAME, "-a", name, "-w"],
                capture_output=True, text=True, timeout=5,
            )
            return result.stdout.strip() if result.returncode == 0 else None
        except Exception:
            return None

    def set(self, name: str, api_key: str) -> None:
        subprocess.run(
            ["security", "add-generic-password",
             "-s", SERVICE_NAME, "-a", name, "-w", api_key, "-U"],
            capture_output=True, timeout=5,
        )

    def delete(self, name: str) -> bool:
        result = subprocess.run(
            ["security", "delete-generic-password",
             "-s", SERVICE_NAME, "-a", name],
            capture_output=True, timeout=5,
        )
        return result.returncode == 0


class LinuxSecretService(KeychainBackend):
    """Linux Secret Service (通过 secret-tool CLI)。"""

    @staticmethod
    def available() -> bool:
        if sys.platform != "linux":
            return False
        try:
            subprocess.run(["secret-tool", "--help"],
                           capture_output=True, timeout=3)
            return True
        except FileNotFoundError:
            return False

    def get(self, name: str) -> str | None:
        try:
            result = subprocess.run(
                ["secret-tool", "lookup", "service", SERVICE_NAME, "account", name],
                capture_output=True, text=True, timeout=5,
            )
            return result.stdout.strip() if result.returncode == 0 else None
        except Exception:
            return None

    def set(self, name: str, api_key: str) -> None:
        subprocess.run(
            ["secret-tool", "store", "--label=f"ZMAI {name}",
             "service", SERVICE_NAME, "account", name],
            input=api_key, text=True, capture_output=True, timeout=5,
        )

    def delete(self, name: str) -> bool:
        subprocess.run(
            ["secret-tool", "clear", "service", SERVICE_NAME, "account", name],
            capture_output=True, timeout=5,
        )
        return True
```

### 4.3 降级加密文件

当 OS Keychain 不可用时，使用加密文件（AES-GCM）：

```python
# 仅当 OS Keychain 不可用时创建
# ~/.zmai/credentials  — AES-GCM 加密，密钥派生自机器标识

# 密钥派生（改进版）：
from hashlib import scrypt
import secrets

# 启动时生成随机 salt，保存在 ~/.zmai/.salt
salt = _load_or_create_salt()  # 每个机器不同
key = scrypt(
    password=b"zmai-static-pepper",  # 配合 salt 防止彩虹表
    salt=salt,
    n=2**14, r=8, p=1,
    dklen=32,
)
```

**关键改进：** 使用 `scrypt` 替代 XOR，salt 文件随机生成，不可预测。

### 4.4 数据格式

```json
// ~/.zmai/credentials (AES-GCM 加密前的明文格式)
{
  "version": 2,
  "backends": {
    "anthropic": {
      "api_key": "sk-ant-xxx",
      "model": "claude-sonnet-4-6",
      "verified_at": "2026-07-16T10:00:00Z",
      "created_at": "2026-07-16T09:00:00Z"
    },
    "deepseek": {
      "api_key": "sk-xxx",
      "model": "deepseek-chat",
      "verified_at": "2026-07-15T14:30:00Z",
      "created_at": "2026-07-15T14:00:00Z"
    },
    "openai": {
      "api_key": "sk-xxx",
      "model": "gpt-4o",
      "verified_at": "",
      "created_at": "2026-07-16T10:00:00Z"
    },
    "gemini": {
      "api_key": "AIza...",
      "model": "gemini-2.0-flash",
      "verified_at": "",
      "created_at": "2026-07-16T10:00:00Z"
    }
  },
  "active_backend": "deepseek",
  "updated_at": "2026-07-16T10:00:00Z"
}
```

---

## 5. 自动初始化流程

### 5.1 首次运行检测链

```
$ zmai
    │
    ▼ detect.py:detect_credentials()
    │
    ├── 1. 检查环境变量
    │   ├── ANTHROPIC_API_KEY → 有 → ✅ (跳过初始化)
    │   ├── DEEPSEEK_API_KEY  → 有 → ✅ (跳过初始化)
    │   ├── OPENAI_API_KEY    → 有 → ✅ (跳过初始化)
    │   └── GEMINI_API_KEY    → 有 → ✅ (跳过初始化)
    │
    ├── 2. 检查 OS Keychain
    │   ├── Windows Credential Manager → 有条目 → ✅ (跳过初始化)
    │   ├── macOS Keychain → 有条目 → ✅ (跳过初始化)
    │   └── Linux secret-tool → 有条目 → ✅ (跳过初始化)
    │
    ├── 3. 检查加密文件
    │   ├── ~/.zmai/credentials → 有 → 迁移到 OS Keychain → ✅
    │   └── 无 → ⬇
    │
    └── 4. 均未找到 → 进入初始化向导
```

### 5.2 初始化向导

```python
# auth/wizard.py (新增)

class InitWizard:
    """交互式初始化向导。"""

    def __init__(self, theme: Theme):
        self._theme = theme

    def run(self) -> bool:
        """执行向导。返回 True 表示配置成功。"""
        self._print_header()
        backend = self._select_backend()
        if not backend:
            return False
        api_key = self._input_api_key(backend)
        if not api_key:
            return False
        model = self._select_model(backend)
        success = self._verify_and_save(backend, api_key, model)
        if success:
            self._print_success(backend)
        return success

    def _select_backend(self) -> str | None:
        """选择 Backend 类型。自动检测已有的 Key（编排而非重复输入）。"""
        ...
```

**用户看到的界面：**

```
$ zmai

  ⚡ ZMAI v0.1.0 — 首次启动
  ─────────────────────────────────────────────────────
  未检测到 API Key。

  需要配置至少一个 Backend 才能使用 ZMAI。

  支持的 Backend:
    [1] Claude     (claude-sonnet-4-6)
    [2] DeepSeek   (deepseek-chat)
    [3] OpenAI     (gpt-4o)
    [4] Google Gemini (gemini-2.0-flash)

  选择 [1-4，默认 2]:

  ─────────────────────────────────────────────────────
  DeepSeek API Key:
  （输入密钥，输入为空则跳过）

  ─────────────────────────────────────────────────────
  ⟳ 正在验证 DeepSeek API Key ...
  ✅ 验证成功 (0.3s)

  设为默认 Backend？ [Y/n]:

  ─────────────────────────────────────────────────────
  ✅ 配置完成！

  您随时可以输入以下命令管理凭证：
    zmai auth list        查看已配置的 Backend
    zmai auth switch      切换默认 Backend
    zmai auth update      更新 API Key

  现在开始使用：
    zmai 分析这个项目
  ─────────────────────────────────────────────────────

  zmai>
```

### 5.3 关键设计：退出向导后可立即使用

```python
def _run_init_wizard():
    wizard = InitWizard()
    if wizard.run():
        # 向导成功后立即注入凭证到环境
        inject_credentials()
        # 然后直接进入 REPL，不退出
        return True
    return False
```

**用户在向导完成后不需要重新运行 `zmai`。** 直接进入 REPL。

### 5.4 跳过检测

用户可以用 `--skip-auth` 跳过初始化（例如只想看帮助）：

```bash
zmai --skip-auth --help
```

但这不在正常使用路径中。

---

## 6. 命令设计

### 6.1 命令集

```
# ── 配置管理 ──────────────────────────────────────
zmai auth                       交互式配置向导（同首次运行）
zmai auth list                  列出所有已配置 Backend 及状态
zmai auth switch <backend>      切换默认 Backend
zmai auth update <backend>      更新 API Key（重新验证）
zmai auth remove <backend>      删除凭证
zmai auth verify [backend]      验证 API Key 是否有效

# ── 别名 ─────────────────────────────────────────
zmai login                      ≡ zmai auth
zmai logout                     删除所有凭证（需确认）
```

### 6.2 查看凭证列表

```
$ zmai auth list

  Backend      Status     Model                Key            Verified
  ─────────────────────────────────────────────────────────────────────
  ● deepseek   ✅ active  deepseek-chat        sk-*****b6125  今天 10:00
  ○ anthropic  ✅ ready   claude-sonnet-4-6    sk-*****a3f2e  昨天 14:30
  ○ openai     ⚠ 未验证  gpt-4o               sk-*****9c8d1  -
  ○ gemini     ❌ 未配置  -                    -              -

  当前默认: deepseek
  存储位置: Windows Credential Manager
```

### 6.3 切换默认 Backend

```
$ zmai auth switch claude

  ✅ 默认 Backend 已切换至 claude (claude-sonnet-4-6)
```

### 6.4 更新 API Key

```
$ zmai auth update deepseek

  当前 Key: sk-*****b6125
  新 Key（留空保持当前）:
  ⟳ 正在验证新 Key ...
  ✅ 验证成功 (0.3s)
  ✅ 凭证已更新
```

### 6.5 删除凭证

```
$ zmai auth remove openai

  确认删除 openai 凭证？[y/N]: y
  ✅ 已删除
```

### 6.6 验证凭证

```
$ zmai auth verify

  正在验证当前默认 Backend (deepseek)...

  Backend:  deepseek
  Model:    deepseek-chat
  状态:     ✅ 可用
  延迟:     0.3s
  存储:     Windows Credential Manager

  ── 其他 Backend ──
  anthropic  ✅ 可用 (0.5s)
  openai     ⚠ 未验证 (跳过)
  gemini     ❌ 未配置
```

### 6.7 一键登出

```
$ zmai logout

  确认删除所有已保存的凭证？[y/N]: y
  ✅ 已清除以下 Backend 的凭证:
    - deepseek (Windows Credential Manager)
    - anthropic (Windows Credential Manager)
  ⚠ 环境变量中的凭证不受影响
```

---

## 7. Backend 支持矩阵

### 7.1 支持的 Backend

| Backend | 环境变量 | 默认模型 | 验证接口 | 需要 Gateway 类? |
|---------|---------|---------|---------|-----------------|
| Claude | `ANTHROPIC_API_KEY` | claude-sonnet-4-6 | `GET /v1/messages` | ✅ 已有 |
| DeepSeek | `DEEPSEEK_API_KEY` | deepseek-chat | `GET /v1/models` | ✅ 已有 |
| OpenAI | `OPENAI_API_KEY` | gpt-4o | `GET /v1/models` | ❌ 待加（不影响 Auth） |
| Gemini | `GEMINI_API_KEY` | gemini-2.0-flash | `GET /v1/models` | ❌ 待加（不影响 Auth） |

### 7.2 验证接口

```python
# auth/verify.py (新增)

VERIFIERS: dict[str, Callable] = {
    "anthropic": _verify_anthropic,
    "deepseek":  _verify_deepseek,
    "openai":    _verify_openai,
    "gemini":    _verify_gemini,
}

def _verify_anthropic(api_key: str) -> VerifyResult:
    """验证 Anthropic API Key。"""
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        },
        method="GET",
    )
    try:
        start = time.time()
        with urllib.request.urlopen(req, timeout=15) as resp:
            elapsed = time.time() - start
            return VerifyResult(valid=True, elapsed=elapsed, status=resp.status)
    except urllib.error.HTTPError as e:
        elapsed = 0
        if e.code == 401:
            return VerifyResult(valid=False, error="API Key 无效 (401)")
        if e.code == 403:
            return VerifyResult(valid=False, error="无权限 (403)，请检查模型访问权限")
        return VerifyResult(valid=False, error=f"HTTP {e.code}")
    except Exception as e:
        return VerifyResult(valid=False, error=str(e))


def _verify_gemini(api_key: str) -> VerifyResult:
    """验证 Google Gemini API Key。"""
    req = urllib.request.Request(
        f"https://generativelanguage.googleapis.com/v1/models?key={api_key}",
        method="GET",
    )
    try:
        start = time.time()
        with urllib.request.urlopen(req, timeout=15) as resp:
            elapsed = time.time() - start
            return VerifyResult(valid=True, elapsed=elapsed, status=resp.status)
    except urllib.error.HTTPError as e:
        ...
```

### 7.3 未配置 Backend 的处理

在 Gateway 层未实现某 Backend 类时，Auth 层仍可存储其凭证。

例如 `GEMINI_API_KEY` 已存入 OS Keychain，但 `GeminiBackend` 尚未在 Gateway 中注册：

```
zmai auth list  →  ✅ gemini 已配置（凭证就绪）
zmai            →  ⚠ gemini 凭证已找到，但未找到对应 Gateway 实现
                   请安装插件或更新 ZMAI 版本
```

---

## 8. 密钥验证机制

### 8.1 验证时机

| 时机 | 触发 | 行为 |
|------|------|------|
| 输入时 | 初始化向导 / `auth update` | 输入后立即验证，不通过不保存 |
| 启动时 | `zmai` / `zmai <task>` | 后台异步验证当前默认 Backend |
| 手动 | `zmai auth verify [name]` | 验证所有或指定 Backend |

### 8.2 验证结果

```python
@dataclass
class VerifyResult:
    valid: bool
    elapsed: float = 0.0
    status: int = 0
    error: str = ""
    model: str = ""           # 实际可用的模型名（如有）
    quota_remaining: str = "" # Token/调用配额（如有）
```

### 8.3 错误处理

| 错误 | 显示 | 建议操作 |
|------|------|---------|
| 401 Unauthorized | ❌ API Key 无效 | 重新输入 Key |
| 403 Forbidden | ❌ 无权限 | 检查账号是否有模型访问权限 |
| 429 Rate Limited | ⚠ 请求过于频繁 | 等待 30 秒后重试 |
| 500 Server Error | ❌ 服务暂时不可用 | 稍后重试 |
| Timeout | ❌ 网络连接超时 | 检查网络/代理设置 |
| DNS 解析失败 | ❌ 域名不可达 | 检查网络/API 端点配置 |

```python
def format_verify_error(result: VerifyResult) -> str:
    """将验证错误格式化为用户可读的建议。"""
    messages = {
        "401": f"❌ API Key 无效\n  请重新输入: zmai auth update {name}",
        "403": f"❌ 无权限访问该模型\n  请检查 API 账号的模型访问权限",
        "429": f"⚠ 请求过于频繁\n  请等待 30 秒后重试",
        "timeout": f"❌ 连接超时\n  请检查网络连接或代理设置",
    }
    return messages.get(result.error[:3], f"❌ {result.error}")
```

### 8.4 离线检测

验证只在有网络时进行。离线时跳过验证，仅提示：

```
⚠ 当前离线，跳过验证
  凭证将在有网络时自动验证
```

---

## 9. 安全设计

### 9.1 安全层级

| 层级 | 安全性 | 实现 | 适用范围 |
|------|--------|------|---------|
| 1 — OS Keychain | ⭐⭐⭐ 高 | Windows Credential Manager / macOS Keychain / Linux secret-tool | 90% 用户 |
| 2 — AES-GCM 文件 | ⭐⭐ 中 | `~/.zmai/credentials` + scrypt 派生密钥 | 6% 用户（无 OS Keychain 环境） |
| 3 — 明文文件（降级） | ⭐ 警告 | `~/.zmai/credentials.plain` + chmod 600 | 4% 用户（嵌入式/容器环境） |
| 环境变量覆盖 | ⭐⭐ 中 | `DEEPSEEK_API_KEY` 等 | CI/CD 场景（只读不写） |

### 9.2 凭证生命周期

```
创建:  用户输入 → 验证 → OS Keychain 存储
读取:  启动时 → OS Keychain 读取 → 注入环境变量
更新:  用户输入新 Key → 验证 → 覆盖 OS Keychain 条目
删除:  用户确认 → 删除 OS Keychain 条目
过期:  自动检测 401 → 提示用户更新
```

### 9.3 安全注意事项

| 注意事项 | 处理方式 |
|---------|---------|
| 密钥在终端回显 | `getpass` 隐藏输入（Python 标准库） |
| 密钥在进程内存 | 仅暂存于 Python 变量，用完 gc |
| 密钥在日志 | `AuthStore` 所有方法过滤 api_key 字段 |
| 密钥在环境变量 | 仅注入到当前进程，不持久化到 `.env` 文件 |
| 密钥在项目目录 | **永不。** 凭证只在 `~/.zmai/` |

```python
def _sanitize_for_log(data: dict) -> dict:
    """过滤日志中的敏感字段。"""
    result = dict(data)
    if "api_key" in result:
        key = result["api_key"]
        result["api_key"] = key[:7] + "..." if len(key) > 10 else "***"
    return result
```

### 9.4 密钥显示保护

所有界面只显示 Key 的前 7 位：

```
当前 Key: sk-*****b6125
```

输入时不回显（`getpass`）。

---

## 10. 迁移方案

### 10.1 从 v1 加密文件 → v2 OS Keychain

```python
# auth/migration.py (新增)

class Migration:
    """从旧版存储迁移到新版存储。"""

    @staticmethod
    def migrate_if_needed(store: AuthStore) -> bool:
        """检查旧凭证文件，如有则迁移到 OS Keychain。"""
        old_path = Path.home() / ".zmai" / "credentials"
        if not old_path.exists():
            return False

        v1_store = _V1Store(old_path)
        backends = v1_store.load()
        if not backends:
            return False

        migrated = 0
        for name, info in backends.items():
            if info.get("api_key") and not store.get_backend(name):
                store.set_backend(name, info["api_key"],
                                  model=info.get("model", ""),
                                  make_active=False)
                migrated += 1

        if migrated > 0:
            # 备份旧文件，创建新文件
            old_path.rename(old_path.with_suffix(".v1.bak"))

        return migrated > 0


class _V1Store:
    """读取 v1 加密文件（兼容旧版 XOR）。"""
    ...
```

### 10.2 自动迁移时机

```
第一次运行 zmai v2.0+：
  ├── 检测到 ~/.zmai/credentials（旧格式）
  ├── 自动迁移到 OS Keychain
  ├── 备份旧文件为 ~/.zmai/credentials.v1.bak
  └── 继续正常启动（用户无感知）
```

### 10.3 降级恢复

如果 OS Keychain 出现问题，用户可以从旧文件恢复：

```bash
zmai auth migrate --from-file ~/.zmai/credentials.v1.bak
```

---

## 11. 文件清单与实现计划

### 11.1 新增文件

```
src/zmai/auth/
├── __init__.py          # 🔧 更新导出
├── store.py             # 🔧 重写 — AuthStore 统一入口（OS Keychain + 文件）
├── keychain.py          # 🔴 新增 — OS 安全存储适配器（Win/macOS/Linux）
├── verify.py            # 🔴 新增 — API Key 验证（4 Backend）
├── wizard.py            # 🔴 新增 — 交互式初始化向导
├── detect.py            # 🔴 新增 — 自动检测可用凭证
└── migration.py         # 🔴 新增 — 从旧版迁移到新版
```

### 11.2 修改文件

```
src/zmai/cli/main.py     # 🔧 修改 — 集成 detect + wizard + auth 子命令
src/zmai/auth/__init__.py # 🔧 修改 — 新增导出
```

### 11.3 不变文件

```
src/zmai/gateway/backends/claude.py    ✅ 不变（读 ANTHROPIC_API_KEY）
src/zmai/gateway/backends/deepseek.py  ✅ 不变（读 DEEPSEEK_API_KEY）
src/zmai/gateway/registry.py           ✅ 不变
src/zmai/gateway/base.py               ✅ 不变
src/zmai/config/config.py              ✅ 不变
src/zmai/config/sources.py             ✅ 不变
src/zmai/runtime/runtime.py            ✅ 不变
src/zmai/workspace/workspace.py        ✅ 不变
```

### 11.4 实现优先级

```
P0 — 基础安全存储 + 自动检测（1 天）
├── keychain.py          OS Keychain 适配器（全平台）
├── store.py             重写 — 统一入口
├── detect.py            自动检测链
└── migration.py         旧版迁移

P1 — 交互式向导 + 命令（1 天）
├── wizard.py            交互式初始化向导
├── verify.py            API Key 验证（4 Backend）
└── main.py              集成 auth 子命令

P2 — 体验完善（0.5 天）
├── zmai login / logout  别名
├── auth verify 显式验证
└── 离线状态检测
```

### 11.5 代码量估算

```
新增:
  keychain.py         ~150 行
  store.py (重写)     ~120 行
  verify.py           ~120 行
  wizard.py           ~150 行
  detect.py            ~60 行
  migration.py         ~60 行
  总计                 ~660 行

修改:
  auth/__init__.py      ~5 行
  cli/main.py          ~50 行
  总计                  ~55 行

不变:
  下游模块             ~2000+ 行不变
```

---

> **总结：**
>
> ZMAI Auth v2.0 的核心变化：
>
> 1. **存储从 XOR 文件 → OS Keychain + AES-GCM 降级** — 真正的安全存储
> 2. **支持从 2 个到 4 个 Backend** — Claude + DeepSeek + OpenAI + Gemini
> 3. **自动检测 + 迁移** — 旧版本无感迁移，环境变量自动兼容
> 4. **首次运行 `zmai` 完成初始化** — 向导 → 验证 → 存入 → 直接 REPL
> 5. **验证机制** — 输入时验证 + 启动时验证 + 手动验证
>
> **不修改 Gateway/Runtime 一行代码。** 所有适配通过环境变量注入完成。
