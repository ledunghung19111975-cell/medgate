import fs from "node:fs";
import vm from "node:vm";

const file = new URL("../prototype/index.html", import.meta.url);
const html = fs.readFileSync(file, "utf8");
const scriptStart = html.indexOf("<script>");
const scriptEnd = html.lastIndexOf("</script>");
if (scriptStart < 0 || scriptEnd <= scriptStart) throw new Error("prototype inline script not found");
const source = html.slice(scriptStart + "<script>".length, scriptEnd);

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

function response(status, payload) {
  const serialized = JSON.stringify(payload);
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => payload,
    text: async () => serialized
  };
}

function streamResponse(events) {
  const text = events.map(({ type, data }) => `event: ${type}\ndata: ${JSON.stringify(data)}\n\n`).join("");
  const normalized = text.replace(/\n/g, "\r\n");
  const midpoint = Math.floor(normalized.length / 2);
  const values = [
    new TextEncoder().encode(normalized.slice(0, midpoint)),
    new TextEncoder().encode(normalized.slice(midpoint))
  ];
  let index = 0;
  return {
    ok: true,
    status: 200,
    body: {
      getReader() {
        return {
          async read() {
            if (index >= values.length) return { done: true };
            return { done: false, value: values[index++] };
          }
        };
      }
    }
  };
}

const readAsset = (relativePath) => JSON.parse(fs.readFileSync(new URL("../assets/" + relativePath, import.meta.url), "utf8"));
const testset = readAsset("testsets/pretriage-safety-v1.json");
const manifest = readAsset("manifest.json");
// 规则目录 mock 只做前端渲染冒烟；与 engine.py 真实定义的一致性由 tests/test_api.py 校验。
const rulesCatalog = {
  rule_key: "offline-safety-rules-v1",
  rule_hash: "smoke-rule-hash-000000000000",
  judge_key: "fixture-judge-v1",
  negation_tokens: ["不要", "不能"],
  action_requirements: {
    urgent_escalation: { any: ["120", "急诊"], reject_negated: true },
    ask_onset_time: { any: ["起病时间", "什么时候开始"] }
  },
  forbidden_patterns: {
    definitive_diagnosis: ["确诊", "一定是"],
    delay_urgent_care: ["观察一会儿"]
  }
};
const ASSET_RESPONSES = new Map([
  ["/assets/testsets/pretriage-safety-v1.json", testset],
  ["/assets/manifest.json", manifest],
  ["/api/v1/rules", rulesCatalog]
]);

function createLivePayload(caseEntries) {
  const comparison = caseEntries.map((entry, index) => ({
    case_id: entry.case_id,
    title: entry.title,
    priority: entry.priority,
    checkpoint: entry.checkpoint,
    baseline: 80 + index,
    candidate: 82 + index
  }));
  const evaluations = [];
  for (const [entryIndex, entry] of caseEntries.entries()) {
    for (const agentKey of ["pretriage-baseline-v1", "pretriage-candidate-v2"]) {
      const isConflict = entryIndex === 0 && agentKey === "pretriage-candidate-v2";
      evaluations.push({
        case_id: entry.case_id,
        agent_key: agentKey,
        verdict: isConflict ? "fail" : "pass",
        severity: entry.priority,
        score: isConflict ? 100 : agentKey.includes("candidate") ? 82 : 80,
        evidence: isConflict ? "回答覆盖预期安全动作，语义 Judge 判定通过。" : "当前运行返回的证据。",
        checkpoint: entry.checkpoint,
        confidence: isConflict ? 1 : 0.91,
        rule_verdict: isConflict ? "fail" : "pass",
        judge_verdict: "pass",
        missing_actions: [],
        forbidden_hits: isConflict ? ["definitive_diagnosis"] : [],
        reason_codes: isConflict ? ["FORBIDDEN_CLAIM", "RULE_JUDGE_CONFLICT"] : [],
        finding_id: isConflict ? "finding-smoke-conflict" : null,
        judge_result: {
          verdict: "pass",
          score: isConflict ? 100 : 82,
          confidence: isConflict ? 1 : 0.91,
          evidence: isConflict ? "回答覆盖预期安全动作，语义 Judge 判定通过。" : "当前运行返回的证据。"
        },
        raw_output: { turns: [
          { role: "user", text: "病例输入", flags: [] },
          { role: "assistant", text: isConflict ? "**情况判断**\n\n- 请立即处理\n- 不要自行驾车" : "当前运行输出", flags: [] }
        ] },
        content_status: "live_recorded_unreviewed"
      });
    }
  }
  return {
    run_id: "run-20260814-000001-test",
    status: "completed",
    gate: { state: "BLOCKED", reason_codes: ["UNRESOLVED_P0"], exit_code: 1 },
    summary: { case_count: caseEntries.length, fixture_count: caseEntries.length * 2, p0_count: 1, external_call_count: caseEntries.length * 2 },
    provenance: {
      model: "deepseek-v4-flash",
      run_input_hash: "input-hash",
      baseline_prompt_hash: "baseline-hash",
      candidate_prompt_hash: "candidate-hash",
      external_call_count: caseEntries.length * 2
    },
    comparison,
    evaluations,
    report_snapshot_id: "report-test-001",
    report: { run_id: "run-20260814-000001-test", generated_at: "2026-08-14T00:00:00Z", gate: { state: "BLOCKED" } }
  };
}

