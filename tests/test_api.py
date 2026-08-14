from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from medgate.api import create_app
from medgate.engine import ACTION_REQUIREMENTS, FORBIDDEN_PATTERNS, NEGATION_TOKENS, _rule_hash, rule_catalog


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class ApiFlowTest(unittest.TestCase):
    def test_validation_error_uses_stable_safe_shape(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            client = TestClient(create_app(PROJECT_ROOT, Path(temp_dir) / "medgate.sqlite3"))
            response = client.post(
                "/api/v1/live-runs/stream",
                headers={"Idempotency-Key": "invalid-live-request-001"},
                json={
                    "baseline_prompt": "baseline prompt",
                    "candidate_prompt": "candidate prompt",
                    "case_ids": [None],
                },
            )

            self.assertEqual(response.status_code, 422)
            detail = response.json()["detail"]
            self.assertEqual(detail["code"], "REQUEST_VALIDATION_ERROR")
            self.assertIn("case_ids.0", detail["message"])
            self.assertNotIn("baseline prompt", detail["message"])
            self.assertNotIn("candidate prompt", detail["message"])

    def test_create_query_review_and_recalculate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            client = TestClient(create_app(PROJECT_ROOT, Path(temp_dir) / "medgate.sqlite3"))
            headers = {"Idempotency-Key": "api-flow-001"}
            created = client.post("/api/v1/runs", headers=headers)
            self.assertEqual(created.status_code, 201)
            run = created.json()
            self.assertEqual(run["gate"]["state"], "BLOCKED")
            run_id = run["run_id"]

            replay = client.post("/api/v1/runs", headers=headers)
            self.assertEqual(replay.status_code, 200)
            self.assertEqual(replay.headers["Idempotent-Replayed"], "true")
            self.assertTrue(replay.json()["idempotent_replayed"])

            comparison = client.get(f"/api/v1/runs/{run_id}/comparison")
            self.assertEqual(comparison.status_code, 200)
            finding = comparison.json()["findings"][0]
            review = client.post(
                f"/api/v1/findings/{finding['id']}:review",
                headers={"Idempotency-Key": "review-flow-001"},
                json={
                    "run_id": run_id,
                    "occurrence_id": finding["occurrence_id"],
                    "attempt_id": finding["attempt_id"],
                    "decision": "false_positive",
                    "reason": "API 流程测试用误报复核。",
                    "output_hash": finding["output_hash"],
                },
            )
            self.assertEqual(review.status_code, 200)
            replayed_review = client.post(
                f"/api/v1/findings/{finding['id']}:review",
                headers={"Idempotency-Key": "review-flow-001"},
                json={
                    "run_id": run_id,
                    "occurrence_id": finding["occurrence_id"],
                    "attempt_id": finding["attempt_id"],
                    "decision": "false_positive",
                    "reason": "API 流程测试用误报复核。",
                    "output_hash": finding["output_hash"],
                },
            )
            self.assertEqual(replayed_review.status_code, 200)
            self.assertTrue(replayed_review.json()["review"]["idempotent_replay"])

            recalculated = client.post(f"/api/v1/runs/{run_id}:calculate-gate")
            self.assertEqual(recalculated.status_code, 200)
            self.assertEqual(recalculated.json()["gate"]["state"], "PASSED")

            gate = client.get(f"/api/v1/runs/{run_id}/gate")
            self.assertEqual(gate.status_code, 200)
            self.assertEqual(gate.json()["gate"]["state"], "PASSED")

    def test_rules_catalog_matches_engine_definitions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            client = TestClient(create_app(PROJECT_ROOT, Path(temp_dir) / "medgate.sqlite3"))
            response = client.get("/api/v1/rules")
            self.assertEqual(response.status_code, 200)
            catalog = response.json()
            self.assertEqual(catalog["rule_key"], "offline-safety-rules-v1")
            self.assertEqual(catalog["judge_key"], "fixture-judge-v1")
            self.assertEqual(catalog["action_requirements"], ACTION_REQUIREMENTS)
            self.assertEqual(catalog["forbidden_patterns"], FORBIDDEN_PATTERNS)
            self.assertEqual(catalog["negation_tokens"], list(NEGATION_TOKENS))
            self.assertEqual(catalog["rule_hash"], _rule_hash())

    def test_rules_catalog_covers_all_testset_keys(self) -> None:
        catalog = rule_catalog()
        testset = json.loads((PROJECT_ROOT / "assets" / "testsets" / "pretriage-safety-v1.json").read_text(encoding="utf-8"))
        for case in testset:
            unknown_actions = set(case["expected_safety_actions"]) - set(catalog["action_requirements"])
            unknown_claims = set(case["forbidden_claims"]) - set(catalog["forbidden_patterns"])
            self.assertFalse(unknown_actions, f"{case['case_id']} 引用了未实现的期望动作：{unknown_actions}")
            self.assertFalse(unknown_claims, f"{case['case_id']} 引用了未实现的禁止表述：{unknown_claims}")

    def test_review_rejects_wrong_output_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            client = TestClient(create_app(PROJECT_ROOT, Path(temp_dir) / "medgate.sqlite3"))
            created = client.post("/api/v1/runs", headers={"Idempotency-Key": "api-flow-002"})
            finding = client.get(f"/api/v1/runs/{created.json()['run_id']}/comparison").json()["findings"][0]
            response = client.post(
                f"/api/v1/findings/{finding['id']}:review",
                headers={"Idempotency-Key": "review-flow-002"},
                json={
                    "run_id": created.json()["run_id"],
                    "occurrence_id": finding["occurrence_id"],
                    "attempt_id": finding["attempt_id"],
                    "decision": "confirmed",
                    "effective_severity": "P0",
                    "reason": "确认 P0，但使用了错误输出哈希。",
                    "output_hash": "tampered",
                },
            )
            self.assertEqual(response.status_code, 422)
            self.assertEqual(response.json()["detail"]["code"], "REVIEW_REJECTED")


if __name__ == "__main__":
    unittest.main()
