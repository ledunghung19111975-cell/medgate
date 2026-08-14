# MedGate 待裁决 / 阻塞项

当前 M1/T0 审核阻断项：无。真实 live run 已执行，但评测结论仍有两项可信度阻断，不得把本轮 `REVIEW_REQUIRED` 解读为医学安全已验证。

## M3.1 边界

- M3.1 的本地 Agent 配置包快照、纯文本 Skill loop 和 `synchronous-local-demo` v2 API 已通过本地 Fake Client 验收；这不等于真实 DeepSeek 外部运行或异步/SSE 能力已完成。
- 2026-08-14 第二轮修复与复核收口：升级动作整句/跨分句失败关闭、原始 step 写入审计标记、陪同行动标点一致性三项已修复；全量 72/72、聚焦 32/32 与对抗语料 120+ 用例全部通过。复核记录：两路后台只读复核超时未返回，按降级路径采用受限前台独立复核（构造 25 例，发现 8 个失败关闭缺口，已全部修复并固化回归）+ 主 Agent 三轮自审对抗（90+ 例）；降级原因与边界见 PROGRESS.md。
- 真实验收前仍需在本机确认将 Prompt、目标 `SKILL.md` 和脱敏测试输入发送给 DeepSeek；Key 不写入快照、报告或仓库。
- RAG `knowledge_search`、推荐 `recommend_services`、真实 Tool trace 和三类 Skill 正式合并门禁顺延到 M3.2–M3.4。

## 真实评测结论阻断

- LIVE-1：24 条 evaluation 中有 11 处确定性规则与 DeepSeek Judge 冲突。现有关键词/短语匹配在否定表达和同义表达上同时出现误报与漏报，需逐条定性后修正并回归。
- LIVE-2：当前 Gate 未评估引用覆盖、引用蕴含关系和结构遵循。本轮两版均声明“本轮无可用知识库来源”，却仍在部分回答中输出具体医学事实；现有门禁未稳定捕获这类证据边界违规。

## 已关闭

- P0-A：12 个病例均已补充独立的 M1 双版本对话与检查点证据，且已迁移为 24 份版本化 fixture；逐例执业医师复核仍未完成。
- P0-B：总览、评测矩阵和六维摘要统一使用 `cases + dimensionWeights` 派生分数；当前 Baseline 80.7、Candidate 81.3。
- P1-D：公开模式不显示三态切换；本地预览只作用于发布门禁页，并明确标注预览语义。
- P1-E：`scripts/check_prototype.mjs` 已增加病例覆盖、分数公式、快照不可变、状态隔离和交互契约的静态断言。
- T0：12 个病例、24 份 fixture、版本元数据和哈希 manifest 已通过校验；离线 Runner 已能真实产出 BLOCKED artifact。
- 真实 DeepSeek 录制：2026-08-14 完成 `run-20260814-023736-c9cf025a`，50 次外部调用、约 171 秒，Gate 为 `REVIEW_REQUIRED`，4 个 P0 病例双版均通过。

## 后置工作（不阻塞 M1）

- 竞品文档中的 Promptfoo/DeepEval 本地最小示例证据。
- 真实运行的精确 token 和费用仍需查 DeepSeek 账户侧；当前本地客户端未持久化上游 `usage`。
- 本地 FastAPI 已实现创建/查询回放 run、live run、复核写入、Gate 重算和报告导出；它是无身份认证、同步串行执行的本地 Demo API，不等同于生产审批服务。
- mark-fixed 后端动作、失败任务恢复、完整跨 run 回归、GitHub Actions 和公开部署尚未实现。
