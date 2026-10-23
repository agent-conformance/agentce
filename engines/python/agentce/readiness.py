"""Report-readiness verdict and the manual-protocol linters (SPEC §13.3.4, §10.3).

The readiness verdict logic lives here, in the engine, never in a skill (SPEC §8.5), so signing never
depends on a skill script. ``compute_readiness`` reads a finished report directory and returns one of
``READY``, ``READY WITH LIMITATIONS``, or ``NOT READY`` with reasons, implementing the stage-4
post-run checks of §13.3.4. ``deviation_lint``, ``checklist_lint``, and ``claim_check`` are the
manual-protocol linters the ``agentce-prepare-to-share`` skill wraps; each returns a list of problems
(empty means clean). All checks are deterministic and read-only over their inputs.
"""

from __future__ import annotations

import os
import re
from datetime import date, datetime
from importlib import resources
from pathlib import Path
from collections.abc import Callable
from typing import Any, TypeVar

import jsonschema

from .coverage import STATUS_UNKNOWN
from .integrity import IntegrityStatus
from .safe_json import (
    JSONError,
    JsonProfileError,
    jsonl_lines,
    load_report_json,
    read_bundled,
)
from .signing import is_regular_file

_T = TypeVar("_T")

READY = "READY"
READY_WITH_LIMITATIONS = "READY WITH LIMITATIONS"
NOT_READY = "NOT READY"

#: Integrity statuses that block a report from being signed (SPEC §13.3.4).
_BAD_INTEGRITY = frozenset({"failed", "gap", "reordered"})
#: Every status integrity-result.schema.json allows; a record with no status or another one is unreadable.
_INTEGRITY_STATUSES = frozenset(s.value for s in IntegrityStatus)
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


def _read_report_file(path: Path) -> str | None:
    """The text of one report file, or ``None`` when the report folder has no entry by that name.
    An entry that is there but cannot be read as a regular file (a folder, a dangling link, a link
    into a folder that can be listed but not entered, a file without read permission) raises
    ``OSError``: readiness reports it as a blocking reason rather than reading it as absent, which
    would let a coverage gap or an integrity break stop blocking. ``os.stat`` answers the same on
    every supported Python, as ``signing.is_regular_file`` does, where ``Path.is_file`` does not.
    The bytes are decoded without newline translation, so a lone CR stays a CR for :func:`jsonl_lines`;
    bytes that are not UTF-8 raise :class:`_Unreadable` naming the profile rule and line."""
    try:
        os.lstat(path)
    except FileNotFoundError:
        return None
    if not is_regular_file(path):
        raise OSError(f"{path} is not a regular file")
    raw = path.read_bytes()
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        line = raw[: exc.start].count(b"\n") + 1
        raise _Unreadable("not UTF-8") from JsonProfileError("invalid_utf8", line)


class _Unreadable(ValueError):
    """A report file whose content is not JSON of the shape readiness reads."""


def _parse_json(text: str, line: int | None = None) -> Any:
    """One report file's JSON (or its JSON Lines line ``line``) by the rule readiness, sign and
    ``report --validate`` share (:func:`safe_json.load_report_json`)."""
    try:
        return load_report_json(text, line)
    except JSONError as exc:
        raise _Unreadable(str(exc)) from exc


def _profile_cause(exc: BaseException) -> JsonProfileError | None:
    """The :class:`JsonProfileError` at the root of ``exc``'s cause chain, if any."""
    while exc is not None and not isinstance(exc, JsonProfileError):
        exc = exc.__cause__  # type: ignore[assignment]
    return exc


def _objects(values: list[Any]) -> list[dict[str, Any]]:
    if not all(isinstance(value, dict) for value in values):
        raise _Unreadable("an entry is not a JSON object")
    return values


def _strings(record: dict[str, Any], *fields: str) -> dict[str, Any]:
    """Each named field the verdict is decided on is a JSON string when present (null included), or the
    file is unreadable: a wrong-typed field read loosely hides a gap or a break (SPEC §13.3.4)."""
    if any(field in record and not isinstance(record[field], str) for field in fields):
        raise _Unreadable(f"a field of {', '.join(fields)} is not a JSON string")
    return record


def _jsonl(text: str) -> list[dict[str, Any]]:
    return _objects([_parse_json(line, n) for n, line in jsonl_lines(text)])


def _integrity(text: str) -> list[dict[str, Any]]:
    """integrity.jsonl: one object per line, each with a ``status`` from integrity-result.schema.json's
    enum. A record without one, or with another value, is unreadable, never read as no break."""
    records = _jsonl(text)
    if any(
        not isinstance(status := record.get("status"), str)
        or status not in _INTEGRITY_STATUSES
        for record in records
    ):
        raise _Unreadable("an integrity status is missing or not one the schema allows")
    return records


