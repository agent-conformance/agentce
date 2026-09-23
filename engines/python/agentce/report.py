"""Render the report artifacts from assertions (SPEC §9).

``assertions.json`` is written in RFC 8785 canonical form (byte-identical across engines); the human
report (md/html), OSCAL Assessment Results, SARIF, and role-aware evidence packs are rendered from it,
and the reproducibility manifest records the digest of every input and output. DC-5 is enforced
before anything is written: a supporting verdict without an evidence pointer aborts the run.
Superseded reports are named in ``manifest.supersedes`` (HR-10). ``validate_report`` checks every
emitted artifact against its vendored schema.
"""

from __future__ import annotations

import csv
import hashlib
import html
import io
import json
import os
import platform
import uuid
import xml.etree.ElementTree as ET
from importlib import resources
from pathlib import Path
from typing import Any

import jsonschema
import regex

from . import ENGINE_NAME, SPEC_VERSION, __version__, canonical, messages, verdict
from .assertions import Assertion, aggregate, check_dc5
from .catalog import Catalog, ControlSpec, catalog_provenance_digest

_ARTIFACT_SCHEMAS = {
    "assertions.json": "assertions",
    "manifest.json": "manifest",
    "oscal-ar.json": "oscal-assessment-results",
    "results.sarif": "results-sarif",
}
_SARIF_LEVEL = {
    "non-conformant": "error",
    "partial": "warning",
    "insufficient_evidence": "warning",
    "not_assessed": "note",
}
_OSCAL_STATE = {
    "conformant": "satisfied",
    "non-conformant": "not-satisfied",
    "partial": "not-satisfied",
    "not_applicable": "not-satisfied",
    "not_assessed": "not-satisfied",
    "insufficient_evidence": "not-satisfied",
}


def _digest_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _package_digest() -> str:
    return (
        "sha256:" + hashlib.sha256(f"{ENGINE_NAME}:{__version__}".encode()).hexdigest()
    )


def _uuid(*parts: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "agentce:" + ":".join(parts)))


