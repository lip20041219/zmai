# ZMAI 测试运行指南

本文件说明开发者如何运行全量测试，以及如何保存测试输出用于归档。
最新一轮全量结果见 [`FULL_TEST_REPORT.md`](FULL_TEST_REPORT.md)。

## 环境

- Python 3.11.9（`hermes-agent` venv）
- pytest 9.x（另装 `pytest-timeout`、`pytest-anyio`）
- Windows 平台

## 测试配置

收集与运行参数全部来自仓库根目录 `pyproject.toml` 的
`[tool.pytest.ini_options]`：

- `testpaths = ["tests"]`
- `python_files = ["test_*.py"]`
- `norecursedirs = ["hermes_validation"]`：`tests/hermes_validation` 的验证 demo
  数据不参与全量收集，由该目录驱动脚本单独运行。

## 运行全量测试

在仓库根目录 `D:\desk\ZMAI` 执行：

```powershell
# 激活测试 venv 后
python -m pytest
```

预期结果：collected 1320 items，最终
`1311 passed, 9 skipped`（0 failed / 0 errors / 0 warnings），耗时约 23–28 分钟。

### 只运行单文件

```powershell
python -m pytest tests/test_run_agent_encoding.py -v
```

### 只运行单条用例

```powershell
python -m pytest tests/test_swe_regression.py::TestEditToolEdgeCases::test_append_empty_content -v
```

## 保存测试输出

全量测试需 20+ 分钟，务必落盘以便失败时留证、修复后可回看。输出文件统一存放
在 `test-results/`（该目录已被 git 跟踪，原始日志请勿删除）。

### Windows PowerShell

PowerShell 中用 `*>` 把 stdout 与 stderr 一并重定向到文件：

```powershell
# 先确保目录存在
New-Item -ItemType Directory -Force test-results | Out-Null
python -m pytest *> test-results/full-test-output-final.txt
```

### cmd / Git Bash

```bash
mkdir -p test-results
python -m pytest > test-results/full-test-output-final.txt 2>&1
```

### 命名约定

同一验证轮次内多轮复跑时，用后缀区分版本，避免覆盖上一轮证据：

| 文件名 | 含义 |
|---|---|
| `test-results/full-test-output.txt` | 初始全量运行 |
| `test-results/full-test-output-round2.txt` | 修复后的再次运行 |
| `test-results/full-test-output-final.txt` | 最终确认运行 |

复跑同一阶段请使用新后缀（如 `-round3.txt`），不要覆盖既有日志。

## 读取结果

运行结束后查看文件尾部的汇总行，形式为：

```
================ NNNN passed, NN skipped in MM:SS =================
```

带 warning/失败时：

```
========== NNNN passed, NN skipped, K warnings in MM:SS ===========
=========== N failed, NNNN passed, NN skipped in MM:SS =============
```

判定标准（当前基线）：1320 executed → **1311 passed, 9 skipped, 0 failed,
0 warnings**。任何 failed / error / 新增 warning 都应在改动后归零再提交。

## 编码提示

Windows 终端默认 GBK，若在输出中看到 `UnicodeDecodeError: 'gbk' codec` 或乱码，
说明运行环境编码未对齐，可显式指定 UTF-8 后重跑：

```powershell
$env:PYTHONIOENCODING = "utf-8"
python -m pytest *> test-results/full-test-output-final.txt
```

注意：这只解决**输出层**编码问题；若属代码内子进程解码缺陷，仍需修复源码
（参考 `FULL_TEST_REPORT.md` 中 `issue/agent.py` 的案例），而非仅靠环境变量掩盖。