def _json_object(text: str) -> dict[str, Any]:
    value = _parse_json(text)
    if not isinstance(value, dict):
        raise _Unreadable("not a JSON object")
    return value


def _json_objects(text: str) -> list[dict[str, Any]]:
    value = _parse_json(text)
    if not isinstance(value, list):
        raise _Unreadable("not a JSON array")
    return _objects(value)


def _object_of_objects(value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict):
        raise _Unreadable("not a JSON object")
    _objects(list(value.values()))
    return value


def _coverage(text: str) -> dict[str, Any]:
    """coverage.json: an object whose ``subjects`` (when present) maps each subject to an object,
    and each subject's ``event_types`` (when present) maps each type to an object; a subject's
    ``coverage_status`` and an event type's ``status`` are strings when present. An entry of
    another shape makes the file unreadable, never skipped: skipping it loses what deleting it
    would (SPEC §13.3.4)."""
    coverage = _json_object(text)
    if "subjects" in coverage:
        for entry in _object_of_objects(coverage["subjects"]).values():
            _strings(entry, "coverage_status")
            if "event_types" in entry:
                for detail in _object_of_objects(entry["event_types"]).values():
                    _strings(detail, "status")
    return coverage


def _applicability(text: str) -> list[dict[str, Any]]:
    """applicability.jsonl: one object per line, whose ``drift`` (when present) is an array of
    objects. A drift of another shape makes the file unreadable, never skipped (SPEC §13.3.4)."""
    statements = _jsonl(text)
    for statement in statements:
        if "drift" in statement:
            drift = statement["drift"]
            if not isinstance(drift, list):
                raise _Unreadable("drift is not a JSON array")
            _objects(drift)
    return statements


def _assertions(text: str) -> list[dict[str, Any]]:
    """assertions.json: an array of objects whose ``control`` and ``outcome`` are strings, ``window`` an
    object whose ``end`` is a string, and ``deviation`` a string or null, each when present. Another
    type makes the file unreadable, never read loosely (SPEC §13.3.4)."""
    assertions = _json_objects(text)
    for assertion in assertions:
        _strings(assertion, "control", "outcome")
        if "window" in assertion:
            window = assertion["window"]
            if not isinstance(window, dict):
                raise _Unreadable("window is not a JSON object")
            _strings(window, "end")
        if assertion.get("deviation") is not None:
            _strings(assertion, "deviation")
    return assertions


#: The reader readiness applies to each report data file; ``report --validate`` reads them with these
#: same readers, so a file one command refuses the other refuses too (18.184q).
_DATA_FILE_READERS: dict[str, Callable[[str], Any]] = {
    "integrity.jsonl": _integrity,
    "coverage.json": _coverage,
    "applicability.jsonl": _applicability,
    "quarantine.jsonl": _jsonl,
}


def data_file_problem(name: str, text: str) -> str | None:
    """Why readiness cannot read the report data file ``name`` holding ``text``, or ``None`` when it can."""
    try:
        _DATA_FILE_READERS[name](text)
    except _Unreadable as exc:
        return str(exc)
    return None


def _schema(name: str) -> dict[str, Any]:
    text = (
        resources.files("agentce.data.schemas")
        .joinpath(f"{name}.schema.json")
        .read_bytes()
    )
    parsed: dict[str, Any] = read_bundled(text)
    return parsed


