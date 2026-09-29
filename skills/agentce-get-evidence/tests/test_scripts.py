"""linkml_validate, bundle_preflight, explain_insufficient, and the shared pin gate (SPEC 13.3)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

import bundle_preflight
import explain_insufficient
import linkml_validate
import _common

ROOT = Path(__file__).resolve().parent.parent
ADAPTERS = ROOT.parent.parent / "adapters"
CTX = "https://agent-conformance.org/contexts/evidence/v1"


def _event(
    eid: str, source: str, sclass: str, payload: dict[str, Any] | None = None
) -> dict[str, Any]:
    return {
        "specversion": "1.0",
        "id": eid,
        "source": source,
        "type": "org.agent-conformance.evidence.ModelCall.v1",
        "time": "2026-05-01T09:00:00.000Z",
        "subject": "spiffe://corp/agents/x",
        "datacontenttype": "application/ld+json",
        "agentcesourceclass": sclass,
        "data": {
            "@context": CTX,
            "@type": "ModelCall",
            "operation": "chat",
            **(payload or {}),
        },
    }


def _write_bundle(
    root: Path,
    events: list[dict[str, Any]],
    source_classes: dict[str, str] | None = None,
) -> Path:
    from agentce.canonical import canonical_string

    (root / "events").mkdir(parents=True)
    stream = root / "events" / "s.jsonl"
    stream.write_text(
        "".join(canonical_string(e) + "\n" for e in events), encoding="utf-8"
    )
    digest = hashlib.sha256(stream.read_bytes()).hexdigest()
    manifest: dict[str, Any] = {
        "agentce_bundle_version": 1,
        "files": [{"path": "events/s.jsonl", "sha256": digest}],
    }
    if source_classes is not None:
        manifest["sources"] = [{"id": s, "class": c} for s, c in source_classes.items()]
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root


# --- _common (the version-pin gate, S-5). ---------------------------------------------------------


def test_version_range_satisfies() -> None:
    assert _common._satisfies("0.1.0", ">=0.1.0,<0.2")
    assert not _common._satisfies("1.2.0", ">=0.1.0,<0.2")
    assert _common._satisfies("0.6", "==0.6")


def test_pins_match_the_installed_engine() -> None:
    # The skill's SKILL.md pins must match the engine it ships against, or every script would exit 3.
    assert _common.check_pins() is None


# --- linkml_validate. -----------------------------------------------------------------------------


def test_linkml_validate_accepts_valid_events(tmp_path: Path) -> None:
    events = tmp_path / "e.jsonl"
    events.write_text(
        json.dumps(_event("a" * 64, "urn:x", "self_report")) + "\n", encoding="utf-8"
    )
    assert (
        linkml_validate.main(["--events", str(events), "--json"]) == linkml_validate.OK
    )


def test_linkml_validate_flags_an_invalid_event(tmp_path: Path) -> None:
    events = tmp_path / "e.jsonl"
    events.write_text(json.dumps({"id": "x"}) + "\n", encoding="utf-8")
    assert linkml_validate.main(["--events", str(events)]) == linkml_validate.FINDINGS


def test_linkml_validate_requires_one_target(tmp_path: Path) -> None:
    assert linkml_validate.main([]) == linkml_validate.INPUT_ERROR


# --- bundle_preflight. ----------------------------------------------------------------------------


def test_preflight_clean_bundle(tmp_path: Path) -> None:
    _write_bundle(
        tmp_path / "b",
        [_event("a" * 64, "urn:x", "self_report")],
        {"urn:x": "self_report"},
    )
    assert (
        bundle_preflight.main(["--bundle", str(tmp_path / "b"), "--json"])
        == bundle_preflight.OK
    )


def test_preflight_flags_a_class_mismatch(tmp_path: Path) -> None:
    # The event is self_report but the manifest declares the source enforcement_point.
    _write_bundle(
        tmp_path / "b",
        [_event("a" * 64, "urn:x", "self_report")],
        {"urn:x": "enforcement_point"},
    )
    summary, findings = bundle_preflight.preflight(tmp_path / "b", None)
    assert any(f.code == "preflight.quarantine" for f in findings)


def test_preflight_flags_a_dangling_ref(tmp_path: Path) -> None:
    event = _event(
        "a" * 64,
        "urn:x",
        "self_report",
        {"refs": {"instruction": "agentce:event/missing"}},
    )
    _write_bundle(tmp_path / "b", [event], {"urn:x": "self_report"})
    _summary, findings = bundle_preflight.preflight(tmp_path / "b", None)
    assert any(f.code == "preflight.dangling_ref" for f in findings)


# --- explain_insufficient. ------------------------------------------------------------------------


def test_explain_groups_by_root_cause(tmp_path: Path) -> None:
    assertions = tmp_path / "assertions.json"
    assertions.write_text(
        json.dumps(
            [
                {
                    "control": "OVS-01",
                    "outcome": "insufficient_evidence",
                    "missing_members": ["refs.authorization"],
                },
                {
                    "control": "OVS-02",
                    "outcome": "insufficient_evidence",
                    "missing_members": ["refs.authorization"],
                },
                {"control": "REC-01", "outcome": "conformant"},
            ]
        ),
        encoding="utf-8",
    )
    supplies = explain_insufficient._adapters_supplying(ADAPTERS)
    report = explain_insufficient.explain(
        json.loads(assertions.read_text(encoding="utf-8")), supplies
    )
    assert report["insufficient"] == 2
    cause = report["root_causes"][0]
    assert cause["missing_member"] == "refs.authorization"
    assert cause["controls"] == ["OVS-01", "OVS-02"]
    assert (
        explain_insufficient.main(
            [
                "--assertions",
                str(assertions),
                "--support-matrices",
                str(ADAPTERS),
                "--json",
            ]
        )
        == explain_insufficient.FINDINGS
    )


@pytest.mark.skipif(
    not ADAPTERS.is_dir(),
    reason="adapters/ support matrices only exist in the monorepo checkout, not a standalone skill install",
)
def test_the_repo_mcp_gateway_matrix_supplies_authorization() -> None:
    # The mcp-gateway adapter can supply refs.authorization (SPEC 12.3).
    supplies = explain_insufficient._adapters_supplying(ADAPTERS)
    assert "mcp-gateway" in supplies["refs.authorization"]


def test_explain_suggests_the_adapter_a_support_matrix_names(tmp_path: Path) -> None:
    matrix = tmp_path / "gateway" / "support-matrix.yaml"
    matrix.parent.mkdir()
    matrix.write_text(
        "adapter: gateway\nevents:\n  ToolCall:\n    members: [refs.authorization]\n",
        encoding="utf-8",
    )
    missing = {
        "outcome": "insufficient_evidence",
        "missing_members": ["refs.authorization"],
    }
    report = explain_insufficient.explain(
        [{"control": "OVS-01", **missing}],
        explain_insufficient._adapters_supplying(tmp_path),
    )
    assert report["root_causes"][0]["suggested_adapters"] == ["gateway"]


def test_without_support_matrices_nothing_is_suggested(tmp_path: Path) -> None:
    # A standalone install has no adapters/ beside it: no suggestions, no error.
    assert explain_insufficient._adapters_supplying(tmp_path / "absent") == {}


def test_skill_is_for_the_coding_assistant_and_keeps_its_rules() -> None:
    front = _common.read_frontmatter()
    assert front["name"] == "agentce-get-evidence"
    assert "never for the agent being checked" in " ".join(front["description"].split())
    skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
    rules = [
        line.split("|")[1].strip()
        for line in skill.splitlines()
        if line.startswith("| S-")
    ]
    assert rules == [f"S-{n}" for n in range(1, 11)]


def test_version_mismatch_gate_returns_exit_3(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # If SKILL.md pins do not match the engine, every script stops with exit 3 (S-5).
    monkeypatch.setattr(
        _common,
        "check_pins",
        lambda: _common.Finding("skill.spec_version_mismatch", "pinned 9.9 != engine"),
    )
    assert _common.run_guarded(["--json"], lambda _argv: 0) == _common.VERSION_MISMATCH
    # When the pins hold, the body runs and its exit code is returned.
    monkeypatch.setattr(_common, "check_pins", lambda: None)
    assert _common.run_guarded([], lambda _argv: 7) == 7
