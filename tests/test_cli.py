from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from medgate import cli


class CliExitCodeTest(unittest.TestCase):
    def test_unexpected_exception_exits_3_not_1(self) -> None:
        # 执行错误必须是退出码 3：未捕获异常（如并发同 key 触发的 IntegrityError）
        # 不得以 Python 默认退出码 1 冒充 BLOCKED（2026-08-20 审核 P1-2 的回归锚点）
        real_connect = sqlite3.connect

        def raise_integrity_error(*args, **kwargs):
            raise sqlite3.IntegrityError("UNIQUE constraint failed: eval_runs.idempotency_key")

        with mock.patch.object(sqlite3, "connect", side_effect=raise_integrity_error):
            code = cli.main([
                "run",
                "--db", "/tmp/does-not-matter.sqlite3",
                "--report", "/tmp/does-not-matter.json",
                "--idempotency-key", "conflict-key",
            ])
        self.assertEqual(code, 3)

    def test_gate_rejects_forged_exit_code(self) -> None:
        # gate 子命令不得透传外部报告里伪造的 exit_code（2026-08-20 审核 P2-④）
        with tempfile.TemporaryDirectory() as temp_dir:
            forged = Path(temp_dir) / "forged.json"
            forged.write_text(json.dumps({"gate": {"state": "BLOCKED", "reason_codes": ["UNRESOLVED_P0"], "exit_code": 0}}), encoding="utf-8")
            self.assertEqual(cli.main(["gate", str(forged)]), 3)

            mismatched = Path(temp_dir) / "mismatched.json"
            mismatched.write_text(json.dumps({"gate": {"state": "PASSED", "reason_codes": [], "exit_code": 7}}), encoding="utf-8")
            self.assertEqual(cli.main(["gate", str(mismatched)]), 3)

            valid = Path(temp_dir) / "valid.json"
            valid.write_text(json.dumps({"gate": {"state": "BLOCKED", "reason_codes": ["UNRESOLVED_P0"], "exit_code": 1}}), encoding="utf-8")
            self.assertEqual(cli.main(["gate", str(valid)]), 1)


if __name__ == "__main__":
    unittest.main()
