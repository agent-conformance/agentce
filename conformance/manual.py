"""Phase-4 P4.3 orchestrator: manual protocol, readiness, and the Conduct overlay (SPEC §13.3.4, §7.7).

``python manual.py --json`` prints
``{"readiness_match", "deviation_lint_refuses", "cnd_recall", "cnd_known_pass_conformant"}``:
``report_readiness`` verdicts on the corpus variants equal the SPEC §13.3.4 table; ``deviation_lint``
refuses a deviation on an ``insufficient_evidence`` outcome and on the INT family; the four seeded CND
faults are detected (recall 1.0); and a CND-conformant project is conformant on every CND control.

The CND corpus is built here as self-contained assess bundles and evaluated with the base catalog plus
the Conduct overlay (``--catalog eu-ai-act@2026.09,conduct@2026.09`` resolves both from the catalogs
the engine ships), so it never perturbs the two-engine ECS corpus.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import subprocess
import sys
import tempfile
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_ENGINE = _REPO / "engines" / "python"
sys.path.insert(0, str(_ENGINE))
sys.path.insert(0, str(_REPO))  # so the corpus namespace package resolves

from agentce.readiness import (  # noqa: E402
    NOT_READY,
    READY,
    compute_readiness,
    deviation_lint,
)
from corpus.assess_one import assess_project  # type: ignore[import-not-found]  # noqa: E402

_BASE = _REPO / "spec" / "catalogs" / "base" / "eu-ai-act"
_SUBJECT = "spiffe://corp/agents/a"
_AGENT = {"id": _SUBJECT}
_WINDOW = {"start": "2026-01-01T00:00:00Z", "end": "2026-06-01T00:00:00Z"}

#: fault variant -> the CND control it must drive non-conformant.
_FAULTS = {
    "scope-breach": "CND-01",
    "tool-output-instruction": "CND-05",
    "unrecorded-refusal": "CND-06",
    "budget-overrun": "CND-07",
}


def _event(
    eid: str,
    ptype: str,
    source_class: str,
    data: dict,
    time: str = "2026-02-01T00:00:00Z",
) -> dict:
    return {
        "specversion": "1.0",
        "id": eid,
        "source": "urn:src:gw",
        "subject": _SUBJECT,
        "time": time,
        "type": f"org.agent-conformance.evidence.{ptype}.v1",
        "datacontenttype": "application/ld+json",
        "agentcesourceclass": source_class,
        "data": {
            "@context": "https://agent-conformance.org/contexts/evidence/v1",
            "@type": ptype,
            **data,
        },
    }


def _events(variant: str) -> list[dict]:
    """A base of a conformant conduct record, with one fault injected per variant."""
    decision = _event(
        "d1",
        "Decision",
        "self_report",
        {"decision_type": "dom:Credit", "agent": _AGENT},
    )
    approval = _event(
        "ap1",
        "ApprovalDecided",
        "independent_system",
        {"refs": {"decision": "agentce:event/d1"}},
    )
    instruction = _event(
        "i1", "Instruction", "enforcement_point", {"source_class": "user"}
    )
    tool = {
        "agent": _AGENT,
        "effect_class": "write",
        "refs": {"decision": "agentce:event/d1", "instruction": "agentce:event/i1"},
    }
    events = [decision, approval, instruction]
    if variant == "scope-breach":
        # The enforcement point denied the tool call's request, but it was made anyway.
        events.append(
            _event(
                "pd1",
                "PolicyDecision",
                "enforcement_point",
                {"decision": "deny", "refs": {"request": "agentce:event/tc1"}},
            )
        )
    elif variant == "budget-overrun":
        # A budget-exceeded refusal names the request, which proceeded regardless.
        events.append(
            _event(
                "rf1",
                "Refusal",
                "enforcement_point",
                {
                    "reason_class": "budget_exceeded",
                    "refs": {"request": "agentce:event/tc1"},
                },
            )
        )
    elif variant == "tool-output-instruction":
        instruction["data"]["source_class"] = "tool_output"
    elif variant == "unrecorded-refusal":
        # A standalone untrusted instruction that was never acted on and never refused.
        events.append(
            _event("i2", "Instruction", "self_report", {"source_class": "tool_output"})
        )
    events.append(_event("tc1", "ToolCall", "enforcement_point", tool))
    return events


def _write_bundle(root: Path, variant: str) -> None:
    events_dir = root / "evidence" / "events"
    events_dir.mkdir(parents=True)
    stream = events_dir / "gw.jsonl"
    stream.write_text(
        "".join(json.dumps(e, separators=(",", ":")) + "\n" for e in _events(variant)),
        encoding="utf-8",
    )
    manifest = {
        "agentce_bundle_version": 1,
        "files": [
            {
                "path": "events/gw.jsonl",
                "sha256": hashlib.sha256(stream.read_bytes()).hexdigest(),
            }
        ],
        "sources": [{"id": "urn:src:gw"}],
    }
    (root / "evidence" / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    (root / "applicability.yaml").write_text(
        "profile_version: 1\n"
        f'observation_window: {{start: "{_WINDOW["start"]}", end: "{_WINDOW["end"]}"}}\n'
        "content_capture: {mode: hashes_only}\n"
        'catalogs: ["eu-ai-act@2026.09", "conduct@2026.09"]\n'
        "subjects:\n"
        f'  - id: "{_SUBJECT}"\n'
        "    role: both\n"
        '    declared_decision_types: ["dom:Credit"]\n'
        '    declared_oversight: {"dom:Credit": "review_before"}\n'
        "    evidence_sources:\n"
        '      - {source: "urn:src:gw", class: enforcement_point, class_justification: "the gateway."}\n',
        encoding="utf-8",
    )
    (root / "domain.linkml.yaml").write_text(
        "decision_types:\n"
        '  - {id: "dom:Credit", subclass_of: "agentce:ConsequentialDecision", consequential: true, required_oversight_modality: "review_before"}\n'
        '  - {id: "dom:Minor", subclass_of: "agentce:Decision", consequential: false}\n',
        encoding="utf-8",
    )


def _assess_cnd(variant: str) -> dict[str, str]:
    with tempfile.TemporaryDirectory() as tmp:
        project = Path(tmp) / variant
        _write_bundle(project, variant)
        out = Path(tmp) / "out"
        result = subprocess.run(
            [
                "uv",
                "run",
                "agentce",
                "assess",
                "--bundle",
                str(project / "evidence"),
                "--profile",
                str(project / "applicability.yaml"),
                "--domain",
                str(project / "domain.linkml.yaml"),
                "--catalog",
                "eu-ai-act@2026.09,conduct@2026.09",
                "--out",
                str(out),
            ],
            cwd=_ENGINE,
            capture_output=True,
            text=True,
            check=False,
        )
        assertions_path = out / "assertions.json"
        if not assertions_path.is_file():
            raise SystemExit(
                f"assess of {variant} produced no assertions: {result.stderr[-400:]}"
            )
        assertions = json.loads(assertions_path.read_text(encoding="utf-8"))
        return {
            a["control"]: a["outcome"]
            for a in assertions
            if a["control"].startswith("CND-")
        }


def _cnd_scores() -> tuple[float, bool]:
    detected = 0
    for variant, control in _FAULTS.items():
        if _assess_cnd(variant).get(control) == "non-conformant":
            detected += 1
    recall = detected / len(_FAULTS)
    known_pass = _assess_cnd("known-pass")
    conformant = all(outcome != "non-conformant" for outcome in known_pass.values())
    return recall, conformant


def _readiness_match() -> bool:
    """report_readiness verdicts on the corpus variants equal the SPEC §13.3.4 table."""
    from corpus.generator import generate  # type: ignore[import-not-found]

    expected = {
        "credit/langgraph/known-pass": READY,
        "credit/langgraph/tampered": NOT_READY,
        "credit/langgraph/coverage-gap": NOT_READY,
    }
    severities = _severities()
    with tempfile.TemporaryDirectory() as tmp:
        corpus_root = Path(tmp) / "corpus"
        generate.build_corpus(corpus_root, "v1")
        for project_id, want in expected.items():
            report = Path(tmp) / project_id.replace("/", "_")
            assess_project(corpus_root, project_id, report)
            verdict = compute_readiness(report, severities=severities)
            if verdict["verdict"] != want:
                return False
    return True


def _severities() -> dict[str, str]:
    from agentce.catalog import load_catalog

    return {c.id: c.severity for c in load_catalog(_BASE).controls}


def _deviation_lint_refuses() -> bool:
    insufficient = deviation_lint(
        [
            {
                "control": "OVS-03",
                "rationale": "x",
                "compensating_control": "y",
                "owner": "a",
                "approver": "b",
                "granted": "2026-01-01",
                "expiry": "2026-03-01",
            }
        ],
        control_ids={"OVS-03", "INT-01"},
        outcome_by_control={"OVS-03": "insufficient_evidence"},
    )
    int_family = deviation_lint(
        [
            {
                "control": "INT-01",
                "rationale": "x",
                "compensating_control": "y",
                "owner": "a",
                "approver": "b",
                "granted": "2026-01-01",
                "expiry": "2026-03-01",
            }
        ],
        control_ids={"INT-01"},
        outcome_by_control={"INT-01": "non-conformant"},
    )
    return any("insufficient_evidence" in p for p in insufficient) and any(
        "INT family" in p for p in int_family
    )


def evaluate() -> dict[str, object]:
    recall, conformant = _cnd_scores()
    return {
        "readiness_match": _readiness_match(),
        "deviation_lint_refuses": _deviation_lint_refuses(),
        "cnd_recall": recall,
        "cnd_known_pass_conformant": conformant,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Phase-4 P4.3 manual/conduct gate.")
    parser.add_argument(
        "--json", action="store_true", help="emit machine-readable JSON"
    )
    parser.parse_args(argv)
    # Keep stdout clean for the JSON: assess_one prints a human line per project as it runs.
    with contextlib.redirect_stdout(io.StringIO()):
        result = evaluate()
    print(json.dumps(result, sort_keys=True))
    ok = (
        result["readiness_match"]
        and result["deviation_lint_refuses"]
        and result["cnd_recall"] == 1.0
        and result["cnd_known_pass_conformant"]
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