def normalize_deviation_dates(
    deviations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Normalize a deviation register's ``granted``/``expiry`` values that YAML parsed as a
    ``date``/``datetime`` (an unquoted ``expiry: 2021-12-31``) to their ISO string form, so an
    unquoted date validates and compares exactly like a quoted one -- :func:`deviation_lint` and
    :func:`compute_readiness` would otherwise treat it as absent via :func:`parse_date`, bypassing
    both the 180-day cap and the expiry check. Every caller that loads a register straight off disk
    (``cmd_assess``/``cmd_readiness`` and the ``agentce-prepare-to-share`` skill scripts) must call
    this before passing entries to either function."""
    return [
        {
            k: (str(v) if isinstance(v, (date, datetime)) else v)
            for k, v in entry.items()
        }
        for entry in deviations
    ]


#: RFC 3339 only (2026-09-30 maintainer decision, `TRADEOFFS.md`/inbox row 19): the same grammar
#: TypeScript's and Java's own ``parseDate`` accept -- full calendar date, optional ``T``/space-
#: separated time with mandatory seconds, optional fractional seconds, optional ``Z``/numeric offset --
#: never ``datetime.fromisoformat``'s additional basic-format (``20211231``), week-date
#: (``2021-W52-5``), or reduced-precision (hour-only/minute-only) forms, which no producer this engine
#: reads ever emits.
_PARSE_DATE_RE = re.compile(
    r"(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})"
    r"(?:[T ](?P<hour>\d{2}):(?P<minute>\d{2}):(?P<second>\d{2})"
    r"(?:\.\d+)?(?P<offset>Z|[+-]\d{2}:?\d{2})?)?",
    re.ASCII,
)
_OFFSET_RE = re.compile(r"[+-](\d{2}):?(\d{2})", re.ASCII)


def parse_date(value: Any) -> date | None:
    """Parse an RFC 3339 date or date-time string, or an already-parsed ``date``/``datetime`` (what a
    YAML loader turns an *unquoted* date into), to a ``date``; anything else (not parseable, or a
    string outside RFC 3339's grammar) is ``None`` -- never an exception, so a hostile or malformed
    field is simply absent for comparison, not a crash. Accepting ``date``/``datetime`` directly, not
    just ``str``, means a caller that reads a register straight off disk and forgets
    :func:`normalize_deviation_dates` still gets a correct parse here rather than a silently-absent or
    falsely-invalid field."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None
    m = _PARSE_DATE_RE.fullmatch(value)
    if m is None:
        return None
    try:
        result = date(int(m.group("year")), int(m.group("month")), int(m.group("day")))
    except ValueError:
        return None
    if m.group("hour") is not None:
        hour, minute, second = (
            int(m.group("hour")),
            int(m.group("minute")),
            int(m.group("second")),
        )
        if hour > 23 or minute > 59 or second > 59:
            return None
        offset = m.group("offset")
        if offset is not None and offset != "Z":
            offset_match = _OFFSET_RE.fullmatch(offset)
            if offset_match is None:
                return None
            if int(offset_match.group(1)) > 23 or int(offset_match.group(2)) > 59:
                return None
    return result


def compute_readiness(
    report_dir: Path,
    *,
    severities: dict[str, str],
    deviations: list[dict[str, Any]] | None = None,
    gaps: set[str] | None = None,
    limitations: list[str] | None = None,
) -> dict[str, Any]:
    """Return the readiness verdict for a finished report directory (SPEC §13.3.4 stage 4).

    ``severities`` maps control id to severity (from the catalogs the report used); ``gaps`` is the
    set of control ids recorded in the operator's gaps file with an owner and date. A high-severity
    ``insufficient_evidence`` outcome is a limitation when recorded in ``gaps`` and a blocker
    otherwise; integrity breaks, coverage shortfalls, applicability drift, and invalid deviations
    always block."""
    gaps = gaps or set()
    reasons: list[str] = []
    limitations = list(limitations or [])

    def report_file(name: str, parse: Callable[[str], _T]) -> _T | None:
        """A stage-4 input every report has: an absent one is a blocking reason, never an empty
        file, so deleting the record of a gap or a break cannot clear it (SPEC §13.3.4). One that
        is not UTF-8 JSON of its shape blocks the same way, never a crash and never read as empty."""
        try:
            text = _read_report_file(report_dir / name)
            if text is not None:
                return parse(text)
        except (OSError, _Unreadable) as exc:
            # The profile rule and line when the file is outside the JSON profile (SPEC 8.7).
            rule = _profile_cause(exc)
            reasons.append(
                f"cannot read report file {name}" + (f": {rule}" if rule else "")
            )
            return None
        reasons.append(f"missing report file {name}")
        return None

    for record in report_file("integrity.jsonl", _integrity) or []:
        if record.get("status") in _BAD_INTEGRITY:
            reasons.append(
                f"integrity {record.get('status')} on stream {record.get('stream')}"
            )

    coverage = report_file("coverage.json", _coverage) or {}
    for subject, entry in sorted(coverage.get("subjects", {}).items()):
        if entry.get("coverage_status") == "gap":
            reasons.append(f"coverage shortfall for subject {subject}")
        elif entry.get("coverage_status") == STATUS_UNKNOWN:
            # No independent denominator reconciles the subject's records (SPEC §6.5, §13.3.4).
            reasons.append(
                f"coverage unknown for subject {subject}: no independent denominator"
            )
        for event_type, detail in sorted(entry.get("event_types", {}).items()):
            # A declared source below the coverage threshold is a shortfall even when another event
            # type has unknown coverage and masks it in the subject-level roll-up (SPEC §13.3.4).
            if detail.get("status") == "below_threshold":
                reasons.append(f"coverage shortfall for {subject} {event_type}")

    # Read for its presence and shape only: deleting it must not read READY (SPEC §13.3.4, 18.184q).
    report_file("quarantine.jsonl", _DATA_FILE_READERS["quarantine.jsonl"])

    for statement in report_file("applicability.jsonl", _applicability) or []:
        for drift in statement.get("drift", []):
            reasons.append(
                f"applicability drift: {drift.get('kind')} {drift.get('ref')}"
            )

    assertions = report_file("assertions.json", _assertions) or []
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
        outcomes_by_control: dict[str, set[str]] = {}
        applied_controls: set[str] = set()
        window_ends: list[str] = []
        for a in assertions:
            control = str(a.get("control"))
            outcomes_by_control.setdefault(control, set()).add(str(a.get("outcome")))
            if a.get("deviation"):
                applied_controls.add(control)
            window = a.get("window") or {}
            end = window.get("end")
            if end:
                window_ends.append(str(end))
        problems = deviation_lint(
            deviations,
            control_ids=set(severities),
            outcomes_by_control={
                control: frozenset(outcomes)
                for control, outcomes in outcomes_by_control.items()
            },
            applied_controls=frozenset(applied_controls),
            as_of=max(window_ends) if window_ends else None,
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
    outcomes_by_control: dict[str, frozenset[str]],
    applied_controls: frozenset[str] = frozenset(),
    as_of: str | None = None,
    max_days: int = DEFAULT_MAX_DEVIATION_DAYS,
) -> list[str]:
    """Enforce the deviation rules of SPEC §13.3.4: the control exists and its outcome was
    ``non-conformant`` (never ``insufficient_evidence``), no deviation touches the INT family, every
    field is present, the approver is a named person distinct from the owner, and the deviation
    expires within the catalog's maximum lifetime.

    ``outcomes_by_control`` carries every outcome recorded for a control across every subject in this
    run (not a single collapsed value), so the accept/reject verdict never depends on assertion
    iteration order. A control already in ``applied_controls`` (its deviation was applied by
    ``assess.apply_deviations``) skips the outcome re-check entirely -- its outcome is now ``partial``,
    which a fresh readiness re-lint must not reject -- but is instead checked, when ``as_of`` is given,
    against its own ``expiry``: an *applied* deviation that has since expired is flagged. An expiry on a
    not-yet-applied entry is never a lint failure (that is ``apply_deviations``'s job: ignored and
    reported, never refused)."""
    problems: list[str] = []
    seen_controls: set[str] = set()
    as_of_date = parse_date(as_of) if as_of is not None else None
    for deviation in deviations:
        control = str(deviation.get("control", ""))
        if control in seen_controls:
            problems.append(f"{control}: duplicate deviation entry for this control")
        seen_controls.add(control)
        if control not in control_ids:
            problems.append(f"{control or '<none>'}: control is not in the catalog")
        if control.split("-", 1)[0] == _INT_FAMILY:
            problems.append(
                f"{control}: the INT family cannot be deviated (integrity and coverage)"
            )
        if control not in applied_controls:
            outcomes = outcomes_by_control.get(control, frozenset())
            if "insufficient_evidence" in outcomes:
                problems.append(
                    f"{control}: insufficient_evidence is an evidence gap, not a risk acceptance"
                )
            elif outcomes and "non-conformant" not in outcomes:
                problems.append(
                    f"{control}: only a non-conformant outcome may be deviated "
                    f"(got {', '.join(sorted(outcomes))})"
                )
        for field in _DEVIATION_FIELDS:
            if not deviation.get(field):
                problems.append(f"{control}: deviation is missing {field}")
        owner, approver = deviation.get("owner"), deviation.get("approver")
        if owner and approver and owner == approver:
            problems.append(
                f"{control}: the approver must be a person distinct from the owner"
            )
        granted_raw, expiry_raw = deviation.get("granted"), deviation.get("expiry")
        granted, expiry = parse_date(granted_raw), parse_date(expiry_raw)
        for date_field, raw, parsed in (
            ("granted", granted_raw, granted),
            ("expiry", expiry_raw, expiry),
        ):
            if raw and parsed is None:
                problems.append(
                    f"{control}: {date_field} is not a valid RFC 3339 date ({raw!r})"
                )
        if granted and expiry and (expiry - granted).days > max_days:
            problems.append(f"{control}: deviation lifetime exceeds {max_days} days")
        if (
            as_of_date is not None
            and expiry is not None
            and control in applied_controls
            and expiry < as_of_date
        ):
            problems.append(
                f"{control}: applied deviation has expired (expiry {deviation.get('expiry')})"
            )
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