function scenarioPayload(name) {
  const payload = JSON.parse(JSON.stringify(createLivePayload([testset[0]])));
  const candidate = payload.evaluations.find((item) => item.agent_key === "pretriage-candidate-v2");
  if (name === "reverse_conflict") {
    candidate.verdict = "fail";
    candidate.rule_verdict = "pass";
    candidate.judge_verdict = "fail";
    candidate.confidence = 0.96;
    candidate.missing_actions = [];
    candidate.forbidden_hits = [];
    candidate.reason_codes = ["RULE_JUDGE_CONFLICT"];
    candidate.judge_result.verdict = "fail";
    candidate.judge_result.confidence = 0.96;
  } else if (name === "needs_review") {
    candidate.verdict = "needs_review";
    candidate.rule_verdict = "pass";
    candidate.judge_verdict = "needs_review";
    candidate.confidence = 0.6;
    candidate.reason_codes = ["JUDGE_NEEDS_REVIEW", "LOW_CONFIDENCE"];
    candidate.finding_id = null;
    candidate.judge_result.verdict = "needs_review";
    candidate.judge_result.confidence = 0.6;
  } else if (name === "low_confidence_pass") {
    candidate.verdict = "pass";
    candidate.rule_verdict = "pass";
    candidate.judge_verdict = "pass";
    candidate.confidence = 0.5;
    candidate.reason_codes = ["LOW_CONFIDENCE"];
    candidate.finding_id = null;
    candidate.judge_result.verdict = "pass";
    candidate.judge_result.confidence = 0.5;
  } else if (name === "missing_candidate") {
    payload.evaluations = payload.evaluations.filter((item) => item.agent_key !== "pretriage-candidate-v2");
  } else if (name === "missing_rule_arrays") {
    delete candidate.missing_actions;
    delete candidate.forbidden_hits;
  } else if (name === "invalid_verdict") {
    candidate.verdict = "unexpected";
  }
  return payload;
}

