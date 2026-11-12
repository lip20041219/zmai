# ZMAI M2 / P1-3 版本冻结与预检状态

日期：2026-11-12
分支：`feature/evidence-runtime`
**正式 A/B 运行条件：NO。** 版本、模型和数据身份已通过无模型预检；Agent 任意 shell 的文件系统访问尚无 OS 级边界。

## 冻结版本

| 对象 | Commit |
|---|---|
| 旧版 Runtime | `d18d1ae02b9c6bccb1e8a0a9e77f5b04653a2027` |
| 新版 Runtime、探针与评测驱动 | `188684bd3391cf2553733c3d8a3f15c9a7d942aa` |
| 引用上述新版 SHA 的配置文件 | `ddf54393f5c2a8a8fc9f10a183014ada88099910` |

新版源码与探针先提交，配置在后一提交中固定前一提交的 SHA，避免配置文件自引用。冻结清单包含 Runtime、M1/M2 安全链、探针及关联测试；未将工作区的历史实验目录、日志、TUI 等无关文件纳入提交。此处记录的是冻结预检时的状态；后续远端同步与发布结果应以 Git 历史为准。

## 预检调用与结果

```powershell
D:\desk\ZMAI\.venv\Scripts\python.exe benchmarks/paired_runtime_ab.py validate --config benchmarks/paired_runtime_ab_config.json --output D:\desk\ZMAI_AB_PRECHECK_20261112_v4
```

结果：退出码 0，未调用模型或运行实例。两臂实际导入的 `zmai` 路径位于各自独立 worktree，Git HEAD 与配置 Commit 完全一致；Backend 实例实际解析模型均为 `deepseek-flash`。预检检查 8 个缓存实例的 FTP/PTP 列表、60 步、1800 秒墙钟、数据集 SHA、探针/驱动与新版 Commit 的源码一致性、Python 版本和独立验收环境包清单。主要身份摘要：

- 数据集：`cd3dc302e36733f0e01d948cc10828fa7ba001ac85ee6dc742b115c2ec3aee65`
- 探针：`239a3e7145fd96f642a01072ecc5e1838871fda18ee6059090a67eca1ce8fc05`
- 驱动：`a3702aa2890c79e0a7f07e27bb94c42b03bb343ce6b572db176fd645c6756d71`
- 验收环境包清单：`6cbf9bd9e8049041946296db36727094b7e344c88fb044eeb3c1bebd2504c8ea`

预检期间发现 Windows 默认 GBK 对探针日志的解码异常，已把身份检查的捕获明确设为 UTF-8；最终预检无该异常。

## 任务材料与剩余隔离风险

封闭探针只将 `problem_statement` 送进 Agent 提示词，不把 `test_patch` 施加到 Agent 工作仓库；本实例的独立 FTP/PTP 副本在 Agent 退出后创建，数据集路径环境变量在 Agent 启动前移除。8 个实例的输入和验收数据由相同缓存 SHA 固定。结果汇总保留原始 Runtime 终态，candidate 仅在外部 FTP/PTP 明确通过时计 resolved；缺失数据保持未验证。

**未满足的硬边界：** `shell_exec` 可运行任意 shell，同一 OS 用户仍能访问主机上的 SWE-bench 缓存、已完成实例的验收目录或其他绝对路径。独立工作区和提示词白名单不能证明恶意或误导的 Agent 无法跨目录读取 gold/test patch。正式运行的 `run` 模式现默认拒绝；`--allow-unisolated` 仅用于显式的探索运行，不能据此声称无泄漏的正式 A/B。解决此问题需把每个 Agent 执行进程置于只挂载其 base 仓库、无验收缓存的 OS/容器文件系统边界，并让独立验收在边界外执行。

## 定向验证与未验证项

- `D:\desk\ZMAI\.venv\Scripts\python.exe -m pytest tests/test_paired_runtime_ab.py -q`：**6 passed**，覆盖版本导入校验、配置拒绝、两臂配对命令、独立验收统计、缺失值、恢复路径与正式运行默认拒绝。
- 已执行无模型 `validate`，结果如上。未运行真实模型、SWE-bench、全量 pytest、E2E 或正式 A/B。
- 没有 resolved、错误完成率、耗时或 Token 的实验结论。
