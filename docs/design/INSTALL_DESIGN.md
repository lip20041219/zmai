# ZMAI Install Design v2.0

Version: 2.0
Date: 2026-07-16

> **安装一次，任何目录，直接 `zmai`。**
>
> 不修改 Runtime / Agent / Gateway / Memory / Workspace / Workflow 模块。
>
> 仅修改安装入口和首次运行流程。

---

## 目录

1. [现状审查](#1-现状审查)
2. [设计原则](#2-设计原则)
3. [安装方式矩阵](#3-安装方式矩阵)
4. [pip / pipx / uv 安装](#4-pip--pipx--uv-安装)
5. [Windows 安装](#5-windows-安装)
6. [零安装使用（uvx）](#6-零安装使用uvx)
7. [安装后诊断](#7-安装后诊断)
8. [自更新机制](#8-自更新机制)
9. [多平台 PATH 处理](#9-多平台-path-处理)
10. [首次运行流程](#10-首次运行流程)
11. [文件清单](#11-文件清单)

---

## 1. 现状审查

### 1.1 当前已支持

| 能力 | 文件 | 状态 |
|------|------|------|
| `pyproject.toml` 入口点 | `[project.scripts] zmai = "zmai.cli.main:main"` | ✅ 已配置 |
| 项目检测 | `cli/detector.py` — 向上遍历找项目根 | ✅ 已完成 |
| 配置层级 | 项目 + 全局 + 环境变量 | ✅ 已完成 |
| Workspace 路径解析 | `cli/detector.py:_resolve_workspace` | ✅ 已完成 |
| 非项目目录聊天模式 | `main.py:398-400` | ✅ 已完成 |

### 1.2 当前缺失

| 能力 | 缺失情况 | 影响 |
|------|----------|------|
| `__main__.py` | `python -m zmai` 不可用 | 无 Python module 入口 |
| Windows 安装 | 无 winget / choco / 安装脚本 | Windows 用户需手动处理 |
| `uvx` 支持 | 无零安装使用方式 | 快速尝鲜门槛高 |
| `zmai doctor` | 无故障诊断命令 | 安装问题排查困难 |
| `zmai self-update` | 无自更新命令 | 更新需手动 pipx upgrade |
| `zmai init` | 无 shell completion 安装 | 补全需手动配置 |
| Windows PATH 引导 | 无平台适配的 PATH 提示 | Windows 用户易困惑 |
| 安装后校验 | 无 smoke test | 安装成功与否不明确 |
| 源码开发安装 | 无 `pip install -e .` 指引 | 开发者无法快速开始 |
| 平台启动包装器 | 无 `.bat` / `.ps1` entry points | Windows 上 pip 安装后可能 PATH 不可用 |

### 1.3 根因

安装体验的问题根因在于**入口点单一**和**缺少平台适配**。

pyproject.toml 只配置了 `[project.scripts]` 入口点，这在 Linux/macOS 上足够，但在 Windows 上：
1. pip 安装的脚本在 `Scripts/` 目录，用户需要手动把目录加到 PATH
2. 没有 `.bat` 或 `.ps1` 包装器，用户无法在 PowerShell 直接调用
3. 没有提供安装后的验证和引导

---

## 2. 设计原则

### 2.1 零摩擦原则

```bash
# 安装一次，之后就只在脑子里记着「用 zmai」
pipx install zmai      # 一次
zmai                    # 从此任何目录
zmai 帮我重构代码         # 任何时间
```

### 2.2 平台平等原则

各平台安装体验一致：

| 环节 | Linux | macOS | Windows |
|------|-------|-------|---------|
| 安装 | pipx install | pipx install | pipx install |
| 命令 | `zmai` | `zmai` | `zmai` |
| PATH | 自动 | 自动 | **需确认** |
| 补全 | bash/zsh | zsh | PowerShell |
| 升级 | `zmai self-update` | 同左 | 同左 |
| 诊断 | `zmai doctor` | 同左 | 同左 |

### 2.3 不可变安装原则

安装包本身不可变。用户配置存储在 `~/.zmai/`：

```
~/.zmai/                    ← 用户数据（安装后生成）
  ├── config.json           ← 用户配置
  ├── credentials           ← 凭证（加密）
  ├── sessions/             ← 会话记录
  └── cache/                ← 缓存

pipx 安装目录              ← 包本身（不写用户数据）
  └── zmai/
```

### 2.4 不修改下游原则

```
仅修改：
  src/zmai/__main__.py      ← 🔴 新增
  src/zmai/cli/main.py      ← 🔧 增加 doctor / self-update / init 命令
  pyproject.toml             ← 🔧 完善元数据

不修改：
  src/zmai/runtime/*        ✗
  src/zmai/gateway/*        ✗
  src/zmai/agent/*          ✗
  src/zmai/workspace/*      ✗
  src/zmai/memory/*         ✗
  src/zmai/auth/*           ✗
  src/zmai/workflow/*       ✗
  src/zmai/swe/*            ✗
```

---

## 3. 安装方式矩阵

### 3.1 全方式对比

| 方式 | 命令 | 隔离 | PATH | 适用场景 |
|------|------|------|------|---------|
| **pipx** | `pipx install zmai` | ✅ 隔离环境 | 自动 | ⭐ 主推：日常使用 |
| **uv tool** | `uv tool install zmai` | ✅ 隔离环境 | 自动 | ⭐ 主推：uv 用户 |
| **pip** | `pip install zmai` | ❌ 全局安装 | 自动 | 备选：Python 开发者 |
| **pip --user** | `pip install --user zmai` | ❌ 用户安装 | 自动 | 备选：无 sudo |
| **uvx** | `uvx zmai <task>` | ✅ 每次临时 | 不需要 | 尝鲜：零安装试用 |
| **winget** | `winget install zmai` | ✅ 系统级 | 自动 | Windows 优先 |
| **choco** | `choco install zmai` | ❌ 系统级 | 自动 | Windows 备选 |
| **源码** | `pip install -e .` | ❌ 开发模式 | 自动 | 开发者 |

### 3.2 推荐路线

```
一般用户:     pipx install zmai
uv 用户:      uv tool install zmai
Windows 用户: pipx install zmai  (或 winget install zmai)
快速尝鲜:     uvx zmai --version
开发者:       git clone + pip install -e .
```

### 3.3 安装命令设计

```bash
# 官方安装脚本（推荐入口，自动检测最佳方式）
curl -fsSL https://zmai.dev/install.sh | sh
# 或 Windows:
powershell -c "irm https://zmai.dev/install.ps1 | iex"

# 自动检测逻辑：
# 1. 有 uv  → uv tool install zmai
# 2. 有 pipx → pipx install zmai
# 3. 有 pip  → pip install --user zmai
# 4. 无 Python → 提示安装 Python 3.10+
```

---

## 4. pip / pipx / uv 安装

### 4.1 pyproject.toml 完善

当前 `pyproject.toml` 基本配置已完整，需补充：

```toml
[build-system]
requires = ["setuptools>=68.0"]
build-backend = "setuptools.build_meta"

[project]
name = "zmai"
version = "0.1.0"
description = "Model-Agnostic Agent Runtime"
readme = "README.md"
license = {text = "MIT"}
requires-python = ">=3.10"

# 依赖：最小化
dependencies = [
    # 无第三方依赖 — 仅用 Python 标准库
]

[project.urls]
Homepage = "https://zmai.dev"
Documentation = "https://zmai.dev/docs"
Source = "https://github.com/zmai/zmai"
Download = "https://pypi.org/project/zmai/"

[project.scripts]
zmai = "zmai.cli.main:main"

# 新增：python -m zmai 支持
# → 需要新增 src/zmai/__main__.py

[tool.setuptools.packages.find]
where = ["src"]
```

### 4.2 新增 `__main__.py`

```python
# src/zmai/__main__.py (新增)
"""支持 `python -m zmai <task>` 调用。"""

from zmai.cli.main import main

main()
```

`python -m zmai` 和 `zmai` CLI 命令行为完全一致。

### 4.3 pip 安装流程

```bash
# 标准安装
pip install zmai

# 用户安装（无 sudo）
pip install --user zmai

# 验证
zmai --version
# → ZMAI v0.1.0

zmai --help
# → 显示帮助
```

**pip 安装后的 PATH 问题**

不同平台 pip 脚本安装位置：

| 平台 | pip 脚本目录 | 是否在 PATH |
|------|-------------|------------|
| Linux | `~/.local/bin/` | ❌ 部分发行版默认不在 |
| macOS | `~/Library/Python/3.x/bin/` | ❌ 默认不在 |
| Windows | `%USERPROFILE%\AppData\Local\Programs\Python\Python3x\Scripts\` | ❌ 仅全局 Python 默认在 |

**解决方案：** `zmai doctor` 检测并提示修复。

### 4.4 pipx 安装（主推）

```bash
# 安装 pipx（如果未安装）
python -m pip install --user pipx
python -m pipx ensurepath

# 安装 zmai
pipx install zmai

# 验证
zmai --version
# → ZMAI v0.1.0
which zmai
# → ~/.local/bin/zmai
```

pipx 的优势：
- **隔离环境** — 不污染全局 Python
- **自动 PATH** — `pipx ensurepath` 自动配置
- **独立管理** — `pipx list` / `pipx upgrade zmai`
- **零冲突** — 不同项目依赖互不影响

### 4.5 uv tool 安装

```bash
# 安装 uv（如果未安装）
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
# 或 curl -fsSL https://astral.sh/uv/install.sh | sh

# 安装 zmai
uv tool install zmai

# 验证
zmai --version
# → ZMAI v0.1.0
```

uv tool 的额外优势：
- **安装速度最快** — Rust 实现的 pip 替代
- **全局隔离** — 类似 pipx
- **自动 PATH** — uv tool 自动处理

---

## 5. Windows 安装

### 5.1 推荐方式：pipx

```powershell
# PowerShell (管理员)
python -m pip install --user pipx
python -m pipx ensurepath
# 重启终端
pipx install zmai
zmai --version
```

### 5.2 PowerShell 安装脚本

```powershell
# install.ps1
# 官方 Windows 安装脚本：irm https://zmai.dev/install.ps1 | iex

$ErrorActionPreference = "Stop"

Write-Host "⚡ ZMAI Installer for Windows" -ForegroundColor Cyan

# 1. 检测 Python
$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    Write-Host "❌ Python 3.10+ 未安装" -ForegroundColor Red
    Write-Host "   请从 https://python.org 安装 Python 3.10+"
    exit 1
}

# 2. 检测/安装 pipx
$pipx = Get-Command pipx -ErrorAction SilentlyContinue
if (-not $pipx) {
    Write-Host "📦 正在安装 pipx ..."
    python -m pip install --user pipx 2>&1 | Out-Null
    python -m pipx ensurepath 2>&1 | Out-Null
    # 刷新 PATH
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "User") + ";$env:Path"
}

# 3. 安装 zmai
Write-Host "📦 正在安装 zmai ..."
pipx install zmai 2>&1 | Out-Null

# 4. 验证
$version = & zmai --version 2>&1
if ($LASTEXITCODE -eq 0) {
    Write-Host "✅ ZMAI 安装完成: $version" -ForegroundColor Green
    Write-Host ""
    Write-Host "现在可以在任何目录使用: zmai" -ForegroundColor Cyan
} else {
    Write-Host "❌ 安装失败" -ForegroundColor Red
    Write-Host "请尝试: pipx install zmai"
}
```

### 5.3 Windows 启动包装器

pip 在 Windows 上安装脚本到 `Python\Scripts\`，该目录可能在 PATH 也可能不在。

为确保 `zmai` 命令在任何 Windows 终端可用，增加 Platform Bootstrap：

```bat
:: Windows CMD: %LOCALAPPDATA%\zmai\zmai.cmd
@echo off
python -m zmai %*
```

```powershell
# Windows PowerShell: %LOCALAPPDATA%\zmai\zmai.ps1
python -m zmai @args
```

这两个包装器在 `zmai init` 安装到 PATH 目录。

**不修改 pyproject.toml。** 仅通过 post-install 脚本或 `zmai init` 创建这些包装器。

### 5.4 winget / choco（发布后）

PyPI 发布后，可为 Windows 包管理器提交 manifest：

```yaml
# winget: https://github.com/microsoft/winget-pkgs
Id: zmai.zmai
Name: ZMAI
Version: 0.1.0
InstallerType: wix
Installers:
  - Architecture: x64
    InstallerUrl: https://github.com/zmai/zmai/releases/download/v0.1.0/zmai-0.1.0.msi
```

但 winget/choco 需要打包 MSI 或 portable exe。在纯 Python 项目早期，**pipx 是主要推荐方式**。

---

## 6. 零安装使用（uvx）

### 6.1 uvx 简介

`uvx` 是 `uv` 提供的临时运行工具，类似 `npx`：

- **不需要安装 zmai**
- 每次自动从 PyPI 下载最新版本
- 运行后自动清理缓存
- 适合快速尝鲜和 CI 环境

### 6.2 使用方式

```bash
# 先安装 uv（一次）
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"

# 然后用 uvx 运行 zmai（每次自动下载）
uvx zmai --version
uvx zmai 帮我总结这个项目
uvx zmai --backend claude 分析代码
```

### 6.3 uvx 运行原理

```bash
uvx zmai 分析代码
# ↓ 等价于
uv tool run zmai 分析代码
# ↓
# 1. 检查 ~/.local/share/uv/tools/zmai 是否存在
# 2. 不存在 → 从 PyPI 下载最新版
# 3. 创建隔离环境
# 4. 运行 zmai 并传入参数
```

### 6.4 使用场景

| 场景 | 推荐方式 |
|------|---------|
| 快速看看 zmai 是什么 | `uvx zmai --help` |
| CI 中临时使用 | `uvx zmai --json 执行任务` |
| 不确定是否长期使用 | `uvx zmai ...` 先试用 |
| 日常使用 | `pipx install zmai` |
| 需要特定版本 | `uvx zmai==0.1.0 ...` |

---

## 7. 安装后诊断

### 7.1 `zmai doctor` 命令

新增诊断子命令，检查安装完整性：

```bash
$ zmai doctor

  ⚡ ZMAI Doctor
  ─────────────────────────────────────────────────────
  ✅ Python       3.13.2 (C:\Users\me\AppData\Local\Programs\Python\Python313)
  ✅ Package      zmai v0.1.0 (pip: site-packages/zmai)
  ✅ Entry Point  C:\Users\me\.local\bin\zmai (指向正确)
  ✅ PATH         zmai 在 PATH 中可用
  ✅ Config       ~/.zmai/config.json (存在)
  ✅ Auth         deepseek (已验证) · claude (已验证)
  ✅ Workspace    ./workspace/ (存在, 0 active agents)
  ✅ Network      api.deepseek.com 可达
  ⚠  Shell       未安装命令补全 (运行 zmai init)

  ── 建议 ──
  $ zmai init    安装命令补全
```

### 7.2 检查项

```python
# cli/doctor.py (新增)
class Doctor:
    """安装诊断。"""

    checks: list[Check] = [
        Check("python_version",  _check_python_version),
        Check("package",         _check_package),
        Check("entry_point",     _check_entry_point),
        Check("path",            _check_path),
        Check("config",          _check_config),
        Check("auth",            _check_auth),
        Check("workspace",       _check_workspace),
        Check("network",         _check_network),
        Check("shell_completion",_check_shell_completion),
    ]

    def run(self) -> DiagnosticReport:
        results = []
        for check in self.checks:
            try:
                result = check.fn()
            except Exception as e:
                result = CheckResult(status="error", detail=str(e))
            results.append(result)
        return DiagnosticReport(results)
```

### 7.3 失败处理

每项检查有三种结果：

| 结果 | 示例 | 行为 |
|------|------|------|
| ✅ pass | Python 版本正确 | 显示绿色勾 |
| ⚠ warn | Shell 补全未安装 | 黄色警告 + 修复建议 |
| ❌ fail | PATH 中找不到 zmai | 红色错误 + 修复命令 |

### 7.4 自动修复

```python
# cli/doctor.py
class Fix:
    """诊断修复操作。"""

    @staticmethod
    def fix_path() -> str:
        """生成 PATH 修复提示。"""
        import sys
        if sys.platform == "win32":
            return (
                "将以下目录添加到 PATH:\n"
                f"  {Path.home() / '.local' / 'bin'}\n"
                "或运行: pipx ensurepath"
            )
        return "运行: pipx ensurepath"
```

---

## 8. 自更新机制

### 8.1 `zmai self-update` 命令

新增自更新命令，自动检测安装方式并升级：

```bash
$ zmai self-update

  ⟳ 检查更新 ...
  当前版本: v0.1.0
  最新版本: v0.1.1
  ⟳ 正在更新 ...

  ✅ 已更新到 v0.1.1
  当前安装方式: pipx
  更新方式: pipx upgrade zmai
```

### 8.2 检测逻辑

```python
def self_update():
    """自动检测安装方式并更新。"""
    install_method = _detect_install_method()
    latest = _check_pypi_version()

    if latest <= CURRENT_VERSION:
        print("已是最新版本")
        return

    if install_method == "pipx":
        subprocess.run(["pipx", "upgrade", "zmai"])
    elif install_method == "uv_tool":
        subprocess.run(["uv", "tool", "upgrade", "zmai"])
    elif install_method == "pip":
        subprocess.run(["pip", "install", "--upgrade", "zmai"])
    else:
        print(f"无法自动更新 ({install_method})")
        print(f"请手动运行: pip install --upgrade zmai")
```

### 8.3 安装方式检测

```python
def _detect_install_method() -> str:
    """检测当前 zmai 的安装方式。"""
    import inspect
    import zmai
    module_path = inspect.getfile(zmai)

    if "pipx" in module_path:
        return "pipx"
    if "uv" in module_path and "tools" in module_path:
        return "uv_tool"
    if "site-packages" in module_path:
        return "pip"
    if "src" in module_path and "zmai" in module_path:
        return "source"
    return "unknown"
```

### 8.4 版本检查频率

```python
# ~/.zmai/cache/version-check.json
{
    "last_check": "2026-07-16T10:00:00Z",
    "latest_version": "0.1.1",
    "cached_until": "2026-07-17T10:00:00Z"
}
```

- 每次启动时检查缓存（非阻塞，不等待网络）
- 缓存有效期 24 小时
- 仅在交互模式下显示更新提示
- 不打断正在执行的任务

---

## 9. 多平台 PATH 处理

### 9.1 各平台脚本安装位置

| 安装方式 | Linux | macOS | Windows |
|---------|-------|-------|---------|
| pipx | `~/.local/bin/` | `~/.local/bin/` | `~\.local\bin\` |
| pip 全局 | `/usr/local/bin/` | `/usr/local/bin/` | `Python\Scripts\` |
| pip --user | `~/.local/bin/` | `~/Library/Python/3.x/bin/` | `Python\Scripts\` |
| uv tool | `~/.local/bin/` | `~/.local/bin/` | `~\.local\bin\` |

### 9.2 PATH 自动检测

```python
def _check_path() -> PathCheckResult:
    """检查 zmai 是否在 PATH 中。"""
    import shutil
    import sys

    zmai_path = shutil.which("zmai")
    if zmai_path:
        return PathCheckResult(
            in_path=True,
            path=zmai_path,
        )

    # 不在 PATH → 找出安装脚本实际位置
    script_dir = _get_script_dir()
    suggestions = []
    if sys.platform == "win32":
        suggestions.append(
            f'$env:Path += ";{script_dir}"\n'
            f"[Environment]::SetEnvironmentVariable('Path', "
            f"[Environment]::GetEnvironmentVariable('Path', 'User') + "
            f"';{script_dir}', 'User')"
        )
    else:
        suggestions.append(
            f'export PATH="$PATH:{script_dir}"\n'
            f'# 添加到 ~/.bashrc 或 ~/.zshrc'
        )

    return PathCheckResult(
        in_path=False,
        script_dir=script_dir,
        suggestions=suggestions,
    )
```

### 9.3 Windows PATH 引导

`zmai init` 在 Windows 上的特殊处理：

```powershell
# zmai init on Windows
$scriptDir = "$env:USERPROFILE\.local\bin"
if (-not ($env:Path -split ";" -contains $scriptDir)) {
    Write-Host "📁 添加 $scriptDir 到 PATH ..."

    # 当前会话
    $env:Path += ";$scriptDir"

    # 永久（用户级别）
    [Environment]::SetEnvironmentVariable(
        "Path",
        [Environment]::GetEnvironmentVariable("Path", "User") + ";$scriptDir",
        "User"
    )

    Write-Host "✅ 已添加。请重启终端或运行:"
    Write-Host "   \$env:Path += `";$scriptDir`""
}
```

### 9.4 安装后 PATH 校验流程

```
安装完成
  │
  ▼ zmai doctor
  │
  ├── PATH 检查
  │   ├── 在 PATH 中 → ✅ 正常
  │   └── 不在 PATH 中 →
  │       ├── 找到脚本位置: ~/.local/bin/zmai
  │       ├── 检测平台: Windows
  │       └── 建议:
  │           [Windows] $env:Path += ";~\.local\bin"
  │           [macOS]   export PATH="$PATH:~/.local/bin"
  │           [Linux]   export PATH="$PATH:~/.local/bin"
```

---

## 10. 首次运行流程

### 10.1 全局首次启动

```
$ pipx install zmai
...
$ zmai
  │
  ├── detect.py 检测: 无项目（CWD 无标记文件）
  │
  ├── auth.detect 检测: 无 API Key
  │
  ├── ⚡ ZMAI v0.1.0 — 首次启动
  │   未检测到项目目录和 API Key。
  │
  ├── 进入初始化向导
  │   ├── [1] 配置 Backend（Claude / DeepSeek / OpenAI / Gemini）
  │   ├── [2] 选择 Shell 补全（bash / zsh / fish / powershell）
  │   └── [3] 选择主题（dark / light）
  │
  └── ✅ 配置完成 → 进入 REPL
      zmai>
```

### 10.2 项目内首次启动

```
$ cd my-project
$ zmai
  │
  ├── detect.py 检测到: my-project (python 3.13)
  │
  ├── auth.detect 检测: 无 API Key
  │
  ├── 进入初始化向导（同上）
  │   但保留项目上下文信息
  │
  └── ✅ 配置完成
      ⚡ zmai v0.1.0  my-project (python 3.13)
      zmai>
```

### 10.3 `zmai init` 命令

```bash
zmai init [--shell bash|zsh|fish|powershell]
```

`zmai init` 执行：

| 步骤 | 操作 | 跳过条件 |
|------|------|---------|
| 1 | 检测安装完整性 | `zmai doctor` 全通过 |
| 2 | 安装 Shell 补全 | 已安装 |
| 3 | 确认 PATH 可用 | 已在 PATH 中 |
| 4 | 可选：配置默认 Backend | 已有凭证 |

```bash
$ zmai init

  ⚡ ZMAI 初始化
  ─────────────────────────────────────────────────────
  ✅ 安装完整性检查通过

  Shell 补全:
  ➜ 检测到 PowerShell
  ➜ 安装补全到: $PROFILE
  ✅ 补全已安装

  PATH:
  ✅ zmai 在 PATH 中: ~\.local\bin\zmai

  ── 完成 ──
  现在可以在任何目录下运行 zmai
```

### 10.4 Shell 补全安装

```python
# cli/init.py (新增)
class ShellInit:
    """Shell 补全安装。"""

    @staticmethod
    def detect_shell() -> str:
        """检测当前 Shell。"""
        import os
        shell = os.environ.get("SHELL", "")
        if "zsh" in shell:
            return "zsh"
        if "bash" in shell:
            return "bash"
        if "fish" in shell:
            return "fish"
        if "powershell" in shell or os.environ.get("PSModulePath"):
            return "powershell"
        return "unknown"

    @staticmethod
    def install(shell: str) -> bool:
        """安装补全到 Shell 配置。"""
        if shell == "powershell":
            return _install_powershell()
        ...

    @staticmethod
    def _install_powershell() -> bool:
        profile = _get_powershell_profile()
        completion_script = _generate_completion("powershell")
        if profile:
            profile.write_text(completion_script, encoding="utf-8")
            return True
        return False
```

---

## 11. 文件清单

### 11.1 新增文件

```
src/zmai/
├── __main__.py           # 🔴 新增 — python -m zmai 支持

src/zmai/cli/
├── doctor.py             # 🔴 新增 — zmai doctor 诊断
├── init_cmd.py           # 🔴 新增 — zmai init 初始化
├── update.py             # 🔴 新增 — zmai self-update 更新

scripts/
├── install.sh            # 🔴 新增 — Unix 安装脚本
├── install.ps1           # 🔴 新增 — Windows 安装脚本
```

### 11.2 修改文件

```
pyproject.toml             # 🔧 完善元数据（URLs、classifiers）
src/zmai/cli/main.py       # 🔧 注册 doctor / self-update / init 子命令
```

### 11.3 不变文件

```
src/zmai/runtime/*          ✅
src/zmai/gateway/*          ✅
src/zmai/agent/*            ✅
src/zmai/workspace/*        ✅
src/zmai/memory/*           ✅
src/zmai/auth/*             ✅
src/zmai/workflow/*         ✅
src/zmai/swe/*              ✅
src/zmai/cli/detector.py    ✅ 已有
src/zmai/cli/context.py     ✅ 已有
src/zmai/cli/formatters.py  ✅ 已有
```

### 11.4 实现优先级

```
P0 — 安装基础（1 天）
├── src/zmai/__main__.py           — python -m zmai
├── pyproject.toml 完善             — URLs, classifiers
└── zmai doctor 基础版              — PATH/版本检查

P1 — 体验完善（1 天）
├── zmai init                       — Shell 补全安装
├── zmai self-update                — 自动更新
├── scripts/install.sh              — Unix 安装脚本
└── scripts/install.ps1             — Windows 安装脚本

P2 — 平台适配（0.5 天）
├── Windows PATH 引导               — 平台检测 + 修复建议
├── zmai doctor 完整版              — 全部检查项
└── ⽀持 winget/choco manifest      — 发布准备
```

### 11.5 代码量估算

```
新增:
  __main__.py           ~3 行
  cli/doctor.py        ~150 行
  cli/init_cmd.py      ~120 行
  cli/update.py        ~80 行
  scripts/install.sh   ~80 行
  scripts/install.ps1  ~100 行
  总计                  ~533 行

修改:
  pyproject.toml        ~10 行
  cli/main.py           ~40 行
  总计                   ~50 行
```

---

> **总结：**
>
> ZMAI Install v2.0 的核心改进：
>
> 1. **入口扩展** — 新增 `__main__.py` 支持 `python -m zmai`，安装方式从 1 种扩展到 6 种
> 2. **平台平等** — Windows 专属安装脚本、PATH 引导、PowerShell 补全
> 3. **零安装尝鲜** — `uvx zmai` 无需安装即可使用
> 4. **安装后诊断** — `zmai doctor` 一键检查 9 项安装完整性指标，带修复建议
> 5. **自更新** — `zmai self-update` 自动检测安装方式并升级
> 6. **首次运行引导** — `zmai init` 一站式完成 Shell 补全 + PATH 配置
>
> **`pipx install zmai` → 任何目录 → `zmai`。** 仅此三步。