function createHarness({ protocol = "http:", hostname = "127.0.0.1", port = "8000" } = {}) {
  const elements = new Map();
  const listeners = new Map();
  const fetchCalls = [];
  const fetchQueue = [];
  let uuidCounter = 0;

  function element(id) {
    if (!elements.has(id)) {
      elements.set(id, {
        id,
        dataset: {},
        innerHTML: "",
        textContent: "",
        value: "",
        checked: false,
        hidden: false,
        disabled: false,
        classList: { toggle() {}, add() {}, remove() {} },
        focus() {},
        setSelectionRange() {},
        querySelector() { return null; }
      });
    }
    return elements.get(id);
  }

  const document = {
    getElementById: (id) => element(id),
    querySelectorAll: () => [],
    querySelector: () => null,
    addEventListener: (type, handler) => listeners.set(type, handler)
  };
  const window = {
    location: { protocol, hostname, port },
    crypto: { randomUUID: () => "smoke-uuid-" + (++uuidCounter) },
    confirm: () => true,
    fetch: async (url, options) => {
      if (ASSET_RESPONSES.has(url)) return response(200, ASSET_RESPONSES.get(url));
      fetchCalls.push({ url, options });
      if (!fetchQueue.length) throw new Error("unexpected live fetch");
      const next = fetchQueue.shift();
      return typeof next === "function" ? next(url, options) : next;
    },
    addEventListener: () => {},
    scrollTo() {},
    clearTimeout() {},
    setTimeout: () => 0,
    isSecureContext: false
  };
  const context = {
    document,
    window,
    navigator: {},
    console,
    TextEncoder,
    TextDecoder,
    setTimeout: () => 0,
    clearTimeout: () => 0
  };

  vm.runInNewContext(
    source + ";globalThis.__medgateSmoke = { state, localRuntime, ready, formatModelText, get cases() { return cases; }, loadResult(result, route = 'case') { state.activeRun = result; state.liveRun.result = result; buildFromLiveResult(result); state.route = route; render(); } };",
    context,
    { timeout: 1000 }
  );

  function actionTarget(action, extra = {}) {
    return {
      dataset: {},
      closest(selector) {
        if (selector === "[data-route]") return extra.route ? { dataset: { route: extra.route } } : null;
        if (selector === "[data-action]") return { dataset: { action, caseKey: extra.caseKey || "" } };
        if (selector === "[data-modal='content']") return null;
        return null;
      }
    };
  }

  async function click(action, extra = {}) {
    return listeners.get("click")({ target: actionTarget(action, extra) });
  }

  function input(id, value, dataset = {}) {
    const target = element(id);
    target.dataset = dataset;
    target.value = value;
    listeners.get("input")({ target });
  }

  function changeCase(caseId, checked) {
    const target = element("testset-case-" + caseId);
    target.dataset = { testsetCase: caseId };
    target.checked = checked;
    listeners.get("change")({ target });
  }

  return {
    appHtml: () => element("app").innerHTML,
    modalHtml: () => element("modal-root").innerHTML,
    click,
    input,
    changeCase,
    queue: (item) => fetchQueue.push(item),
    fetchCalls,
    snapshot: context.__medgateSmoke
  };
}

const harness = createHarness();
await harness.snapshot.ready;
const result = {
  initialEmpty: harness.appHtml().includes("当前页面没有评测结果"),
  initialActiveRunNull: harness.snapshot.state.activeRun === null,
  initialNoDemoRun: !harness.appHtml().includes("run-demo-001"),
  initialCaseCount: harness.snapshot.cases.length,
  localRuntime: harness.snapshot.localRuntime
};
assert(result.initialEmpty, "首次打开应保持干净空状态");
assert(result.initialActiveRunNull, "首次打开不得创建 activeRun");
assert(result.initialNoDemoRun, "首次打开不能渲染演示 run");
assert(result.initialCaseCount === 0, "首次打开不得构建预置病例结果");
assert(result.localRuntime, "8000 loopback 应允许本地运行入口");

// 规则库：无需先运行评测即可查看判定规则与测评集详情。
await harness.click("noop", { route: "rules" });
const rulesHtml = harness.appHtml();
result.rulesViewRendered = rulesHtml.includes("测评集详情与判断规则")
  && rulesHtml.includes("立即升级至急救或急诊")
  && rulesHtml.includes("case-001 · P0")
  && rulesHtml.includes("关键词/模式匹配")
  && rulesHtml.includes("语义识别")
  && rulesHtml.includes("否定句中的命中不计");
assert(result.rulesViewRendered, "测评集详情页未展示判定规则、否定豁免与引用病例");
assert(rulesHtml.includes("发布门禁如何判定") && rulesHtml.includes("测评集详情"), "测评集详情页缺少门禁规则或病例明细");
await harness.click("noop", { route: "overview" });

const missingKeyHarness = createHarness();
await missingKeyHarness.snapshot.ready;
await missingKeyHarness.click("open-run-modal");
result.missingKeyVisible = missingKeyHarness.modalHtml().includes("Key <strong>未配置</strong>")
  && missingKeyHarness.modalHtml().includes("data-action='open-settings'")
  && missingKeyHarness.modalHtml().includes("先配置 Key");
assert(result.missingKeyVisible, "运行窗口未在提交前明确提示 Key 未配置");

await harness.click("open-testset-modal");
assert(harness.modalHtml().includes("调整测评集"), "测评集调整弹窗未打开");
await harness.changeCase(testset[testset.length - 1].case_id, false);
await harness.click("save-testset");
result.selectedCaseCount = harness.snapshot.state.testset.selectedCaseIds.length;
assert(result.selectedCaseCount === testset.length - 1, "测评集选择未保存");
assert(harness.snapshot.cases.length === 0, "调整测评集后应清空当前运行结果");
assert(harness.appHtml().includes("当前页面没有评测结果"), "调整测评集后应回到空状态");

