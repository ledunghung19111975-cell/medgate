# MedGate Agent 入口

本文件适用于本仓库全部目录，是 AI Agent 接手开发前的第一份必读。

**这是什么**：医疗 AI 的**发布门禁**演示项目。对同一批合成病例回放「基线版 / 候选版」两个 Agent 配置包，用确定性规则 + 语义 Judge + 人工复核三层证据算出门禁结论，候选版任何 P0 失败都锁为 `BLOCKED`，与平均分无关。使用方式是**下载到本地自己跑**，不做公开网页。

---

## 一、开工前只读这四份（约 5 万字符）

仓库有 18 份 markdown 共约 30 万字符，**不要通读**。按顺序读这四份就够开工：

| 顺序 | 文件 | 读它拿什么 |
| --- | --- | --- |
| 1 | `README.md` | 项目是什么、怎么跑起来、当前状态表 |
| 2 | `PROGRESS.md` | 什么**已经验证过**、验证证据是什么、未覆盖项在哪 |
| 3 | `BLOCKED.md` | 未解阻断项。**下一步要做的事，规格原文通常在这里** |
| 4 | `14_开发计划_20260818.md` | 只读 **§〇 执行顺序** 与 **§四 贯穿性约束**（尤其 8/9/10 三条） |

若你在作者的桌面工作区内运行，另跑一步拿当前状态与唯一下一步，**不要直接读整份看板**：

```bash
python3 00_工作台/scripts/context-bootstrap.py MedGate
```

## 二、按任务再加载，不要一次全读

| 你要做什么 | 再读 |
| --- | --- |
| 引用门禁 / 提示词相关 | `10_`（V3 提示词，定义 `[K#]` 引用合同）、`11_`（版本管理模板）；代码 `medgate/engine.py`、`live.py`、`prompts.py` |
| 测试集扩充 | `12_`（选型与许可证红线）；`assets/manifest.json`、`assets/testsets/` |
| RAG / Agent Skill / 推荐 Tool | `09_`（45K，仓库最大一份，只在 M3.1–M3.3 相关时读）、`13_`（并发改造） |
| 门禁语义 / 报告结构 | `07_`（真实制品门禁改造）；代码 `medgate/engine.py` |

## 三、⚠️ 这三份别当现行规格读

- **`01_需求方案.md`、`02_技术方案.md`** —— 首版方案，**已被 `03_审核意见_20260812.md` 判为「范围严重超载」并裁剪**。实际执行范围以 `05_关键决策记录.md` 的 **D-01「首发做门禁演示，不做完整评测平台」** 为准。照这两份开发，会去做已经砍掉的完整 CRUD、PostgreSQL、多租户、RBAC。**当历史读，不当规格读。**
- **`03_` / `06_` / `07_`** —— 审核与改造历史。查「为什么这么设计」时才读，不是待办清单。

## 四、动代码前必看的三条不变量

完整十条在 `14_开发计划_20260818.md` §四，下面三条是最容易踩且返工面最大的：

1. **`dimension` 字段不可复用。** 它在 `assets/testsets/pretriage-safety-v1.json` 中**已存在**，承载六个**质量维度**取值（风险识别／行动建议／信息完整／角色边界／依据充分／多轮一致），总览分数经 `dimensionWeights` 派生。新增的**场景类型**分类与它正交，必须另起字段名 `scenario`。覆盖它会直接打烂现有六维摘要与分数口径。
2. **`pretriage-safety-v1` 冻结。** 其 `manifest.json` 硬编码 `expected_case_count=12`、`expected_fixture_count=24`、`expected_gate=BLOCKED`、阻断 fixture 为 `case-003__pretriage-candidate-v2`，并带 sha256 链，是「离线回放真实产出 BLOCKED」这一核心叙事的唯一依托。新测试集一律进**独立 `testset_key` 与独立 manifest**，不得并入，也不得改动这四个 expected 值。
3. **失败关闭语义不可破坏。** 语义层判 fail 必须 fail；词表放宽只作用于规则层的字面误报。

## 五、怎么跑、怎么验证

```bash
# 依赖（本地事实源是 uv.lock；CI 用 pip 装 pyproject 的有界依赖）
python3 -m venv .venv && ./.venv/bin/pip install -e .

# 单元测试：当前 110 项，ResourceWarning 视为错误
./.venv/bin/python -B -W error::ResourceWarning -m unittest discover -s tests

# 资产与 manifest 校验（应 status=ok、fixture_count=24）
./.venv/bin/python -m medgate validate

# 离线回放：无外连、无密钥，退出码应恰好为 1（BLOCKED）
./.venv/bin/python -m medgate run --db artifacts/local.sqlite3 --report artifacts/gate.json --idempotency-key dev-1

# 资产与原型静态检查（只用 node 内置模块，无 npm 依赖）
node scripts/validate_assets.mjs && node scripts/smoke_assets.mjs
node scripts/check_prototype.mjs && node scripts/smoke_prototype.mjs

# 本地服务（http://127.0.0.1:8000/），或双击 启动MedGate.command
./.venv/bin/python -m medgate.api
```

**三态退出码是本项目的核心语义**：`0=PASSED`、`1=BLOCKED`、`2=REVIEW_REQUIRED`（`3` 为执行错误）。写 CI 或脚本断言时用**恰好等于**，不要用「非零即失败」——门禁不该拦却拦了、该拦却没拦，两种都必须失败。

## 六、红线

- **真实 Key 外发必须先获用户确认。** 默认只跑离线回放与 Fake Client；不要自行发起 DeepSeek 真实调用。Key 不写入快照、报告或仓库。
- **许可证。** 只有 Apache / MIT / CC-BY / CC0 系数据可改写并入并署名；GPL 系与无 license 来源仅供参考，零文本入库。
- **数据边界声明不得删改。** 所有合成与改写条目保留「未经执业医师复核，不得用于临床决策」口径。
- **本仓库对外公开。** 不要写入具体公司名、求职过程、其他任务线信息或外部任务编号。项目自身的 `PROGRESS.md` / `BLOCKED.md` / 开发计划仍是过程记录的正当落点。
- **不做的事**（已明确非目标，别顺手加）：完整 CRUD、PostgreSQL、多租户、RBAC、断点续跑、异步/SSE。

## 七、收工动作

每完成一个阶段：全量测试 + 资产校验 + 同步 `PROGRESS.md`／`BLOCKED.md` + 独立提交（形成回滚点）。改了行为就补或改测试。CI 三个 job 必须保持全绿。
