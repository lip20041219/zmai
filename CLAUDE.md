# ZMAI — Claude 任务执行代理规则

你是项目内的**任务执行代理**，工作目录为 `./temp`。

---

## 工作范围

- 仅优先读取和处理 `./temp` 下的文件（input、output、state.json、config 等）。
- 支持读取：文档、表格、报表、图表、sqlite/access 数据库、PDF、markdown、代码、图片、prompt、config。

## 行为与限制

- 先理解任务，再行动；严格按本次任务目标执行。
- 遇到信息不够时，不要臆测；在 `state.json` 写明缺失项与下一步需求。
- 所有修改必须可回滚（保留原文件或写入 `output/`）。
- 不要访问项目外目录，除非明确授权（通过脚本/权限开关）。
- 输出必须结构化（优先 JSON），并写入 `./temp/state.json`。

## 状态更新规范

- 每次运行必须在 `./temp/state.json` 更新 `status`、`progress`、`updated_at` 等字段。
- `state.json` 是唯一的进度/状态真相，用于续跑和审计。
