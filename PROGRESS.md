# MedGate 开发进度

## 当前事实（2026-08-14）

- M1 五页面本地交互原型、T0 版本化资产、M2 离线 Runner、本地 FastAPI、手动提示词 live run 与一次真实 DeepSeek 录制已完成；CI 和公开部署尚未完成。
- 2026-08-14 真实 run `run-20260814-023736-c9cf025a` 使用 `deepseek-v4-flash`，12 例产生 24 条 evaluation、50 次外部调用，约 171 秒完成；Gate 为 `REVIEW_REQUIRED`，4 个 P0 病例的双版结果均为 100 分且无 P0 Finding。
- 真实运行显示 V2 的四段结构遵循率为 12/12（V1 为 0/12），但平均分 95.42 低于 V1 的 95.83；24 条结果中有 11 处确定性规则与 Judge 冲突，当前不能宣称 V2 质量更好。
- 原型首次打开为干净空状态，只加载 `assets/testsets/`、`assets/manifest.json` 和 `/api/v1/rules`；`assets/fixtures/` 不进入前端默认数据源，真实 live 完成后才构建当前结果。对话、Judge 证据与分数仍是合成内容，未经过执业医师复核。
- 六维分数由病例主维度和需求权重派生，当前显示 Baseline 80.7、Candidate 81.3；分数只用于比较与定位，P0 独立决定 Gate。
- `report-demo-001` 是固定的 BLOCKED 历史快照；最新 Gate、三态语义预览和跨 run Finding 生命周期分开保存。

## M3.1 本轮新增（2026-08-14）

- [x] 新增 `medgate/agent.py`：受控 `example-pack` / `local-assets` 根、no-follow 安全读取、Prompt/Skills 扫描、UTF-8/大小/深度/预置输出预检、Baseline/Candidate 制品 diff、变更覆盖矩阵和不可变 Agent 快照。
- [x] 冻结 M3.1 `PackageSnapshot → AgentLoop → Trace → AssertionResult → Gate` 数据合同；`snapshot_hash`、`run_input_hash`、`gate_input_hash` 和实际回答/断言轨迹分层保存，部分失败只生成独立 provisional Gate，不生成最终 Gate。
- [x] 新增 `/api/v2/local-assets/roots`、`/api/v2/local-assets/entries`、`/api/v2/agent-packages/inspect`、`/api/v2/live-runs` 和运行查询；新增 SQLite `agent_snapshots` / `agent_runs` / `agent_run_steps` 表。当前 v2 明确为 `synchronous-local-demo`，不宣称异步/SSE 或生产服务。
- [x] 新增脱敏 `examples/agent-pack/`：两版本地 Prompt、只改 Candidate `SKILL.md` 的纯文本回归集；本地 `local-assets/` 与 raw trace 目录不进入仓库。

## T0 / M2 本轮新增

- [x] 新增 `assets/agents.yaml`、12 例测试集、24 份双版本 fixture 与 `assets/manifest.json`。
- [x] 新增 `scripts/validate_assets.mjs`、`scripts/smoke_assets.mjs`，校验覆盖率、schema、来源边界、哈希和预期阻断项。
- [x] 新增无外部调用的 Python 离线 Runner：`medgate validate`、`medgate run`、`medgate gate`。
- [x] 新增 SQLite 运行记录、attempt、evaluation result、Finding、GateDecision、ReportSnapshot 和 audit event。
- [x] 用规则层 + 固定 Judge 结果计算 `case-003` 候选版 P0，真实生成 `BLOCKED` 报告和 CLI 退出码 1。
- [x] 新增哈希绑定的 `assets/reviews/demo-confirmed-p0.json`；确认型人工复核包重放后仍保持 `BLOCKED`。
- [x] 新增本地 FastAPI：创建/幂等重放 run、查询对比与 Gate、写入复核、重算 Gate、导出报告快照。
- [x] 补齐 `request_hash`、`rule_hash`、`judge_hash`、`review_pack_hash`、快照哈希一致性、active attempt 覆盖守卫和精确 occurrence 复核绑定。
- [x] 新增 `/api/v1/live-runs`：页面手工输入两版提示词，服务端固定调用 `deepseek-v4-flash`，保存回答、Judge、确定性规则证据与提示词哈希。
- [x] 保留 `/api/v1/runs` 与 `medgate run` 的纯回放语义；公开模式不发 live 请求，也不读取 live 结果。

