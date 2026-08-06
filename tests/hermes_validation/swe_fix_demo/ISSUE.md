# 任务：修复 calculator 模块的算术错误

## 背景

项目 `app/` 下的 `calculator.py` 是一个简单的四则运算工具库，
但其中两个函数的运算逻辑写错了。

## Bug 描述

1. `add(a, b)` 应该返回 `a + b`，但当前实现返回的是 `a - b`
2. `multiply(a, b)` 应该返回 `a * b`，但当前实现返回的是 `a + b`

## 验收标准

运行 `python -m pytest tests/` 必须全部通过（4 个测试）。

- 测试 `tests/test_calculator.py` 中已有对 add/subtract/multiply/divide 四个函数的验证。
- 请先运行测试确认失败，再定位原因，修改 `app/calculator.py`，最后重新运行测试确认全部通过。

## 约束

- 只允许修改 `app/calculator.py`，禁止修改测试文件。
- 保持 subtract/divide 两个函数原有行为不变。
- 完成后输出修复说明。
