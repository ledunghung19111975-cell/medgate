from __future__ import annotations

import hashlib
import json
import uuid
from sqlite3 import Connection
from typing import Any

from .engine import utc_now

ROLES = {"baseline", "candidate", "either"}


def prompt_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def list_versions(connection: Connection) -> list[dict[str, Any]]:
    rows = connection.execute(
        "SELECT id, name, role, sha256, created_at, source_run_id, note FROM prompt_versions ORDER BY created_at ASC, name ASC"
    ).fetchall()
    versions: list[dict[str, Any]] = []
    for row in rows:
        versions.append({
            "id": row["id"],
            "name": row["name"],
            "role": row["role"],
            "sha256": row["sha256"],
            "created_at": row["created_at"],
            "source_run_id": row["source_run_id"],
            "note": row["note"],
            "bad_case_count": _count_bad_cases(connection, row["sha256"]),
            "run_count": _count_matching_runs(connection, row["sha256"]),
        })
    return versions


def get_version(connection: Connection, sha256: str) -> dict[str, Any] | None:
    row = connection.execute(
        "SELECT id, name, role, prompt_text, sha256, created_at, source_run_id, note FROM prompt_versions WHERE sha256 = ?",
        (sha256,),
    ).fetchone()
    if not row:
        return None
    return {
        "id": row["id"],
        "name": row["name"],
        "role": row["role"],
        "prompt_text": row["prompt_text"],
        "sha256": row["sha256"],
        "created_at": row["created_at"],
        "source_run_id": row["source_run_id"],
        "note": row["note"],
        "bad_case_count": _count_bad_cases(connection, sha256),
        "run_count": _count_matching_runs(connection, sha256),
    }


def create_version(
    connection: Connection,
    *,
    name: str,
    role: str,
    prompt_text: str = "",
    note: str | None = None,
    source_run_id: str | None = None,
) -> dict[str, Any]:
    name = name.strip()
    role = role.strip().lower()
    if not name:
        raise ValueError("版本名称不能为空")
    if role not in ROLES:
        raise ValueError(f"role 必须是 {sorted(ROLES)} 之一")
    imported = False
    if not prompt_text.strip():
        if not source_run_id:
            raise ValueError("提示词全文不能为空（或提供 source_run_id 从 run 导入）")
        if role == "either":
            raise ValueError("从 run 导入时必须指定 role 为 baseline 或 candidate")
        prompt_text = _extract_prompt_from_run(connection, source_run_id, role)
        imported = True
    # 导入文本保留原文（run 的 prompt_hash 按用户输入原样计算，strip 会导致 sha 失配无法关联）；
    # 手动粘贴文本做 trim 便于规范化。
    text = prompt_text if imported else prompt_text.strip()
    if not text:
        raise ValueError("从 run 导入的提示词为空，无法保存")
    if len(text) > 12000:
        raise ValueError("提示词超过 12000 字限制")
    existing_name = connection.execute("SELECT id FROM prompt_versions WHERE name = ?", (name,)).fetchone()
    if existing_name:
        raise ValueError(f"版本名称已存在：{name}")
    sha = prompt_hash(text)
    existing_sha = connection.execute("SELECT id FROM prompt_versions WHERE sha256 = ?", (sha,)).fetchone()
    if existing_sha:
        raise ValueError("相同内容的提示词已存在，请复用既有版本而不是新建")
    if source_run_id:
        run = connection.execute("SELECT id FROM eval_runs WHERE id = ?", (source_run_id,)).fetchone()
        if not run:
            raise ValueError(f"source_run_id 不存在：{source_run_id}")
    version_id = f"version-{uuid.uuid4().hex}"
    created_at = utc_now()
    connection.execute(
        "INSERT INTO prompt_versions(id, name, role, prompt_text, sha256, created_at, source_run_id, note) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (version_id, name, role, text, sha, created_at, source_run_id, note.strip() if note else None),
    )
    connection.commit()
    return get_version(connection, sha) or {}


def _extract_prompt_from_run(connection: Connection, run_id: str, role: str) -> str:
    row = connection.execute(
        "SELECT snapshot_json FROM report_snapshots WHERE run_id = ? ORDER BY rowid DESC LIMIT 1",
        (run_id,),
    ).fetchone()
    if not row:
        raise ValueError(f"source_run_id 没有报告快照：{run_id}")
    snapshot = json.loads(row["snapshot_json"])
    artifact = (snapshot.get("provenance") or {}).get("artifact") or {}
    prompts = artifact.get("prompts") or {}
    entry = prompts.get(role) or {}
    text = str(entry.get("text") or "")
    if not text:
        raise ValueError(f"run {run_id} 的 {role} 提示词无全文（非 live 运行，仅记录了哈希）")
    return text


