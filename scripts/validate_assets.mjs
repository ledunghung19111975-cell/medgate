import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

function fail(message, details = {}) {
  console.error(JSON.stringify({ error: message, ...details }, null, 2));
  process.exit(1);
}

function assert(condition, message, details = {}) {
  if (!condition) fail(message, details);
}

function readJson(file) {
  try {
    return JSON.parse(fs.readFileSync(file, "utf8"));
  } catch (error) {
    fail(`invalid JSON: ${path.relative(root, file)}`, { reason: error.message });
  }
}

function sha256(file) {
  return crypto.createHash("sha256").update(fs.readFileSync(file)).digest("hex");
}

const AGENTS = ["pretriage-baseline-v1", "pretriage-candidate-v2"];

const ALLOWED_PROVENANCE = {
  "self_authored_synthetic": ["project-owned"],
  "rewritten_from_cmb_clin": ["Apache-2.0 (FreedomIntelligence/CMB)"]
};

function validateAssetHashes(manifest) {
  for (const [name, asset] of Object.entries(manifest.assets)) {
    const file = path.join(root, "assets", asset.path);
    assert(fs.existsSync(file), `manifest asset is missing: ${name}`, { path: asset.path });
    const actual = sha256(file);
    assert(actual === asset.sha256, `asset hash mismatch: ${name}`, { expected: asset.sha256, actual });
  }
}

function validateCommon(manifest, testset, fixtures) {
  assert(manifest.manifest_version === "1.0.0", "unsupported manifest version");
  const allowedLicenses = ALLOWED_PROVENANCE[manifest.source_type];
  assert(Array.isArray(allowedLicenses) && allowedLicenses.includes(manifest.license_ref), "asset provenance not allowed", {
    source_type: manifest.source_type,
    license_ref: manifest.license_ref
  });
  assert(Array.isArray(testset) && testset.length === manifest.expected_case_count, "testset case count mismatch", {
    expected: manifest.expected_case_count,
    actual: testset.length
  });
  assert(Array.isArray(fixtures) && fixtures.length === manifest.expected_fixture_count, "fixture count mismatch", {
    expected: manifest.expected_fixture_count,
    actual: fixtures.length
  });
  validateAssetHashes(manifest);
}

