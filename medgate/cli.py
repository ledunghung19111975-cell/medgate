from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path

from .assets import AssetError, load_bundle
from .engine import EXIT_CODES, run_offline


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="medgate", description="MedGate 离线评测与发布门禁 CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate = subparsers.add_parser("validate", help="校验版本化病例与回放资产")
    validate.add_argument("--project-root", type=Path, default=None)
    validate.add_argument("--test-set", default="pretriage-safety-v1")
    run = subparsers.add_parser("run", help="执行固定回放并生成 Gate 报告")
    run.add_argument("--project-root", type=Path, default=None)
    run.add_argument("--test-set", default="pretriage-safety-v1")
    run.add_argument("--baseline", default="pretriage-baseline-v1")
    run.add_argument("--candidate", default="pretriage-candidate-v2")
    run.add_argument("--db", type=Path, default=Path("artifacts/medgate.sqlite3"))
    run.add_argument("--report", type=Path, default=Path("artifacts/gate.json"))
    run.add_argument("--idempotency-key", default=None)
    run.add_argument("--review-pack", type=Path, default=None)
    gate = subparsers.add_parser("gate", help="读取已有 Gate 报告")
    gate.add_argument("report", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "validate":
            bundle = load_bundle(args.project_root)
            if bundle.testset_key != args.test_set:
                raise ValueError(f"未知测试集：{args.test_set}")
            print(json.dumps({
                "testset_key": bundle.testset_key,
                "case_count": len(bundle.cases),
                "fixture_count": len(bundle.fixtures),
                "agents": list(bundle.agent_keys),
                "expected_gate": bundle.manifest["expected_gate"],
                "status": "ok",
            }, ensure_ascii=False, indent=2))
            return 0
        if args.command == "gate":
            report = json.loads(args.report.read_text(encoding="utf-8"))
            print(json.dumps(report.get("gate", {}), ensure_ascii=False, indent=2))
            return int(report.get("gate", {}).get("exit_code", 3))
        bundle = load_bundle(args.project_root)
        if bundle.testset_key != args.test_set:
            raise ValueError(f"未知测试集：{args.test_set}")
        report = run_offline(
            bundle,
            db_path=args.db,
            report_path=args.report,
            baseline_key=args.baseline,
            candidate_key=args.candidate,
            idempotency_key=args.idempotency_key or str(uuid.uuid4()),
            review_pack_path=args.review_pack,
        )
        print(json.dumps({
            "run_id": report["run_id"],
            "gate": report["gate"],
            "report": str(args.report),
            "report_snapshot_id": report.get("report_snapshot_id"),
            "idempotent_replay": report.get("idempotent_replay", False),
        }, ensure_ascii=False, indent=2))
        return int(report["gate"]["exit_code"])
    except (AssetError, ValueError, OSError, KeyError, json.JSONDecodeError) as exc:
        print(json.dumps({"error": str(exc), "exit_code": 3}, ensure_ascii=False), file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
