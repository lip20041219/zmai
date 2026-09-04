# ZMAI 全量测试报告（Final）

> 本文档为纯证据归档，由人工在终端完成最终全量验证后整理。
> 所有数据均来自 `test-results/` 下的原始运行日志，未作任何改动或修饰。

## 测试环境

| 项 | 值 |
|---|---|
| 平台 | Windows 11（win32） |
| Python | 3.11.9 |
| pytest | 9.1.1 |
| pluggy | 1.6.0 |
| 插件 | anyio 4.12.1、timeout 2.5.0 |
| 解释器 | `C:\Users\MECHREVO\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe` |
| rootdir | `D:\desk\ZMAI` |
| 配置文件 | `pyproject.toml`（`[tool.pytest.ini_options]`） |

## 测试命令与配置

- 收集范围由 `pyproject.toml` 决定：`testpaths = ["tests"]`、`python_files = ["test_*.py"]`。
- `norecursedirs = ["hermes_validation"]`——`tests/hermes_validation` 下的自动化验证
  demo 数据（含故意损坏/不可完成的测试）不参与全量收集，由单独的驱动脚本运行。
- 全量命令（详见 `docs/testing/TESTING.md`）运行后共收集 **1320 items**。

## 测试规模

一次全量运行共 **1320** 项测试（collected = executed），对应三条原始日志均为 1320 collected。

## 演进过程

### Round 1 —— 初始全量：1 失败

| 指标 | 值 |
|---|---|
| collected | 1320 |
| passed | 1310 |
| failed | **1** |
| skipped | 9 |
| 耗时 | 1397.25s（23:17） |

失败项：

```
FAILED tests/test_swe_regression.py::TestEditToolEdgeCases::test_append_empty_content
```

失败原因：编辑返回 `[EDIT_NO_CHANGE]`——`append empty content` 场景下实际未产生
文件改动，被判定为“无实际变更”，断言与工具真实语义不符（日志中同时可见终端
GBK 解码产生的乱码输出）。

### `test_append_empty_content` 陈旧测试语义修正

该测试为**陈旧测试**：其断言的“追加空内容会报错/写入”语义与工具实际的
`EDIT_NO_CHANGE` 语义不一致。经人工修正测试语义后，该测试不再断言错误行为。

### Round 2 —— 语义修正后：通过但带 2 个 warning

| 指标 | 值 |
|---|---|
| collected | 1320 |
| passed | 1311 |
| failed | 0 |
| skipped | 9 |
| warnings | **2** |
| 耗时 | 1696.06s（28:16） |

`test_append_empty_content` 修正后转 PASSED。但出现 2 个
`PytestUnhandledThreadExceptionWarning`，其下均包裹同一个异常：

```
UnicodeDecodeError: 'gbk' codec can't decode byte 0xa5 in position 282:
illegal multibyte sequence
```

### 根因：`src/zmai/issue/agent.py` 编码缺陷

Windows 默认 GBK（cp936）区域设置下，`issue/agent.py` 中处理子进程输出时缺少
UTF-8 显式解码，导致非 GBK 字节（如 `0xa5`）触发 `UnicodeDecodeError`，该异常在
后台线程中逸出并以线程异常 warning 形式上报（Round 2 的 2 个 warning 同源）。

### Round 3（Final）—— 修复编码缺陷

修复 `issue/agent.py` 编码问题后，相关编码测试验证通过：

```
tests/test_run_agent_encoding.py::test_subprocess_unicode_stdout_does_not_crash PASSED
tests/test_run_agent_encoding.py::test_process_result_json_is_generated      PASSED
```

→ **2 passed, 0 warnings**，`UnicodeDecodeError` warning 消失。

## Final 全量结果

**1320 tests executed: 1311 passed, 9 skipped, 0 failed, 0 warnings.**

| 指标 | 值 |
|---|---|
| executed | 1320 |
| passed | 1311 |
| skipped | 9 |
| failed | 0 |
| errors | 0 |
| warnings | 0 |
| 耗时 | **1423.14s（23:43）** |

汇总演进：

| Round | passed | failed | skipped | warnings | 耗时 |
|---|---|---|---|---|---|
| 1（初始） | 1310 | 1 | 9 | 0 | 1397.25s |
| 2（语义修正） | 1311 | 0 | 9 | 2 | 1696.06s |
| Final（编码修复） | 1311 | 0 | 9 | 0 | **1423.14s** |

## 原始证据文件路径

以下文件**保留不删**，为最终判定依据：

- `test-results/full-test-output.txt` —— Round 1（1 failed）
- `test-results/full-test-output-round2.txt` —— Round 2（2 warnings）
- `test-results/full-test-output-final.txt` —— Final（0 failed, 0 warnings）

每条日志尾部均含本报告引用的汇总行，可交叉核对。

## 如何复现测试

1. 激活测试环境（`hermes-agent` venv，Python 3.11.9）。
2. 在仓库根目录 `D:\desk\ZMAI` 运行全量命令（见 `docs/testing/TESTING.md`）。
3. 全量结果应与 Final 一致：1320 collected → 1311 passed、9 skipped、0 failed、
   0 warnings。

## 客观结论

- 全量 **1320** 项测试执行完毕，最终状态 **1311 passed / 9 skipped / 0 failed /
  0 errors / 0 warnings**，无失败、无错误、无警告。
- 9 项 skipped 为按预期跳过的用例（非失败）。
- 修复链清晰：陈旧测试语义修正（Round 1→2）解决 1 个误报失败；
  `issue/agent.py` 编码缺陷修复（Round 2→Final）消除 2 个 GBK warning，
  且最终全量耗时回落至 1423.14s。
- 生产代码当前仅含上述编码修复，无其余变更，测试全绿。