function validatePretriage(manifest, testset, fixtures) {
  const agentsYaml = fs.readFileSync(path.join(root, "assets", manifest.assets.agents.path), "utf8");
  const valuesFromYaml = (key) => [...agentsYaml.matchAll(new RegExp(`^\\s*(?:-\\s*)?${key}:\\s*(.+)$`, "gm"))].map((m) => m[1].trim());
  assert(valuesFromYaml("key").length === 2, "agents.yaml must declare exactly two agent versions");
  assert(valuesFromYaml("role").sort().join(",") === "baseline,candidate", "agents.yaml roles must be baseline and candidate");

  const caseIds = new Set();
  const priorities = new Set(["P0", "P1", "P2"]);
  for (const item of testset) {
    assert(!caseIds.has(item.case_id), `duplicate case_id: ${item.case_id}`);
    caseIds.add(item.case_id);
    assert(item.title && item.checkpoint && item.domain === "pretriage_safety", `case metadata incomplete: ${item.case_id}`);
    assert(priorities.has(item.priority), `invalid priority: ${item.case_id}`);
    assert(item.input && Array.isArray(item.input.turns) && item.input.turns.length >= 1, `case input missing: ${item.case_id}`);
    assert(Array.isArray(item.expected_safety_actions) && item.expected_safety_actions.length > 0, `expected actions missing: ${item.case_id}`);
    assert(Array.isArray(item.forbidden_claims) && item.forbidden_claims.length > 0, `forbidden claims missing: ${item.case_id}`);
    assert(item.source_type === manifest.source_type && item.license_ref === manifest.license_ref, `case provenance mismatch: ${item.case_id}`);
    assert(item.content_status === manifest.content_status, `case review status mismatch: ${item.case_id}`);
  }

  const fixtureIds = new Set();
  const fixtureByCase = new Map();
  for (const fixture of fixtures) {
    assert(!fixtureIds.has(fixture.fixture_id), `duplicate fixture_id: ${fixture.fixture_id}`);
    fixtureIds.add(fixture.fixture_id);
    assert(caseIds.has(fixture.case_id), `fixture references unknown case: ${fixture.case_id}`);
    assert(AGENTS.includes(fixture.agent_key), `unknown agent: ${fixture.agent_key}`);
    assert(fixture.fixture_id === `${fixture.case_id}__${fixture.agent_key}`, `fixture_id is not deterministic: ${fixture.fixture_id}`);
    assert(fixture.fixture_version === "1.0.0", `fixture version mismatch: ${fixture.fixture_id}`);
    assert(fixture.raw_output && Array.isArray(fixture.raw_output.turns) && fixture.raw_output.turns.length >= 2, `fixture transcript incomplete: ${fixture.fixture_id}`);
    assert(fixture.raw_output.turns.every((turn) => ["user", "assistant"].includes(turn.role) && turn.text), `fixture transcript schema invalid: ${fixture.fixture_id}`);
    assert(fixture.raw_output.turns.some((turn) => turn.role === "assistant"), `fixture has no assistant output: ${fixture.fixture_id}`);
    assert(fixture.judge_result && ["pass", "fail"].includes(fixture.judge_result.verdict), `judge verdict invalid: ${fixture.fixture_id}`);
    assert(priorities.has(fixture.judge_result.severity), `judge severity invalid: ${fixture.fixture_id}`);
    assert(Number.isFinite(fixture.judge_result.score), `judge score invalid: ${fixture.fixture_id}`);
    assert(Number.isFinite(fixture.judge_result.confidence) && fixture.judge_result.confidence >= 0 && fixture.judge_result.confidence <= 1, `judge confidence invalid: ${fixture.fixture_id}`);
    assert(fixture.judge_result.verdict !== "pass" || fixture.judge_result.finding_id === null, `passing fixture must not carry a Finding: ${fixture.fixture_id}`);
    assert(fixture.source_type === manifest.source_type && fixture.license_ref === manifest.license_ref, `fixture provenance mismatch: ${fixture.fixture_id}`);
    assert(fixture.content_status === manifest.content_status, `fixture review status mismatch: ${fixture.fixture_id}`);
    if (!fixtureByCase.has(fixture.case_id)) fixtureByCase.set(fixture.case_id, new Set());
    fixtureByCase.get(fixture.case_id).add(fixture.agent_key);
  }

  for (const caseId of caseIds) {
    const agents = fixtureByCase.get(caseId) || new Set();
    assert(agents.size === 2, `case does not have two agent fixtures: ${caseId}`);
  }

  const blockingFixtures = fixtures.filter((fixture) => fixture.judge_result.verdict === "fail" && fixture.judge_result.severity === "P0").map((fixture) => fixture.fixture_id);
  assert(blockingFixtures.length === manifest.expected_blocking_fixtures.length, "blocking fixture count mismatch", { blockingFixtures });
  assert(blockingFixtures.join(",") === manifest.expected_blocking_fixtures.join(","), "blocking fixture set drifted", { expected: manifest.expected_blocking_fixtures, actual: blockingFixtures });
}

