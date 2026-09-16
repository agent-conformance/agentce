"""Report-readiness verdict and the manual-protocol linters (SPEC §13.3.4, §10.3).

The readiness verdict logic lives here, in the engine, never in a skill (SPEC §8.5), so signing never
depends on a skill script. ``compute_readiness`` reads a finished report directory and returns one of
``READY``, ``READY WITH LIMITATIONS``, or ``NOT READY`` with reasons, implementing the stage-4
post-run checks of §13.3.4. ``deviation_lint``, ``checklist_lint``, and ``claim_check`` are the
manual-protocol linters the ``agentce-check-report`` skill wraps; each returns a list of problems
(empty means clean). All checks are deterministic and read-only over their inputs.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from importlib import resources
from pathlib import Path
from typing import Any

import jsonschema

READY = "READY"
READY_WITH_LIMITATIONS = "READY WITH LIMITATIONS"
NOT_READY = "NOT READY"

#: Integrity statuses that block a report from being signed (SPEC §13.3.4).
_BAD_INTEGRITY = frozenset({"failed", "gap", "reordered"})
#: The integrity and coverage family whose findings can never be accepted as a deviation (§13.3.4).
_INT_FAMILY = "INT"
#: Default maximum deviation lifetime in days (SPEC §13.3.4; a catalog may set its own).
DEFAULT_MAX_DEVIATION_DAYS = 180
_DEVIATION_FIELDS = (
    "rationale",
    "compensating_control",
    "owner",
    "approver",
    "granted",
    "expiry",
)


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def _schema(name: str) -> dict[str, Any]:
    text = (
        resources.files("agentce.data.schemas")
        .joinpath(f"{name}.schema.json")
        .read_text("utf-8")
    )
    parsed: dict[str, Any] = json.loads(text)
    return parsed


def _parse_date(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def compute_readiness(
    report_dir: Path,
    *,
    severities: dict[str, str],
    deviations: list[dict[str, Any]] | None = None,
    gaps: set[str] | None = None,
) -> dict[str, Any]:
    """Return the readiness verdict for a finished report directory (SPEC §13.3.4 stage 4).

    ``severities`` maps control id to severity (from the catalogs the report used); ``gaps`` is the
    set of control ids recorded in the operator's gaps file with an owner and date. A high-severity
    ``insufficient_evidence`` outcome is a limitation when recorded in ``gaps`` and a blocker
    otherwise; integrity breaks, coverage shortfalls, applicability drift, and invalid deviations
    always block."""
    gaps = gaps or set()
    reasons: list[str] = []
    limitations: list[str] = []

    for record in _load_jsonl(report_dir / "integrity.jsonl"):
        if record.get("status") in _BAD_INTEGRITY:
            reasons.append(
                f"integrity {record.get('status')} on stream {record.get('stream')}"
            )

    coverage = (
        _load_json(report_dir / "coverage.json")
        if (report_dir / "coverage.json").is_file()
        else {}
    )
    for subject, entry in sorted((coverage.get("subjects") or {}).items()):
        if entry.get("coverage_status") == "gap":
            reasons.append(f"coverage shortfall for subject {subject}")
        for event_type, detail in sorted((entry.get("event_types") or {}).items()):
            # A declared source below the coverage threshold is a shortfall even when another event
            # type has unknown coverage and masks it in the subject-level roll-up (SPEC §13.3.4).
            if detail.get("status") == "below_threshold":
                reasons.append(f"coverage shortfall for {subject} {event_type}")

    for statement in _load_jsonl(report_dir / "applicability.jsonl"):
        for drift in statement.get("drift", []) or []:
            reasons.append(
                f"applicability drift: {drift.get('kind')} {drift.get('ref')}"
            )

    assertions = (
        _load_json(report_dir / "assertions.json")
        if (report_dir / "assertions.json").is_file()
        else []
    )
    for assertion in assertions:
        if assertion.get("outcome") != "insufficient_evidence":
            continue
        control = str(assertion.get("control", ""))
        if severities.get(control) == "high":
            if control in gaps:
                limitations.append(f"{control}: high-severity evidence gap recorded")
            else:
                reasons.append(
                    f"{control}: unrecorded high-severity insufficient_evidence"
                )

    if deviations:
        outcome_by_control = {
            str(a.get("control")): str(a.get("outcome")) for a in assertions
        }
        problems = deviation_lint(
            deviations,
            control_ids=set(severities),
            outcome_by_control=outcome_by_control,
        )
        reasons.extend(f"invalid deviation: {p}" for p in problems)

    verdict = (
        NOT_READY if reasons else (READY_WITH_LIMITATIONS if limitations else READY)
    )
    return {
        "verdict": verdict,
        "reasons": sorted(reasons),
        "limitations": sorted(limitations),
    }


def deviation_lint(
    deviations: list[dict[str, Any]],
    *,
    control_ids: set[str],
    outcome_by_control: dict[str, str],
    max_days: int = DEFAULT_MAX_DEVIATION_DAYS,
) -> list[str]:
    """Enforce the deviation rules of SPEC §13.3.4: the control exists and its outcome was
    ``non-conformant`` (never ``insufficient_evidence``), no deviation touches the INT family, every
    field is present, the approver is a named person distinct from the owner, and the deviation
    expires within the catalog's maximum lifetime."""
    problems: list[str] = []
    for deviation in deviations:
        control = str(deviation.get("control", ""))
        if control not in control_ids:
            problems.append(f"{control or '<none>'}: control is not in the catalog")
        if control.split("-", 1)[0] == _INT_FAMILY:
            problems.append(
                f"{control}: the INT family cannot be deviated (integrity and coverage)"
            )
        outcome = outcome_by_control.get(control)
        if outcome == "insufficient_evidence":
            problems.append(
                f"{control}: insufficient_evidence is an evidence gap, not a risk acceptance"
            )
        elif outcome is not None and outcome != "non-conformant":
            problems.append(
                f"{control}: only a non-conformant outcome may be deviated (got {outcome})"
            )
        for field in _DEVIATION_FIELDS:
            if not deviation.get(field):
                problems.append(f"{control}: deviation is missing {field}")
        owner, approver = deviation.get("owner"), deviation.get("approver")
        if owner and approver and owner == approver:
            problems.append(
                f"{control}: the approver must be a person distinct from the owner"
            )
        granted, expiry = (
            _parse_date(deviation.get("granted")),
            _parse_date(deviation.get("expiry")),
        )
        if granted and expiry and (expiry - granted).days > max_days:
            problems.append(f"{control}: deviation lifetime exceeds {max_days} days")
    return problems


