import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const read = (relativePath) => JSON.parse(fs.readFileSync(path.join(root, relativePath), "utf8"));
const manifest = read("assets/manifest.json");
const cases = read("assets/testsets/pretriage-safety-v1.json");
const fixtures = read("assets/fixtures/pretriage-safety-v1.json");
const sha256 = (relativePath) => crypto.createHash("sha256").update(fs.readFileSync(path.join(root, "assets", relativePath))).digest("hex");
const assert = (condition, message) => { if (!condition) throw new Error(message); };

assert(cases.length === 12, "T0 testset must contain 12 cases");
assert(fixtures.length === 24, "T0 fixture set must contain 24 fixtures");
assert(new Set(cases.map((item) => item.case_id)).size === 12, "case IDs must be unique");
assert(fixtures.every((fixture) => fixture.fixture_id === `${fixture.case_id}__${fixture.agent_key}`), "fixture IDs must be deterministic");
assert(fixtures.filter((fixture) => fixture.agent_key === "pretriage-baseline-v1").length === 12, "baseline coverage incomplete");
assert(fixtures.filter((fixture) => fixture.agent_key === "pretriage-candidate-v2").length === 12, "candidate coverage incomplete");
const blocking = fixtures.filter((fixture) => fixture.judge_result.verdict === "fail" && fixture.judge_result.severity === "P0").map((fixture) => fixture.fixture_id);
assert(blocking.length === 1 && blocking[0] === "case-003__pretriage-candidate-v2", "blocking fixture drifted");
for (const [name, asset] of Object.entries(manifest.assets)) assert(sha256(asset.path) === asset.sha256, `hash mismatch: ${name}`);

console.log(JSON.stringify({
  testset: cases.length,
  fixtures: fixtures.length,
  baseline: 12,
  candidate: 12,
  blocking,
  hashes: Object.fromEntries(Object.entries(manifest.assets).map(([name, asset]) => [name, asset.sha256])),
  status: "ok"
}, null, 2));