def _safe(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-._" else "_" for c in name)


def _outcome_label(catalogue: dict[str, str], outcome: str) -> str:
    return catalogue.get(f"outcome.{outcome}", outcome)


def _crosswalk_text(entry: dict[str, Any], cat: dict[str, str]) -> str:
    """One clause citation, labelled unverified when the carried flag is not ``True`` (SPEC §7.3)."""
    text = f"{entry.get('framework', '')} {entry.get('clause', '')}".strip()
    if entry.get("verified") is not True:
        text += f" {cat['report.crosswalk_unverified']}"
    return text


def _control_index(catalogs: list[Catalog]) -> dict[str, ControlSpec]:
    """Every control, keyed by id, across every resolved catalog -- so a finding can carry its
    control's title, severity, and remediation technique (SPEC §9.3). A later catalog in the list
    (an overlay) wins over an earlier one (the base) for the same control id."""
    index: dict[str, ControlSpec] = {}
    for catalog in catalogs:
        for control in catalog.controls:
            index[control.id] = control
    return index


def _remediation_hints(spec: ControlSpec | None) -> list[str]:
    """The control's technique pointers, the catalog's only machine-readable remediation hint
    (SPEC §7.3), stripped of any ``techniques/`` path prefix for a readable label."""
    if spec is None:
        return []
    return [str(t).rsplit("/", 1)[-1] for t in spec.raw.get("techniques", []) or []]


_SEVERITY_LEVELS = ("high", "medium", "low")
_SEVERITY_HEADING_KEY = {
    "high": "report.severity_high",
    "medium": "report.severity_medium",
    "low": "report.severity_low",
}


def _severity_heading(level: str, cat: dict[str, str]) -> str:
    return cat.get(_SEVERITY_HEADING_KEY.get(level, ""), cat["report.severity_unrated"])


def _grouped_by_severity(
    assertions: list[Assertion], by_control: dict[str, ControlSpec]
) -> list[tuple[str, list[Assertion]]]:
    """Findings grouped by control severity, highest risk first, then ``unrated`` for a control
    with no severity known to this render (no catalog resolved, or an id the catalog does not
    carry) -- so the report reads as a ranked audit, never a bare alphabetical tally (SPEC §9.3)."""
    groups: dict[str, list[Assertion]] = {}
    for a in assertions:
        spec = by_control.get(a.control)
        level = (
            spec.severity if spec and spec.severity in _SEVERITY_LEVELS else "unrated"
        )
        groups.setdefault(level, []).append(a)
    levels = [lvl for lvl in _SEVERITY_LEVELS if lvl in groups]
    if "unrated" in groups:
        levels.append("unrated")
    return [
        (level, sorted(groups[level], key=lambda a: (a.subject, a.control)))
        for level in levels
    ]


def _finding_title(a: Assertion, spec: ControlSpec | None) -> str:
    return spec.title if spec and spec.title else a.control


def _verdict_md(summary: dict[str, Any], cat: dict[str, str]) -> list[str]:
    """The lines that lead the report: the verdict, the top gaps, and the next step (SPEC §9.2)."""
    state = summary["verdict"]
    lines = [
        f"## {cat['report.verdict_heading']}",
        "",
        f"**{cat[f'verdict.{state}']}**",
        "",
    ]
    if summary["top_gaps"]:
        lines += [f"{cat['report.top_gaps_heading']}:", ""]
        lines += [f"- {verdict.gap_text(gap, cat)}" for gap in summary["top_gaps"]]
    else:
        lines.append(f"{cat['report.top_gaps_heading']}: {cat['report.no_gaps']}")
    lines += ["", f"{cat['report.next_step_heading']}: {cat[f'next.{state}']}", ""]
    return lines


def _verdict_html(summary: dict[str, Any], cat: dict[str, str]) -> str:
    state = summary["verdict"]
    heading = html.escape(cat["report.top_gaps_heading"])
    if summary["top_gaps"]:
        items = "".join(
            f"<li>{html.escape(verdict.gap_text(gap, cat))}</li>"
            for gap in summary["top_gaps"]
        )
        gaps = f"<p>{heading}:</p><ul>{items}</ul>"
    else:
        gaps = f"<p>{heading}: {html.escape(cat['report.no_gaps'])}</p>"
    return (
        '<section aria-labelledby="verdict"><h2 id="verdict">'
        f"{html.escape(cat['report.verdict_heading'])}</h2>"
        f"<p><strong>{html.escape(cat[f'verdict.{state}'])}</strong></p>{gaps}"
        f"<p>{html.escape(cat['report.next_step_heading'])}: "
        f"{html.escape(cat[f'next.{state}'])}</p></section>"
    )


def _reproduce_command(invocation: list[str] | None) -> str:
    return "agentce " + " ".join(invocation) if invocation else "agentce quickstart"


def _provenance_md(catalogs: list[str], invocation: list[str] | None) -> list[str]:
    """The provenance line every report body carries: engine version, catalog(s), reproduce command
    (SPEC §9.1) -- so a reader of the report file alone, without opening manifest.json, can see what
    produced it and how to redo it."""
    return [
        "## Provenance",
        "",
        f"- Engine: {ENGINE_NAME} {__version__}",
        f"- Catalog: {', '.join(catalogs) if catalogs else '(none)'}",
        f"- Reproduce: `{_reproduce_command(invocation)}`",
        "",
    ]


def _finding_md(
    a: Assertion, spec: ControlSpec | None, cat: dict[str, str]
) -> list[str]:
    """One finding: its control title, outcome, crosswalk citation, and -- when present -- the
    evidence pointers, the offending nodes from its violations, and the control's remediation
    technique (SPEC §9.3), so a reviewer sees not just the verdict but why and what to do about it."""
    lines = [
        f"- **{_finding_title(a, spec)}** (`{a.control}` @ `{a.subject}`) -> "
        f"**{_outcome_label(cat, a.outcome)}** "
        f"(rung {a.rung}, {a.mode}; {a.population[1]}/{a.population[0]} failed)"
    ]
    lines += [f"  - {_crosswalk_text(e, cat)}" for e in a.crosswalk]
    if a.evidence:
        refs = ", ".join(f"`{e.ref}`" for e in a.evidence)
        lines.append(f"  - {cat['report.evidence_label']}: {refs}")
    offending = [str(v.get("focus", "")) for v in a.violations if v.get("focus")]
    if offending:
        nodes = ", ".join(f"`{node}`" for node in offending)
        lines.append(f"  - {cat['report.violations_label']}: {nodes}")
    hints = _remediation_hints(spec)
    if hints:
        lines.append(f"  - {cat['report.remediation_label']}: {', '.join(hints)}")
    return lines


def render_report_md(
    assertions: list[Assertion],
    counts: dict[str, int],
    *,
    language: str = messages.DEFAULT_LANGUAGE,
    catalogs: list[Catalog] | None = None,
    invocation: list[str] | None = None,
) -> str:
    cat = messages.catalogue(language)
    by_control = _control_index(catalogs or [])
    labels = [f"{c.id}@{c.version}" for c in (catalogs or [])]
    summary = verdict.summarize(assertions)
    lines = [f"# {cat['report.title']}", ""]
    lines += _verdict_md(summary, cat)
    lines += [f"## {cat['report.summary_heading']}", ""]
    lines += [
        f"- {_outcome_label(cat, outcome)}: {count}"
        for outcome, count in counts.items()
    ]
    lines += ["", f"## {cat['report.assertions_heading']}", ""]
    if not assertions:
        lines.append(f"_{cat['report.no_controls']}_")
    for level, items in _grouped_by_severity(assertions, by_control):
        lines += [f"### {_severity_heading(level, cat)}", ""]
        for a in items:
            lines += _finding_md(a, by_control.get(a.control), cat)
        lines.append("")
    lines += [""] + _provenance_md(labels, invocation)
    return "\n".join(lines) + "\n"


#: A self-contained stylesheet (no external references) with a print rule for A4 and Letter (§9.3).
_HTML_STYLE = (
    "body{font-family:system-ui,sans-serif;margin:2rem;color:#111;background:#fff;line-height:1.5}"
    "h1{font-size:1.5rem}h2{font-size:1.2rem;margin-top:1.5rem}"
    "table{border-collapse:collapse;width:100%}"
    "th,td{border:1px solid #999;padding:.35rem .5rem;text-align:left}"
    "th{background:#f0f0f0}caption{text-align:left;font-weight:bold;margin-bottom:.5rem}"
    "@media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}"
    "@media print{@page{size:A4;margin:1.5cm}body{margin:0}@page :first{size:letter}"
    "table{page-break-inside:auto}tr{page-break-inside:avoid}}"
)


def _provenance_html(catalogs: list[str], invocation: list[str] | None) -> str:
    catalog_text = html.escape(", ".join(catalogs) if catalogs else "(none)")
    return (
        '<section aria-labelledby="provenance"><h2 id="provenance">Provenance</h2><ul>'
        f"<li>Engine: {html.escape(ENGINE_NAME)} {html.escape(__version__)}</li>"
        f"<li>Catalog: {catalog_text}</li>"
        f"<li>Reproduce: <code>{html.escape(_reproduce_command(invocation))}</code></li>"
        "</ul></section>"
    )


def _row_html(a: Assertion, spec: ControlSpec | None, cat: dict[str, str]) -> str:
    """One finding row: its title, severity, outcome, clause citation, and -- when present -- the
    evidence pointers, offending nodes, and remediation technique in a Details cell (SPEC §9.3),
    every field escaped since a control title, an evidence ref, and a violation's focus node can
    all originate in evidence or a third-party catalog."""
    details: list[str] = []
    if a.evidence:
        refs = ", ".join(html.escape(e.ref) for e in a.evidence)
        details.append(f"{html.escape(cat['report.evidence_label'])}: {refs}")
    offending = [str(v.get("focus", "")) for v in a.violations if v.get("focus")]
    if offending:
        nodes = ", ".join(html.escape(node) for node in offending)
        details.append(f"{html.escape(cat['report.violations_label'])}: {nodes}")
    hints = _remediation_hints(spec)
    if hints:
        label = html.escape(cat["report.remediation_label"])
        details.append(f"{label}: {html.escape(', '.join(hints))}")
    return (
        f"<tr><td>{html.escape(_finding_title(a, spec))}</td>"
        f"<td>{html.escape(a.control)}</td><td>{html.escape(a.subject)}</td>"
        f"<td>{html.escape(spec.severity if spec else '')}</td>"
        f"<td>{html.escape(_outcome_label(cat, a.outcome))}</td>"
        f"<td>{'; '.join(html.escape(_crosswalk_text(e, cat)) for e in a.crosswalk)}</td>"
        f"<td>{'<br>'.join(details)}</td></tr>"
    )


def render_report_html(
    assertions: list[Assertion],
    counts: dict[str, int],
    *,
    language: str = messages.DEFAULT_LANGUAGE,
    catalogs: list[Catalog] | None = None,
    invocation: list[str] | None = None,
) -> str:
    """Render a self-contained, escaped, WCAG 2.2 AA report page (SPEC §9.3): a strict CSP meta tag,
    no external references, one ``h1``, a ``main`` landmark, a print stylesheet, and every string that
    originates in evidence or declarations rendered as escaped text, never as markup."""
    cat = messages.catalogue(language)
    by_control = _control_index(catalogs or [])
    labels = [f"{c.id}@{c.version}" for c in (catalogs or [])]
    title = html.escape(cat["report.title"])
    summary = "".join(
        f"<li>{html.escape(_outcome_label(cat, o))}: {c}</li>"
        for o, c in counts.items()
    )
    ordered = [
        a for _, items in _grouped_by_severity(assertions, by_control) for a in items
    ]
    rows = "".join(_row_html(a, by_control.get(a.control), cat) for a in ordered)
    body_rows = rows or (
        f'<tr><td colspan="7">{html.escape(cat["report.no_controls"])}</td></tr>'
    )
    return (
        f'<!doctype html><html lang="{html.escape(language)}"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta http-equiv="Content-Security-Policy" '
        "content=\"default-src 'none'; style-src 'unsafe-inline'; img-src 'none'\">"
        f"<title>{title}</title><style>{_HTML_STYLE}</style></head><body>"
        f"<main><h1>{title}</h1>"
        f"{_verdict_html(verdict.summarize(assertions), cat)}"
        f'<section aria-labelledby="summary"><h2 id="summary">'
        f"{html.escape(cat['report.summary_heading'])}</h2><ul>{summary}</ul></section>"
        f'<section aria-labelledby="assertions"><h2 id="assertions">'
        f"{html.escape(cat['report.assertions_heading'])}</h2>"
        f"<table><caption>{html.escape(cat['report.assertions_heading'])}</caption>"
        '<thead><tr><th scope="col">Title</th><th scope="col">Control</th>'
        '<th scope="col">Subject</th><th scope="col">Severity</th>'
        '<th scope="col">Outcome</th><th scope="col">Clause</th>'
        '<th scope="col">Details</th></tr></thead>'
        f"<tbody>{body_rows}</tbody></table></section>"
        f"{_provenance_html(labels, invocation)}"
        f"<footer><p>{html.escape(cat['report.affected_persons'])}</p></footer>"
        "</main></body></html>\n"
    )


def _oscal_timestamp(assertions: list[Assertion]) -> str:
    """A deterministic OSCAL date-time for this run: the earliest evidence-window start across the
    assertions, so it reflects what was actually reviewed rather than the run's wall clock (the
    byte-identical canonical set must stay clock-independent). A run with no assertions falls back to
    a fixed epoch, never `now()`."""
    if not assertions:
        return "1970-01-01T00:00:00Z"
    return min(a.window[0] for a in assertions)


def render_oscal(assertions: list[Assertion]) -> dict[str, Any]:
    """Render ``oscal-ar.json`` as an importable NIST OSCAL 1.1.2 Assessment Results document (SPEC
    §9, §9.4): every result carries real ``observations[]`` built from the assertion's own evidence
    pointers, every finding resolves to the observation that backs it and links to its real control id
    so a GRC platform can trace the finding to the requirement it assesses. No catalog object is
    needed here -- the control id alone is the traceable token (SPEC §7.3's control ids are globally
    unique) -- so this keeps its existing ``(assertions)`` signature."""
    ordered = sorted(assertions, key=lambda x: (x.subject, x.control))
    when = _oscal_timestamp(assertions)

    observation_uuid: dict[tuple[str, str], str] = {}
    observations: list[dict[str, Any]] = []
    for a in ordered:
        # An observation backs every finding, evidence-bearing or not, so every finding resolves to
        # one (SPEC §9); only an evidence-bearing assertion's observation carries `relevant-evidence`.
        obs_uuid = _uuid("observation", a.control, a.subject)
        observation_uuid[(a.control, a.subject)] = obs_uuid
        observation: dict[str, Any] = {
            "uuid": obs_uuid,
            "description": f"Assessment activity for {a.control} on {a.subject}.",
            "methods": ["TEST"],
            "collected": when,
        }
        if a.evidence:
            observation["relevant-evidence"] = [
                {
                    "href": e.ref,
                    "description": f"{e.source_class} evidence, digest {e.digest}",
                }
                for e in a.evidence
            ]
        observations.append(observation)

    findings: list[dict[str, Any]] = []
    for a in ordered:
        finding: dict[str, Any] = {
            "uuid": _uuid("finding", a.control, a.subject),
            "title": f"{a.control} for {a.subject}",
            "description": f"{a.control} assessed for {a.subject}: {a.outcome}.",
            "target": {
                "type": "objective-id",
                "target-id": a.control,
                "status": {
                    "state": _OSCAL_STATE.get(a.outcome, "not-satisfied"),
                    "reason": a.outcome,
                },
            },
            "links": [{"href": f"urn:agentce:control:{a.control}", "rel": "control"}],
            "related-observations": [
                {"observation-uuid": observation_uuid[(a.control, a.subject)]}
            ],
        }
        findings.append(finding)

    result: dict[str, Any] = {
        "uuid": _uuid("result"),
        "title": "AgentCE structural assessment",
        "description": (
            "AgentCE's structural, statistical, and probe-based assessment of the run's "
            "subjects against the resolved catalog(s)."
        ),
        "start": when,
        "reviewed-controls": {"control-selections": [{"include-all": {}}]},
    }
    if observations:
        result["observations"] = observations
    if findings:
        result["findings"] = findings

    return {
        "assessment-results": {
            "uuid": _uuid("assessment-results"),
            "metadata": {
                "title": "AgentCE Assessment Results",
                "version": __version__,
                "oscal-version": "1.1.2",
                "last-modified": when,
            },
            "import-ap": {"href": "urn:agentce:assessment-plan:structural"},
            "results": [result],
        }
    }


def _xml_tag(key: str) -> str:
    """Turn an OSCAL AR dict key into a well-formed XML element name: an XML ``Name`` cannot start
    with a digit or a character outside ``[A-Za-z_]``, so a key that does is prefixed; every other
    character an XML ``Name`` allows (letters, digits, ``-``, ``_``, ``.``) passes through unchanged
    (every real OSCAL key, e.g. ``last-modified``, already satisfies this)."""
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in key)
    if not safe or not (safe[0].isalpha() or safe[0] == "_"):
        safe = f"_{safe}"
    return safe


