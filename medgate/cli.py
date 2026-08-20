from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import uuid
from pathlib import Path

from .assets import AssetError, load_bundle
from .engine import EXIT_CODES, run_offline
from .multidim import evaluate_multidim


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
            bundle = load_bundle(args.project_root, testset_key=args.test_set)
            if bundle.testset_key != args.test_set:
                raise ValueError(f"未知测试集：{args.test_set}")
            print(json.dumps({
                "testset_key": bundle.testset_key,
                "case_count": len(bundle.cases),
                "fixture_count": len(bundle.fixtures),
                "agents": list(bundle.agent_keys),
                "expected_gate": bundle.manifest.get("expected_gate"),
                "scenarios": bundle.manifest.get("scenarios"),
                "status": "ok",
            }, ensure_ascii=False, indent=2))
            return 0
        if args.command == "gate":
            report = json.loads(args.report.read_text(encoding="utf-8"))
            gate = report.get("gate", {})
            state, exit_code = gate.get("state"), gate.get("exit_code")
            # 报告是外部输入：state/exit_code 必须落在三态白名单内且互相一致，
            # 伪造或损坏的 exit_code（如 0/7）不得原样透传给 CI 断言
            if state not in EXIT_CODES or exit_code != EXIT_CODES[state]:
                print(json.dumps({"error": f"报告 gate 无效：state={state!r}, exit_code={exit_code!r}", "exit_code": 3}, ensure_ascii=False), file=sys.stderr)
                return 3
            print(json.dumps(gate, ensure_ascii=False, indent=2))
            return int(exit_code)
        bundle = load_bundle(args.project_root, testset_key=args.test_set)
        if bundle.testset_key != args.test_set:
            raise ValueError(f"未知测试集：{args.test_set}")
        if "scenarios" in bundle.manifest:
            report = evaluate_multidim(bundle, report_path=args.report)
        else:
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
            "run_id": report.get("run_id"),
            "gate": report["gate"],
            "report": str(args.report),
            "report_snapshot_id": report.get("report_snapshot_id"),
            "idempotent_replay": report.get("idempotent_replay", False),
        }, ensure_ascii=False, indent=2))
        return int(report["gate"]["exit_code"])
    except (AssetError, ValueError, OSError, KeyError, sqlite3.Error, json.JSONDecodeError) as exc:
        print(json.dumps({"error": str(exc), "exit_code": 3}, ensure_ascii=False), file=sys.stderr)
        return 3
    except Exception as exc:  # 执行错误必须是 3，逃逸的未预期异常不得以退出码 1 冒充 BLOCKED
        print(json.dumps({"error": f"{type(exc).__name__}: {exc}", "exit_code": 3}, ensure_ascii=False), file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
