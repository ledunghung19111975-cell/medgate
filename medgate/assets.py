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


def _manifest_path(assets_root: Path, testset_key: str | None) -> Path:
    """测试集 manifest 定位约定：默认（None / pretriage-safety-v1）用根 manifest.json
    保持冻结；其余 testset 用独立 manifest，两套各自校验（14_ 计划四.9）。"""
    key = (testset_key or "pretriage-safety-v1").strip()
    if key == "pretriage-safety-v1":
        return assets_root / "manifest.json"
    return assets_root / "manifests" / f"{key}.json"


def load_bundle(root: Path | None = None, *, testset_key: str | None = None) -> AssetBundle:
    project_root = (root or Path(__file__).resolve().parents[1]).resolve()
    assets_root = project_root / "assets"
    manifest_path = _manifest_path(assets_root, testset_key)
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
    if "scenarios" in manifest:
        _validate_multidim_shape(manifest, agents, cases, fixtures)
        return
    _validate_pretriage_shape(manifest, agents, cases, fixtures)


def _validate_pretriage_shape(
    manifest: dict[str, Any],
    agents: list[dict[str, str]],
    cases: list[dict[str, Any]],
    fixtures: list[dict[str, Any]],
) -> None:
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


def _validate_multidim_shape(
    manifest: dict[str, Any],
    agents: list[dict[str, str]],
    cases: list[dict[str, Any]],
    fixtures: list[dict[str, Any]],
) -> None:
    """多维度测试集（multidim）校验：独立 manifest，允许部分 case 无 fixture（live-only）。

    场景类型（scenario）与 pretriage 的 dimension 正交；只有 boundary 层进 Gate，
    FAQ/复杂疾病/多轮三层只出分不判（14_ 计划四.10、D-12）。
    """
    expected_cases = int(manifest.get("expected_case_count", -1))
    if len(cases) != expected_cases:
        raise AssetError(f"病例数量不符：expected={expected_cases} actual={len(cases)}")
    allowed_scenarios = set(manifest["scenarios"])
    case_ids = [case.get("case_id") for case in cases]
    if len(set(case_ids)) != len(case_ids) or any(not case_id for case_id in case_ids):
        raise AssetError("case_id 必须唯一且非空")
    for case in cases:
        scenario = case.get("scenario")
        if scenario not in allowed_scenarios:
            raise AssetError(f"case 使用了未知 scenario：{case.get('case_id')} -> {scenario}")
        if scenario == "faq" and not str(case.get("faq_reference_answer", "")).strip():
            raise AssetError(f"FAQ case 缺少标答 faq_reference_answer：{case.get('case_id')}")
        if scenario == "boundary" and not str(case.get("boundary_type", "")).strip():
            raise AssetError(f"boundary case 缺少边界类型 boundary_type：{case.get('case_id')}")
    agent_keys = {agent["key"] for agent in agents}
    fixture_keys = {fixture.get("fixture_id") for fixture in fixtures}
    if len(fixture_keys) != len(fixtures):
        raise AssetError("fixture_id 必须唯一")
    for fixture in fixtures:
        case_id = fixture.get("case_id")
        agent_key = fixture.get("agent_key")
        if case_id not in case_ids:
            raise AssetError(f"fixture 引用未知 case：{fixture.get('fixture_id')}")
        if agent_key not in agent_keys:
            raise AssetError(f"fixture 引用未知 agent：{fixture.get('fixture_id')}")
        result = fixture.get("judge_result") or {}
        if result.get("verdict") not in {"pass", "fail"}:
            raise AssetError(f"judge verdict 非法：{fixture.get('fixture_id')}")
        if not isinstance(result.get("score"), (int, float)):
            raise AssetError(f"fixture 缺少 score：{fixture.get('fixture_id')}")
        if result.get("verdict") == "pass" and result.get("finding_id") is not None:
            raise AssetError(f"通过 fixture 不得挂载 Finding：{fixture.get('fixture_id')}")
