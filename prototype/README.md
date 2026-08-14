# MedGate 本地评测工作台

这是 MedGate 的五页面本地工作台：总览、测评集详情、评测详情、病例详情、发布门禁。左侧“测评集详情”在未运行评测时也可直接查看病例覆盖、判定流程、关键词/模式匹配、语义识别、否定句豁免和规则引用；页面首次打开保持干净空状态，不加载历史、演示或 fixture 结果；只有完成一次真实运行后，当前页面才展示本次返回的 Gate、结果与证据。

病例详情把 Candidate 的自动结论拆成“关键词/模式匹配（确定性规则）”与“语义识别 Judge”两条证据链，并单独展示本页人工复核记录：先说明为什么通过、被拦截或仍需复核，再展示公开的评测标准、双版本回答与技术定位信息。人工记录不会直接改写原始运行或服务端 Gate。模型回答只做安全的本地 Markdown 子集渲染（段落、列表、加粗），不执行返回内容中的 HTML。

页面自身不保存病例、分数或对话副本：测评集元数据从 `assets/testsets/` 读取，真实运行结果从本次 API 返回构建；`assets/fixtures/` 仅供 CLI/Runner/CI 显式执行离线回放，不进入前端首屏。点击“调整测评集”可以选择本次运行使用的病例，选择后会清空当前结果，并随下一次真实请求发送到本机服务。

“测评集详情”是只读事实展示：病例的期望安全动作与禁止性表述来自测评集资产；关键词/正则模式、否定词和规则哈希通过 `/api/v1/rules` 从服务端规则目录读取，语义识别由 Judge 单独返回，再与关键词/模式结果合并。规则目录不可达时，页面仍保留测评集明细，并明确提示无法展示匹配模式。

## 本地打开

macOS 可直接双击项目根目录的 `启动MedGate.command`。启动器会检查依赖、拉起同源 FastAPI 并打开页面；进入页面后点击左下角“设置”输入 Key。Key 只保存在当前页面内存中，关闭页面后需要重新配置。关闭启动器终端窗口即可停止服务。

也可以在项目根目录手动安装依赖并启动同源 FastAPI：

```bash
python3 -m pip install -e .
python3 -m medgate.api
```

然后打开 <http://127.0.0.1:8000/>。

真实评测只在 loopback HTTP 环境（`127.0.0.1` / `localhost:8000`）显示入口并允许提交。每次运行使用当前选中的病例，并在本机 `artifacts/` 保存提示词全文、模型回答与证据；不要在提示词中输入模型凭证、真实患者信息或其他敏感内容。页面不会把真实运行结果写成默认数据，刷新后回到干净空状态。

真实运行的 Key 只存在于当前页面内存或服务进程的环境变量中；页面设置通过本机请求头发送，服务端也支持 `DEEPSEEK_API_KEY` 作为后备配置。不要把值写入文件或仓库。没有 Key 时，真实运行会失败关闭且不返回伪造 Gate。刷新页面会回到干净空状态，而不是保留上一次真实运行。

页面的测评集规则数据在运行时从 `assets/testsets/`、`/api/v1/rules`、`assets/manifest.json` 读取，因此**必须通过 HTTP 打开，不能用 `file://` 双击 `index.html`**。只想查看测评集详情时，在**项目根目录**执行 `python3 -m http.server 18181`，打开 <http://127.0.0.1:18181/prototype/>；这个方式能查看规则与病例标准，但不会提供真实评测 API。

## 运行 T0 与离线 Runner

在项目根目录执行：

```bash
python3 -m medgate validate
node scripts/validate_assets.mjs
node scripts/smoke_assets.mjs
python3 -m unittest discover -s tests -v
```

运行固定回放并生成 SQLite 与报告：

```bash
python3 -m medgate run \
  --idempotency-key local-offline-run-001 \
  --db artifacts/medgate.sqlite3 \
  --report artifacts/gate.json
```

当前预置候选版包含 `case-003` P0 回退，因此命令预期退出码为 `1`，报告状态为 `BLOCKED`。Runner 不调用外部模型、不读取 API Key，也不接收真实患者数据。

健康检查：<http://127.0.0.1:8000/health>。`/api/v1/runs` 与 CLI 默认使用版本化资产且不调用外部模型；`/api/v1/live-runs/stream` 只在本地工作区显式提交两版提示词时调用固定模型，并通过 SSE 实时推送阶段、增量回答、病例完成数和最终报告；`/api/v1/live-runs` 保留阻塞式 JSON 兼容入口。两条真实运行 API 都要求 `Idempotency-Key`，并支持 `case_ids` 选择病例；页面设置中的 Key 通过 `X-DeepSeek-API-Key` 仅发送给本机 API。工作台按单用户单运行设计，已有真实评测未结束时会返回 `LIVE_RUN_BUSY`，避免后台排队和重复计费。
