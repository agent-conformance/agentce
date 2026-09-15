"""Tests for the support matrix and its consistency check (SPEC 12.3)."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import yaml
from agentce.schema import evidence_schema

from agentce_adapters.check_support_matrix import ADAPTER_NAME, check, main
from agentce_adapters.members import members_of

ROOT = Path(__file__).resolve().parent.parent
MATRIX = yaml.safe_load((ROOT / "support-matrix.yaml").read_text(encoding="utf-8"))


def _object_ref(spec: dict[str, Any]) -> str | None:
    if "$ref" in spec:
        return str(spec["$ref"]).rsplit("/", 1)[-1]
    for branch in spec.get("anyOf", []):
        if isinstance(branch, dict) and "$ref" in branch:
            return str(branch["$ref"]).rsplit("/", 1)[-1]
    return None


def _allowed_paths(event_type: str) -> set[str]:
    schema = evidence_schema()
    payload = schema["$defs"][f"{event_type}Payload"]["properties"]
    paths: set[str] = set()
    for name, spec in payload.items():
        ref = _object_ref(spec) if isinstance(spec, dict) else None
        sub_props = schema["$defs"].get(ref, {}).get("properties") if ref else None
        paths.add(name)
        if sub_props:
            paths.update(f"{name}.{sub_key}" for sub_key in sub_props)
    return paths


def test_matrix_is_consistent_with_the_adapter() -> None:
    assert check(ROOT) == []


def test_cli_reports_ok_on_the_real_matrix() -> None:
    assert main([str(ROOT)]) == 0


def test_matrix_adapter_name() -> None:
    assert MATRIX["adapter"] == ADAPTER_NAME


def test_every_declared_path_is_a_real_model_member() -> None:
    for event_type, spec in MATRIX["events"].items():
        allowed = _allowed_paths(event_type)
        for path in spec.get("members", []) + spec.get("missing", []):
            assert path in allowed, (
                f"{event_type}: {path} is not a member of the model payload"
            )


def test_members_and_missing_are_disjoint_by_construction() -> None:
    for event_type, spec in MATRIX["events"].items():
        assert set(spec.get("members", [])).isdisjoint(spec.get("missing", [])), (
            event_type
        )


def _broken_copy(tmp_path: Path, mutate: Any) -> Path:
    root = tmp_path / "adapter"
    root.mkdir()
    shutil.copytree(ROOT / "fixtures", root / "fixtures")
    matrix = yaml.safe_load((ROOT / "support-matrix.yaml").read_text(encoding="utf-8"))
    mutate(matrix)
    (root / "support-matrix.yaml").write_text(yaml.safe_dump(matrix), encoding="utf-8")
    return root


def test_check_detects_an_under_claimed_member(tmp_path: Path) -> None:
    def drop_member(matrix: dict[str, Any]) -> None:
        matrix["events"]["AuthzCheck"]["members"].remove("allowed")

    root = _broken_copy(tmp_path, drop_member)
    assert any("AuthzCheck.members" in failure for failure in check(root))
    assert main([str(root)]) == 1


def test_check_detects_a_populated_member_claimed_missing(tmp_path: Path) -> None:
    def mislabel(matrix: dict[str, Any]) -> None:
        matrix["events"]["PolicyDecision"]["missing"].append("decision")

    root = _broken_copy(tmp_path, mislabel)
    assert any("missing lists populated members" in failure for failure in check(root))


def test_check_detects_an_unexercised_convention(tmp_path: Path) -> None:
    def add_convention(matrix: dict[str, Any]) -> None:
        matrix["conventions"].append("opa:9.99")

    root = _broken_copy(tmp_path, add_convention)
    assert any("conventions mismatch" in failure for failure in check(root))


def test_check_detects_a_wrong_adapter_name(tmp_path: Path) -> None:
    def rename(matrix: dict[str, Any]) -> None:
        matrix["adapter"] = "policies"

    root = _broken_copy(tmp_path, rename)
    assert any("support matrix adapter" in failure for failure in check(root))


def test_check_detects_drift_between_output_and_expected(tmp_path: Path) -> None:
    root = tmp_path / "adapter"
    root.mkdir()
    shutil.copytree(ROOT / "fixtures", root / "fixtures")
    shutil.copy(ROOT / "support-matrix.yaml", root / "support-matrix.yaml")
    (root / "fixtures" / "opa-decisions" / "expected.jsonl").write_text(
        "{}\n", encoding="utf-8"
    )
    assert any("does not match expected.jsonl" in failure for failure in check(root))


def test_cli_fails_on_a_missing_directory(tmp_path: Path) -> None:
    assert main([str(tmp_path / "nope")]) == 1


def test_cli_fails_when_the_matrix_is_not_a_mapping(tmp_path: Path) -> None:
    root = tmp_path / "adapter"
    root.mkdir()
    shutil.copytree(ROOT / "fixtures", root / "fixtures")
    (root / "support-matrix.yaml").write_text("- a\n- b\n", encoding="utf-8")
    assert main([str(root)]) == 1


def test_check_detects_a_non_mapping_events_block(tmp_path: Path) -> None:
    def break_events(matrix: dict[str, Any]) -> None:
        matrix["events"] = ["x"]

    root = _broken_copy(tmp_path, break_events)
    assert any("event types mismatch" in failure for failure in check(root))


def test_members_of_handles_a_missing_payload() -> None:
    assert members_of({}) == set()
