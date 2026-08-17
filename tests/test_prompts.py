from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from medgate.api import create_app
from medgate.db import connect
from medgate.prompts import bad_cases_for_version, create_version, list_versions, prompt_hash

from tests.test_live import FakeDeepSeek, PROJECT_ROOT


class PromptVersionsStoreTest(unittest.TestCase):
    def test_create_and_list_version(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "medgate.sqlite3"
            connection = connect(db_path)
            try:
                created = create_version(
                    connection,
                    name="V1",
                    role="baseline",
                    prompt_text="你是基线提示词。",
                    note="初始版本",
                )
                self.assertEqual(created["name"], "V1")
                self.assertEqual(created["role"], "baseline")
                self.assertEqual(created["sha256"], prompt_hash("你是基线提示词。"))
                versions = list_versions(connection)
                self.assertEqual(len(versions), 1)
                self.assertEqual(versions[0]["name"], "V1")
                self.assertEqual(versions[0]["run_count"], 0)
            finally:
                connection.close()

    def test_rejects_duplicate_name_and_content(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            connection = connect(Path(temp_dir) / "medgate.sqlite3")
            try:
                create_version(connection, name="V1", role="either", prompt_text="相同内容")
                with self.assertRaises(ValueError):
                    create_version(connection, name="V1", role="either", prompt_text="不同内容")
                with self.assertRaises(ValueError):
                    create_version(connection, name="V2", role="either", prompt_text="相同内容")
            finally:
                connection.close()

    def test_import_from_live_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            client = TestClient(create_app(PROJECT_ROOT, temp / "medgate.sqlite3", live_client_factory=lambda: FakeDeepSeek()))
            live = client.post(
                "/api/v1/live-runs",
                headers={"Idempotency-Key": "pv-import"},
                json={"baseline_prompt": "\n导入基线带首尾空白\n", "candidate_prompt": "导入候选"},
            )
            self.assertEqual(live.status_code, 201)

            connection = connect(temp / "medgate.sqlite3")
            try:
                created = create_version(
                    connection,
                    name="imported-v1",
                    role="baseline",
                    source_run_id=live.json()["run_id"],
                )
                # 导入保留原文（含首尾空白），sha 与 run 报告的 prompt hash 严格一致，确保 bad case 可关联
                self.assertEqual(created["prompt_text"], "\n导入基线带首尾空白\n")
                self.assertEqual(created["sha256"], prompt_hash("\n导入基线带首尾空白\n"))
                self.assertEqual(created["sha256"], live.json()["report"]["provenance"]["baseline_prompt_hash"])
                self.assertEqual(created["run_count"], 1)
                with self.assertRaises(ValueError):
                    create_version(connection, name="bad", role="either", source_run_id=live.json()["run_id"])
                with self.assertRaises(ValueError):
                    create_version(connection, name="bad2", role="baseline", prompt_text="")
            finally:
                connection.close()

    def test_bad_cases_linked_to_version_sha(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            client = TestClient(create_app(PROJECT_ROOT, temp / "medgate.sqlite3", live_client_factory=lambda: FakeDeepSeek()))
            live = client.post(
                "/api/v1/live-runs",
                headers={"Idempotency-Key": "pv-bad-case"},
                json={"baseline_prompt": "baseline prompt", "candidate_prompt": "candidate prompt"},
            )
            self.assertEqual(live.status_code, 201)
            candidate_prompt_hash = live.json()["report"]["provenance"]["candidate_prompt_hash"]

            connection = connect(temp / "medgate.sqlite3")
            try:
                created = create_version(
                    connection,
                    name="V3",
                    role="candidate",
                    prompt_text="candidate prompt",
                    note="从 live run 导入",
                    source_run_id=live.json()["run_id"],
                )
                self.assertEqual(created["sha256"], candidate_prompt_hash)
                result = bad_cases_for_version(connection, candidate_prompt_hash)
            finally:
                connection.close()

            self.assertIsNotNone(result["version"])
            self.assertEqual(len(result["runs"]), 1)
            run = result["runs"][0]
            self.assertEqual(run["run_id"], live.json()["run_id"])
            self.assertTrue(run["bad_cases"])
            bad_case = run["bad_cases"][0]
            self.assertEqual(bad_case["side"], "candidate")
            self.assertEqual(bad_case["case_id"], "case-003")
            self.assertEqual(bad_case["finding_id"], "finding-017")

    def test_api_routes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            client = TestClient(create_app(PROJECT_ROOT, temp / "medgate.sqlite3", live_client_factory=lambda: FakeDeepSeek()))
            listed = client.get("/api/v1/prompt-versions")
            self.assertEqual(listed.status_code, 200)
            self.assertEqual(listed.json()["versions"], [])

            created = client.post("/api/v1/prompt-versions", json={
                "name": "V1",
                "role": "baseline",
                "prompt_text": "你是基线。",
                "note": "测试",
            })
            self.assertEqual(created.status_code, 201)
            self.assertEqual(created.json()["name"], "V1")

            dup = client.post("/api/v1/prompt-versions", json={
                "name": "V1",
                "role": "baseline",
                "prompt_text": "另一个",
            })
            self.assertEqual(dup.status_code, 422)

            bad = client.get("/api/v1/prompt-versions/nonexistent/bad-cases")
            self.assertEqual(bad.status_code, 404)


if __name__ == "__main__":
    unittest.main()