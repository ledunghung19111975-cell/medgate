from __future__ import annotations

import asyncio
import hashlib
import json
import os
import queue
import threading
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Literal

from fastapi import FastAPI, Header, HTTPException, Query, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .agent import (
    AGENT_RUN_VERSION,
    AGENT_V2_EXECUTION_MODE,
    AgentAssetError,
    AgentSnapshotError,
    AssetReference,
    LocalAssetStore,
    SnapshotStore,
    append_agent_step,
    build_partial_agent_result,
    inspect_agent_package,
    load_agent_step_evidence,
    ordered_case_ids,
    existing_agent_run,
    request_hash_for_request,
    reserve_agent_run,
    run_agent_text,
    set_agent_run_status,
    validate_selected_case_coverage,
    update_agent_run,
)
from .assets import AssetError, load_bundle, select_case_subset
from .db import connect
from .deepseek import ChatClient, DEEPSEEK_MODEL, DeepSeekClient, DeepSeekError
from .engine import recalculate_gate, record_review, rule_catalog, run_evaluation, run_offline, utc_now
from .live import LiveRunCancelled, live_submission_hash, record_live, run_verification_review
from .multidim import evaluate_multidim
from . import prompts as prompt_versions


class RunRequest(BaseModel):
    test_set: str = "pretriage-safety-v1"
    baseline: str = "pretriage-baseline-v1"
    candidate: str = "pretriage-candidate-v2"
    case_ids: list[str] | None = None
    review_pack: str | None = Field(default=None, description="项目内 assets/reviews 下的相对路径")


class LiveRunRequest(BaseModel):
    test_set: str = "pretriage-safety-v1"
    baseline_prompt: str = Field(min_length=1, max_length=12000)
    candidate_prompt: str = Field(min_length=1, max_length=12000)
    case_ids: list[str] | None = None


class ReviewRequest(BaseModel):
    run_id: str
    occurrence_id: str
    attempt_id: str
    decision: str
    reason: str = Field(min_length=5)
    output_hash: str
    effective_severity: str | None = None