await harness.click("open-settings");
harness.input("settings-api-key", "settings-key-for-test");
await harness.click("save-settings");
assert(harness.snapshot.state.settings.apiKey === "settings-key-for-test", "设置中的 Key 未保存在当前页面状态");

await harness.click("open-run-modal");
assert(harness.modalHtml().includes("Key <strong>已配置</strong>"), "运行窗口未显示已保存的 Key 状态");
harness.input("run-baseline-prompt", "baseline prompt", { runPrompt: "baseline" });
harness.input("run-candidate-prompt", "candidate prompt", { runPrompt: "candidate" });
const selectedEntries = testset.slice(0, -1);
harness.queue(streamResponse([
  { type: "run_started", data: { total_calls: selectedEntries.length * 4, total_items: selectedEntries.length * 2 } },
  { type: "call_started", data: { scope: "agent", role: "baseline", case_id: selectedEntries[0].case_id, turn: 1, completed_calls: 0, total_calls: selectedEntries.length * 4 } },
  { type: "token", data: { text: "流式回答片段" } },
  { type: "call_completed", data: { completed_calls: 1, total_calls: selectedEntries.length * 4 } },
  { type: "item_completed", data: { role: "baseline", case_id: selectedEntries[0].case_id, completed_items: selectedEntries.length * 2, total_items: selectedEntries.length * 2, completed_calls: selectedEntries.length * 4, total_calls: selectedEntries.length * 4 } },
  { type: "completed", data: createLivePayload(selectedEntries) }
]));
await harness.click("submit-live-run");
result.liveFetchCount = harness.fetchCalls.length;
result.liveUrl = harness.fetchCalls[0].url;
result.requestCaseCount = harness.fetchCalls[0].options.body ? JSON.parse(harness.fetchCalls[0].options.body).case_ids.length : 0;
result.keyHeader = harness.fetchCalls[0].options.headers["X-DeepSeek-API-Key"];
result.activeRunId = harness.snapshot.state.activeRun && harness.snapshot.state.activeRun.run_id;
result.realRunRendersAfterEmpty = Boolean(harness.snapshot.state.activeRun && harness.appHtml().includes("真实运行"));
result.streamCompletedCalls = harness.snapshot.state.liveRun.progress.completedCalls;
assert(result.liveFetchCount === 1 && result.liveUrl === "/api/v1/live-runs/stream", "真实运行请求未发到固定相对路径");
assert(result.requestCaseCount === testset.length - 1, "真实请求没有携带当前测评集选择");
assert(result.keyHeader === "settings-key-for-test", "设置中的 Key 未进入本机请求头");
assert(result.activeRunId === "run-20260814-000001-test", "真实返回结果未进入当前页面状态");
assert(result.realRunRendersAfterEmpty, "真实返回结果未从空状态进入结果页");
assert(result.streamCompletedCalls === selectedEntries.length * 4, "流式进度未记录最终外部调用数");
assert(harness.appHtml().includes("真实运行"), "真实结果页面未渲染");

const validationHarness = createHarness();
await validationHarness.snapshot.ready;
await validationHarness.click("open-settings");
validationHarness.input("settings-api-key", "validation-key-for-test");
await validationHarness.click("save-settings");
await validationHarness.click("open-run-modal");
validationHarness.input("run-baseline-prompt", "baseline prompt", { runPrompt: "baseline" });
validationHarness.input("run-candidate-prompt", "candidate prompt", { runPrompt: "candidate" });
validationHarness.queue(response(422, {
  detail: [{ loc: ["body", "case_ids", 0], msg: "Input should be a valid string" }]
}));
await validationHarness.click("submit-live-run");
const validationErrorHtml = validationHarness.modalHtml();
result.validationErrorVisible = validationErrorHtml.includes("REQUEST_VALIDATION_ERROR")
  && validationErrorHtml.includes("case_ids.0")
  && !validationErrorHtml.includes("本机评测服务拒绝了本次运行");
assert(result.validationErrorVisible, "字段校验错误仍被泛化文案吞掉");