## 本轮修复

- [x] 修复病例详情标题插值，任意病例显示真实 Case ID。
- [x] 删除通用 `defaultDetailFor` 占位路径，12 个病例均有独立双栏对话和证据文本。
- [x] 从 `cases` 与 `dimensionWeights` 派生六维分数和总分，移除旧的 81.5 / 78.4 / +3.1 硬编码。
- [x] 公开模式隐藏三态切换；本地预览只影响发布门禁页，不改变总览、评测矩阵或历史快照。
- [x] 增加 `confirmed_pending_regression → fixed_pending_regression → regression_passed` 的本地演示路径。
- [x] 复用运行弹窗增加 Baseline/Candidate 双提示词输入、预计 50 次调用提示、运行态、脱敏错误和真实报告展示。
- [x] 首次真实运行暴露回答被 `max_tokens=512` 截断；Agent 输出上限提高到 1024，Judge 保持 700，并新增调用参数回归断言。
- [x] 搜索和筛选只更新病例表格，不重建整页；移除页面级 `min-width`，增加窄屏布局。
- [x] 左侧新增“测评集详情”入口；页面从测评集资产和 `/api/v1/rules` 只读展示病例覆盖、判定流程、门禁、否定句豁免、匹配模式和规则引用，规则接口不可达时保留病例明细并降级提示。
- [x] 按最新需求取消前端首屏固定 fixture 回放：`activeRun` 默认为空，调整测评集后清空当前结果，只有真实 live run 完成后才渲染服务端结果；fixture 保留给 CLI/Runner/CI。

## 验证记录

- `node scripts/check_prototype.mjs`：通过；确认首屏空状态、单一 live endpoint、规则/测评集资产加载、无前端 fixture 回放、无凭证标记、无重复 ID 和无外部资源。
- `node /Users/zhang/.codex/skills/html-prototype-artifacts/scripts/check-html-artifact.mjs prototype/index.html --must-have '总览' --must-have '评测详情' --must-have '病例详情' --must-have '发布门禁' --must-have 'BLOCKED' --must-have 'PASSED' --must-not-have 'defaultDetailFor'`：通过。
- `node scripts/smoke_prototype.mjs`：通过空状态首屏、规则详情、测评集缩减后清空、live 成功/错误合同、服务端文本转义、搜索过滤和跨 run 断言。
- `node /Users/zhang/.codex/skills/html-prototype-artifacts/scripts/check-html-artifact.mjs prototype/index.html --must-have '当前页面没有评测结果' --must-have '测评集详情' --must-not-have '固定离线回放' --must-not-have 'offline_fixture'`：通过；同源浏览器确认首屏为空、规则详情仍可见，控制台 error/warning 为空。
- 历史原型阶段复核记录：两路只读审查曾因等待窗口未返回，按当时范围降级为主 Agent 自审；该记录不作为 M3.1 实现验收结论。
- `python3 -W error::ResourceWarning -m unittest discover -s tests -v`：40 项通过；覆盖 50 次编排、多轮上下文、幂等零重复调用/失败防重费、跨回放/live key 冲突、prompt hash、Judge 失败关闭、否定式与虚构时间规则、Finding 稳定身份/复用/重算、仓库 ReviewPack、错误脱敏、同源/CORS 和旧 API/CLI 回归。
- `./.venv/bin/python -W error::ResourceWarning -m unittest discover -s tests -v`：历史 M3.1 基线 46 项通过；后续复核新增回归后当前总数见下方 M3.1 收口记录。
- `node scripts/validate_assets.mjs`、`node scripts/smoke_assets.mjs`、`./.venv/bin/python -m medgate validate`、`node scripts/check_prototype.mjs`、`node scripts/smoke_prototype.mjs`：均通过。
- Codex 应用内浏览器复验：FastAPI 同源页面、只读/本地模式、双提示词表单、50 次调用与敏感内容提醒、空表单拦截均正常；控制台 error 日志为空。
- 真实 DeepSeek 复验：`live_submissions` 记录 02:34:45Z–02:37:36Z 完成，报告快照 `report-03dc790556784afab1cfb3f92478b8ff`，制品为 `artifacts/live-gate-eeaa283d03de16b1.json`。

