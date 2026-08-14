import fs from "node:fs";
import vm from "node:vm";

const file = new URL("../prototype/index.html", import.meta.url);
const html = fs.readFileSync(file, "utf8");
const testset = JSON.parse(fs.readFileSync(new URL("../assets/testsets/pretriage-safety-v1.json", import.meta.url), "utf8"));
const required = [
  "总览", "评测详情", "病例详情", "发布门禁",
  "测评集详情", "数据集与判断规则", "测评集详情与判断规则", "发布门禁如何判定", "否定句中的命中不计",
  "设置", "调整测评集", "当前页面没有评测结果", "运行真实评测",
  "Baseline 提示词", "Candidate 提示词", "deepseek-v4-flash",
  "真实运行", "case_ids", "X-DeepSeek-API-Key", "consumeLiveRunStream",
  "parseLiveRunHttpError", "REQUEST_VALIDATION_ERROR", "先配置 Key", "Key <strong>",
  "getReader", "run_started", "item_completed",
  "评测严重度", "判定摘要", "最终结论", "确定性规则", "关键词/模式匹配", "语义识别", "语义 Judge",
  "规则与 Judge 结论冲突", "必须完成的安全动作", "不得出现的表述", "技术信息",
  "数据不完整，无法判定", "自动通过·待复核", "病例自动结论"
];
const forbidden = [
  "当前演示运行", "run-demo-001", "run-demo-002", "report-demo-001",
  "M1 原型", "预置运行", "三态语义预览", "合成演示数据",
  "reportSnapshot", "state.gatePreview", "localStorage", "sessionStorage",
  "固定离线回放", "offline_fixture", "ASSET_FIXTURES", "buildOfflineReplay", "离线回放结果"
];
const missing = required.filter((text) => !html.includes(text));
const forbiddenFound = forbidden.filter((text) => html.includes(text));
const ids = [...html.matchAll(/\sid=["']([^"']+)["']/g)].map((match) => match[1]);
const duplicateIds = [...new Set(ids.filter((id, index) => ids.indexOf(id) !== index))];
const scripts = [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi)].map((match) => match[1]);
const externalResources = /<script[^>]+src=|<link[^>]+href=|XMLHttpRequest|WebSocket|EventSource/i.test(html);
const externalUrls = [...html.matchAll(/https?:\/\/[^\s"'<>]+/gi)].map((match) => match[0]);
const credentialMarkers = [
  /Bearer\s/i,
  /DEEPSEEK_API_KEY/i,
  /access[_ -]?token/i,
  /localStorage/i,
  /sessionStorage/i
].filter((pattern) => pattern.test(html)).map((pattern) => pattern.source);

function fail(message, details = {}) {
  console.error(JSON.stringify({ error: message, ...details }, null, 2));
  process.exit(1);
}

function assert(condition, message, details = {}) {
  if (!condition) fail(message, details);
}

assert(!missing.length && !forbiddenFound.length && !duplicateIds.length && scripts.length === 1 && !externalResources && !externalUrls.length && !credentialMarkers.length, "static artifact checks failed", {
  missing,
  forbiddenFound,
  duplicateIds,
  inlineScripts: scripts.length,
  externalResources,
  externalUrls,
  credentialMarkers
});

try {
  new vm.Script(scripts[0]);
} catch (error) {
  fail("inline script syntax error: " + error.message);
}

const source = scripts[0];
const liveFetchCalls = [...source.matchAll(/window\.fetch\(LIVE_RUN_ENDPOINT/g)];
const assetFetchCalls = [...source.matchAll(/window\.fetch\(path/g)];

assert(assetFetchCalls.length === 1, "测评集必须通过单一 fetchAsset helper 加载", { assetFetchCalls: assetFetchCalls.length });
assert(liveFetchCalls.length === 1, "真实运行必须只有一个相对 live fetch", { liveFetchCalls: liveFetchCalls.length });
assert(source.includes("activeRun: null"), "页面默认状态必须没有运行结果");
assert(source.includes("function buildFromLiveResult"), "页面必须从真实 API 返回构建结果");
assert(source.includes("function buildTestset"), "页面必须从测评集资产构建配置");
assert(source.includes("const hasRun = () => Boolean(state.activeRun && cases.length)"), "页面必须只在真实运行结果存在时展示结果页");
assert(!source.includes("offlineReplay") && !source.includes("ASSET_FIXTURES") && !source.includes("buildOfflineReplay"), "前端首屏不能构建固定 fixture 回放");
assert(source.includes("case_ids: state.testset.selectedCaseIds"), "真实请求必须携带当前选择的病例");
assert(source.includes("headers[API_KEY_HEADER] = state.settings.apiKey"), "设置中的 Key 必须进入本机请求头");
assert(source.includes("if (!localRuntime)") && source.includes("window.location.port"), "真实运行必须受本机服务地址约束");
assert(source.includes("await loadAssets()"), "页面必须在启动时加载测评集");
assert(!source.includes("const cases = [") && !source.includes("const detail = {"), "页面不能内嵌病例副本");
for (const entry of testset) {
  assert(!source.includes(String(entry.title)), "病例标题不能硬编码到页面：" + entry.case_id);
}
assert(source.includes("escapeHtml(prettyJson(result.comparison))") && source.includes("escapeHtml(prettyJson(result.evaluations))") && source.includes("escapeHtml(prettyJson(result.report))"), "真实返回内容必须转义后再渲染");
assert(source.includes("function formatModelText") && source.includes("formatModelText(turn[1])"), "模型回答必须通过安全 Markdown 子集渲染");
assert(source.includes("function caseDecisionSummary") && source.includes("function checkpointPanel"), "病例详情必须拆分判定摘要与评测标准");
assert(source.includes("candidateEvaluation.rule_verdict") && source.includes("candidateEvaluation.judge_verdict"), "页面必须分别读取规则与 Judge 结论");
assert(source.includes("function normalizeVerdict") && source.includes("const dataComplete =") && source.includes("!dataComplete"), "缺失或非法评测字段必须失败关闭");
assert(source.includes("function updateEvaluationTable") && source.includes("updateEvaluationTable();"), "评测筛选必须只更新表格");
assert(!source.includes("state.mode") && !html.includes("mode-toggle"), "原型不能恢复双模式切换");
assert(/<button class="nav-button" data-route="rules">[\s\S]*?<span class="nav-title">测评集详情<\/span>[\s\S]*?<span class="nav-caption">数据集与判断规则<\/span>/.test(html), "左侧必须提供测评集详情入口");

console.log(JSON.stringify({
  file: file.pathname,
  requiredMarkers: required.length,
  testsetCaseCount: testset.length,
  defaultState: "empty",
  dataSources: ["assets/testsets", "/api/v1/rules", "assets/manifest"],
  liveEndpoint: "/api/v1/live-runs/stream",
  liveFetchCalls: liveFetchCalls.length,
  assetFetchCalls: assetFetchCalls.length,
  duplicateIds: 0,
  inlineScripts: 1,
  externalResources: false,
  externalUrls: 0,
  credentialMarkers: 0,
  status: "ok"
}, null, 2));