function validateMultidim(manifest, testset, fixtures) {
  const allowedScenarios = new Set(manifest.scenarios);
  const priorities = new Set(["P0", "P1", "P2"]);
  const caseIds = new Set();
  for (const item of testset) {
    assert(!caseIds.has(item.case_id), `duplicate case_id: ${item.case_id}`);
    caseIds.add(item.case_id);
    assert(item.title && item.checkpoint, `case metadata incomplete: ${item.case_id}`);
    assert(priorities.has(item.priority), `invalid priority: ${item.case_id}`);
    assert(allowedScenarios.has(item.scenario), `unknown scenario: ${item.case_id}`);
    assert(item.input && Array.isArray(item.input.turns) && item.input.turns.length >= 1, `case input missing: ${item.case_id}`);
    assert(item.source_type === manifest.source_type && item.license_ref === manifest.license_ref, `case provenance mismatch: ${item.case_id}`);
    assert(item.content_status === manifest.content_status, `case review status mismatch: ${item.case_id}`);
    if (item.scenario === "faq") {
      assert(item.faq_reference_answer && Array.isArray(item.expected_key_terms) && item.expected_key_terms.length > 0, `faq case needs reference + key terms: ${item.case_id}`);
    }
    if (item.scenario === "complex") {
      assert(item.expected_action, `complex case needs expected_action: ${item.case_id}`);
      assert(item.reference_answer, `complex case needs reference_answer: ${item.case_id}`);
      assert(Array.isArray(item.expected_key_terms) && item.expected_key_terms.length > 0, `complex case needs expected_key_terms: ${item.case_id}`);
    }
    if (item.scenario === "boundary") {
      assert(item.boundary_type, `boundary case needs boundary_type: ${item.case_id}`);
      assert(item.priority === "P0", `boundary case must be P0: ${item.case_id}`);
    }
  }
  // multidim 允许部分 case 无 fixture（live-only）
  const fixtureIds = new Set();
  for (const fixture of fixtures) {
    assert(!fixtureIds.has(fixture.fixture_id), `duplicate fixture_id: ${fixture.fixture_id}`);
    fixtureIds.add(fixture.fixture_id);
    assert(caseIds.has(fixture.case_id), `fixture references unknown case: ${fixture.case_id}`);
    assert(AGENTS.includes(fixture.agent_key), `unknown agent: ${fixture.agent_key}`);
    assert(fixture.judge_result && ["pass", "fail"].includes(fixture.judge_result.verdict), `judge verdict invalid: ${fixture.fixture_id}`);
    assert(fixture.judge_result.verdict !== "pass" || fixture.judge_result.finding_id === null, `passing fixture must not carry a Finding: ${fixture.fixture_id}`);
    assert(fixture.source_type === manifest.source_type && fixture.license_ref === manifest.license_ref, `fixture provenance mismatch: ${fixture.fixture_id}`);
    assert(fixture.content_status === manifest.content_status, `fixture review status mismatch: ${fixture.fixture_id}`);
  }
}

function validateManifest(manifestPath) {
  const manifest = readJson(manifestPath);
  const testset = readJson(path.join(root, "assets", manifest.assets.testset.path));
  const fixtures = readJson(path.join(root, "assets", manifest.assets.fixtures.path));
  validateCommon(manifest, testset, fixtures);
  if (manifest.scenarios) {
    validateMultidim(manifest, testset, fixtures);
    console.log(JSON.stringify({
      manifest: manifest.manifest_version,
      testset: { key: manifest.testset_key, cases: testset.length, scenarios: manifest.scenarios },
      fixtures: { total: fixtures.length },
      contentStatus: manifest.content_status,
      status: "ok"
    }, null, 2));
  } else {
    validatePretriage(manifest, testset, fixtures);
    const blockingFixtures = fixtures.filter((f) => f.judge_result.verdict === "fail" && f.judge_result.severity === "P0").map((f) => f.fixture_id);
    console.log(JSON.stringify({
      manifest: manifest.manifest_version,
      testset: { key: manifest.testset_key, cases: testset.length },
      fixtures: { total: fixtures.length, perCase: 2, agents: AGENTS },
      blockingFixtures,
      contentStatus: manifest.content_status,
      hashes: Object.fromEntries(Object.entries(manifest.assets).map(([name, asset]) => [name, asset.sha256])),
      status: "ok"
    }, null, 2));
  }
}

// 校验默认（pretriage）manifest + assets/manifests/ 下所有多维度 manifest
validateManifest(path.join(root, "assets/manifest.json"));
const manifestsDir = path.join(root, "assets/manifests");
if (fs.existsSync(manifestsDir)) {
  for (const file of fs.readdirSync(manifestsDir).filter((f) => f.endsWith(".json")).sort()) {
    validateManifest(path.join(manifestsDir, file));
  }
}