def _count_matching_runs(connection: Connection, sha256: str) -> int:
    row = connection.execute(
        """
        SELECT COUNT(DISTINCT rs.run_id) AS n
        FROM report_snapshots rs
        WHERE json_extract(rs.snapshot_json, '$.provenance.baseline_prompt_hash') = ?
           OR json_extract(rs.snapshot_json, '$.provenance.candidate_prompt_hash') = ?
        """,
        (sha256, sha256),
    ).fetchone()
    return int(row["n"]) if row else 0


def _count_bad_cases(connection: Connection, sha256: str) -> int:
    row = connection.execute(
        """
        SELECT COUNT(DISTINCT fo.case_id || '::' || fo.run_id) AS n
        FROM report_snapshots rs
        JOIN finding_occurrences fo ON fo.run_id = rs.run_id
        WHERE json_extract(rs.snapshot_json, '$.provenance.baseline_prompt_hash') = ?
           OR json_extract(rs.snapshot_json, '$.provenance.candidate_prompt_hash') = ?
        """,
        (sha256, sha256),
    ).fetchone()
    return int(row["n"]) if row else 0


def bad_cases_for_version(connection: Connection, sha256: str) -> dict[str, Any]:
    """返回该版本参与过的每个 run 的 bad case（Finding 级）清单。

    按 run 分组，bad case 指该 run 中产生 Finding 的病例（自动判定 fail/needs_review 且未人工误报消解）。
    """
    version = get_version(connection, sha256)
    if not version:
        return {"version": None, "runs": []}
    rows = connection.execute(
        """
        SELECT
          rs.run_id, r.created_at AS run_created_at, r.status AS run_status,
          rs.snapshot_json, r.baseline_key, r.candidate_key,
          fo.case_id, fo.checkpoint, fo.original_severity,
          f.id AS finding_id, f.status AS finding_status, f.severity AS finding_severity,
          er.verdict AS eval_verdict, er.severity AS eval_severity, er.evidence_json,
          a.agent_key
        FROM report_snapshots rs
        JOIN eval_runs r ON r.id = rs.run_id
        JOIN finding_occurrences fo ON fo.run_id = rs.run_id
        JOIN findings f ON f.id = fo.finding_id
        JOIN attempts a ON a.id = fo.attempt_id
        JOIN evaluation_results er ON er.id = fo.evaluation_result_id
        WHERE json_extract(rs.snapshot_json, '$.provenance.baseline_prompt_hash') = ?
           OR json_extract(rs.snapshot_json, '$.provenance.candidate_prompt_hash') = ?
        ORDER BY rs.run_id, fo.case_id
        """,
        (sha256, sha256),
    ).fetchall()

    runs: dict[str, dict[str, Any]] = {}
    for row in rows:
        run = runs.setdefault(row["run_id"], {
            "run_id": row["run_id"],
            "created_at": row["run_created_at"],
            "status": row["run_status"],
            "bad_cases": [],
        })
        snapshot = json.loads(row["snapshot_json"])
        provenance = snapshot.get("provenance", {})
        matched_side = "baseline" if provenance.get("baseline_prompt_hash") == sha256 else "candidate"
        # bad case 永远来自 candidate 侧（findings 只对 candidate 生成）；matched_side 表示该版本在 run 中扮演的角色
        side = "candidate" if row["agent_key"] == row["candidate_key"] else "baseline"
        eval_data = json.loads(row["evidence_json"]) if row["evidence_json"] else {}
        run["bad_cases"].append({
            "case_id": row["case_id"],
            "checkpoint": row["checkpoint"],
            "priority": eval_data.get("severity") or row["original_severity"],
            "side": side,
            "matched_side": matched_side,
            "verdict": row["eval_verdict"],
            "finding_id": row["finding_id"],
            "finding_status": row["finding_status"],
            "reason_codes": eval_data.get("reason_codes", []),
            "missing_actions": eval_data.get("missing_actions", []),
            "forbidden_hits": eval_data.get("forbidden_hits", []),
            "evidence": eval_data.get("evidence"),
        })
    ordered = sorted(runs.values(), key=lambda item: item["created_at"], reverse=True)
    return {"version": version, "runs": ordered}