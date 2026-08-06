# 任务：修复字符串工具模块的错误

## 背景

`app/string_utils.py` 包含两个字符串处理函数，其中一个存在逻辑错误。

## Bug 描述

- `to_upper(s)` 应该返回全大写字符串，但当前实现返回的是全小写
- `reverse(s)` 实现正确，请勿修改

## 验收标准

运行 `python -m pytest tests/` 必须全部通过（3 个测试）。

先运行测试确认失败，再修复 `app/string_utils.py`，最后重跑测试确认通过。

## 约束

- 只允许修改 `app/string_utils.py`，禁止修改测试文件。
- 完成后立即停止，不要做任何额外操作。