## M3.1 本地实现与回归基线（2026-08-14）

- [x] 预检与运行：受控资产根、逐级 `openat + O_NOFOLLOW`、Prompt/Skill/测试集大小与编码边界、目标 Skill 双侧存在、变更覆盖和 selected case coverage 均有回归；快照正文/哈希篡改会被拒绝。
- [x] 执行合同：只允许脱敏纯文本 `smoke_once`；禁止预置回答/Tool/RAG/推荐结果；单次输入 16,000 token、全程 300 calls/1,500,000 estimated tokens 均在预检和外调前执行，预算口径含实际系统提示、消息 framing 与多轮 assistant 上界。
- [x] 门禁证据：当前回归覆盖否定/疑问/条件/选择/延迟语义与明确肯定升级动作；P0 失败为 `BLOCKED`；partial failure 仅生成 provisional Gate，并恢复已落盘的 Trace、Assertion、调用数、预算与模型漂移；恢复层从 Trace 重新执行断言，`AssertionResult.evidence_hash`、Gate policy/decision 均进入哈希边界。
- [x] API/持久化：幂等重放先查已有 run，可在快照过期或缺 Key 时回放；新 run 缺 Key 不消费快照；断言/Gate step 事务性落 SQLite，外层 step 失败使用下一序号并恢复已有证据，provisional step 落库失败显式标记 `step_persistence_incomplete`；v2 对外标注 `synchronous-local-demo`，`formal_repeated` 未实现不进入 OpenAPI。
- [x] 最新本地验证：`./.venv/bin/python -B -W error::ResourceWarning -m unittest tests.test_agent -v` 为 29/29；全量 `unittest discover -s tests -v` 为 69/69；`compileall`、资产校验、`medgate validate`、原型 check/smoke、`git diff --check` 均通过。仅有既存 Starlette/httpx 弃用警告。
- [ ] 未覆盖：尚未使用用户本机真实 DeepSeek Key 发起新的 v2 外部 smoke；Skill 参考 Markdown 尚未按显式引用加载；v1 全局 422 安全错误 shape 与历史兼容性仍为既有边界；M3.2 RAG、M3.3 推荐 Tool、正式重复回归、异步/SSE、公开脱敏导出未实现。
- [ ] 阶段未最终放行：最后完成的一路对抗复核仍报告 P0/P1/P2=1/3/1，另一路最新复核在关闭前未返回；残余重点为升级动作整句后缀/跨分句冲突、原始 step 写入失败的审计完整性，以及陪同行动在标点变化下的语义一致性。当前 29/29 与 69/69 只证明本地回归基线，不证明 P0 安全验收通过。
- [ ] 阶段唯一下一步：收敛最后一轮对抗复核的 P0 后重新验收 M3.1；在此之前不启动真实 DeepSeek Key 外发。

## 未覆盖项

- 当前 Gate 未检查引用覆盖率、引用蕴含关系和固定版式；真实输出在“无可用知识库”时仍包含具体医学事实，但未被现有门禁稳定捕获。
- 确定性规则在否定语义和同义表达上存在明显误报/漏报；本轮 11 处规则与 Judge 冲突需人工复核后固化回归。
- DeepSeek 客户端尚未持久化 `usage`，因此只能核对调用数与耗时，不能从本地报告声称精确 token 或费用。
- 尚无 GitHub Actions 或公开部署；SQLite、离线 Runner、CLI、本地 FastAPI、前端 live 接线和内容哈希已完成最小切片。
- 竞品文档仍属于官方公开资料整理，Promptfoo/DeepEval 的本地最小示例尚未执行。
- 病例、规则、分数和复核角色均为本人编写的合成演示，尚未经过执业医师复核。
- M3.1 目前只用注入 Fake Client 做离线验收，尚未使用用户本机 DeepSeek Key 发起新的纯文本 Agent v2 外部运行；真实模型验收仍需用户在本机确认外发后执行。
- M3.2 SQLite FTS5 RAG、M3.3 推荐 Tool、v2 页面接线/SSE、公开脱敏报告导出和三类 Skill 统一正式回归尚未实现；本轮没有把它们写成已完成能力。