def _xml_from_value(tag: str, value: Any) -> ET.Element:
    """Recursively render a JSON-like value (dict/list/scalar) as an XML element: a dict's keys are
    sorted and a list's items keep their original order, so two independent renders of the same
    document are byte-identical (no dict hash-order or library-chosen attribute order); every scalar
    becomes element *text*, which :mod:`xml.etree.ElementTree`'s serializer escapes on write -- values
    are never string-concatenated into the markup (the mistake behind finding #19's stored-XSS bug in
    the TypeScript SARIF renderer)."""
    element = ET.Element(tag)
    if isinstance(value, dict):
        for key in sorted(value.keys()):
            element.append(_xml_from_value(_xml_tag(key), value[key]))
    elif isinstance(value, list):
        for item in value:
            element.append(_xml_from_value("item", item))
    elif value is not None:
        element.text = str(value)
    return element


def render_oscal_xml(document: dict[str, Any]) -> str:
    """Render ``oscal-ar.xml`` (SPEC §9): a well-formed XML serialization of the very same OSCAL AR
    object :func:`render_oscal` serializes to ``oscal-ar.json`` -- pass ``render_oscal(assertions)`` in
    directly, so the two artifacts can never drift apart. Every finding's ``target.target-id`` appears
    as element text somewhere in the output. Deterministic: dict keys sorted, list order preserved, no
    library-chosen attribute order, so two renders of the same document are byte-identical."""
    root_tag, root_value = next(iter(document.items()))
    root = _xml_from_value(_xml_tag(root_tag), root_value)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        + ET.tostring(root, encoding="unicode")
        + "\n"
    )


