"""bundle_preflight (SPEC 13.3.3 step C.1): check a bundle before assessment, read-only.

Runs the engine's ingest over ``--bundle`` (never writing to it, rule S-3) and reports: schema
quarantines summarised by reason; accepted events per source; trust-class consistency between each
event's ``agentcesourceclass`` and the class the profile declares for that source (rule S-2); and
dangling references (a ``refs.*`` pointing at an event id not present in the bundle). Deterministic,
offline, model-free (rule S-4). Exit 0 when the bundle is clean, 1 on findings, 2 on input error,
3 on version mismatch.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import yaml

from _common import FINDINGS, INPUT_ERROR, OK, Finding, arg_value, emit, run_guarded


def _profile_source_classes(profile_path: str | None) -> dict[str, str]:
    """Map source id -> declared trust class from a profile's evidence_sources."""
    if not profile_path:
        return {}
    data = yaml.safe_load(Path(profile_path).read_text(encoding="utf-8"))
    classes: dict[str, str] = {}
    if isinstance(data, dict):
        for subject in data.get("subjects", []) or []:
            for source in (
                (subject.get("evidence_sources", []) or [])
                if isinstance(subject, dict)
                else []
            ):
                if (
                    isinstance(source, dict)
                    and source.get("source")
                    and source.get("class")
                ):
                    classes[str(source["source"])] = str(source["class"])
    return classes


def _dangling_refs(events: list[dict[str, Any]]) -> list[Finding]:
    known = {f"agentce:event/{e['id']}" for e in events}
    findings: list[Finding] = []
    for event in events:
        refs = event.get("data", {}).get("refs")
        if not isinstance(refs, dict):
            continue
        for relation, target in sorted(refs.items()):
            if (
                isinstance(target, str)
                and target.startswith("agentce:event/")
                and target not in known
            ):
                findings.append(
                    Finding(
                        "preflight.dangling_ref",
                        f"event {event['id']} refs.{relation} -> {target} is not in the bundle",
                        {
                            "event": str(event["id"]),
                            "relation": relation,
                            "target": target,
                        },
                    )
                )
    return findings


def preflight(
    bundle_dir: Path, profile_path: str | None
) -> tuple[dict[str, Any], list[Finding]]:
    from agentce.bundle import load_bundle
    from agentce.ingest import ingest
    from agentce.quarantine import counts_by_reason

    ingested = ingest(load_bundle(bundle_dir))
    findings: list[Finding] = []

    by_reason = counts_by_reason(ingested.quarantined)
    for reason, count in sorted(by_reason.items()):
        findings.append(
            Finding(
                "preflight.quarantine",
                f"{count} event(s) quarantined: {reason}",
                {"reason": reason, "count": count},
            )
        )

    per_source: dict[str, int] = {}
    declared = _profile_source_classes(profile_path)
    for event in ingested.accepted:
        source = str(event.get("source", ""))
        per_source[source] = per_source.get(source, 0) + 1
        want = declared.get(source)
        got = str(event.get("agentcesourceclass", ""))
        if want is not None and got != want:
            findings.append(
                Finding(
                    "preflight.trust_class_inconsistent",
                    f"event {event.get('id')} from {source} is {got!r} but the profile declares {want!r}",
                    {"source": source, "event_class": got, "declared_class": want},
                )
            )

    findings.extend(_dangling_refs(ingested.accepted))
    summary = {
        "bundle": str(bundle_dir),
        "accepted": len(ingested.accepted),
        "quarantined": len(ingested.quarantined),
        "quarantine_by_reason": by_reason,
        "accepted_by_source": dict(sorted(per_source.items())),
        "clean": not findings,
        "findings": [f.to_json() for f in findings],
    }
    return summary, findings


def _body(argv: list[str]) -> int:
    want_json = "--json" in argv
    bundle_arg = arg_value(argv, "--bundle")
    if bundle_arg is None or not Path(bundle_arg).is_dir():
        emit(
            {"status": "input_error", "message": "pass --bundle <dir>"},
            want_json=want_json,
            human="INPUT ERROR: pass --bundle <dir>",
        )
        return INPUT_ERROR
    summary, findings = preflight(Path(bundle_arg), arg_value(argv, "--profile"))
    emit(
        summary,
        want_json=want_json,
        human="BUNDLE PREFLIGHT OK"
        if not findings
        else f"BUNDLE PREFLIGHT: {len(findings)} finding(s)",
    )
    return OK if not findings else FINDINGS


def main(argv: list[str] | None = None) -> int:
    return run_guarded(list(sys.argv[1:] if argv is None else argv), _body)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
