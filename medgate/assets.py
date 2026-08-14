from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

class AssetError(Exception):
    """Raised when versioned MedGate assets are missing or inconsistent."""


@dataclass(frozen=True)
class AssetBundle:
    root: Path
    manifest: dict[str, Any]
    agents: list[dict[str, str]]
    cases: list[dict[str, Any]]
    fixtures: list[dict[str, Any]]

    @property
    def testset_key(self) -> str:
        return str(self.manifest["testset_key"])

    @property
    def testset_hash(self) -> str:
        return str(self.manifest["assets"]["testset"]["sha256"])

    @property
    def fixture_hash(self) -> str:
        return str(self.manifest["assets"]["fixtures"]["sha256"])

    @property
    def agents_hash(self) -> str:
        return str(self.manifest["assets"]["agents"]["sha256"])

    @property
    def agent_keys(self) -> tuple[str, ...]:
        return tuple(agent["key"] for agent in self.agents)


def select_case_subset(bundle: AssetBundle, case_ids: list[str] | None) -> AssetBundle:
    """Return a deterministic view of the validated bundle for one run."""
    if case_ids is None:
        return bundle
    requested = [str(case_id).strip() for case_id in case_ids]
    if not requested or any(not case_id for case_id in requested):
        raise ValueError("case_ids 至少需要包含 1 个病例")
    requested_set = set(requested)
    if len(requested_set) != len(requested):
        raise ValueError("case_ids 不能重复")
    by_id = {str(case["case_id"]): case for case in bundle.cases}
    unknown = [case_id for case_id in requested if case_id not in by_id]
    if unknown:
        raise ValueError(f"case_ids 包含未知病例：{unknown}")
    full_ids = [str(case["case_id"]) for case in bundle.cases]
    if requested == full_ids:
        return bundle
    selected_cases = [by_id[case_id] for case_id in requested]
    selected_fixtures = [fixture for fixture in bundle.fixtures if fixture.get("case_id") in requested_set]
    return replace(bundle, cases=selected_cases, fixtures=selected_fixtures)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AssetError(f"无法读取 JSON 资产：{path}: {exc}") from exc


def _read_agents(path: Path) -> list[dict[str, str]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise AssetError(f"无法读取 Agent 资产：{path}: {exc}") from exc
    agents: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("- "):
            if current:
                agents.append(current)
            current = {}
            stripped = stripped[2:].strip()
        match = re.match(r"^(key|role|display_name):\s*(.+)$", stripped)
        if match and current is not None:
            current[match.group(1)] = match.group(2).strip()
    if current:
        agents.append(current)
    return agents


def load_bundle(root: Path | None = None) -> AssetBundle:
    project_root = (root or Path(__file__).resolve().parents[1]).resolve()
    assets_root = project_root / "assets"
    manifest_path = assets_root / "manifest.json"
    manifest = _read_json(manifest_path)
    if manifest.get("manifest_version") != "1.0.0":
        raise AssetError("不支持的 manifest 版本")
    if manifest.get("source_type") != "self_authored_synthetic":
        raise AssetError("资产 source_type 必须明确为 self_authored_synthetic")
    if manifest.get("license_ref") != "project-owned":
        raise AssetError("资产 license_ref 必须明确为 project-owned")

    resolved: dict[str, Path] = {}
    for name, asset in manifest.get("assets", {}).items():
        asset_path = assets_root / str(asset["path"])
        if not asset_path.is_file():
            raise AssetError(f"manifest 指向的资产不存在：{name} -> {asset_path}")
        actual_hash = _sha256(asset_path)
        if actual_hash != asset.get("sha256"):
            raise AssetError(f"资产哈希不一致：{name}，expected={asset.get('sha256')} actual={actual_hash}")
        resolved[name] = asset_path

    agents = _read_agents(resolved["agents"])
    cases = _read_json(resolved["testset"])
    fixtures = _read_json(resolved["fixtures"])
    _validate_shape(manifest, agents, cases, fixtures)
    return AssetBundle(project_root, manifest, agents, cases, fixtures)


def _validate_shape(
    manifest: dict[str, Any],
    agents: list[dict[str, str]],
    cases: list[dict[str, Any]],
    fixtures: list[dict[str, Any]],
) -> None:
    if len(agents) != 2 or {agent.get("role") for agent in agents} != {"baseline", "candidate"}:
        raise AssetError("Agent 资产必须包含 baseline 与 candidate 两个版本")
    expected_cases = int(manifest.get("expected_case_count", -1))
    expected_fixtures = int(manifest.get("expected_fixture_count", -1))
    if len(cases) != expected_cases:
        raise AssetError(f"病例数量不符：expected={expected_cases} actual={len(cases)}")
    if len(fixtures) != expected_fixtures:
        raise AssetError(f"fixture 数量不符：expected={expected_fixtures} actual={len(fixtures)}")
    case_ids = [case.get("case_id") for case in cases]
    if len(set(case_ids)) != len(case_ids) or any(not case_id for case_id in case_ids):
        raise AssetError("case_id 必须唯一且非空")
    from .engine import ACTION_REQUIREMENTS, FORBIDDEN_PATTERNS

    for case in cases:
        unknown_actions = sorted(set(case.get("expected_safety_actions", [])) - set(ACTION_REQUIREMENTS))
        unknown_claims = sorted(set(case.get("forbidden_claims", [])) - set(FORBIDDEN_PATTERNS))
        if unknown_actions or unknown_claims:
            raise AssetError(
                f"病例引用未实现的确定性规则：{case.get('case_id')} "
                f"actions={unknown_actions} forbidden={unknown_claims}"
            )
    agent_keys = {agent["key"] for agent in agents}
    agent_roles = {agent["key"]: agent["role"] for agent in agents}
    fixture_keys = {fixture.get("fixture_id") for fixture in fixtures}
    if len(fixture_keys) != len(fixtures):
        raise AssetError("fixture_id 必须唯一")
    seen: dict[str, set[str]] = {case_id: set() for case_id in case_ids}
    for fixture in fixtures:
        case_id = fixture.get("case_id")
        agent_key = fixture.get("agent_key")
        if case_id not in seen or agent_key not in agent_keys:
            raise AssetError(f"fixture 引用未知 case 或 agent：{fixture.get('fixture_id')}")
        if fixture.get("fixture_id") != f"{case_id}__{agent_key}":
            raise AssetError(f"fixture_id 不是稳定派生值：{fixture.get('fixture_id')}")
        seen[case_id].add(agent_key)
        result = fixture.get("judge_result") or {}
        if result.get("verdict") not in {"pass", "fail"}:
            raise AssetError(f"judge verdict 非法：{fixture.get('fixture_id')}")
        if not isinstance(result.get("score"), (int, float)):
            raise AssetError(f"fixture 缺少 score：{fixture.get('fixture_id')}")
        if result.get("verdict") == "pass" and result.get("finding_id") is not None:
            raise AssetError(f"通过 fixture 不得挂载 Finding：{fixture.get('fixture_id')}")
        role_word = "候选" if agent_roles[agent_key] == "baseline" else "基线"
        role_text = f"{result.get('auto', '')}\n{result.get('evidence', '')}"
        if role_word in role_text:
            raise AssetError(f"fixture 证据串用了另一版本角色：{fixture.get('fixture_id')}")
    missing = [case_id for case_id, keys in seen.items() if keys != agent_keys]
    if missing:
        raise AssetError(f"病例缺少双版本 fixture：{missing}")
