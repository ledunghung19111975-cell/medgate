# MedGate｜医疗 AI 评测与发布门禁

[![CI](https://github.com/ledunghung19111975-cell/medgate/actions/workflows/ci.yml/badge.svg)](https://github.com/ledunghung19111975-cell/medgate/actions/workflows/ci.yml)

> 让医疗 AI 的每次版本更新，在 P0 安全问题关闭前无法通过发布检查。

MedGate 是一个**医疗 AI 发布门禁**的本地演示项目：对同一批合成病例回放「基线版 / 候选版」两个 Agent 配置包，用**确定性规则 + 语义 Judge + 人工复核**三层证据计算门禁，任何候选版 P0 失败都会把结论锁为 `BLOCKED`，与平均分无关。

> ⚠️ 这是本人独立编写的**求职作品集 / 技术演示**，不是医疗产品。全部病例、规则、分数与复核记录均为合成演示数据，未经执业医师复核，**不得用于任何临床决策**。

---

## 当前状态

| 阶段 | 状态 |
| --- | --- |
| M1 五页面本地原型（`prototype/index.html`） | ✅ 完成 |
| T0 版本化资产（12 病例、24 份双版本 fixture、manifest 校验） | ✅ 完成 |
| M2 离线评测 Runner / CLI / SQLite（无外连、无密钥） | ✅ 完成 |
| 本地 FastAPI + 手动提示词 live run（`deepseek-v4-flash`） | ✅ 完成 |
| 提示词资产 V1→V2→V3 单变量迭代闭环（真实重跑验收，12/12 无回归） | ✅ 完成 |
| M3.1 Agent 配置包 + 纯文本 Skill 循环评测（`synchronous-local-demo` v2 API） | ✅ 完成 |
| 病例级并发执行 + 429 退避重试 + 预算失败关闭（v2 live） | ✅ 完成 |
| M3.2 SQLite FTS5 中文 RAG（`knowledge_search`） | 🚧 未开始 |
| M3.3 推荐 Tool（`recommend_services`） | 🚧 未开始 |
| GitHub Actions CI（单测 + 资产校验 + 离线门禁三态断言） | ✅ 完成 |
| 异步/SSE、正式 repeated 回归、公开脱敏导出 | 🚧 未开始 |
| 公开静态部署 | 🚧 未开始 |

本仓库的 v2 执行路径明确标注为 `synchronous-local-demo`：无身份认证、同步串行执行的本地 Demo API，**不等同于生产审批服务**。

---

## 快速开始

需要 Python ≥ 3.11。推荐使用 [uv](https://docs.astral.sh/uv/)（仓库带 `uv.lock`），也可以 `pip`。

```bash
# 安装依赖
uv sync                          # 或：python3 -m pip install -e .

# 校验版本化资产（12 病例 / 24 fixture / manifest 哈希）
python3 -m medgate validate
node scripts/validate_assets.mjs
node scripts/smoke_assets.mjs

# 运行离线回放并生成 Gate 报告（不调用外部模型、不读 Key）
python3 -m medgate run \
  --idempotency-key local-offline-run-001 \
  --db artifacts/medgate.sqlite3 \
  --report artifacts/gate.json
# 预置候选版含 case-003 P0 回退，预期退出码 1、Gate=BLOCKED

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
assets/           版本化测试集（12 例）、24 份双版本 fixture、agents.yaml、manifest
examples/agent-pack/   Baseline / Candidate 两套本地 Agent 配置包与脱敏回归测试集
prototype/        五页面本地工作台（总览 / 测评集详情 / 评测详情 / 病例详情 / 发布门禁）
scripts/          资产与原型静态校验脚本（node）
tests/            unittest 回归（当前 110 项）
00_~14_*.md       项目说明、需求、技术、审核、竞品、决策、方案与迭代计划文档
```

---

## 安全与数据边界

- **数据**：只用本人编写的合成病例（`source_type = self_authored_synthetic`），不包含真实患者数据；未经执业医师复核。
- **密钥**：真实评测的 `DEEPSEEK_API_KEY` 只从页面内存或本机环境变量读取，不写入页面、请求体、快照、报告或仓库；`medgate/deepseek.py` 只读 `os.environ`。
- **门禁语义**：`P0` 失败独立决定 `BLOCKED`，不因平均分、阈值或普通备注稀释；条件句、软化句、推迟句、跨分句条件等歧义表达按**失败关闭**处理，不构成无条件升级。
- **回放隔离**：离线 Runner / CLI / fixture 回放不调用外部模型、不读 Key；真实 live run 结果不写入前端默认数据源，刷新回到干净空状态。

---

## 尚未实现（非缺陷，属后续里程碑）

- SQLite FTS5 中文 RAG 与 `knowledge_search`、`recommend_services` 推荐 Tool（M3.2–M3.3）
- 正式 repeated 回归、异步/SSE、公开脱敏报告导出
- 公开静态部署（CI 已完成，见页首徽章）
- 真实运行的精确 token / 费用核算（本地客户端未持久化上游 `usage`）
- 病例的执业医师复核

---

## 第三方研究引用

候选底座（Medmarks、CRAFT-MD、Phlox、PhysicianBench、TrialGPT 等）的许可调研记录在 [`05_关键决策记录.md`](./05_关键决策记录.md)；本仓库**未复制或集成**任何第三方代码/数据，仅以合成演示资产实现评测门禁流程。

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
- [`14_开发计划_20260818.md`](./14_开发计划_20260818.md) — 当前排期基线：执行顺序、六阶段状态、十条贯穿性约束与变更记录