def render_junit(assertions: list[Assertion]) -> str:
    """Render ``report.junit.xml`` (SPEC §9): exactly one ``<testcase>`` per real ``(subject,
    control)`` pair, sorted by ``(subject, control)`` for a canonical, byte-identical-across-runs
    order. A ``<failure>`` child is present iff the pair's outcome is not ``"conformant"``, so a CI
    job's JUnit consumer marks every non-conformant, partial, not-assessed, or unproven pair failed and
    a conformant one green -- the same one-bit-of-information a JUnit consumer already understands,
    without it having to know AgentCE's six-outcome vocabulary."""
    ordered = sorted(assertions, key=lambda a: (a.subject, a.control))
    failures = sum(1 for a in ordered if a.outcome != "conformant")
    suite = ET.Element(
        "testsuite",
        {
            "name": "agentce",
            "tests": str(len(ordered)),
            "failures": str(failures),
            "errors": "0",
            "skipped": "0",
        },
    )
    for a in ordered:
        case = ET.SubElement(
            suite, "testcase", {"classname": a.subject, "name": a.control}
        )
        if a.outcome != "conformant":
            failure = ET.SubElement(
                case,
                "failure",
                {"message": a.outcome, "type": "agentce.non_conformant"},
            )
            failure.text = f"{a.control} on {a.subject}: {a.outcome}"
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        + ET.tostring(suite, encoding="unicode")
        + "\n"
    )


#: report.csv's header row (SPEC §9); `validate_report` requires at least these columns.
CSV_COLUMNS = ("control", "subject", "outcome", "control_version", "rung", "mode")


def render_csv(assertions: list[Assertion]) -> str:
    """Render ``report.csv`` (SPEC §9): one row per real ``(subject, control)`` pair, sorted by
    ``(subject, control)``, a fixed ``csv`` dialect (``\\n`` line terminator, so the bytes never vary
    by platform) -- byte-identical across independent runs of the same input."""
    ordered = sorted(assertions, key=lambda a: (a.subject, a.control))
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(CSV_COLUMNS)
    for a in ordered:
        writer.writerow(
            [a.control, a.subject, a.outcome, a.control_version, a.rung, a.mode]
        )
    return buffer.getvalue()


def _pdf_date(started_at: str) -> str:
    """A PDF-native date string (``D:YYYYMMDDHHMMSSZ``) derived from ``started_at`` -- the run's own
    ``manifest.run.started_at`` -- never an independent wall-clock read."""
    digits = "".join(c for c in started_at if c.isdigit())
    return f"D:{digits}Z" if digits else "D:19700101000000Z"


def _pdf_ascii(text: str) -> str:
    """Standard-14 Helvetica uses WinAnsiEncoding (effectively Latin-1); a character outside it is
    replaced rather than raising, so a subject id or control title with an unusual character never
    breaks PDF generation."""
    return text.encode("latin-1", "replace").decode("latin-1")


def _pdf_escape(text: str) -> str:
    """Escape a PDF string literal's three special characters (SPEC's stored-content rule: no value
    is ever concatenated into the file's syntax unescaped)."""
    return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def _pdf_body_lines(
    assertions: list[Assertion],
    counts: dict[str, int],
    cat: dict[str, str],
    started_at: str,
) -> list[str]:
    summary = verdict.summarize(assertions)
    lines = [
        cat["report.title"],
        "",
        "This PDF is a DERIVED, NON-CANONICAL rendering of the AgentCE report; report.md and "
        "report.html are the canonical artifacts.",
        f"Generated: {started_at}",
        "",
        cat[f"verdict.{summary['verdict']}"],
        "",
    ]
    lines += [
        f"{_outcome_label(cat, outcome)}: {count}" for outcome, count in counts.items()
    ]
    lines.append("")
    for a in sorted(assertions, key=lambda x: (x.subject, x.control)):
        lines.append(f"{a.control} @ {a.subject}: {_outcome_label(cat, a.outcome)}")
    return lines


def _pdf_content_stream(lines: list[str]) -> bytes:
    parts = ["BT", "/F1 10 Tf", "50 740 Td", "14 TL"]
    for i, line in enumerate(lines):
        safe = _pdf_escape(_pdf_ascii(line))
        parts.append(f"({safe}) Tj")
        if i != len(lines) - 1:
            parts.append("T*")
    parts.append("ET")
    return ("\n".join(parts) + "\n").encode("latin-1")


#: This standard-14 font object's bytes are pinned: Helvetica needs no embedded font file (ADR-0015),
#: and this block never varies between runs or reports, satisfying the "byte-identical font object"
#: requirement trivially -- it is simply never computed from run data.
_PDF_FONT_OBJECT = (
    b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"
)


