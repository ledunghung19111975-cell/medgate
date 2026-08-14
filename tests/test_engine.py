from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from medgate.assets import load_bundle, select_case_subset
from medgate.db import connect
from medgate.engine import canonical_hash, recalculate_gate, record_review, run_offline


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class OfflineEngineTest(unittest.TestCase):
    def test_selected_case_subset_keeps_fixture_pairing(self) -> None:
        bundle = select_case_subset(load_bundle(PROJECT_ROOT), ["case-001", "case-003"])
        with tempfile.TemporaryDirectory() as temp_dir:
            report = run_offline(
                bundle,
                db_path=Path(temp_dir) / "medgate.sqlite3",
                report_path=Path(temp_dir) / "subset.json",
                baseline_key="pretriage-baseline-v1",
                candidate_key="pretriage-candidate-v2",
                idempotency_key="subset-run-001",
            )
        self.assertEqual(report["summary"]["case_count"], 2)
        self.assertEqual(report["summary"]["fixture_count"], 4)
        self.assertEqual(len(report["evaluations"]), 4)

    def test_blocked_run_writes_snapshot_and_24_evaluations(self) -> None:
        bundle = load_bundle(PROJECT_ROOT)
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            report_path = temp / "gate.json"
            report = run_offline(
                bundle,
                db_path=temp / "medgate.sqlite3",
                report_path=report_path,
                baseline_key="pretriage-baseline-v1",
                candidate_key="pretriage-candidate-v2",
                idempotency_key="test-run-001",
            )
            self.assertEqual(report["gate"]["state"], "BLOCKED")
            self.assertEqual(report["gate"]["exit_code"], 1)
            self.assertEqual(report["summary"]["fixture_count"], 24)
            self.assertEqual(len(report["findings"]), 1)
            self.assertEqual(report["findings"][0]["id"], "finding-017")
            connection = connect(temp / "medgate.sqlite3")
            try:
                snapshot = connection.execute(
                    "SELECT snapshot_hash FROM report_snapshots WHERE run_id = ?",
                    (report["run_id"],),
                ).fetchone()
            finally:
                connection.close()
            self.assertEqual(report["provenance"]["snapshot_hash"], snapshot["snapshot_hash"])
            self.assertTrue(report_path.exists())

    def test_idempotency_returns_same_run(self) -> None:
        bundle = load_bundle(PROJECT_ROOT)
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            kwargs = dict(
                db_path=temp / "medgate.sqlite3",
                report_path=temp / "gate.json",
                baseline_key="pretriage-baseline-v1",
                candidate_key="pretriage-candidate-v2",
                idempotency_key="same-key",
            )
            first = run_offline(bundle, **kwargs)
            second = run_offline(bundle, **kwargs)
            self.assertEqual(first["run_id"], second["run_id"])
            self.assertTrue(second["idempotent_replay"])
            self.assertEqual(json.loads((temp / "gate.json").read_text())["run_id"], first["run_id"])
            replay_file = json.loads((temp / "gate.json").read_text())
            payload = json.loads(json.dumps(replay_file))
            payload["provenance"].pop("snapshot_hash", None)
            payload.pop("idempotent_replay", None)
            self.assertEqual(canonical_hash(payload), replay_file["provenance"]["snapshot_hash"])

    def test_matching_false_positive_pack_can_clear_p0(self) -> None:
        bundle = load_bundle(PROJECT_ROOT)
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            first = run_offline(
                bundle,
                db_path=temp / "medgate.sqlite3",
                report_path=temp / "first.json",
                baseline_key="pretriage-baseline-v1",
                candidate_key="pretriage-candidate-v2",
                idempotency_key="seed-run",
            )
            finding = first["findings"][0]
            review_path = temp / "review.json"
            review_path.write_text(json.dumps({
                "run_input_hash": first["provenance"]["run_input_hash"],
                "testset_hash": first["provenance"]["testset_hash"],
                "fixture_hash": first["provenance"]["fixture_hash"],
                "rule_hash": first["provenance"]["rule_hash"],
                "judge_hash": first["provenance"]["judge_hash"],
                "reviews": [{
                    "finding_id": finding["id"],
                    "case_id": finding["case_id"],
                    "checkpoint": finding["checkpoint"],
                    "output_hash": finding["output_hash"],
                    "decision": "false_positive",
                    "reason": "用于验证复核包绑定和误报分支。"
                }]
            }, ensure_ascii=False), encoding="utf-8")
            second = run_offline(
                bundle,
                db_path=temp / "medgate.sqlite3",
                report_path=temp / "second.json",
                baseline_key="pretriage-baseline-v1",
                candidate_key="pretriage-candidate-v2",
                idempotency_key="reviewed-run",
                review_pack_path=review_path,
            )
            self.assertEqual(second["gate"]["state"], "PASSED")
            self.assertEqual(second["gate"]["exit_code"], 0)
            self.assertEqual(second["findings"][0]["review"]["decision"], "false_positive")

    def test_mismatched_review_pack_is_rejected(self) -> None:
        bundle = load_bundle(PROJECT_ROOT)
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            review_path = temp / "tampered-review.json"
            review_path.write_text(json.dumps({
                "run_input_hash": "tampered",
                "testset_hash": "tampered",
                "fixture_hash": "tampered",
                "reviews": []
            }), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "run_input_hash"):
                run_offline(
                    bundle,
                    db_path=temp / "medgate.sqlite3",
                    report_path=temp / "gate.json",
                    baseline_key="pretriage-baseline-v1",
                    candidate_key="pretriage-candidate-v2",
                    idempotency_key="tampered-review",
                    review_pack_path=review_path,
                )

    def test_idempotency_conflict_is_rejected(self) -> None:
        bundle = load_bundle(PROJECT_ROOT)
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            run_offline(
                bundle,
                db_path=temp / "medgate.sqlite3",
                report_path=temp / "first.json",
                baseline_key="pretriage-baseline-v1",
                candidate_key="pretriage-candidate-v2",
                idempotency_key="conflict-key",
            )
            with self.assertRaisesRegex(ValueError, "Idempotency-Key"):
                run_offline(
                    bundle,
                    db_path=temp / "medgate.sqlite3",
                    report_path=temp / "second.json",
                    baseline_key="pretriage-candidate-v2",
                    candidate_key="pretriage-baseline-v1",
                    idempotency_key="conflict-key",
                )

    def test_recalculate_rejects_missing_active_attempt(self) -> None:
        bundle = load_bundle(PROJECT_ROOT)
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            report = run_offline(
                bundle,
                db_path=temp / "medgate.sqlite3",
                report_path=temp / "first.json",
                baseline_key="pretriage-baseline-v1",
                candidate_key="pretriage-candidate-v2",
                idempotency_key="coverage-key",
            )
            connection = connect(temp / "medgate.sqlite3")
            try:
                connection.execute(
                    "UPDATE attempts SET is_active = 0 WHERE run_id = ? AND agent_key = ?",
                    (report["run_id"], "pretriage-candidate-v2"),
                )
                connection.commit()
            finally:
                connection.close()
            with self.assertRaisesRegex(ValueError, "覆盖不完整"):
                recalculate_gate(temp / "medgate.sqlite3", run_id=report["run_id"])

    def test_review_must_bind_exact_occurrence(self) -> None:
        bundle = load_bundle(PROJECT_ROOT)
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            first = run_offline(
                bundle,
                db_path=temp / "medgate.sqlite3",
                report_path=temp / "first.json",
                baseline_key="pretriage-baseline-v1",
                candidate_key="pretriage-candidate-v2",
                idempotency_key="review-run-1",
            )
            second = run_offline(
                bundle,
                db_path=temp / "medgate.sqlite3",
                report_path=temp / "second.json",
                baseline_key="pretriage-baseline-v1",
                candidate_key="pretriage-candidate-v2",
                idempotency_key="review-run-2",
            )
            finding = first["findings"][0]
            other = second["findings"][0]
            with self.assertRaisesRegex(ValueError, "Finding 不存在"):
                record_review(
                    temp / "medgate.sqlite3",
                    finding_id=finding["id"],
                    run_id=first["run_id"],
                    occurrence_id=other["occurrence_id"],
                    attempt_id=other["attempt_id"],
                    decision="false_positive",
                    reason="跨 run 错绑应被拒绝。",
                    output_hash=other["output_hash"],
                    idempotency_key="cross-run-review",
                )

    def test_malformed_review_pack_is_rejected(self) -> None:
        bundle = load_bundle(PROJECT_ROOT)
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            review_path = temp / "malformed.json"
            review_path.write_text("[]", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "顶层"):
                run_offline(
                    bundle,
                    db_path=temp / "medgate.sqlite3",
                    report_path=temp / "gate.json",
                    baseline_key="pretriage-baseline-v1",
                    candidate_key="pretriage-candidate-v2",
                    idempotency_key="malformed-review",
                    review_pack_path=review_path,
                )


if __name__ == "__main__":
    unittest.main()