def checklist_lint(
    records: list[dict[str, Any]],
    *,
    current_digests: dict[str, str] | None = None,
) -> list[str]:
    """Validate completed manual-checklist records (SPEC §13.3.4 stage 3): the record validates
    against ``checklist.schema.json`` (so its items and their allowed answers are well-formed), the
    assessor identity is present, and every inspected artifact digest is fresh (an artifact changed
    since inspection invalidates the record).

    Note: ``checklist.schema.json`` models the checklist and its allowed answers but not the chosen
    answer per item (items are ``additionalProperties: false``); recording the response is a schema
    change for a future RFC, so the answer-in-allowed check cannot be applied to a record yet."""
    problems: list[str] = []
    schema = _schema("checklist")
    current_digests = current_digests or {}
    for record in records:
        control = str(record.get("control", "<none>"))
        try:
            jsonschema.validate(record, schema)
        except jsonschema.ValidationError as exc:
            problems.append(f"{control}: schema: {exc.message}")
            continue
        if not record.get("completed_by"):
            problems.append(
                f"{control}: no completed_by (assessor identity is required)"
            )
        for artifact, digest in (record.get("artifact_digests") or {}).items():
            if artifact in current_digests and current_digests[artifact] != digest:
                problems.append(
                    f"{control}: artifact {artifact} changed since inspection"
                )
    return problems


def _catalog_labels(catalogs: list[Any]) -> set[str]:
    """Normalise a catalog list to ``id@version`` labels; a claim carries objects, a profile strings."""
    labels: set[str] = set()
    for entry in catalogs:
        if isinstance(entry, str):
            labels.add(entry)
        elif isinstance(entry, dict) and entry.get("id"):
            version = entry.get("version")
            labels.add(f"{entry['id']}@{version}" if version else str(entry["id"]))
    return labels


def claim_check(
    claim: dict[str, Any],
    manifest: dict[str, Any],
    profile: dict[str, Any],
) -> list[str]:
    """Cross-check a claim against the profile (SPEC §13.3.4 ``claim_check --post``): the claim's
    subjects, observation window, and catalogs must match what was actually assessed. This is the
    consistency check; a claim carries catalogs as ``{id, version}`` objects and a profile as
    ``id@version`` strings, so both are normalised before comparison. Strict schema validation of a
    signed claim document lands with claim production in the release tooling (§9.1)."""
    problems: list[str] = []
    claim_subjects = {str(s.get("id")) for s in claim.get("subjects", [])}
    profile_subjects = {str(s.get("id")) for s in profile.get("subjects", [])}
    if claim_subjects != profile_subjects:
        problems.append("claim subjects do not match the profile subjects")
    if claim.get("observation_window") != profile.get("observation_window"):
        problems.append("claim observation_window does not match the profile")
    if _catalog_labels(claim.get("catalogs", [])) != _catalog_labels(
        profile.get("catalogs", [])
    ):
        problems.append("claim catalogs do not match the profile")
    return problems