def render_pdf(
    assertions: list[Assertion],
    counts: dict[str, int],
    *,
    started_at: str,
    language: str = messages.DEFAULT_LANGUAGE,
) -> bytes:
    """Render ``report.pdf`` (SPEC §9, ADR-0015): a minimal, hand-rolled, real PDF -- no third-party
    PDF library -- with one page of Helvetica text summarizing the run, clearly labelled a derived,
    non-canonical rendering, and dated from ``started_at`` (the run's own ``manifest.run.started_at``,
    never an independent wall-clock read). The font object (``_PDF_FONT_OBJECT``) is a fixed standard-14
    font dictionary, byte-identical across every run."""
    cat = messages.catalogue(language)
    lines = _pdf_body_lines(assertions, counts, cat, started_at)
    content = _pdf_content_stream(lines)

    title = _pdf_escape(_pdf_ascii("AgentCE report (derived, non-canonical)"))
    objects_bodies: dict[int, bytes] = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        3: (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>"
        ),
        4: _PDF_FONT_OBJECT,
        5: (
            f"<< /Length {len(content)} >>\nstream\n".encode("ascii")
            + content
            + b"endstream"
        ),
        6: (
            f"<< /Producer (AgentCE) /Title ({title}) "
            f"/CreationDate ({_pdf_date(started_at)}) >>"
        ).encode("ascii"),
    }

    header = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"
    body = bytearray()
    offsets: dict[int, int] = {}
    cursor = len(header)
    for num in range(1, 7):
        offsets[num] = cursor
        obj_bytes = (
            f"{num} 0 obj\n".encode("ascii") + objects_bodies[num] + b"\nendobj\n"
        )
        body += obj_bytes
        cursor += len(obj_bytes)

    xref_offset = cursor
    xref_lines = ["xref", "0 7", "0000000000 65535 f "]
    xref_lines += [f"{offsets[num]:010d} 00000 n " for num in range(1, 7)]
    xref = ("\n".join(xref_lines) + "\n").encode("ascii")

    trailer = (
        f"trailer\n<< /Size 7 /Root 1 0 R /Info 6 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n"
    ).encode("ascii")

    return header + bytes(body) + xref + trailer


def render_step_summary(
    assertions: list[Assertion],
    counts: dict[str, int],
    *,
    language: str = messages.DEFAULT_LANGUAGE,
    catalogs: list[Catalog] | None = None,
    invocation: list[str] | None = None,
) -> str:
    """The markdown job summary for ``$GITHUB_STEP_SUMMARY`` (SPEC §9): a heading, how many
    ``(control, subject)`` pairs were assessed, and the same verdict/tally/findings ``report.md``
    carries, so a GitHub Actions run's Summary tab shows the real result without opening the report
    artifact."""
    header = f"## AgentCE assessment summary\n\n{len(assertions)} (control, subject) pair(s) assessed.\n\n"
    return header + render_report_md(
        assertions, counts, language=language, catalogs=catalogs, invocation=invocation
    )


def write_github_step_summary(
    assertions: list[Assertion],
    counts: dict[str, int],
    *,
    language: str = messages.DEFAULT_LANGUAGE,
    catalogs: list[Catalog] | None = None,
    invocation: list[str] | None = None,
) -> bool:
    """Append the step summary to ``$GITHUB_STEP_SUMMARY``, unconditionally, whenever that
    environment variable names a writable file (SPEC §9) -- never behind a flag: a CI job sets the
    variable, and a plain ``agentce assess`` honours it. Returns whether anything was written (``False``
    when the variable is unset, or the file could not be appended to)."""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return False
    text = render_step_summary(
        assertions, counts, language=language, catalogs=catalogs, invocation=invocation
    )
    try:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(text)
    except OSError:
        return False
    return True


def _sarif_help_uri(control: str) -> str:
    """A stable, deterministic reference URL for the control's family page (`docs/reference/catalog/`,
    published at the site under the same path). SARIF's `helpUri` is a citation, not a runtime
    dependency: it does not need to resolve for the check that reads it, the same way a JSON Schema
    `$id` does not (`spec/report/vendor/README.md`)."""
    family = control.split("-", 1)[0]
    return f"https://agent-conformance.org/reference/catalog/{family}"


def _sarif_rule_help_text(control: str, spec: ControlSpec | None) -> str:
    if spec is None:
        return f"AgentCE control {control}."
    return spec.title


def _sarif_fingerprint(a: Assertion) -> str:
    """A fingerprint derived only from the assertion's own content -- control, subject, outcome, and
    the evaluation window/population that produced it -- so two independent offline runs over the same
    evidence produce byte-identical fingerprints (no clock, host, or run counter)."""
    payload = "|".join(
        [
            a.control,
            a.subject,
            a.outcome,
            a.window[0],
            a.window[1],
            str(a.population[0]),
            str(a.population[1]),
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def render_sarif(
    assertions: list[Assertion], *, catalogs: list[Catalog] | None = None
) -> dict[str, Any]:
    """Render ``results.sarif`` (SPEC §9) as a document a code-scanning consumer can actually use: every
    rule carries catalog-sourced ``name``/``help``/``helpUri``, every result carries a synthetic
    ``locations`` entry (the subject is not a source file, so the location is a stable pseudo-path for
    that subject) and a content-derived ``partialFingerprints`` (stable across independent runs, so
    findings de-dup across scans), and ``not_assessed`` surfaces at a level distinct from
    ``insufficient_evidence`` so a reader -- and a code-scanning gate -- can tell unproven apart from
    thin evidence instead of one being silent."""
    by_control = _control_index(catalogs or [])
    controls = sorted({a.control for a in assertions})
    rules = [
        {
            "id": control,
            "name": control,
            "help": {"text": _sarif_rule_help_text(control, by_control.get(control))},
            "helpUri": _sarif_help_uri(control),
        }
        for control in controls
    ]
    results = [
        {
            "ruleId": a.control,
            "level": _SARIF_LEVEL[a.outcome],
            "message": {"text": f"{a.control} on {a.subject}: {a.outcome}"},
            "locations": [
                {
                    "physicalLocation": {
                        "artifactLocation": {
                            "uri": f"agentce/subjects/{_safe(a.subject)}"
                        }
                    }
                }
            ],
            "partialFingerprints": {"agentceOutcomeHash/v1": _sarif_fingerprint(a)},
        }
        for a in sorted(assertions, key=lambda x: (x.subject, x.control))
        if a.outcome in _SARIF_LEVEL
    ]
    return {
        "$schema": "https://agent-conformance.org/spec/report/results-sarif.schema.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": ENGINE_NAME,
                        "version": __version__,
                        "rules": rules,
                    }
                },
                "results": results,
            }
        ],
    }


