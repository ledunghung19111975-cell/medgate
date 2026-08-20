# assets/｜版本化测试集的数据边界与许可证

本目录（含 `testsets/`、`fixtures/`、`manifest*.json`）的所有内容均为**演示用合成数据**：病例、对话、Judge 证据与分数未经执业医师复核，**不得用于任何临床决策**。单独引用或流转本目录文件时，本声明与 [`NOTICE`](../NOTICE) 一并适用。

## 四个测试集的来源与许可证

| testset_key | 条目数 | 来源 | 许可证（manifest `license_ref`） |
| --- | --- | --- | --- |
| `pretriage-safety-v1` | 12 病例 / 24 fixture | 本人编写的合成病例（`source_type: self_authored_synthetic`） | project-owned（随仓库 MIT） |
| `multidim-v1` | 82 例（FAQ 60 + 边界 22）/ 8 fixture | 本人编写的合成病例 | project-owned（随仓库 MIT） |
| `complex-v1` | 38 例 / 6 fixture | 改写自 CMB-Clin（`source_type: rewritten_from_cmb_clin`） | Apache-2.0 (FreedomIntelligence/CMB)，见 [`NOTICE`](../NOTICE) 与 [`LICENSES/Apache-2.0.txt`](../LICENSES/Apache-2.0.txt) |
| `multi-turn-v1` | 30 例 / 6 fixture | 改写自 CMB-Clin（`source_type: rewritten_from_cmb_clin`） | Apache-2.0 (FreedomIntelligence/CMB)，见 [`NOTICE`](../NOTICE) 与 [`LICENSES/Apache-2.0.txt`](../LICENSES/Apache-2.0.txt) |

## manifest 元数据契约

每个测试集的独立 manifest 都带 `content_status: synthetic_demo_unreviewed`（未经评审的合成演示内容）与 `license_ref` 字段，`medgate validate` 与 `scripts/validate_assets.mjs` 会校验来源白名单（`medgate/assets.py::ALLOWED_PROVENANCE`）。GPL 系与无 license 来源零文本入库。

## 冻结不变量

`pretriage-safety-v1` 的根 manifest 硬编码 `expected_case_count=12`、`expected_fixture_count=24`、`expected_gate=BLOCKED`、阻断 fixture 为 `case-003__pretriage-candidate-v2`，并带 sha256 链——这四个 expected 值与 sha256 链不得改动，新测试集一律进独立 `testset_key` 与独立 manifest。
