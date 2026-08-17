from __future__ import annotations

import sqlite3
from pathlib import Path


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS actors (
  id TEXT PRIMARY KEY,
  display_name TEXT NOT NULL,
  role TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS eval_runs (
  id TEXT PRIMARY KEY,
  actor_id TEXT NOT NULL REFERENCES actors(id),
  idempotency_key TEXT NOT NULL,
  status TEXT NOT NULL,
  testset_key TEXT NOT NULL,
  baseline_key TEXT NOT NULL,
  candidate_key TEXT NOT NULL,
  testset_hash TEXT NOT NULL,
  fixture_hash TEXT NOT NULL,
  run_input_hash TEXT NOT NULL,
  request_hash TEXT,
  rule_hash TEXT,
  judge_hash TEXT,
  review_pack_hash TEXT,
  created_at TEXT NOT NULL,
  completed_at TEXT,
  UNIQUE(actor_id, idempotency_key)
);

CREATE TABLE IF NOT EXISTS live_submissions (
  actor_id TEXT NOT NULL REFERENCES actors(id),
  idempotency_key TEXT NOT NULL,
  request_hash TEXT NOT NULL,
  status TEXT NOT NULL,
  run_id TEXT REFERENCES eval_runs(id),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY(actor_id, idempotency_key)
);

CREATE TABLE IF NOT EXISTS attempts (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES eval_runs(id),
  case_id TEXT NOT NULL,
  agent_key TEXT NOT NULL,
  attempt_no INTEGER NOT NULL,
  status TEXT NOT NULL,
  is_active INTEGER NOT NULL DEFAULT 1,
  output_hash TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(run_id, case_id, agent_key, attempt_no)
);

CREATE TABLE IF NOT EXISTS evaluation_results (
  id TEXT PRIMARY KEY,
  attempt_id TEXT NOT NULL REFERENCES attempts(id),
  evaluator_key TEXT NOT NULL,
  verdict TEXT NOT NULL,
  severity TEXT NOT NULL,
  score REAL NOT NULL,
  label TEXT NOT NULL,
  evidence_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS findings (
  id TEXT PRIMARY KEY,
  fingerprint TEXT NOT NULL UNIQUE,
  first_run_id TEXT NOT NULL REFERENCES eval_runs(id),
  status TEXT NOT NULL,
  severity TEXT NOT NULL,
  target_candidate_key TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS finding_occurrences (
  id TEXT PRIMARY KEY,
  finding_id TEXT NOT NULL REFERENCES findings(id),
  run_id TEXT NOT NULL REFERENCES eval_runs(id),
  attempt_id TEXT NOT NULL REFERENCES attempts(id),
  evaluation_result_id TEXT NOT NULL REFERENCES evaluation_results(id),
  case_id TEXT NOT NULL,
  checkpoint TEXT NOT NULL,
  original_severity TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reviews (
  id TEXT PRIMARY KEY,
  finding_id TEXT NOT NULL REFERENCES findings(id),
  occurrence_id TEXT NOT NULL REFERENCES finding_occurrences(id),
  run_id TEXT NOT NULL REFERENCES eval_runs(id),
  actor_id TEXT NOT NULL REFERENCES actors(id),
  decision TEXT NOT NULL,
  effective_severity TEXT,
  reason TEXT NOT NULL,
  evidence_refs TEXT NOT NULL,
  idempotency_key TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS gate_decisions (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES eval_runs(id),
  state TEXT NOT NULL,
  reason_codes TEXT NOT NULL,
  input_hash TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS report_snapshots (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES eval_runs(id),
  gate_decision_id TEXT NOT NULL REFERENCES gate_decisions(id),
  snapshot_json TEXT NOT NULL,
  snapshot_hash TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_events (
  id TEXT PRIMARY KEY,
  actor_id TEXT NOT NULL REFERENCES actors(id),
  entity_type TEXT NOT NULL,
  entity_id TEXT NOT NULL,
  action TEXT NOT NULL,
  before_hash TEXT,
  after_hash TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_snapshots (
  token TEXT PRIMARY KEY,
  snapshot_hash TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  consumed_run_id TEXT
);

CREATE TABLE IF NOT EXISTS agent_runs (
  id TEXT PRIMARY KEY,
  actor_id TEXT NOT NULL REFERENCES actors(id),
  idempotency_key TEXT NOT NULL,
  request_hash TEXT NOT NULL,
  snapshot_hash TEXT NOT NULL,
  status TEXT NOT NULL,
  report_json TEXT,
  created_at TEXT NOT NULL,
  completed_at TEXT,
  UNIQUE(actor_id, idempotency_key)
);

CREATE TABLE IF NOT EXISTS agent_run_steps (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES agent_runs(id),
  step_no INTEGER NOT NULL,
  step_type TEXT NOT NULL,
  status TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(run_id, step_no)
);

CREATE TABLE IF NOT EXISTS prompt_versions (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  role TEXT NOT NULL,
  prompt_text TEXT NOT NULL,
  sha256 TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL,
  source_run_id TEXT REFERENCES eval_runs(id),
  note TEXT
);
"""


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(SCHEMA)
    _ensure_columns(connection)
    connection.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_attempts_active_unique ON attempts(run_id, case_id, agent_key) WHERE is_active = 1"
    )
    connection.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_reviews_actor_idempotency ON reviews(actor_id, idempotency_key) WHERE idempotency_key IS NOT NULL"
    )
    return connection


def _ensure_columns(connection: sqlite3.Connection) -> None:
    migrations = {
        "eval_runs": {
            "request_hash": "TEXT",
            "rule_hash": "TEXT",
            "judge_hash": "TEXT",
            "review_pack_hash": "TEXT",
        },
        "reviews": {"idempotency_key": "TEXT"},
    }
    for table, columns in migrations.items():
        existing = {row[1] for row in connection.execute(f"PRAGMA table_info({table})").fetchall()}
        for name, column_type in columns.items():
            if name not in existing:
                connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {column_type}")