def render_evidence_pack(
    subject: str, assertions: list[Assertion], *, role: str | None = None
) -> dict[str, Any]:
    """Render a subject's evidence pack. When ``role`` is given, the pack is the provider or deployer
    variant: it names the role and carries every assertion for that subject with its evidence
    pointers, so the pack is complete for the role's controls (SPEC §9.4, A4)."""
    pack: dict[str, Any] = {
        "subject": subject,
        "assertions": [
            {
                "control": a.control,
                "outcome": a.outcome,
                "mode": a.mode,
                "evidence": sorted({e.ref for e in a.evidence}),
                **({"crosswalk": a.crosswalk} if a.crosswalk else {}),
            }
            for a in assertions
        ],
        "evidence": sorted({e.ref for a in assertions for e in a.evidence}),
    }
    if role is not None:
        pack["role"] = role
    return pack


_NON_DETERMINATION = (
    "This statement reports conformance to the named catalog as evaluated by the Agent Conformance "
    "Engine over the named evidence and observation window. It is not a legal compliance "
    "determination."
)
_CONDUCT_LINE = (
    "Over the observation window, the named subjects acted within their declared boundaries and on "
    "authorised instructions as evidenced by the Conduct overlay controls listed."
)
_STATEMENT_OUTCOMES = (
    "conformant",
    "non-conformant",
    "partial",
    "not_applicable",
    "not_assessed",
    "insufficient_evidence",
)


def render_public_statement(
    assertions: list[Assertion],
    *,
    catalogs: list[str] | None = None,
    statement_date: str | None = None,
    deviations: list[str] | None = None,
) -> str:
    """Render the optional public conformance statement (SPEC §9.5): scope, catalog and date, a
    six-outcome summary per family, accepted deviations by control id only, the Conduct line when the
    overlay is present, how affected persons raise concerns, and the fixed non-determination line."""
    subjects = sorted({a.subject for a in assertions})
    families: dict[str, dict[str, int]] = {}
    for assertion in assertions:
        family = assertion.control.split("-", 1)[0]
        row = families.setdefault(family, dict.fromkeys(_STATEMENT_OUTCOMES, 0))
        if assertion.outcome in row:
            row[assertion.outcome] += 1
    lines = ["# Public conformance statement", ""]
    lines.append("## Scope")
    lines.append("Subjects: " + ", ".join(subjects) if subjects else "Subjects: (none)")
    lines.append(
        f"Catalogs: {', '.join(catalogs)}" if catalogs else "Catalogs: (unspecified)"
    )
    lines.append(f"Date: {statement_date}" if statement_date else "Date: (unspecified)")
    lines += [
        "",
        "## Outcomes by family",
        "",
        "| Family | " + " | ".join(_STATEMENT_OUTCOMES) + " |",
    ]
    lines.append("|---|" + "|".join("---" for _ in _STATEMENT_OUTCOMES) + "|")
    for family in sorted(families):
        row = families[family]
        lines.append(
            f"| {family} | "
            + " | ".join(str(row[o]) for o in _STATEMENT_OUTCOMES)
            + " |"
        )
    lines += ["", "## Accepted deviations"]
    lines.append(", ".join(sorted(deviations)) if deviations else "None.")
    if "CND" in families:
        lines += ["", "## Conduct", _CONDUCT_LINE]
    lines += [
        "",
        "## Affected persons",
        "Affected persons may obtain an explanation of a decision and raise concerns through the "
        "deployer's published contact channel (EU AI Act Arts. 26(11), 85, 86).",
        "",
        "## Basis",
        _NON_DETERMINATION,
    ]
    return "\n".join(lines) + "\n"


def _catalog_refs(catalogs: list[Catalog]) -> list[dict[str, str]]:
    """Each catalog's id, version, and its real, content-derived provenance digest -- never a fixed
    or all-zero constant -- the same recomputation a reader can independently verify against the
    catalog directory (SPEC §14.5 CP-3)."""
    return [
        {
            "id": c.id,
            "version": c.version,
            "digest": catalog_provenance_digest(c.directory),
        }
        for c in catalogs
    ]


def build_manifest(
    *,
    bundle_digest: str,
    catalogs: list[Catalog],
    outputs: dict[str, str],
    operator: str,
    invocation: list[str],
    supersedes: list[str],
    report_language: str = messages.DEFAULT_LANGUAGE,
    limitations: list[str] | None = None,
    started_at: str | None = None,
) -> dict[str, Any]:
    package_digest = _package_digest()
    host = hashlib.sha256(
        f"{platform.system()}|{platform.machine()}|{package_digest}".encode()
    ).hexdigest()
    catalog_refs = _catalog_refs(catalogs)
    manifest: dict[str, Any] = {
        "agentce_manifest_version": 1,
        "engine": {
            "impl": ENGINE_NAME,
            "version": __version__,
            "spec_version": SPEC_VERSION,
            "package_digest": package_digest,
        },
        "inputs": {"bundle_digest": bundle_digest, "catalogs": catalog_refs},
        "outputs": outputs,
        "run": {
            "started_at": started_at or _now(),
            "operator": operator,
            "host_fingerprint": "sha256:" + host,
            "invocation": invocation,
            "report_language": report_language,
        },
    }
    if supersedes:
        manifest["supersedes"] = supersedes
    if limitations:
        # What the run could not verify but was told to use anyway (SPEC §8.7
        # ``--allow-unverified-catalog``): recorded here so a reader of the report knows, and absent
        # from an ordinary run's manifest.
        manifest["limitations"] = limitations
    return manifest


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


#: The fixed statement text every claim carries (SPEC §9.1; ``claim.schema.json``'s ``statement`` const).
_CLAIM_STATEMENT = (
    "This report states conformance to the named catalogs as evaluated by the named engine over "
    "the named evidence. It is not a legal compliance determination."
)


def _build_claim(
    assertions: list[Assertion],
    *,
    catalogs: list[Catalog],
    operator: str,
    limitations: list[str] | None = None,
) -> dict[str, Any]:
    """The conformance claim body (SPEC §9.1, ``claim.schema.json``): the scoped, content-addressed
    statement of what was assessed, against what catalogs, that ``agentce sign`` attaches a claimant
    or assessor signature to. Every field is real data already computed for this run -- no field is
    fabricated -- so the claim is ready for a human claimant to review and sign, never pre-signed by
    the engine itself."""
    subjects = sorted({a.subject for a in assertions})
    starts = [a.window[0] for a in assertions]
    ends = [a.window[1] for a in assertions]
    body: dict[str, Any] = {
        "subjects": [{"id": s, "role": "both"} for s in subjects],
        "observation_window": {"start": min(starts), "end": max(ends)},
        "catalogs": _catalog_refs(catalogs),
        "engine": {
            "impl": ENGINE_NAME,
            "version": __version__,
            "spec_version": SPEC_VERSION,
        },
        "claimant": {"org": operator},
        "statement": _CLAIM_STATEMENT,
    }
    if limitations:
        # ``limitations`` is optional in claim.schema.json, so an ordinary claim omits it; a claim
        # produced over an unverified catalog carries what the claimant is signing over (SPEC §8.7).
        body["limitations"] = limitations
    claim_id = "sha256:" + hashlib.sha256(canonical.canonicalize(body)).hexdigest()
    return {"claim_id": claim_id, **body}