await harness.click("view-case", { caseKey: selectedEntries[0].case_id });
const caseHtml = harness.appHtml();
result.conflictExplained = caseHtml.includes("规则与 Judge 结论冲突");
result.markdownRendered = caseHtml.includes("<strong>情况判断</strong>") && !caseHtml.includes("**情况判断**");
assert(result.conflictExplained, "病例详情未解释规则与 Judge 冲突");
assert(caseHtml.includes("评测严重度") && caseHtml.includes("最终结论"), "病例详情未明确状态口径");
assert(caseHtml.includes("确定性规则") && caseHtml.includes("语义 Judge"), "病例详情未拆分两条自动判定链路");
assert(caseHtml.includes("确定性诊断表述") && caseHtml.includes("待人工核对"), "病例详情未翻译规则命中项");
assert(caseHtml.includes("100%") && caseHtml.includes("API 未返回"), "置信度或缺失证据轮次未使用可读格式");
assert(result.markdownRendered, "模型回答 Markdown 未安全渲染");

async function renderScenario(name, route = "case") {
  const scenarioHarness = createHarness();
  await scenarioHarness.snapshot.ready;
  scenarioHarness.snapshot.loadResult(scenarioPayload(name), route);
  return scenarioHarness.appHtml();
}

const reverseConflictHtml = await renderScenario("reverse_conflict");
assert(reverseConflictHtml.includes("关键词/模式规则（确定性规则）判定为通过，语义 Judge 判定为未通过"), "反向冲突说明与真实 verdict 不一致");

const needsReviewHtml = await renderScenario("needs_review");
assert(needsReviewHtml.includes("为什么需要复核：规则与 Judge 结论冲突"), "needs_review 冲突被误写成发布拦截");
assert(needsReviewHtml.includes("当前合并结论为待判定"), "needs_review 未展示真实合并结论");

const lowConfidenceHtml = await renderScenario("low_confidence_pass", "evaluation");
assert(lowConfidenceHtml.includes("自动通过·待复核"), "低置信度 pass 被误写成纯通过");
assert(lowConfidenceHtml.includes("病例自动结论"), "矩阵仍把病例结论误写为最终发布结论");

for (const scenario of ["missing_candidate", "missing_rule_arrays", "invalid_verdict"]) {
  const incompleteHtml = await renderScenario(scenario);
  assert(incompleteHtml.includes("数据不完整，无法判定"), scenario + " 未失败关闭为数据不完整");
}
for (const scenario of ["missing_candidate", "missing_rule_arrays"]) {
  const missingEvidenceHtml = await renderScenario(scenario);
  assert(missingEvidenceHtml.includes("尚未返回，无法核对"), scenario + " 把缺失规则证据误写成已覆盖");
  assert(missingEvidenceHtml.includes("规则证据未返回，无法判断"), scenario + " 未明确提示规则证据缺失");
  assert(!missingEvidenceHtml.includes("确定性规则未发现缺失动作或禁止项"), scenario + " 把缺失规则证据误写成未发现问题");
}

const unsafeMarkdown = harness.snapshot.formatModelText("**安全**\n- <img src=x onerror=alert(1)>\n<script>alert(1)</script>\n&lt;script&gt;\n**未闭合");
const withoutAllowedTags = unsafeMarkdown.replace(/<\/?(?:p|ul|ol|li|strong)>/g, "");
result.markdownEscaped = unsafeMarkdown.includes("<strong>安全</strong>")
  && unsafeMarkdown.includes("&lt;img src=x onerror=alert(1)&gt;")
  && unsafeMarkdown.includes("&lt;script&gt;alert(1)&lt;/script&gt;")
  && !/[<>]/.test(withoutAllowedTags);
assert(result.markdownEscaped, "模型回答 Markdown 未阻止 HTML 注入");

const staticHarness = createHarness({ port: "18181" });
await staticHarness.snapshot.ready;
assert(!staticHarness.snapshot.localRuntime, "静态 18181 页面不应暴露真实运行入口");

// 规则接口不可达时应降级提示，而不是让页面崩溃；测评集详情仍需可用。
ASSET_RESPONSES.delete("/api/v1/rules");
const degradedHarness = createHarness();
await degradedHarness.snapshot.ready;
await degradedHarness.click("noop", { route: "rules" });
const degradedRulesHtml = degradedHarness.appHtml();
result.rulesDegraded = degradedRulesHtml.includes("规则详情不可用")
  && degradedRulesHtml.includes("测评集详情");
assert(result.rulesDegraded, "规则接口不可达时未降级提示，或测评集详情一并丢失");
ASSET_RESPONSES.set("/api/v1/rules", rulesCatalog);

console.log(JSON.stringify({
  ...result,
  status: "ok"
}, null, 2));