class PromptVersionRequest(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    role: str = "either"
    prompt_text: str = Field(default="", max_length=12000, description="留空时从 source_run_id 的对应侧导入")
    note: str | None = Field(default=None, max_length=500)
    source_run_id: str | None = None


class LocalAssetReferenceRequest(BaseModel):
    root_id: str = Field(min_length=1, max_length=64)
    relative_path: str = Field(min_length=1, max_length=512)


class AgentPackageInspectRequest(BaseModel):
    baseline_prompt: LocalAssetReferenceRequest
    candidate_prompt: LocalAssetReferenceRequest
    baseline_skills: LocalAssetReferenceRequest
    candidate_skills: LocalAssetReferenceRequest
    test_set: LocalAssetReferenceRequest
    run_mode: Literal["smoke_once"] = "smoke_once"
    repeat_count: int = Field(default=1, ge=1, le=1)


class AgentRunV2Request(BaseModel):
    test_set: str = Field(min_length=1, max_length=128)
    snapshot_token: str = Field(min_length=8, max_length=128)
    expected_snapshot_hash: str = Field(min_length=64, max_length=128)
    case_ids: list[str] | None = None
    run_mode: Literal["smoke_once"] = "smoke_once"
    repeat_count: int = Field(default=1, ge=1, le=1)


@dataclass(frozen=True)
class ApiSettings:
    project_root: Path
    db_path: Path
    live_concurrency: int = 4


def _settings(project_root: Path | None, db_path: Path | None, live_concurrency: int = 4) -> ApiSettings:
    root = (project_root or Path(__file__).resolve().parents[1]).resolve()
    database = (db_path or root / "artifacts" / "medgate.sqlite3").resolve()
    return ApiSettings(project_root=root, db_path=database, live_concurrency=live_concurrency)


def _error(code: str, message: str, status_code: int = 400) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


class _StepPersistenceError(Exception):
    """Marker: an original audit step failed to persist before the run aborted."""


def _review_pack_path(settings: ApiSettings, relative_path: str | None) -> Path | None:
    if relative_path is None:
        return None
    reviews_root = (settings.project_root / "assets" / "reviews").resolve()
    candidate = (settings.project_root / relative_path).resolve()
    if not candidate.is_relative_to(reviews_root) or not candidate.is_file():
        raise _error("INVALID_REVIEW_PACK_PATH", "ReviewPack 必须是项目内 assets/reviews 下的已存在文件")
    return candidate


def _report_for_run(settings: ApiSettings, run_id: str) -> dict[str, Any]:
    connection = connect(settings.db_path)
    try:
        row = connection.execute(
            "SELECT snapshot_json FROM report_snapshots WHERE run_id = ? ORDER BY rowid DESC LIMIT 1",
            (run_id,),
        ).fetchone()
        if not row:
            raise _error("RUN_NOT_FOUND", f"run 不存在或尚未生成报告：{run_id}", 404)
        return json.loads(row["snapshot_json"])
    finally:
        connection.close()


def _live_response(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "run_id": report["run_id"],
        "status": "completed",
        "gate": report["gate"],
        "summary": report["summary"],
        "provenance": report["provenance"],
        "comparison": report.get("comparison", []),
        "evaluations": report.get("evaluations", []),
        "report_snapshot_id": report.get("report_snapshot_id"),
        "report": report,
    }


def _existing_live_report(
    settings: ApiSettings,
    *,
    idempotency_key: str,
    submission_hash: str,
) -> dict[str, Any] | None:
    connection = connect(settings.db_path)
    try:
        row = connection.execute(
            "SELECT request_hash, status, run_id FROM live_submissions WHERE actor_id = ? AND idempotency_key = ?",
            ("demo-operator", idempotency_key),
        ).fetchone()
        if not row:
            return None
        if row["request_hash"] != submission_hash:
            raise _error("IDEMPOTENCY_CONFLICT", "Idempotency-Key 已用于不同提示词，拒绝重复计费", 409)
        if row["status"] != "completed" or not row["run_id"]:
            completed_run = connection.execute(
                "SELECT id FROM eval_runs WHERE actor_id = ? AND idempotency_key = ? AND request_hash = ? AND status = 'completed'",
                ("demo-operator", idempotency_key, submission_hash),
            ).fetchone()
            if completed_run:
                connection.execute(
                    "UPDATE live_submissions SET status = ?, run_id = ?, updated_at = ? WHERE actor_id = ? AND idempotency_key = ?",
                    ("completed", completed_run["id"], utc_now(), "demo-operator", idempotency_key),
                )
                connection.commit()
                row = {"status": "completed", "run_id": completed_run["id"]}
            else:
                raise _error(
                    "LIVE_RUN_RETRY_BLOCKED",
                    "同一真实运行未完整完成；为避免重复计费，不能自动整批重试，请新建一次运行",
                    409,
                )
        snapshot = connection.execute(
            "SELECT snapshot_json FROM report_snapshots WHERE run_id = ? ORDER BY created_at DESC LIMIT 1",
            (row["run_id"],),
        ).fetchone()
        if not snapshot:
            raise _error("LIVE_RUN_RETRY_BLOCKED", "真实运行缺少报告快照，已拒绝自动重试以避免重复计费", 409)
        report = json.loads(snapshot["snapshot_json"])
        report["idempotent_replay"] = True
        return report
    finally:
        connection.close()


def create_app(
    project_root: Path | None = None,
    db_path: Path | None = None,
    *,
    live_client_factory: Callable[[], ChatClient] | None = None,
    agent_client_factory: Callable[[], ChatClient] | None = None,
    live_model: str = DEEPSEEK_MODEL,
    live_concurrency: int | None = None,
) -> FastAPI:
    concurrency_value = live_concurrency
    if concurrency_value is None:
        concurrency_value = int(os.getenv("MEDGATE_LIVE_CONCURRENCY", "4"))
    concurrency_value = max(1, min(concurrency_value, 16))
    settings = _settings(project_root, db_path, live_concurrency=concurrency_value)
    app = FastAPI(title="MedGate API", version="0.1.0")
    app.state.medgate = settings
    app.state.live_lock = threading.Lock()
    app.state.live_active = False
    app.state.agent_lock = threading.Lock()
    app.state.agent_client_factory = agent_client_factory
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:18181", "http://localhost:18181"],
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "Idempotency-Key", "X-DeepSeek-API-Key"],
    )

    @app.exception_handler(RequestValidationError)
    async def request_validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        issues: list[str] = []
        for error in exc.errors():
            location = ".".join(str(part) for part in error.get("loc", ()) if part != "body")
            message = str(error.get("msg") or "字段值无效")
            issues.append(f"{location}: {message}" if location else message)
        summary = "；".join(issues[:3]) or "请求字段无效"
        if len(issues) > 3:
            summary += f"；另有 {len(issues) - 3} 项"
        return JSONResponse(
            status_code=422,
            content={
                "detail": {
                    "code": "REQUEST_VALIDATION_ERROR",
                    "message": f"请求参数校验失败：{summary}",
                }
            },
        )

    @app.get("/", include_in_schema=False)
    def prototype() -> FileResponse:
        return FileResponse(settings.project_root / "prototype" / "index.html")

    # 原型直接读取这批只读资产，与离线 Runner 共用同一份 testset 和 fixture。
    app.mount(
        "/assets",
        StaticFiles(directory=settings.project_root / "assets"),
        name="assets",
    )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "service": "medgate-api"}

    @app.get("/api/v2/local-assets/roots")
    def list_agent_asset_roots() -> dict[str, Any]:
        store = LocalAssetStore(settings.project_root)
        return {"roots": [root.to_public() for root in store.roots.values()]}

    @app.get("/api/v2/local-assets/entries")
    def list_agent_asset_entries(
        root_id: str = Query(min_length=1, max_length=64),
        relative_path: str = Query(default="", max_length=512),
    ) -> dict[str, Any]:
        try:
            entries = LocalAssetStore(settings.project_root).list_entries(
                AssetReference(root_id=root_id, relative_path=relative_path)
            )
        except AgentAssetError as exc:
            raise _error("LOCAL_ASSET_REJECTED", str(exc), 422) from exc
        return {"root_id": root_id, "relative_path": relative_path, "entries": entries}

    @app.post("/api/v2/agent-packages/inspect")
    def inspect_agent_packages(payload: AgentPackageInspectRequest) -> dict[str, Any]:
        try:
            snapshot = inspect_agent_package(
                settings.project_root,
                baseline_prompt=AssetReference(payload.baseline_prompt.root_id, payload.baseline_prompt.relative_path),
                candidate_prompt=AssetReference(payload.candidate_prompt.root_id, payload.candidate_prompt.relative_path),
                baseline_skills=AssetReference(payload.baseline_skills.root_id, payload.baseline_skills.relative_path),
                candidate_skills=AssetReference(payload.candidate_skills.root_id, payload.candidate_skills.relative_path),
                test_set=AssetReference(payload.test_set.root_id, payload.test_set.relative_path),
                run_mode=payload.run_mode,
                repeat_count=payload.repeat_count,
                model=live_model,
            )
            SnapshotStore(settings.db_path).save(snapshot)
        except (AgentAssetError, OSError) as exc:
            raise _error("AGENT_PREFLIGHT_FAILED", str(exc), 422) from exc
        return {"status": "preflight_passed", **snapshot.to_public()}

    def _agent_report_response(run_id: str, report: dict[str, Any], *, replayed: bool = False) -> JSONResponse:
        content = {
            "run_id": run_id,
            "status": report.get("status", "partial_failed"),
            "gate": report.get("gate"),
            "provisional_gate": report.get("provisional_gate"),
            "report": report,
        }
        if replayed:
            content["idempotent_replayed"] = True
        return JSONResponse(
            status_code=status.HTTP_200_OK if replayed else status.HTTP_201_CREATED,
            content=content,
            headers={"Idempotent-Replayed": "true"} if replayed else None,
        )

    @app.post("/api/v2/live-runs", status_code=status.HTTP_201_CREATED)
    def create_agent_run(
        payload: AgentRunV2Request,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        deepseek_api_key: str | None = Header(default=None, alias="X-DeepSeek-API-Key"),
    ) -> JSONResponse:
        if not idempotency_key or not idempotency_key.strip():
            raise _error("IDEMPOTENCY_KEY_REQUIRED", "Agent 真实运行必须提供 Idempotency-Key", 400)
        key = idempotency_key.strip()
        snapshot_store = SnapshotStore(settings.db_path)
        client: ChatClient | None = None
        request_hash_value = request_hash_for_request(
            snapshot_hash=payload.expected_snapshot_hash,
            test_set=payload.test_set,
            case_ids=payload.case_ids,
            run_mode=payload.run_mode,
            repeat_count=payload.repeat_count,
        )
        try:
            with app.state.agent_lock:
                existing = existing_agent_run(
                    settings.db_path,
                    actor_id="demo-operator",
                    idempotency_key=key,
                    request_hash_value=request_hash_value,
                )
                if existing:
                    if not existing.get("report"):
                        raise _error("AGENT_RUN_RETRY_BLOCKED", "同一 Agent 运行未完整结束，请新建一次运行", 409)
                    return _agent_report_response(
                        str(existing.get("run_id") or existing["report"].get("run_id", "")),
                        existing["report"],
                        replayed=True,
                    )
            snapshot = snapshot_store.load(payload.snapshot_token, payload.expected_snapshot_hash)
            if payload.test_set != snapshot.test_set:
                raise AgentSnapshotError("test_set 与快照不一致")
            if payload.run_mode != snapshot.run_mode or payload.repeat_count != snapshot.repeat_count:
                raise AgentSnapshotError("运行模式或重复次数与快照不一致")
            case_ids = ordered_case_ids(snapshot, payload.case_ids)
            validate_selected_case_coverage(snapshot, case_ids)
            if not app.state.agent_client_factory:
                api_key = (
                    deepseek_api_key.strip()
                    if deepseek_api_key and deepseek_api_key.strip()
                    else os.getenv("DEEPSEEK_API_KEY", "")
                )
                if not api_key:
                    raise _error("DEEPSEEK_API_KEY_MISSING", "本机未配置 DeepSeek API Key，未创建 Agent 运行", 503)
            with app.state.agent_lock:
                existing = existing_agent_run(
                    settings.db_path,
                    actor_id="demo-operator",
                    idempotency_key=key,
                    request_hash_value=request_hash_value,
                )
                if existing:
                    if not existing.get("report"):
                        raise _error("AGENT_RUN_RETRY_BLOCKED", "同一 Agent 运行未完整结束，请新建一次运行", 409)
                    return _agent_report_response(
                        str(existing.get("run_id") or existing["report"].get("run_id", "")),
                        existing["report"],
                        replayed=True,
                    )
                run_id = f"agent-run-{uuid.uuid4().hex}"
                existing = reserve_agent_run(
                    settings.db_path,
                    run_id=run_id,
                    actor_id="demo-operator",
                    idempotency_key=key,
                    request_hash_value=request_hash_value,
                    snapshot_token=payload.snapshot_token,
                    snapshot_hash=payload.expected_snapshot_hash,
                )
                if existing:
                    if not existing.get("report"):
                        raise _error("AGENT_RUN_RETRY_BLOCKED", "同一 Agent 运行未完整结束，请新建一次运行", 409)
                    return _agent_report_response(
                        str(existing["report"].get("run_id", "")),
                        existing["report"],
                        replayed=True,
                    )
        except AgentSnapshotError as exc:
            code = "AGENT_IDEMPOTENCY_CONFLICT" if str(exc).startswith("Idempotency-Key") else "AGENT_SNAPSHOT_REJECTED"
            raise _error(code, str(exc), 409) from exc

        def report_from_result(result: Any, *, step_persistence_incomplete: bool = False) -> dict[str, Any]:
            return {
                "schema_version": AGENT_RUN_VERSION,
                "run_id": run_id,
                "status": result.status,
                "test_set": snapshot.test_set,
                "snapshot_hash": snapshot.snapshot_hash,
                "run_input_hash": result.run_input_hash,
                "execution_mode": snapshot.environment.get("execution_mode", AGENT_V2_EXECUTION_MODE),
                "variable_mode": snapshot.variable_mode,
                "artifact_diff": snapshot.artifact_diff,
                "coverage_matrix": snapshot.coverage_matrix,
                "comparison": result.comparison,
                "traces": [asdict(trace) for trace in result.traces],
                "assertions": [asdict(assertion) for assertion in result.assertions],
                "gate": asdict(result.gate) if result.gate else None,
                "provisional_gate": asdict(result.provisional_gate) if result.provisional_gate else None,
                "gate_input_hash": result.gate_input_hash,
                "error": result.error,
                "external_call_count": result.external_call_count,
                "estimated_tokens": result.estimated_tokens,
                "environment_drift": result.environment_drift,
                "step_persistence_incomplete": step_persistence_incomplete,
                "provenance": {
                    "snapshot_hash": snapshot.snapshot_hash,
                    "testset_hash": snapshot.testset_hash,
                    "environment": snapshot.environment,
                    "outbound_envelope": snapshot.outbound_envelope,
                },
            }

        def persist_setup_failure(error: dict[str, str], *, original_step_failure: bool = False) -> dict[str, Any]:
            traces, assertions, external_call_count, estimated_tokens, environment_drift = load_agent_step_evidence(
                settings.db_path,
                run_id=run_id,
                snapshot=snapshot,
                case_ids=case_ids,
                expected_model=snapshot.environment.get("model"),
            )
            result = build_partial_agent_result(
                snapshot,
                case_ids=case_ids,
                error=error,
                traces=traces,
                assertions=assertions,
                external_call_count=external_call_count,
                estimated_tokens=estimated_tokens,
                environment_drift=environment_drift,
            )
            # 原始 step 已确认丢失时，即使本次 provisional 落库成功，也必须如实标记审计不完整。
            step_persistence_incomplete = original_step_failure
            try:
                append_agent_step(
                    settings.db_path,
                    run_id=run_id,
                    step_no=None,
                    event={
                        "type": "provisional_gate_decided",
                        "gate": asdict(result.provisional_gate) if result.provisional_gate else None,
                        "gate_input_hash": result.gate_input_hash,
                        "error": error,
                    },
                )
            except Exception:  # noqa: BLE001 - preserve the sanitized partial report and expose incomplete audit persistence.
                step_persistence_incomplete = True
            report = report_from_result(result, step_persistence_incomplete=step_persistence_incomplete)
            update_agent_run(settings.db_path, run_id=run_id, status=result.status, report=report)
            return report

        try:
            if app.state.agent_client_factory:
                client = app.state.agent_client_factory()
            else:
                api_key = (
                    deepseek_api_key.strip()
                    if deepseek_api_key and deepseek_api_key.strip()
                    else os.getenv("DEEPSEEK_API_KEY", "")
                )
                client = DeepSeekClient(api_key, model=live_model)
            set_agent_run_status(settings.db_path, run_id=run_id, status="running")

            def persist_step(event: dict[str, Any]) -> None:
                try:
                    append_agent_step(settings.db_path, run_id=run_id, step_no=int(event["step_no"]), event=event)
                except Exception:  # noqa: BLE001 - any step write failure is an audit integrity break.
                    raise _StepPersistenceError from None

            result = run_agent_text(
                snapshot,
                client=client,
                case_ids=case_ids,
                on_event=persist_step,
            )
            report = report_from_result(result)
            update_agent_run(settings.db_path, run_id=run_id, status=result.status, report=report)
            return _agent_report_response(run_id, report)
        except _StepPersistenceError:
            report = persist_setup_failure(
                {"code": "AGENT_RUN_FAILED", "message": "Agent 运行中止：审计步骤写入失败，已保留已落盘步骤"},
                original_step_failure=True,
            )
            return _agent_report_response(run_id, report)
        except DeepSeekError as exc:
            report = persist_setup_failure({"code": exc.code, "message": exc.message})
            raise _error(exc.code, exc.message, exc.status_code) from exc
        except Exception:
            report = persist_setup_failure({"code": "AGENT_RUN_FAILED", "message": "Agent 运行异常中止，已保留已落盘步骤"})
            return _agent_report_response(run_id, report)
        finally:
            close_client = getattr(client, "close", None)
            if callable(close_client):
                close_client()

    @app.get("/api/v2/live-runs/{run_id}")
    def get_agent_run(run_id: str) -> dict[str, Any]:
        connection = connect(settings.db_path)
        try:
            row = connection.execute("SELECT status, report_json FROM agent_runs WHERE id = ?", (run_id,)).fetchone()
            steps = connection.execute(
                "SELECT step_no, step_type, status, payload_json, created_at FROM agent_run_steps WHERE run_id = ? ORDER BY step_no",
                (run_id,),
            ).fetchall()
        finally:
            connection.close()
        if not row:
            raise _error("AGENT_RUN_NOT_FOUND", "Agent 运行不存在", 404)
        report = json.loads(row["report_json"]) if row["report_json"] else None
        return {
            "run_id": run_id,
            "status": row["status"],
            "gate": report.get("gate") if report else None,
            "provisional_gate": report.get("provisional_gate") if report else None,
            "report": report,
            "steps": [
                {
                    "step_no": step["step_no"],
                    "step_type": step["step_type"],
                    "status": step["status"],
                    "payload": json.loads(step["payload_json"]),
                    "created_at": step["created_at"],
                }
                for step in steps
            ],
        }

    @app.get("/api/v1/rules")
    def get_rules() -> dict[str, Any]:
        return rule_catalog()

    @app.get("/api/v1/prompt-versions")
    def list_prompt_versions() -> dict[str, Any]:
        connection = connect(settings.db_path)
        try:
            return {"versions": prompt_versions.list_versions(connection)}
        finally:
            connection.close()

    @app.post("/api/v1/prompt-versions", status_code=status.HTTP_201_CREATED)
    def create_prompt_version(payload: PromptVersionRequest) -> dict[str, Any]:
        connection = connect(settings.db_path)
        try:
            version = prompt_versions.create_version(
                connection,
                name=payload.name,
                role=payload.role,
                prompt_text=payload.prompt_text,
                note=payload.note,
                source_run_id=payload.source_run_id,
            )
        except ValueError as exc:
            raise _error("PROMPT_VERSION_REJECTED", str(exc), 422) from exc
        finally:
            connection.close()
        return version

    @app.get("/api/v1/prompt-versions/{sha256}/bad-cases")
    def get_prompt_version_bad_cases(sha256: str) -> dict[str, Any]:
        connection = connect(settings.db_path)
        try:
            result = prompt_versions.bad_cases_for_version(connection, sha256)
        finally:
            connection.close()
        if result["version"] is None:
            raise _error("PROMPT_VERSION_NOT_FOUND", f"提示词版本不存在：{sha256}", 404)
        return result

    @app.post("/api/v1/runs", status_code=status.HTTP_201_CREATED)
    def create_run(payload: RunRequest | None = None, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")) -> dict[str, Any]:
        if not idempotency_key or not idempotency_key.strip():
            raise _error("IDEMPOTENCY_KEY_REQUIRED", "必须提供 Idempotency-Key", 400)
        payload = payload or RunRequest()
        try:
            bundle = load_bundle(settings.project_root, testset_key=payload.test_set)
            bundle = select_case_subset(bundle, payload.case_ids)
            if "scenarios" in bundle.manifest:
                # 多维测试集离线评估：独立路径（复用 evaluate_multidim，不落 pretriage SQLite finding）
                report = evaluate_multidim(
                    bundle,
                    report_path=settings.project_root / "artifacts" / f"gate-{hashlib.sha256(idempotency_key.encode()).hexdigest()[:16]}.json",
                )
                # 与 pretriage 离线报告保持最小响应形态一致，便于前端与 CI 断言
                if "run_id" not in report:
                    report["run_id"] = f"run-{uuid.uuid4().hex[:8]}"
                    report["generated_at"] = utc_now()
                    report["idempotent_replay"] = False
                    # 补齐 summary.provenance 供前端展示
                    report.setdefault("summary", {})
                    report.setdefault("provenance", {})
            else:
                report = run_offline(
                    bundle,
                    db_path=settings.db_path,
                    report_path=settings.project_root / "artifacts" / f"gate-{hashlib.sha256(idempotency_key.encode()).hexdigest()[:16]}.json",
                    baseline_key=payload.baseline,
                    candidate_key=payload.candidate,
                    idempotency_key=idempotency_key.strip(),
                    review_pack_path=_review_pack_path(settings, payload.review_pack),
                    case_ids=payload.case_ids,
                )
        except (AssetError, ValueError, OSError) as exc:
            raise _error("RUN_REJECTED", str(exc), 422) from exc
        replayed = bool(report.get("idempotent_replay"))
        response = {
            "run_id": report["run_id"],
            "status": "completed",
            "gate": report["gate"],
            "summary": report["summary"],
            "provenance": report["provenance"],
        }
        if replayed:
            response["idempotent_replayed"] = True
        return JSONResponse(
            status_code=status.HTTP_200_OK if replayed else status.HTTP_201_CREATED,
            content=response,
            headers={"Idempotent-Replayed": "true"} if replayed else None,
        )

    def _validate_live_request(payload: LiveRunRequest, idempotency_key: str | None) -> str:
        if not idempotency_key or not idempotency_key.strip():
            raise _error("IDEMPOTENCY_KEY_REQUIRED", "真实运行必须提供 Idempotency-Key", 400)
        if not payload.baseline_prompt.strip() or not payload.candidate_prompt.strip():
            raise _error("PROMPT_REQUIRED", "Baseline 与 Candidate 提示词均不能为空", 422)
        if payload.baseline_prompt == payload.candidate_prompt:
            raise _error("PROMPTS_IDENTICAL", "Baseline 与 Candidate 提示词不能完全相同", 422)
        return idempotency_key.strip()

    def _prepare_live_run(
        payload: LiveRunRequest,
        idempotency_key: str | None,
        deepseek_api_key: str | None,
    ) -> tuple[Any, str, str, ChatClient | None, dict[str, Any] | None]:
        key = _validate_live_request(payload, idempotency_key)
        bundle = load_bundle(settings.project_root, testset_key=payload.test_set)
        bundle = select_case_subset(bundle, payload.case_ids)
        submission_hash = live_submission_hash(
            bundle,
            baseline_prompt=payload.baseline_prompt,
            candidate_prompt=payload.candidate_prompt,
            model=live_model,
            case_ids=payload.case_ids,
        )
        with app.state.live_lock:
            existing = _existing_live_report(
                settings,
                idempotency_key=key,
                submission_hash=submission_hash,
            )
            if existing:
                return bundle, key, submission_hash, None, existing
            if app.state.live_active:
                raise _error("LIVE_RUN_BUSY", "已有真实评测正在运行，请等待当前运行结束", 409)
            shared_key = connect(settings.db_path)
            try:
                conflicting_run = shared_key.execute(
                    "SELECT request_hash FROM eval_runs WHERE actor_id = ? AND idempotency_key = ?",
                    ("demo-operator", key),
                ).fetchone()
            finally:
                shared_key.close()
            if conflicting_run:
                raise _error(
                    "IDEMPOTENCY_CONFLICT",
                    "Idempotency-Key 已用于另一条回放或真实运行，已在模型调用前拒绝",
                    409,
                )
            if live_client_factory:
                client = live_client_factory()
            else:
                api_key = (
                    deepseek_api_key.strip()
                    if deepseek_api_key and deepseek_api_key.strip()
                    else os.getenv("DEEPSEEK_API_KEY", "")
                )
                client = DeepSeekClient(api_key, model=live_model)
            reservation = connect(settings.db_path)
            try:
                with reservation:
                    reservation.execute(
                        "INSERT OR IGNORE INTO actors(id, display_name, role) VALUES (?, ?, ?)",
                        ("demo-operator", "MedGate 本地演示操作者", "operator"),
                    )
                    reservation.execute(
                        "INSERT INTO live_submissions(actor_id, idempotency_key, request_hash, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                        ("demo-operator", key, submission_hash, "running", utc_now(), utc_now()),
                    )
            finally:
                reservation.close()
            app.state.live_active = True
        return bundle, key, submission_hash, client, None

    def _mark_live_submission_status(key: str, status_value: str) -> None:
        connection = connect(settings.db_path)
        try:
            with connection:
                connection.execute(
                    "UPDATE live_submissions SET status = ?, updated_at = ? WHERE actor_id = ? AND idempotency_key = ?",
                    (status_value, utc_now(), "demo-operator", key),
                )
        finally:
            connection.close()

    def _execute_live_run(
        bundle: Any,
        payload: LiveRunRequest,
        key: str,
        submission_hash: str,
        client: ChatClient,
        on_event: Callable[[dict[str, Any]], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        try:
            with app.state.live_lock:
                recording = record_live(
                    bundle,
                    baseline_prompt=payload.baseline_prompt,
                    candidate_prompt=payload.candidate_prompt,
                    model=live_model,
                    client=client,
                    on_event=on_event,
                    should_cancel=should_cancel,
                    concurrency=settings.live_concurrency,
                )
                if "scenarios" in bundle.manifest:
                    # 多维测试集 live：用 candidate 侧 live 回答按 scenario 确定性评分（只出分，boundary 零容忍）
                    from .multidim import _assistant_text as _md_assistant_text

                    candidate_answers = {}
                    for fixture in recording.fixtures:
                        if fixture.get("agent_key") == "pretriage-candidate-v2":
                            candidate_answers[str(fixture.get("case_id"))] = _md_assistant_text(fixture.get("raw_output"))
                    md_report = evaluate_multidim(
                        bundle,
                        candidate_answers=candidate_answers,
                        report_path=settings.db_path.parent / f"live-gate-{hashlib.sha256(key.encode()).hexdigest()[:16]}.json",
                    )
                    run_id = f"run-{uuid.uuid4().hex[:8]}"
                    md_report["run_id"] = run_id
                    md_report["generated_at"] = utc_now()
                    md_report["idempotent_replay"] = False
                    md_report.setdefault("summary", {})["external_call_count"] = recording.external_call_count
                    md_report.setdefault("provenance", {}).update(
                        {
                            "external_call_count": recording.external_call_count,
                            "artifact": recording.artifact,
                            "run_id": run_id,
                            "request_hash": submission_hash,
                        }
                    )
                    md_report["provenance"]["external_call_count"] = recording.external_call_count
                    # 兼容 pretriage live 的响应形态：summary.p0_count 等由 evaluate_multidim 已给出 boundary 结论
                    report = md_report
                else:
                    report = run_evaluation(
                        bundle,
                        db_path=settings.db_path,
                        report_path=settings.db_path.parent / f"live-gate-{hashlib.sha256(key.encode()).hexdigest()[:16]}.json",
                        baseline_key="pretriage-baseline-v1",
                        candidate_key="pretriage-candidate-v2",
                        idempotency_key=key,
                        fixtures=recording.fixtures,
                        fixture_hash=recording.fixture_hash,
                        artifact=recording.artifact,
                        external_call_count=recording.external_call_count,
                        judge_hash_override=recording.judge_hash,
                        request_hash_override=submission_hash,
                        case_ids=payload.case_ids,
                    )
                completed = connect(settings.db_path)
                try:
                    with completed:
                        completed.execute(
                            "UPDATE live_submissions SET status = ?, run_id = ?, updated_at = ? WHERE actor_id = ? AND idempotency_key = ?",
                            ("completed", report["run_id"], utc_now(), "demo-operator", key),
                        )
                finally:
                    completed.close()
                return report
        except LiveRunCancelled:
            _mark_live_submission_status(key, "cancelled")
            raise
        except Exception:
            _mark_live_submission_status(key, "failed")
            raise
        finally:
            app.state.live_active = False
            close_client = getattr(client, "close", None)
            if callable(close_client):
                close_client()

    def _sse(event_type: str, payload: dict[str, Any]) -> str:
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        return f"event: {event_type}\ndata: {data}\n\n"

    @app.post("/api/v1/live-runs", status_code=status.HTTP_201_CREATED)
    def create_live_run(
        payload: LiveRunRequest,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        deepseek_api_key: str | None = Header(default=None, alias="X-DeepSeek-API-Key"),
    ) -> dict[str, Any]:
        try:
            bundle, key, submission_hash, client, existing = _prepare_live_run(payload, idempotency_key, deepseek_api_key)
            if existing:
                return JSONResponse(
                    status_code=status.HTTP_200_OK,
                    content={**_live_response(existing), "idempotent_replayed": True},
                    headers={"Idempotent-Replayed": "true"},
                )
            if client is None:
                raise _error("LIVE_RUN_REJECTED", "无法创建真实评测客户端", 422)
            report = _execute_live_run(bundle, payload, key, submission_hash, client)
        except DeepSeekError as exc:
            raise _error(exc.code, exc.message, exc.status_code) from exc
        except HTTPException:
            raise
        except (AssetError, ValueError, OSError) as exc:
            raise _error("LIVE_RUN_REJECTED", str(exc), 422) from exc
        return _live_response(report)

    @app.post("/api/v1/live-runs/stream")
    def create_live_run_stream(
        request: Request,
        payload: LiveRunRequest,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        deepseek_api_key: str | None = Header(default=None, alias="X-DeepSeek-API-Key"),
    ) -> StreamingResponse:
        try:
            bundle, key, submission_hash, client, existing = _prepare_live_run(payload, idempotency_key, deepseek_api_key)
        except DeepSeekError as exc:
            raise _error(exc.code, exc.message, exc.status_code) from exc
        except HTTPException:
            raise
        except (AssetError, ValueError, OSError) as exc:
            raise _error("LIVE_RUN_REJECTED", str(exc), 422) from exc

        async def event_stream():
            if existing:
                yield _sse("run_started", {
                    "status": "completed",
                    "replayed": True,
                    "case_count": len(bundle.cases),
                    "total_items": len(bundle.cases) * 2,
                    "total_calls": sum(len(case["input"]["turns"]) + 1 for case in bundle.cases) * 2,
                })
                yield _sse("completed", {**_live_response(existing), "idempotent_replayed": True})
                return

            if client is None:
                raise _error("LIVE_RUN_REJECTED", "无法创建真实评测客户端", 422)
            events: queue.Queue[dict[str, Any] | None] = queue.Queue()
            cancel_event = threading.Event()

            def on_event(event: dict[str, Any]) -> None:
                events.put(event)

            def worker() -> None:
                try:
                    report = _execute_live_run(
                        bundle,
                        payload,
                        key,
                        submission_hash,
                        client,
                        on_event=on_event,
                        should_cancel=cancel_event.is_set,
                    )
                    events.put({"type": "completed", **_live_response(report)})
                except LiveRunCancelled:
                    events.put({"type": "cancelled", "message": "浏览器已断开，已停止后续模型调用。"})
                except DeepSeekError as exc:
                    events.put({
                        "type": "error",
                        "code": exc.code,
                        "message": exc.message,
                        "requires_new_attempt": True,
                    })
                except (AssetError, ValueError, OSError) as exc:
                    events.put({
                        "type": "error",
                        "code": "LIVE_RUN_REJECTED",
                        "message": str(exc),
                        "requires_new_attempt": True,
                    })
                except Exception:
                    events.put({
                        "type": "error",
                        "code": "LIVE_RUN_FAILED",
                        "message": "真实评测未完成，请新建一次运行。",
                        "requires_new_attempt": True,
                    })
                finally:
                    events.put(None)

            threading.Thread(target=worker, name="medgate-live-stream", daemon=True).start()
            try:
                while True:
                    if await request.is_disconnected():
                        cancel_event.set()
                        return
                    try:
                        event = await asyncio.to_thread(events.get, True, 10.0)
                    except queue.Empty:
                        yield ": keep-alive\n\n"
                        continue
                    if event is None:
                        break
                    event_type = str(event.get("type") or "progress")
                    yield _sse(event_type, {field_name: value for field_name, value in event.items() if field_name != "type"})
            finally:
                cancel_event.set()

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @app.get("/api/v1/runs/{run_id}")
    def get_run(run_id: str) -> dict[str, Any]:
        report = _report_for_run(settings, run_id)
        return {
            "run_id": report["run_id"],
            "status": "completed",
            "gate": report["gate"],
            "summary": report["summary"],
            "provenance": report["provenance"],
            "report_snapshot_id": report.get("report_snapshot_id"),
        }

    @app.get("/api/v1/runs/{run_id}/comparison")
    def get_comparison(run_id: str) -> dict[str, Any]:
        report = _report_for_run(settings, run_id)
        return {
            "run_id": run_id,
            "comparison": report.get("comparison", []),
            "findings": report.get("findings", []),
            "provenance": report.get("provenance", {}),
        }

    @app.post("/api/v1/findings/{finding_id}:review")
    def review_finding(
        finding_id: str,
        payload: ReviewRequest,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        if not idempotency_key or not idempotency_key.strip():
            raise _error("IDEMPOTENCY_KEY_REQUIRED", "复核写入必须提供 Idempotency-Key", 400)
        try:
            review = record_review(
                settings.db_path,
                finding_id=finding_id,
                run_id=payload.run_id,
                occurrence_id=payload.occurrence_id,
                attempt_id=payload.attempt_id,
                decision=payload.decision,
                reason=payload.reason,
                output_hash=payload.output_hash,
                effective_severity=payload.effective_severity,
                idempotency_key=idempotency_key.strip(),
            )
        except ValueError as exc:
            raise _error("REVIEW_REJECTED", str(exc), 422) from exc
        return {"status": "recorded", "review": review, "next": f"POST /api/v1/runs/{review['run_id']}:calculate-gate"}

    @app.post("/api/v1/runs/{run_id}:calculate-gate")
    def calculate_gate(run_id: str) -> dict[str, Any]:
        try:
            report = recalculate_gate(settings.db_path, run_id=run_id)
        except ValueError as exc:
            raise _error("GATE_REJECTED", str(exc), 422) from exc
        return {
            "run_id": run_id,
            "gate": report["gate"],
            "summary": report["summary"],
            "findings": report.get("findings", []),
            "report_snapshot_id": report.get("report_snapshot_id"),
        }

    @app.get("/api/v1/runs/{run_id}/gate")
    def get_gate(run_id: str) -> dict[str, Any]:
        report = _report_for_run(settings, run_id)
        return {
            "run_id": run_id,
            "gate": report["gate"],
            "report_snapshot_id": report.get("report_snapshot_id"),
            "provenance": report.get("provenance", {}),
        }

    @app.post("/api/v1/runs/{run_id}:export-report")
    def export_report(run_id: str) -> dict[str, Any]:
        report = _report_for_run(settings, run_id)
        if report["gate"]["state"] == "REVIEW_REQUIRED":
            raise _error("REPORT_NOT_FINAL", "REVIEW_REQUIRED run 不能导出最终报告", 409)
        return {
            "status": "exported",
            "run_id": run_id,
            "report_snapshot_id": report.get("report_snapshot_id"),
            "report": report,
        }

    return app


app = create_app()


def main() -> None:
    import uvicorn

    host = os.getenv("MEDGATE_HOST", "127.0.0.1")
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError("MedGate 本地 live API 只允许绑定 loopback 地址")
    uvicorn.run(
        app,
        host=host,
        port=int(os.getenv("MEDGATE_PORT", "8000")),
    )


if __name__ == "__main__":
    main()