#: Every token `assess --emit` and `write_report`'s `emit` accept. The first six are the formats
#: `report --format` has always rendered one at a time; the last four are new (SPEC §9).
EMIT_FORMATS = (
    "md",
    "html",
    "oscal",
    "sarif",
    "public",
    "pack",
    "junit",
    "csv",
    "oscal_xml",
    "pdf",
)
#: What an `emit`-less `write_report` call renders (the engine's original fixed bundle, predating
#: `--emit`): every non-regression test pins this set exactly.
_LEGACY_EMIT = frozenset({"md", "html", "oscal", "sarif", "pack"})


def write_report(
    out_dir: Path,
    assertions: list[Assertion],
    *,
    bundle_digest: str,
    catalogs: list[Catalog],
    operator: str = "unknown",
    invocation: list[str] | None = None,
    supersedes: list[str] | None = None,
    report_language: str = messages.DEFAULT_LANGUAGE,
    limitations: list[str] | None = None,
    emit: frozenset[str] | None = None,
) -> dict[str, Any]:
    """Write every report artifact for ``assertions`` and return the reproducibility manifest.

    ``limitations`` names what the run was told to use without being able to verify it (SPEC §8.7);
    the manifest and the claim both carry it, and both omit it on an ordinary run.

    ``emit`` selects which report renderings to write, from :data:`EMIT_FORMATS`. ``None`` (the
    default) reproduces the engine's original fixed bundle exactly -- ``report.md``, ``report.html``,
    ``oscal-ar.json``, ``results.sarif``, and ``packs/<subject>/pack.json`` -- unchanged, byte for
    byte, regardless of anything added since. A caller that passes a set renders only the formats
    named in it (``frozenset()`` renders none of them); ``assertions.json``, ``claim.json``, and
    ``manifest.json`` are never gated by ``emit`` -- they are the run's structural core, not a
    rendering choice."""
    check_dc5(
        assertions
    )  # DC-5: refuse a supporting verdict without an evidence pointer
    out_dir.mkdir(parents=True, exist_ok=True)
    outputs: dict[str, str] = {}
    started_at = (
        _now()
    )  # read once; also embedded in report.pdf and manifest.run.started_at

    def write_json(name: str, obj: Any) -> None:
        data = canonical.canonicalize(obj)
        (out_dir / name).write_bytes(data)
        outputs[name] = _digest_bytes(data)

    def write_text(name: str, text: str) -> None:
        data = text.encode("utf-8")
        (out_dir / name).write_bytes(data)
        outputs[name] = _digest_bytes(data)

    def write_bytes(name: str, data: bytes) -> None:
        (out_dir / name).write_bytes(data)
        outputs[name] = _digest_bytes(data)

    counts = aggregate(assertions)
    write_github_step_summary(
        assertions,
        counts,
        language=report_language,
        catalogs=catalogs,
        invocation=invocation,
    )

    # `emit is None`: the legacy fixed bundle -- exactly the five formats every run always rendered
    # before `--emit` existed (never the four new ones, and never `public`, which was previously
    # reachable only through `report --format public`). `emit` given: only the named formats render.
    def wants(token: str) -> bool:
        if emit is None:
            return token in _LEGACY_EMIT
        return token in emit

    write_json("assertions.json", [a.to_json() for a in assertions])

    if wants("md"):
        write_text(
            "report.md",
            render_report_md(
                assertions,
                counts,
                language=report_language,
                catalogs=catalogs,
                invocation=invocation,
            ),
        )
    if wants("html"):
        write_text(
            "report.html",
            render_report_html(
                assertions,
                counts,
                language=report_language,
                catalogs=catalogs,
                invocation=invocation,
            ),
        )

    oscal_doc: dict[str, Any] | None = None
    if wants("oscal") or wants("oscal_xml"):
        oscal_doc = render_oscal(assertions)
    if wants("oscal") and oscal_doc is not None:
        write_json("oscal-ar.json", oscal_doc)
    if wants("oscal_xml") and oscal_doc is not None:
        write_text("oscal-ar.xml", render_oscal_xml(oscal_doc))

    if wants("sarif"):
        write_json("results.sarif", render_sarif(assertions, catalogs=catalogs))
    if wants("junit"):
        write_text("report.junit.xml", render_junit(assertions))
    if wants("csv"):
        write_text("report.csv", render_csv(assertions))
    if wants("pdf"):
        write_bytes(
            "report.pdf",
            render_pdf(
                assertions, counts, started_at=started_at, language=report_language
            ),
        )
    if wants("public"):
        write_text(
            "public-statement.md",
            render_public_statement(
                assertions,
                catalogs=[f"{c.id}@{c.version}" for c in catalogs],
            ),
        )

    if wants("pack"):
        subjects = sorted({a.subject for a in assertions})
        for subject in subjects:
            pack = render_evidence_pack(
                subject, [a for a in assertions if a.subject == subject]
            )
            rel = f"packs/{_safe(subject)}/pack.json"
            data = canonical.canonicalize(pack)
            path = out_dir / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            outputs[rel] = _digest_bytes(data)

    if assertions:
        # claim.json is unsigned here (SPEC §9.1: the engine never signs its own claim); it exists
        # so the already-built `agentce sign --as claimant|assessor` can reach and sign a real run.
        claim = _build_claim(
            assertions, catalogs=catalogs, operator=operator, limitations=limitations
        )
        (out_dir / "claim.json").write_text(
            json.dumps(claim, sort_keys=True, indent=2) + "\n", encoding="utf-8"
        )

    manifest = build_manifest(
        bundle_digest=bundle_digest,
        catalogs=catalogs,
        outputs=outputs,
        operator=operator,
        invocation=invocation or [],
        supersedes=supersedes or [],
        report_language=report_language,
        limitations=limitations,
        started_at=started_at,
    )
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, sort_keys=True, indent=2), encoding="utf-8"
    )
    return manifest


