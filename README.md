# MedGate｜医疗 AI 评测与发布门禁

[![CI](https://github.com/ledunghung19111975-cell/medgate/actions/workflows/ci.yml/badge.svg)](https://github.com/ledunghung19111975-cell/medgate/actions/workflows/ci.yml)

> 让医疗 AI 的每次版本更新，在 P0 安全问题关闭前无法通过发布检查。

MedGate 是一个**医疗 AI 发布门禁**的本地演示项目：对同一批合成病例回放「基线版 / 候选版」两个 Agent 配置包，用**确定性规则 + 语义 Judge + 人工复核**三层证据计算门禁，任何候选版 P0 失败都会把结论锁为 `BLOCKED`，与平均分无关。

> ⚠️ 这是本人独立编写的**求职作品集 / 技术演示**，不是医疗产品。全部病例、规则、分数与复核记录均为合成演示数据，未经执业医师复核，**不得用于任何临床决策**。

---

## 当前状态

状态标注分三级：**离线可复现**（clone 后即可用命令验证）／**本机已验**（仅作者本机有证据，快照见 `examples/live-reports/`）／**待真实冒烟**（需用户 Key 外发）。

| 阶段 | 状态 |
| --- | --- |
| M1 五页面本地原型（`prototype/index.html`） | ✅ 离线可复现 |
| T0 版本化资产（12 病例、24 份双版本 fixture、manifest 校验） | ✅ 离线可复现 |
| M2 离线评测 Runner / CLI / SQLite（无外连、无密钥） | ✅ 离线可复现 |
| 本地 FastAPI + 手动提示词 live run（`deepseek-v4-flash`） | ✅ 本机已验（Fake Client 链路离线可复现） |
| 提示词资产 V1→V2→V3 单变量迭代闭环（真实重跑验收，12/12 无回归） | ✅ 本机已验（[报告快照](./examples/live-reports/) 已入库） |
| M3.1 Agent 配置包 + 纯文本 Skill 循环评测（`synchronous-local-demo` v2 API） | ✅ 本机已验（Fake Client 验收；真实 v2 smoke 待用户 Key，P3-0） |
| 病例级并发执行 + 429 退避重试 + 预算失败关闭（v2 live） | ✅ 本机已验（真实性能验收待 P2-1） |
| P1-1 多维度 schema + 独立 `multidim-v1` 评估路径 | ✅ 离线可复现 |
| P1-2 FAQ 维 60 条（自建） | ✅ 离线可复现（3 例关键 case 配 fixture，其余 live-only） |
| P1-3 边界维 22 条（自建，NOHARM 实看后判定不适用撤下） | ✅ 离线可复现（bnd-013 配 fixture；21 例 live-only 未评估 → 整体 `REVIEW_REQUIRED`，见下） |
| P1-4 复杂疾病维 38 条（CMB-Clin Apache-2.0 改写，独立 `complex-v1`） | ✅ 离线可复现（3 例关键 case 配 fixture，其余 live-only） |
| GitHub Actions CI（单测 + 资产校验 + 离线门禁三态断言） | ✅ 离线可复现（每次 push 实跑，见页首徽章） |
| P1-5 多轮维 30 条（CMB-Clin 多轮 QA 改写，独立 `multi-turn-v1`） | ✅ 离线可复现（3 例关键 case 配 fixture，其余 live-only） |
| M3.2 SQLite FTS5 中文 RAG（`knowledge_search`） | ✅ 已验证（`cjk_bigram_v1` + 6 条知识库，检索进 trace） |
| M3.3 推荐 Tool（`recommend_services`） | 🚧 未开始 |
| 异步/SSE、正式 repeated 回归、公开脱敏导出 | 🚧 未开始 |
| 公开静态部署 | ❌ 已取消（2026-08-20 用户裁决：使用方式为本地下载运行） |

**multidim 覆盖语义（2026-08-20 起）**：边界层是唯一硬门禁（零容忍），其完整性前提是全部 boundary case 已评估——`medgate run --test-set multidim-v1` 在 21 个 live-only 边界 case 未评估时输出 `REVIEW_REQUIRED`（`BOUNDARY_NOT_EVALUATED`，退出码 2）而非 `PASSED`（**未评估 ≠ 通过**）；live 冒烟补齐回答后回到 `PASSED`。

本仓库的 v2 执行路径明确标注为 `synchronous-local-demo`：无身份认证、同步串行执行的本地 Demo API，**不等同于生产审批服务**。

---

## 快速开始

需要 Python ≥ 3.11。推荐使用 [uv](https://docs.astral.sh/uv/)（仓库带 `uv.lock`），也可以 `pip`。

```bash
# 安装依赖
uv sync                          # 或：python3 -m pip install -e .

# 校验版本化资产（12 病例 / 24 fixture / manifest 哈希）
python3 -m medgate validate
python3 -m medgate validate --test-set multidim-v1   # 82 例（FAQ 60 + 边界 22）
python3 -m medgate validate --test-set complex-v1    # 38 例（CMB-Clin 改写）
node scripts/validate_assets.mjs
node scripts/smoke_assets.mjs

# 运行离线回放并生成 Gate 报告（不调用外部模型、不读 Key）
python3 -m medgate run \
  --idempotency-key local-offline-run-001 \
  --db artifacts/medgate.sqlite3 \
  --report artifacts/gate.json
# 预置候选版含 case-003 P0 回退，预期退出码 1、Gate=BLOCKED

# 多维度测试集离线评估（21 个 live-only 边界 case 未评估 → 预期退出码 2）
python3 -m medgate run --test-set multidim-v1 --report artifacts/multidim-gate.json
# 复杂疾病维（3 例 fixture 满分、35 例 live-only 出 0 分）→ 预期退出码 0
python3 -m medgate run --test-set complex-v1 --report artifacts/complex-gate.json
# 多轮对话维（3 例 fixture 满分、27 例 live-only 出 0 分）→ 预期退出码 0
python3 -m medgate run --test-set multi-turn-v1 --report artifacts/multi-turn-gate.json

# 运行测试
python3 -m unittest discover -s tests -v

# 启动本地工作台（同源 FastAPI + 前端），打开 http://127.0.0.1:8000/
python3 -m medgate.api
```

macOS 也可以直接双击项目根目录的 `启动MedGate.command`。

前端 `prototype/index.html` 依赖 HTTP 同源加载规则与测评集资产，**不能双击 `file://` 打开**。只想查看测评集详情时，在项目根目录执行：

```bash
python3 -m http.server 18181   # 然后打开 http://127.0.0.1:18181/prototype/
```

---

## 目录结构

```
medgate/          Python 包：离线评测引擎、CLI、SQLite、DeepSeek 客户端、Agent 包评测、FastAPI
assets/           版本化测试集（12 例）、24 份双版本 fixture、agents.yaml、manifest（数据边界见 assets/README.md）
examples/agent-pack/   Baseline / Candidate 两套本地 Agent 配置包与脱敏回归测试集
examples/live-reports/ 两份真实 DeepSeek live run 报告快照（BLOCKED 与 REVIEW_REQUIRED 各一）
prototype/        六页面本地工作台（总览 / 测评集详情 / 多维测试集 / 评测详情 / 病例详情 / 发布门禁）
scripts/          资产与原型静态校验脚本（node）
tests/            unittest 回归（当前 155 项）
00_~15_*.md       项目说明、需求、技术、审核、竞品、决策、方案与迭代计划文档
LICENSE           本仓库 MIT；complex-v1 改写来源 Apache-2.0（副本见 LICENSES/，署名见 NOTICE）
```

---

## 安全与数据边界

- **数据**：FAQ/边界/主测试集为本人编写的合成病例（`source_type = self_authored_synthetic`），不包含真实患者数据；复杂疾病维（`complex-v1`）与多轮维（`multi-turn-v1`）改写自 CMB-Clin 开源病例（Apache-2.0，见 [NOTICE](./NOTICE)）。全部内容未经执业医师复核。
- **密钥**：真实评测的 `DEEPSEEK_API_KEY` 只从页面内存或本机环境变量读取，不写入页面、请求体、快照、报告或仓库；`medgate/deepseek.py` 只读 `os.environ`。
- **门禁语义**：`P0` 失败独立决定 `BLOCKED`，不因平均分、阈值或普通备注稀释；条件句、软化句、推迟句、跨分句条件等歧义表达按**失败关闭**处理，不构成无条件升级。
- **回放隔离**：离线 Runner / CLI / fixture 回放不调用外部模型、不读 Key；真实 live run 结果不写入前端默认数据源，刷新回到干净空状态。

---

## 尚未实现（非缺陷，属后续里程碑）

- live 路径的测试集参数化（`/api/v1/live-runs` 当前只支持 `pretriage-safety-v1`；P1-6 live 冒烟与 P2-1 大规模真实运行的前置）
- `recommend_services` 推荐 Tool（M3.3）与正式 repeated 回归、异步/SSE（M3.2 RAG 的 `cjk_bigram_v1` 预分词与 6 条知识库已验证，`knowledge_search` 预检索引入 trace，见 `medgate/rag.py` 与 `assets/knowledge/`）
- 公开脱敏导出
- 公开静态部署（已取消，2026-08-20 用户裁决；CI 见页首徽章）
- 真实运行的精确 token / 费用报告核算与 DB 落盘完善（`usage` 已可在 `Trace` 透传，见 `medgate/deepseek.py` / `medgate/agent.py`，P5-3）
- 病例的执业医师复核

---

## 许可证

本仓库以 [MIT](./LICENSE) 发布。复杂疾病维（`complex-v1`）与多轮维（`multi-turn-v1`）改写自 [FreedomIntelligence/CMB](https://github.com/FreedomIntelligence/CMB) 的 CMB-Clin 病例（Apache-2.0，许可证副本见 [`LICENSES/Apache-2.0.txt`](./LICENSES/Apache-2.0.txt)，署名与改写说明见 [NOTICE](./NOTICE)）。各测试集的来源与许可证元数据见 [`assets/README.md`](./assets/README.md)。

---

## 第三方研究引用

候选底座（Medmarks、CRAFT-MD、Phlox、PhysicianBench、TrialGPT 等）的许可调研记录在 [`05_关键决策记录.md`](./05_关键决策记录.md)；本仓库**未复制或集成**任何第三方代码/数据，仅以合成演示资产实现评测门禁流程。

复杂疾病维（`complex-v1`，38 条）与多轮维（`multi-turn-v1`，30 条）依据 [FreedomIntelligence/CMB](https://github.com/FreedomIntelligence/CMB) 的 CMB-Clin 病例改写（Apache-2.0），改写后的病例与期望回答均为演示用合成内容，**未经执业医师复核，不得用于任何临床决策**；CMB 引用：Wang, Xidong et al., "CMB: A Comprehensive Medical Benchmark in Chinese", arXiv:2308.08833。

---

## 继续阅读

- [`AGENTS.md`](./AGENTS.md) — **AI Agent 接手开发的第一份必读**：只读哪四份、按任务加载什么、哪几份别当现行规格、动代码前必看的不变量、跑与验证的命令
- [`00_项目说明.md`](./00_项目说明.md) — 为什么做、首发范围、3 分钟讲解脚本
- [`01_需求方案.md`](./01_需求方案.md) / [`02_技术方案.md`](./02_技术方案.md) — 首版需求与技术方案。**注意：已被 [`03_审核意见_20260812.md`](./03_审核意见_20260812.md) 判为范围超载并裁剪，实际范围以 [`05_关键决策记录.md`](./05_关键决策记录.md) 的 D-01 为准；当历史读，不当现行规格读**
- [`PROGRESS.md`](./PROGRESS.md) / [`BLOCKED.md`](./BLOCKED.md) — 开发进度与待裁决项
- [`09_Agent配置包与三类Skill评测方案_20260814.md`](./09_Agent配置包与三类Skill评测方案_20260814.md) — M3.1 Agent 评测方案
- [`10_提示词V3迭代_20260817.md`](./10_提示词V3迭代_20260817.md) / [`11_提示词版本管理模板_20260817.md`](./11_提示词版本管理模板_20260817.md) — 提示词 V1→V3 迭代闭环与版本管理
- [`12_医疗测评集选型调研_20260817.md`](./12_医疗测评集选型调研_20260817.md) — 多维度测评集选型（四维分布与许可证评估）
- [`13_并发执行改造方案_20260817.md`](./13_并发执行改造方案_20260817.md) — 病例级并发 + 429 退避 + 预算关闭
- [`14_开发计划_20260818.md`](./14_开发计划_20260818.md) — 当前排期基线：执行顺序、六阶段状态、十一条贯穿性约束与变更记录
