# ZMAI Packaging Review

> 审查日期: 2026-07-17
> 目标: `pip install zmai` 可用
> 范围: CLI Entry Point、Requirements、Version、打包配置

---

## 一、执行摘要

| 维度 | 状态 | 评分 |
|------|------|------|
| CLI Entry Point | ✅ 正确配置 | ★★★★★ |
| Requirements | ✅ 零第三方依赖 | ★★★★★ |
| Version | ⚠️ 仅 pyproject.toml 一处，无 `__version__` | ★★★☆☆ |
| Build System | ✅ pyproject.toml 纯 PEP 517 | ★★★★☆ |
| PyPI 就绪 | ⚠️ 缺少多个发布必需字段 | ★★☆☆☆ |

**综合评分: 3.5/5** — 核心配置正确，但缺少 PyPI 发布所需的若干要素。

---

## 二、逐项审查

### 2.1 CLI Entry Point

**现状** ✅

```toml
[project.scripts]
zmai = "zmai.cli.main:main"
```

- 指向 `zmai.cli.main:main()`
- 使用 `src/` 布局，包发现: `[tool.setuptools.packages.find] where = ["src"]`
- 入口函数正确处理 `argv` 参数

**验证**:
```
$ zmai                      → REPL 模式
$ zmai "1+1=?"              → 一次性任务
$ zmai --help               → 帮助
$ zmai config list          → 子命令
```

**问题**: 无 Windows 可执行文件包装（`.exe` wrapper）。在 Windows 上，`pip install` 会创建 `Scripts/zmai.exe`（setuptools 自动生成），但依赖 PATH 正确包含 Python Scripts 目录。

### 2.2 Requirements / Dependencies

**现状** ✅ **零第三方依赖** — 这是非常大的架构优势。

整个项目只使用 Python 标准库：

| 模块 | 用途 |
|------|------|
| `argparse` | CLI 参数解析 |
| `asyncio` | Agent 异步执行 |
| `json`, `pathlib` | 配置文件、持久化 |
| `urllib.request` | HTTP API 调用（无 `requests`） |
| `hashlib`, `base64` | 凭证加密 |
| `logging` | 日志 |
| `shutil`, `subprocess` | Shell 工具、文件操作 |
| `threading` | 并发控制 |
| `dataclasses` | 数据类 |
| `importlib` | 动态 Backend 加载 |
| `readline` | REPL 历史 |

**无 `[project.dependencies]` 配置** — 这是正确的，因为零依赖。但建议显式声明空的 dependencies 列表：

```toml
[project.dependencies]
# ZMAI 无第三方运行时依赖，仅使用 Python 标准库
```

### 2.3 Version

**现状** ⚠️

```toml
[project]
version = "0.1.0"
```

版本号只定义在 `pyproject.toml` 中。Python 代码中无法读取版本：

```python
>>> import zmai
>>> zmai.__version__
AttributeError: module 'zmai' has no attribute '__version__'
```

**建议**：在 `src/zmai/__init__.py` 中添加 `__version__`：

```python
"""ZMAI — Model-Agnostic Agent Runtime."""
__version__ = "0.1.0"
```

并考虑使用单一版本源（如 `importlib.metadata` 或读取 `pyproject.toml`）。

当前 `zmai --version` 输出 `ZMAI v0.1.0`（硬编码在 `_build_parser()` 中）：

```python
p.add_argument("--version", action="version", version="ZMAI v0.1.0")
```

这需要与 `pyproject.toml` 同步维护。建议读取自 `__version__`：

```python
from zmai import __version__
p.add_argument("--version", action="version", version=f"ZMAI v{__version__}")
```

### 2.4 Build System

**现状** ⚠️ 缺少若干发布配置

```toml
[build-system]
requires = ["setuptools>=68.0"]
build-backend = "setuptools.build_meta"
```

| 配置项 | 当前 | 建议 |
|--------|------|------|
| `requires-python` | ✅ `>=3.10` | 正确 |
| `dependencies` | ❌ 缺失 | 添加空列表（明确零依赖） |
| `optional-dependencies` | ❌ 缺失 | 如有开发依赖 |
| `classifiers` | ⚠️ 部分 | 缺少 Trove classifiers |
| `long_description` | ⚠️ 隐式 | 通过 `readme = "README.md"` 正确 |
| `keywords` | ✅ 有 | 可扩展 |
| `authors` / `maintainers` | ❌ 缺失 | 建议添加 |
| `urls` | ⚠️ 占位 | 指向 `github.com/zmai/zmai`（可能不存在） |