def _load_schema(name: str) -> dict[str, Any]:
    text = (
        resources.files("agentce.data.schemas")
        .joinpath(f"{name}.schema.json")
        .read_text("utf-8")
    )
    parsed: dict[str, Any] = json.loads(text)
    return parsed


def _pattern_with_regex_module(
    validator: Any, patrn: str, instance: Any, schema: dict[str, Any]
) -> Any:
    """`jsonschema`'s built-in ``pattern`` keyword compiles with the standard-library ``re`` module,
    which cannot compile a Unicode property escape (``\\p{L}``); the vendored NIST OSCAL schema's
    ``TokenDatatype`` uses one. This overrides the keyword to match with the third-party ``regex``
    module instead, which supports it, so the vendored schema is validated unmodified."""
    if validator.is_type(instance, "string") and not regex.search(patrn, instance):
        yield jsonschema.exceptions.ValidationError(
            f"{instance!r} does not match {patrn!r}"
        )


def _pattern_properties_with_regex_module(
    validator: Any,
    pattern_properties: dict[str, Any],
    instance: Any,
    schema: dict[str, Any],
) -> Any:
    if not validator.is_type(instance, "object"):
        return
    for pattern, subschema in pattern_properties.items():
        for key, value in instance.items():
            if regex.search(pattern, key):
                yield from validator.descend(
                    value, subschema, path=key, schema_path=pattern
                )


#: A draft-07 validator identical to `jsonschema`'s, except that ``pattern``/``patternProperties``
#: match with the ``regex`` module so it can validate the vendored NIST OSCAL 1.1.2 schema's
#: `TokenDatatype` (SPEC §9, §7.3; see ``spec/report/vendor/README.md``). Schema-checking (which would
#: itself try to compile that pattern with stdlib `re`) is deliberately skipped by never calling
#: `check_schema` -- only the pinned, already-vendored schema is ever passed to it.
_OscalArValidator = jsonschema.validators.extend(
    jsonschema.Draft7Validator,
    {
        "pattern": _pattern_with_regex_module,
        "patternProperties": _pattern_properties_with_regex_module,
    },
)


def validate_oscal_ar_nist(document: dict[str, Any]) -> list[str]:
    """Validate ``document`` against the vendored NIST OSCAL 1.1.2 assessment-results schema
    (SPEC §9: "validate offline against vendored schemas"); return a list of problems, empty on
    success."""
    schema = _load_schema("oscal-assessment-results-nist-1.1.2")
    errors = sorted(
        _OscalArValidator(schema).iter_errors(document),
        key=lambda e: list(e.absolute_path),
    )
    problems = []
    for error in errors:
        location = "/".join(str(p) for p in error.absolute_path) or "<root>"
        problems.append(f"{location}: {error.message}")
    return problems


def validate_sarif_2_1_0(document: dict[str, Any]) -> list[str]:
    """Validate ``document`` against the vendored, real OASIS SARIF 2.1.0 schema (SPEC §9: "validate
    offline against vendored schemas"), not just the bounded local profile; return a list of problems,
    empty on success."""
    schema = _load_schema("sarif-2.1.0")
    errors = sorted(
        jsonschema.Draft4Validator(schema).iter_errors(document),
        key=lambda e: list(e.absolute_path),
    )
    problems = []
    for error in errors:
        location = "/".join(str(p) for p in error.absolute_path) or "<root>"
        problems.append(f"{location}: {error.message}")
    return problems


def _validate_xml_wellformed(path: Path) -> list[str]:
    try:
        ET.parse(path)
    except ET.ParseError as exc:
        return [f"{path.name}: invalid XML ({exc})"]
    return []


def _validate_csv(path: Path) -> list[str]:
    """A ``report.csv`` is valid iff it parses as CSV, carries every column in
    :data:`CSV_COLUMNS`, and every row has the same field count as the header -- enough to accept a
    genuine run's output and reject garbage (a non-CSV-structured file either fails to parse, is
    missing the required columns, or has ragged rows)."""
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            rows = list(csv.reader(handle))
    except (OSError, csv.Error, UnicodeDecodeError) as exc:
        return [f"{path.name}: invalid CSV ({exc})"]
    if not rows:
        return [f"{path.name}: empty CSV (no header row)"]
    header = rows[0]
    problems = [
        f"{path.name}: missing column {column!r}"
        for column in CSV_COLUMNS
        if column not in header
    ]
    width = len(header)
    problems += [
        f"{path.name}: row {i} has {len(row)} field(s), expected {width}"
        for i, row in enumerate(rows[1:], start=2)
        if len(row) != width
    ]
    return problems


#: The optional new artifacts (SPEC §9): validated when a run's `--emit` produced them, unlike
#: `_ARTIFACT_SCHEMAS`'s entries, which every run has always written and whose absence is itself a
#: problem.
_OPTIONAL_ARTIFACTS = ("report.junit.xml", "oscal-ar.xml", "report.csv")


def validate_report(out_dir: Path) -> list[str]:
    """Validate every emitted artifact against its vendored schema; return a list of problems."""
    problems: list[str] = []
    for filename, schema_name in _ARTIFACT_SCHEMAS.items():
        path = out_dir / filename
        if not path.is_file():
            problems.append(f"{filename}: missing")
            continue
        try:
            instance = json.loads(path.read_text(encoding="utf-8"))
            jsonschema.validate(instance, _load_schema(schema_name))
        except json.JSONDecodeError as exc:
            problems.append(f"{filename}: invalid JSON ({exc.msg})")
        except jsonschema.ValidationError as exc:
            problems.append(f"{filename}: {exc.message}")
            continue
        if filename == "oscal-ar.json":
            problems += [
                f"oscal-ar.json (NIST OSCAL 1.1.2): {p}"
                for p in validate_oscal_ar_nist(instance)
            ]
        if filename == "results.sarif":
            problems += [
                f"results.sarif (OASIS SARIF 2.1.0): {p}"
                for p in validate_sarif_2_1_0(instance)
            ]
    for filename in ("report.md", "report.html"):
        path = out_dir / filename
        if not path.is_file() or not path.read_text(encoding="utf-8").strip():
            problems.append(f"{filename}: missing or empty")
    for filename in _OPTIONAL_ARTIFACTS:
        path = out_dir / filename
        if not path.is_file():
            continue  # optional: only present, and only validated, when `--emit` requested it
        if filename == "report.csv":
            problems += _validate_csv(path)
        else:
            problems += _validate_xml_wellformed(path)
    return problems
