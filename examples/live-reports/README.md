# live-reports/｜真实 DeepSeek 运行报告样本（入库快照）

本目录是「真实运行维度」的入库证据：两份报告均产自 2026-08-17 本机真实 DeepSeek live run（模型 `deepseek-v4-flash`），原文快照入库、未删改。clone 后可直接查看，无需 Key。

| 文件 | run_id | Gate | 说明 |
| --- | --- | --- | --- |
| `live-run-20260817-022535-blocked.json` | run-20260817-022535-60a8f6fd | `BLOCKED`（exit 1，4 个 P0） | 50 次外部调用。含 case-002 的规则层捕获 Judge 幻觉证据（Judge 声称回答含「驾车」并给出证据引用，原文无此字样） |
| `live-run-20260817-144511-v3-review.json` | run-20260817-144511-56dd7d67 | `REVIEW_REQUIRED`（exit 2，0 findings） | 55 次外部调用，提示词 V3 验收 run（12/12 无回归） |

- 报告不含 API Key 或任何凭证（`deepseek.py` 只从 `os.environ` 读 Key，不进报告/快照/日志）。
- 病例对话、Judge 证据与分数均为合成演示内容，**未经执业医师复核，不得用于临床决策**。
- 完整运行历史（15 次 live run）保存在本机 `artifacts/`（`.gitignore` 排除）；本目录只入库两份代表性快照。