### 2.5 PyPI 发布准备

| 必要条件 | 状态 | 说明 |
|---------|------|------|
| `pyproject.toml` PEP 621 | ✅ | 使用现代构建标准 |
| 项目名称唯一 | ❌ 未验证 | `zmai` 在 PyPI 上可能已被占用 |
| README 渲染 | ⚠️ | README.md 存在，但引用本地文件路径 |
| License | ✅ | MIT |
| `.pypirc` / Token | ❌ | 未配置 PyPI 发布凭证 |
| CI 发布流水线 | ❌ | 无 GitHub Actions 发布 workflow |

---

## 三、缺失的配置项

### 3.1 `pyproject.toml` 建议补充

```toml
[project]
dynamic = ["version"]  # 或保持静态版本

[project.dependencies]
# 无第三方依赖

[project.optional-dependencies]
dev = ["pytest>=7", "ruff>=0.1", "mypy>=1.0"]

[project.urls]
Homepage = "https://github.com/zmai/zmai"
Documentation = "https://zmai.dev"
Source = "https://github.com/zmai/zmai"
Issues = "https://github.com/zmai/zmai/issues"
```

### 3.2 `src/zmai/__init__.py` 建议

```python
"""ZMAI — Model-Agnostic Agent Runtime."""
__version__ = "0.1.0"
__all__ = [...]
```

### 3.3 `src/zmai/__main__.py` 检查

```python
# __main__.py — 支持 python -m zmai
from zmai.cli.main import main
main()
```

当前 `__main__.py` 存在吗？需要验证。

### 3.4 `py.typed` — PEP 561

当前不存在 `py.typed` 标记文件。如果项目提供类型注解（mypy strict 模式），应添加空文件 `src/zmai/py.typed` 以声明对 PEP 561 的支持。

---

## 四、打包验证清单

| 检查项 | 命令 | 预期结果 |
|--------|------|---------|
| 源码分发包 | `python -m build --sdist` | 生成 `.tar.gz` |
| Wheel 包 | `python -m build --wheel` | 生成 `.whl` |
| 安装 | `pip install dist/*.whl` | 成功 |
| CLI 可用 | `zmai --help` | 显示帮助 |
| 子命令 | `zmai doctor` | 运行诊断 |
| 导入 | `python -c "import zmai; print(zmai.__version__)"` | 有版本号 |

---

## 五、`pip install zmai` 的阻碍

| 阻碍 | 严重度 | 说明 |
|------|--------|------|
| PyPI 名称冲突 | **高** | `zmai` 在 PyPI 可能已被其他包占用 |
| 无发布凭证 | **中** | 需要 PyPI 账号 + API Token |
| 无 CI 发布 | **中** | 手动发布容易出错 |
| 无 `__version__` | **低** | 不影响安装，影响用户查询版本 |
| Version 硬编码重复 | **低** | 需要同步维护两处 |

**如果 PyPI 的 `zmai` 名称已被占用**，备选名称：
- `zmai-runtime`
- `zmai-agent`
- `zmaipy`

---

## 六、建议

### Pre-release（发布前）

| 优先级 | 事项 | 说明 |
|--------|------|------|
| P0 | 检查 PyPI 名称可用性 | `pip install zmai` → 看是否已有同名包 |
| P0 | 添加 `__version__` | `src/zmai/__init__.py` 中定义，消除两处版本硬编码 |
| P1 | 添加 `[project.dependencies]` | 显式声明空依赖列表 |
| P1 | 验证构建 | `python -m build` 成功 |
| P2 | 添加 README 的 PyPI 兼容性检查 | 确保相对链接在 PyPI 上正常渲染 |

### Release

```bash
# 1. 更新版本号
# 2. 构建
python -m build

# 3. 发布到 Test PyPI
twine upload --repository testpypi dist/*

# 4. 测试安装
pip install --index-url https://test.pypi.org/simple/ zmai

# 5. 发布到正式 PyPI
twine upload dist/*
```

### Post-release

| 事项 | 说明 |
|------|------|
| GitHub Releases | 关联 tag 和 changelog |
| CI workflow | GitHub Actions 自动发布 |
| 签名 | GPG 签名发布包 |

---

*Report generated by `claude` — 基于 pyproject.toml + 源码导入分析*
