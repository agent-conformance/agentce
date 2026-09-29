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
import unicodedata
import uuid
import xml.etree.ElementTree as ET
from importlib import resources
from pathlib import Path
from typing import Any

import jsonschema
import regex
import yaml

from . import (
    ENGINE_NAME,
    SPEC_VERSION,
    __version__,
    bundled,
    canonical,
    i18n_format,
    messages,
    templating,
    verdict,
)
from .activity import DENIED_KINDS, RECORDER_CLASSES, summarize_activity
from .assertions import Assertion, aggregate, check_dc5
from .assess import index_by_subject, requirement_met
from .catalog import Catalog, ControlSpec, catalog_provenance_digest
from .profile import Profile, Subject
from .project import (
    blind_spots_by_subject,
    compute_project_view,
    no_population_by_subject,
)
from .security_view import FRAMEWORK_VERSIONS, compute_security_view

#: The empty, honest answer for a caller with no assertions to explain (write_report's own
#: docstring): never recomputed from an empty `Profile()`, unlike `activity`'s fallback -- an empty
#: profile has zero subjects, which would fail `compute_blind_spots`' positional pairing immediately
#: against any non-empty `assertions` list (RFC 0008 Sec.6).
_EMPTY_BLIND_SPOTS: dict[str, Any] = {"blind_spots": [], "no_population": []}

#: Always written, regardless of `--emit`: the run's structural core (write_report's docstring).
_MANDATORY_ARTIFACT_SCHEMAS = {
    "assertions.json": "assertions",
    "manifest.json": "manifest",
    "activity.json": "activity",
    "blind-spots.json": "blind-spots",
}
#: Written only when `--emit` selects the format that produces them: validated when present,
#: skipped when a narrower `--emit` legitimately left them unwritten.
_OPTIONAL_ARTIFACT_SCHEMAS = {
    "oscal-ar.json": "oscal-assessment-results",
    "results.sarif": "results-sarif",
    "project.json": "project",
    "security.json": "security",
}
_ARTIFACT_SCHEMAS = {**_MANDATORY_ARTIFACT_SCHEMAS, **_OPTIONAL_ARTIFACT_SCHEMAS}
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


def _hash_safe(text: str) -> str:
    """``text`` with every lone (unpaired) UTF-16 surrogate replaced by U+FFFD, and nothing else
    changed: the narrowest possible fix for ``str.encode`` raising ``UnicodeEncodeError`` on a lone
    surrogate before ``uuid.uuid5`` ever reaches it (SPEC §7 injection hardening;
    `contracts/P18-18.21.md`'s Design section). Used only for a hash input, never for a rendered
    field, so it must not be lossy or lossless-truncating like `sanitize_for_markdown` (whose
    200-character cap and lookalike substitutions would collide two distinct long or punctuated
    control/subject ids onto the same UUID) -- for every other character it must match TypeScript's
    own UUID/fingerprint hashing exactly, which sanitises nothing at all (Node's `Buffer.from` writes
    the 3-byte U+FFFD sequence for an unpaired surrogate, the same substitution this function makes),
    so Python and TypeScript hash the same non-surrogate control/subject to the same UUID (SPEC C5
    byte-identity). Java's `String.getBytes(UTF_8)` substitutes `?` for an unpaired surrogate instead
    of U+FFFD, a pre-existing, narrower divergence from both other engines for that one input class,
    unrelated to and not fixed by this function (`contracts/P18-18.21.md`'s Dispositions)."""
    return "".join("�" if 0xD800 <= ord(ch) <= 0xDFFF else ch for ch in text)


def _uuid_raw(*parts: str) -> str:
    """As `_uuid`, but ``parts`` are already hash-safe (`_hash_safe`): for a caller that needs the
    same hash-safe control/subject in more than one UUID, converting once and passing it here avoids
    repeating that work per UUID."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "agentce:" + ":".join(parts)))


def _uuid(*parts: str) -> str:
    """A UUID over ``parts``, which can carry a catalog- or evidence-derived control/subject id: hash
    the hash-safe (`_hash_safe`, never the raw canonical-JSON, and never the Markdown/HTML-sanitised)
    form, since ``str.encode`` raises on a lone surrogate before any Markdown/HTML sanitiser is ever
    reached (SPEC §7 injection hardening; `contracts/P18-18.21.md`'s Design section). The OSCAL/SARIF
    document's own ``control``/``subject`` fields are written separately from their raw, unsanitised
    value (C5 byte-identity) -- only this hash input is protected, and only from the one thing that
    would otherwise crash it."""
    return _uuid_raw(*(_hash_safe(p) for p in parts))


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


def _activity_tally_text(
    counts: dict[str, int], label_of: dict[str, str] | None = None
) -> str:
    """``label 3, label 2`` for every nonzero count, in the dict's (fixed) key order; ``0`` when
    every count is zero -- shared by the Markdown, HTML, and terminal renderings. ``label_of``
    translates a key to its catalogue label; a key with no translation (the effect classes, which
    are stable identifiers, not prose) stands for itself."""
    parts = [f"{(label_of or {}).get(key, key)} {n}" for key, n in counts.items() if n]
    return ", ".join(parts) if parts else "0"


def _activity_undeclared_lines(
    undeclared: dict[str, list[str]], cat: dict[str, str]
) -> list[str]:
    if not (undeclared["tools"] or undeclared["models"] or undeclared["agents"]):
        return [cat["report.activity_none_undeclared"]]
    lines = []
    if undeclared["agents"]:
        lines.append(
            f"{cat['report.activity_undeclared_agents_label']}: "
            + ", ".join(sanitize_for_markdown(name) for name in undeclared["agents"])
        )
    if undeclared["tools"]:
        lines.append(
            f"{cat['report.activity_undeclared_tools_label']}: "
            + ", ".join(sanitize_for_markdown(name) for name in undeclared["tools"])
        )
    if undeclared["models"]:
        lines.append(
            f"{cat['report.activity_undeclared_models_label']}: "
            + ", ".join(sanitize_for_markdown(name) for name in undeclared["models"])
        )
    return lines


def _activity_rows(
    activity: dict[str, Any], cat: dict[str, str]
) -> list[tuple[str, str]]:
    """``(label, value)`` for every counted-facts row -- the one place the row set and order is
    decided, shared by the Markdown and HTML renderings. Agent, model, and tool names are
    event-derived strings (SPEC §7 injection hardening), escaped with :func:`sanitize_for_markdown`
    before joining so a hostile name (embedded newlines) can never start a new Markdown/terminal line
    -- this section renders before the verdict."""
    recorder_labels = {
        k: cat[f"report.activity_recorder_{k}"] for k in RECORDER_CLASSES
    }
    denied_labels = {k: cat[f"report.activity_denied_{k}"] for k in DENIED_KINDS}
    agents = [sanitize_for_markdown(a) for a in activity["agents"]]
    return [
        (
            cat["report.activity_agents_label"],
            ", ".join(agents) if agents else cat["report.activity_none_agents"],
        ),
        (
            cat["report.activity_models_label"],
            ", ".join(sanitize_for_markdown(m["name"]) for m in activity["models"])
            or "0",
        ),
        (
            cat["report.activity_tools_label"],
            ", ".join(sanitize_for_markdown(t["name"]) for t in activity["tools"])
            or "0",
        ),
        (
            cat["report.activity_actions_label"],
            _activity_tally_text(activity["actions_by_effect_class"]),
        ),
        (
            cat["report.activity_approvals_label"],
            _activity_tally_text(activity["approvals_by_recorder"], recorder_labels),
        ),
        (
            cat["report.activity_denied_label"],
            _activity_tally_text(activity["denied_or_blocked"], denied_labels),
        ),
    ]


def _activity_md(activity: dict[str, Any], cat: dict[str, str]) -> list[str]:
    """The lines that lead the report body (before the verdict, SPEC's evidence-first framing):
    what the records show your agents did, regardless of how they measure up."""
    lines = [f"## {cat['report.activity_heading']}", ""]
    lines += [f"- {label}: {value}" for label, value in _activity_rows(activity, cat)]
    lines += ["", f"### {cat['report.activity_undeclared_heading']}", ""]
    lines += [
        f"- {line}" for line in _activity_undeclared_lines(activity["undeclared"], cat)
    ]
    lines.append("")
    return lines


def _activity_html(activity: dict[str, Any], cat: dict[str, str]) -> str:
    items = "".join(
        f"<li>{html.escape(label)}: {html.escape(value)}</li>"
        for label, value in _activity_rows(activity, cat)
    )
    undeclared = "".join(
        f"<p>{html.escape(line)}</p>"
        for line in _activity_undeclared_lines(activity["undeclared"], cat)
    )
    return (
        '<section aria-labelledby="activity"><h2 id="activity">'
        f"{html.escape(cat['report.activity_heading'])}</h2><ul>{items}</ul>"
        f"<h3>{html.escape(cat['report.activity_undeclared_heading'])}</h3>{undeclared}"
        "</section>"
    )


def preset_cli_lines(
    preset: str | None,
    preset_source: str,
    formats: frozenset[str],
    catalogue: dict[str, str],
) -> list[str]:
    """The one line a run prints when ``--for`` or CI auto-detection resolved the ``--emit`` set
    (contracts/P18-18.7.md). ``preset_source`` is ``"flag"`` (an explicit ``--for <preset>``) or
    ``"ci-env"`` (the ``CI`` environment variable was detected, extending the legacy default); the
    CI-detected note never names a preset, since none ran (N-b: only ``--for`` selected one)."""
    formats_text = ", ".join(sorted(formats))
    if preset_source == "flag":
        return [
            i18n_format.format_message(
                catalogue["report.for_preset_used"], preset=preset, formats=formats_text
            )
        ]
    return [
        i18n_format.format_message(
            catalogue["report.for_ci_detected"], formats=formats_text
        )
    ]


def activity_cli_lines(
    activity: dict[str, Any], catalogue: dict[str, str]
) -> list[str]:
    """The lines a command prints for ``activity``: agents, tools, models, and anything not yet
    declared -- the same dictionary :func:`_activity_md`/:func:`_activity_html` render."""
    lines = [
        f"{label}: {value}" for label, value in _activity_rows(activity, catalogue)
    ]
    lines.append(f"{catalogue['report.activity_undeclared_heading']}:")
    lines += [
        f"  {line}"
        for line in _activity_undeclared_lines(activity["undeclared"], catalogue)
    ]
    return lines


def _blind_spot_owner_label(owner_key: str, cat: dict[str, str]) -> str:
    return cat[f"report.blind_spots_owner_{owner_key}"]


def _blind_spot_step_text(step_kind: str, owner_label: str, cat: dict[str, str]) -> str:
    return i18n_format.format_message(
        cat[f"report.blind_spots_step_{step_kind}"], owner=owner_label
    )


def _blind_spot_rows(
    blind_spots: list[dict[str, Any]], cat: dict[str, str]
) -> list[tuple[str, str]]:
    """``(label, value)`` for every blind spot, in the module's own ranked order (never re-sorted
    here): the label names the missing event/class, the value the rung, the step, the owner, and the
    counts -- the report's rendering of the evidence ladder (RFC 0008 Sec.7). ``event``/``class`` come
    from the catalog, but a ``no_population`` entry's ``subject`` can be records-derived (the
    records-folder auto-derived-profile path, RFC 0008 Sec.9) -- the same "renders right after
    activity, before the verdict" position P11 (item 18.4 rework) forged a fake verdict line through,
    so every field here is sanitised via :func:`_sanitize_field`. ``_blind_spots_html`` escapes
    independently via ``_li_items``/``html.escape`` on the same already-sanitised text, matching how
    ``_activity_rows`` escapes once for all three renderings."""
    rows: list[tuple[str, str]] = []
    for bs in blind_spots:
        owner_label = _blind_spot_owner_label(bs["owner_key"], cat)
        step = _blind_spot_step_text(bs["step_kind"], owner_label, cat)
        adapters = (
            ", ".join(_sanitize_field(a) for a in bs["supplying_adapters"])
            or cat["report.blind_spots_no_adapters"]
        )
        value = i18n_format.format_message(
            cat["report.blind_spots_row"],
            checks_unlocked=bs["checks_unlocked"],
            needed_by=bs["needed_by"],
            ladder_rung=bs["ladder_rung"],
            step=step,
            adapters=adapters,
        )
        label = f"{_sanitize_field(bs['event'])} ({_sanitize_field(bs['class'])})"
        rows.append((label, value))
    return rows


def _no_population_rows(
    no_population: list[dict[str, str]], cat: dict[str, str]
) -> list[tuple[str, str]]:
    """``(label, value)`` for every ``no_population`` entry: the one fixed, deliberately generic
    sentence (RFC 0008 Sec.4) -- no rung, owner, or step, since none is knowable for this case."""
    return [
        (
            f"{_sanitize_field(entry['control'])} on {_sanitize_field(entry['subject'])} "
            f"({_sanitize_field(entry['catalog'])}@{_sanitize_field(entry['control_version'])})",
            i18n_format.format_message(
                cat["report.blind_spots_no_population_text"],
                control=_sanitize_field(entry["control"]),
            ),
        )
        for entry in no_population
    ]


def _blind_spots_md(blind_spots: dict[str, Any], cat: dict[str, str]) -> list[str]:
    """The not-enough-evidence section (18.4/RFC 0008): what the records can't show yet, ranked by
    how many checks the one missing requirement would unlock -- right after activity and before the
    verdict (``VALUE-PROP.md``: "the first report is never empty ... the not-enough-evidence grid
    comes second")."""
    rows = _blind_spot_rows(blind_spots["blind_spots"], cat)
    no_pop_rows = _no_population_rows(blind_spots["no_population"], cat)
    lines = [f"## {cat['report.blind_spots_heading']}", ""]
    if not rows and not no_pop_rows:
        lines += [f"- {cat['report.blind_spots_none']}", ""]
        return lines
    # The label is backtick-wrapped, not just interpolated after the list marker (post-implementation
    # critic finding, closed here as a same-item follow-up): a sanitised value alone can still start
    # with `#`/`~~~`/a digit-`.` sequence that CommonMark parses as a heading, code fence, or nested
    # list when it is the first token of a list item's content -- `_neutralize`'s character
    # substitutions never touch those (deliberately: see the Design section's own reasoning for why
    # block-marker characters are not in the substitution set). A single leading backtick (matching
    # the assertions table's existing, already-safe `` `{control}` `` shape) means the payload's own
    # leading character is never the line's first content, so it can only ever be parsed as an inline
    # code span, never as a block start; `sanitize_for_markdown` already substitutes any embedded
    # backtick, so this added pair is always the only backtick on the line (never three or more, which
    # would instead risk opening a fenced code block).
    lines += [f"- `{label}`: {value}" for label, value in rows]
    if no_pop_rows:
        lines += ["", f"### {cat['report.blind_spots_no_population_heading']}", ""]
        lines += [f"- `{label}`: {value}" for label, value in no_pop_rows]
    lines.append("")
    return lines


def _li_items(rows: list[tuple[str, str]]) -> str:
    return "".join(
        f"<li><strong>{html.escape(label)}</strong>: {html.escape(value)}</li>"
        for label, value in rows
    )


def _blind_spots_html(blind_spots: dict[str, Any], cat: dict[str, str]) -> str:
    rows = _blind_spot_rows(blind_spots["blind_spots"], cat)
    no_pop_rows = _no_population_rows(blind_spots["no_population"], cat)
    if not rows and not no_pop_rows:
        body = f"<p>{html.escape(cat['report.blind_spots_none'])}</p>"
    else:
        body = f"<ul>{_li_items(rows)}</ul>"
        if no_pop_rows:
            body += (
                f"<h3>{html.escape(cat['report.blind_spots_no_population_heading'])}</h3>"
                f"<ul>{_li_items(no_pop_rows)}</ul>"
            )
    return (
        '<section aria-labelledby="blind-spots"><h2 id="blind-spots">'
        f"{html.escape(cat['report.blind_spots_heading'])}</h2>{body}</section>"
    )


def blind_spots_cli_lines(
    blind_spots: dict[str, Any], catalogue: dict[str, str]
) -> list[str]:
    """The lines a command prints for ``blind_spots``: the same dictionary
    :func:`_blind_spots_md`/:func:`_blind_spots_html` render."""
    rows = _blind_spot_rows(blind_spots["blind_spots"], catalogue)
    no_pop_rows = _no_population_rows(blind_spots["no_population"], catalogue)
    lines = [f"{catalogue['report.blind_spots_heading']}:"]
    if not rows and not no_pop_rows:
        lines.append(f"  {catalogue['report.blind_spots_none']}")
        return lines
    lines += [f"  {label}: {value}" for label, value in rows]
    if no_pop_rows:
        lines.append(f"  {catalogue['report.blind_spots_no_population_heading']}:")
        lines += [f"    {label}: {value}" for label, value in no_pop_rows]
    return lines


def _agent_dirname(subject_id: str) -> str:
    """The traversal-proof, collision-resistant directory name ``write_report`` writes a subject's
    own report under (``agents/<dirname>/``, 18.14 C3) -- the single formula both ``write_report``
    (which creates the directory) and :func:`_project_agent_view_rows`/the undeclared-agents section
    (which link to it) must share, so it is defined once here rather than duplicated at each call
    site."""
    return f"{_safe(subject_id)[:40]}-{_uuid(subject_id)[:8]}"


def _project_agent_view_rows(
    project_view: dict[str, Any],
    activity_by_subject: dict[str, dict[str, Any]],
    cat: dict[str, str],
) -> list[dict[str, str]]:
    """One row per agent, in ``project_view["agents"]``'s own order (never re-sorted here): the
    sanitised id, its report's directory name, the declared/undeclared badge, the verdict-and-counts
    cell, and the what-it-did cell -- shared by the Markdown and HTML renderings exactly like
    ``_activity_rows``."""
    rows = []
    for agent in project_view["agents"]:
        activity = activity_by_subject.get(agent["id"], {})
        counts = agent["summary"]["counts"]
        label_of = {outcome: _outcome_label(cat, outcome) for outcome in counts}
        verdict_label = cat[f"verdict.{agent['summary']['verdict']}"]
        agents_observed = (
            ", ".join(_sanitize_field(a) for a in activity.get("agents", []))
            or cat["report.activity_none_agents"]
        )
        rows.append(
            {
                "id": _sanitize_field(agent["id"]),
                "dirname": _agent_dirname(agent["id"]),
                "badge": (
                    cat["report.project_declared_badge"]
                    if agent["declared"]
                    else cat["report.project_undeclared_badge"]
                ),
                "verdict": f"{verdict_label} ({_activity_tally_text(counts, label_of)})",
                "what_it_did": i18n_format.format_message(
                    cat["report.project_what_it_did_cell"],
                    agents=agents_observed,
                    actions=_activity_tally_text(
                        activity.get("actions_by_effect_class", {})
                    ),
                ),
            }
        )
    return rows


def _project_top_gap_rows(
    top_gaps: list[dict[str, Any]], cat: dict[str, str]
) -> list[tuple[str, str]]:
    """As :func:`_blind_spot_rows`, but each row also names the agents this gap touches
    (``compute_project_view``'s own ``top_gaps``, C2) -- the project view's per-gap agent list, not
    a per-agent re-scoping."""
    rows = _blind_spot_rows(top_gaps, cat)
    return [
        (
            label,
            value
            + " "
            + i18n_format.format_message(
                cat["report.project_top_gap_agents"],
                agents=", ".join(_sanitize_field(a) for a in entry["agents"]),
            ),
        )
        for (label, value), entry in zip(rows, top_gaps, strict=True)
    ]


def render_project_md(
    project_view: dict[str, Any],
    activity_by_subject: dict[str, dict[str, Any]],
    *,
    language: str = messages.DEFAULT_LANGUAGE,
) -> str:
    """The project view (Hill 7, 18.14 C3): every agent this run assessed, side by side -- a summary
    row per agent (declared or discovered), the top gaps across the whole project naming which
    agents each touches, then the agents nobody declared, each linking to its own full report under
    ``agents/<dirname>/``."""
    cat = messages.catalogue(language)
    rows = _project_agent_view_rows(project_view, activity_by_subject, cat)
    lines = [
        f"# {cat['report.project_title']}",
        "",
        f"## {cat['report.project_heading']}",
        "",
        f"| {cat['report.project_agent_column']} | {cat['report.project_declared_column']} | "
        f"{cat['report.project_verdict_column']} | {cat['report.project_what_it_did_column']} |",
        "|---|---|---|---|",
    ]
    lines += [
        f"| [{row['id']}](agents/{row['dirname']}/report.md) | {row['badge']} | "
        f"{row['verdict']} | {row['what_it_did']} |"
        for row in rows
    ]
    lines += ["", f"## {cat['report.project_top_gaps_heading']}", ""]
    gap_rows = _project_top_gap_rows(project_view["top_gaps"], cat)
    if gap_rows:
        lines += [f"- `{label}`: {value}" for label, value in gap_rows]
    else:
        lines.append(f"- {cat['report.no_gaps']}")
    if project_view["undeclared_agents"]:
        lines += ["", f"## {cat['report.project_undeclared_heading']}", ""]
        lines += [
            f"- `{_sanitize_field(agent_id)}` -- "
            f"[{cat['report.project_agent_report_link']}]"
            f"(agents/{_agent_dirname(agent_id)}/report.md)"
            for agent_id in project_view["undeclared_agents"]
        ]
    lines.append("")
    return "\n".join(lines) + "\n"


def render_project_html(
    project_view: dict[str, Any],
    activity_by_subject: dict[str, dict[str, Any]],
    *,
    language: str = messages.DEFAULT_LANGUAGE,
) -> str:
    """As :func:`render_project_md`, rendered as the same self-contained, escaped, WCAG 2.2 AA page
    shape as :func:`render_report_html`."""
    cat = messages.catalogue(language)
    rows = _project_agent_view_rows(project_view, activity_by_subject, cat)
    title = html.escape(cat["report.project_title"])
    body_rows = "".join(
        f'<tr><td><a href="agents/{row["dirname"]}/report.html">'
        f"{html.escape(row['id'])}</a></td>"
        f"<td>{html.escape(row['badge'])}</td><td>{html.escape(row['verdict'])}</td>"
        f"<td>{html.escape(row['what_it_did'])}</td></tr>"
        for row in rows
    )
    gap_rows = _project_top_gap_rows(project_view["top_gaps"], cat)
    gaps_html = (
        f"<ul>{_li_items(gap_rows)}</ul>"
        if gap_rows
        else f"<p>{html.escape(cat['report.no_gaps'])}</p>"
    )
    undeclared_html = ""
    if project_view["undeclared_agents"]:
        items = "".join(
            f"<li>{sanitize_for_html(agent_id)} -- "
            f'<a href="agents/{_agent_dirname(agent_id)}/report.html">'
            f"{html.escape(cat['report.project_agent_report_link'])}</a></li>"
            for agent_id in project_view["undeclared_agents"]
        )
        undeclared_html = (
            '<section aria-labelledby="project-undeclared"><h2 id="project-undeclared">'
            f"{html.escape(cat['report.project_undeclared_heading'])}</h2>"
            f"<ul>{items}</ul></section>"
        )
    return (
        f'<!doctype html><html lang="{html.escape(language)}"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta http-equiv="Content-Security-Policy" '
        "content=\"default-src 'none'; style-src 'unsafe-inline'; img-src 'none'\">"
        f"<title>{title}</title><style>{_HTML_STYLE}</style></head><body>"
        f"<main><h1>{title}</h1>"
        '<section aria-labelledby="project-agents"><h2 id="project-agents">'
        f"{html.escape(cat['report.project_heading'])}</h2>"
        f"<table><caption>{html.escape(cat['report.project_heading'])}</caption>"
        f'<thead><tr><th scope="col">{html.escape(cat["report.project_agent_column"])}</th>'
        f'<th scope="col">{html.escape(cat["report.project_declared_column"])}</th>'
        f'<th scope="col">{html.escape(cat["report.project_verdict_column"])}</th>'
        f'<th scope="col">{html.escape(cat["report.project_what_it_did_column"])}</th>'
        "</tr></thead>"
        f"<tbody>{body_rows}</tbody></table></section>"
        '<section aria-labelledby="project-top-gaps"><h2 id="project-top-gaps">'
        f"{html.escape(cat['report.project_top_gaps_heading'])}</h2>{gaps_html}</section>"
        f"{undeclared_html}"
        "</main></body></html>\n"
    )


def _security_tool_access_lines(
    tool_access: list[dict[str, str]], cat: dict[str, str]
) -> list[str]:
    """One line per tool this run called -- name, server, and protocol, all event-derived strings
    sanitised before they reach Markdown/terminal output (SPEC §7 injection hardening, 18.21)."""
    if not tool_access:
        return [cat["report.security_tool_access_none"]]
    return [
        f"{_sanitize_field(t['name'])} ({_sanitize_field(t['server'])}, "
        f"{_sanitize_field(t['protocol'])})"
        for t in tool_access
    ]


def _security_effect_class_text(actions_by_effect_class: dict[str, int]) -> str:
    """``_activity_tally_text`` over the same counts, ``irreversible`` moved first (the security
    reader's own priority) -- every other key keeps its existing order."""
    reordered = {
        "irreversible": actions_by_effect_class.get("irreversible", 0),
        **{k: v for k, v in actions_by_effect_class.items() if k != "irreversible"},
    }
    return _activity_tally_text(reordered)


def _security_drift_lines(
    drift: dict[str, list[str]], cat: dict[str, str]
) -> list[str]:
    """As :func:`_activity_undeclared_lines`, restricted to the two fields the security view's
    ``drift`` carries (no ``agents`` key -- this view is a whole-run posture, not a per-agent one)."""
    if not (drift["tools"] or drift["models"]):
        return [cat["report.security_drift_none"]]
    lines = []
    if drift["tools"]:
        lines.append(
            f"{cat['report.activity_undeclared_tools_label']}: "
            + ", ".join(sanitize_for_markdown(name) for name in drift["tools"])
        )
    if drift["models"]:
        lines.append(
            f"{cat['report.activity_undeclared_models_label']}: "
            + ", ".join(sanitize_for_markdown(name) for name in drift["models"])
        )
    return lines


def _security_citation_text(entry: dict[str, Any], cat: dict[str, str]) -> str:
    """One clause citation with its framework's version (Role-views' "standard, catalog, version and
    clause" rule) -- delegates the "(clause reference unverified)" label to ``_crosswalk_text``
    rather than re-checking the ``verified`` flag a second time."""
    version = FRAMEWORK_VERSIONS.get(entry["framework"], "")
    framework = (
        f"{entry['framework']} {version}".strip() if version else entry["framework"]
    )
    return _crosswalk_text({**entry, "framework": framework}, cat)


def _security_citation_rows(
    citations: list[dict[str, Any]], cat: dict[str, str]
) -> list[tuple[str, str]]:
    """``(control, citation text)`` for every ``standards_citations`` entry, in the view's own order
    (already deduplicated and sorted by :func:`agentce.security_view.compute_security_view`) -- the
    control id is the row's check-id link (Role-views' own requirement)."""
    return [
        (
            _sanitize_field(entry["control"]),
            sanitize_for_markdown(_security_citation_text(entry, cat)),
        )
        for entry in citations
    ]


def _security_enforcement_rows(
    enforcement: dict[str, dict[str, int]], cat: dict[str, str]
) -> list[tuple[str, str]]:
    """``(label, value)`` for the approvals/denied rows -- the one place the label set is decided,
    shared by the Markdown and HTML renderings (as :func:`_activity_rows`)."""
    recorder_labels = {
        k: cat[f"report.activity_recorder_{k}"] for k in RECORDER_CLASSES
    }
    denied_labels = {k: cat[f"report.activity_denied_{k}"] for k in DENIED_KINDS}
    return [
        (
            cat["report.activity_approvals_label"],
            _activity_tally_text(enforcement["approvals_by_recorder"], recorder_labels),
        ),
        (
            cat["report.activity_denied_label"],
            _activity_tally_text(enforcement["denied_or_blocked"], denied_labels),
        ),
    ]


def render_security_md(
    security: dict[str, Any], *, language: str = messages.DEFAULT_LANGUAGE
) -> str:
    """The security view (18.16): tool access, actions by effect class (irreversible leads),
    enforcement-point evidence, drift, then each cited control's OWASP agentic (ASI)/MITRE ATLAS/OWASP
    Agent Control Standard clauses -- run-level facts only (SPEC §7.3, no per-action join exists)."""
    cat = messages.catalogue(language)
    lines = [
        f"# {cat['report.security_title']}",
        "",
        cat["report.security_intro"],
        "",
        f"## {cat['report.security_tool_access_heading']}",
        "",
    ]
    lines += [
        f"- {line}"
        for line in _security_tool_access_lines(security["tool_access"], cat)
    ]
    lines += [
        "",
        f"## {cat['report.security_effect_class_heading']}",
        "",
        f"- {_security_effect_class_text(security['actions_by_effect_class'])}",
        "",
        f"## {cat['report.security_enforcement_heading']}",
        "",
        f"- {cat['report.security_enforcement_scope_note']}",
    ]
    lines += [
        f"- {label}: {value}"
        for label, value in _security_enforcement_rows(
            security["enforcement_point_evidence"], cat
        )
    ]
    lines += [
        "",
        f"## {cat['report.security_drift_heading']}",
        "",
    ]
    lines += [f"- {line}" for line in _security_drift_lines(security["drift"], cat)]
    lines += ["", f"## {cat['report.security_citations_heading']}", ""]
    citation_rows = _security_citation_rows(security["standards_citations"], cat)
    if citation_rows:
        lines += [f"- `{control}`: {text}" for control, text in citation_rows]
    else:
        lines.append(f"- {cat['report.security_citations_none']}")
    lines.append("")
    return "\n".join(lines) + "\n"


def render_security_html(
    security: dict[str, Any], *, language: str = messages.DEFAULT_LANGUAGE
) -> str:
    """As :func:`render_security_md`, rendered as the same self-contained, escaped, WCAG 2.2 AA page
    shape as :func:`render_report_html`."""
    cat = messages.catalogue(language)
    title = html.escape(cat["report.security_title"])
    tool_items = "".join(
        f"<li>{sanitize_for_html(line)}</li>"
        for line in _security_tool_access_lines(security["tool_access"], cat)
    )
    enforcement_rows = _security_enforcement_rows(
        security["enforcement_point_evidence"], cat
    )
    enforcement_items = "".join(
        f"<p>{html.escape(label)}: {html.escape(value)}</p>"
        for label, value in enforcement_rows
    )
    drift_items = "".join(
        f"<li>{sanitize_for_html(line)}</li>"
        for line in _security_drift_lines(security["drift"], cat)
    )
    citation_rows = _security_citation_rows(security["standards_citations"], cat)
    citations_html = (
        f"<ul>{_li_items(citation_rows)}</ul>"
        if citation_rows
        else f"<p>{html.escape(cat['report.security_citations_none'])}</p>"
    )
    return (
        f'<!doctype html><html lang="{html.escape(language)}"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta http-equiv="Content-Security-Policy" '
        "content=\"default-src 'none'; style-src 'unsafe-inline'; img-src 'none'\">"
        f"<title>{title}</title><style>{_HTML_STYLE}</style></head><body>"
        f"<main><h1>{title}</h1><p>{html.escape(cat['report.security_intro'])}</p>"
        '<section aria-labelledby="security-tool-access"><h2 id="security-tool-access">'
        f"{html.escape(cat['report.security_tool_access_heading'])}</h2><ul>{tool_items}</ul></section>"
        '<section aria-labelledby="security-effect-class"><h2 id="security-effect-class">'
        f"{html.escape(cat['report.security_effect_class_heading'])}</h2>"
        f"<p>{html.escape(_security_effect_class_text(security['actions_by_effect_class']))}</p></section>"
        '<section aria-labelledby="security-enforcement"><h2 id="security-enforcement">'
        f"{html.escape(cat['report.security_enforcement_heading'])}</h2>"
        f"<p>{html.escape(cat['report.security_enforcement_scope_note'])}</p>"
        f"{enforcement_items}</section>"
        '<section aria-labelledby="security-drift"><h2 id="security-drift">'
        f"{html.escape(cat['report.security_drift_heading'])}</h2><ul>{drift_items}</ul></section>"
        '<section aria-labelledby="security-citations"><h2 id="security-citations">'
        f"{html.escape(cat['report.security_citations_heading'])}</h2>{citations_html}</section>"
        "</main></body></html>\n"
    )


def _reproduce_command(invocation: list[str] | None) -> str:
    return "agentce " + " ".join(invocation) if invocation else "agentce quickstart"


def _lenses_text() -> str:
    """The lenses a run can choose, the default marked and the way to pick another named -- so no
    report presents one standard as the only option."""
    lenses = [
        f"{lens} (default)" if lens == bundled.DEFAULT_LENS else lens
        for lens in bundled.base_lenses()
    ]
    return f"{', '.join(lenses)}; choose one with --catalog <id@version>"


def _provenance_md(catalogs: list[str], invocation: list[str] | None) -> list[str]:
    """The provenance line every report body carries: engine version, catalog(s), the lenses
    available, reproduce command (SPEC §9.1) -- so a reader of the report file alone, without opening
    manifest.json, can see what produced it and how to redo it."""
    return [
        "## Provenance",
        "",
        f"- Engine: {ENGINE_NAME} {__version__}",
        "- Catalog: "
        + (
            ", ".join(sanitize_for_markdown(c) for c in catalogs)
            if catalogs
            else "(none)"
        ),
        f"- Lenses available: {_lenses_text()}",
        f"- Reproduce: `{_reproduce_command(invocation)}`",
        "",
    ]


def _finding_md(
    a: Assertion, spec: ControlSpec | None, cat: dict[str, str]
) -> list[str]:
    """One finding: its control title, outcome, crosswalk citation, and -- when present -- the
    evidence pointers, the offending nodes from its violations, and the control's remediation
    technique (SPEC §9.3), so a reviewer sees not just the verdict but why and what to do about it.
    The title (including :func:`_finding_title`'s raw-control fallback), control id, subject id,
    evidence refs, and violation focus nodes all originate in evidence or a third-party catalog, so
    every one is sanitised before it reaches this Markdown line (SPEC §7 injection hardening)."""
    title = sanitize_for_markdown(_finding_title(a, spec))
    control = sanitize_for_markdown(a.control)
    subject = sanitize_for_markdown(a.subject)
    lines = [
        f"- **{title}** (`{control}` @ `{subject}`) -> "
        f"**{sanitize_for_markdown(_outcome_label(cat, a.outcome))}** "
        f"(rung {a.rung}, {sanitize_for_markdown(a.mode)}; "
        f"{a.population[1]}/{a.population[0]} failed)"
    ]
    lines += [
        f"  - `{sanitize_for_markdown(_crosswalk_text(e, cat))}`" for e in a.crosswalk
    ]
    if a.evidence:
        refs = ", ".join(f"`{_sanitize_field(e.ref)}`" for e in a.evidence)
        lines.append(f"  - {cat['report.evidence_label']}: {refs}")
    offending = [str(v.get("focus", "")) for v in a.violations if v.get("focus")]
    if offending:
        nodes = ", ".join(f"`{_sanitize_field(node)}`" for node in offending)
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
    activity: dict[str, Any] | None = None,
    blind_spots: dict[str, Any] | None = None,
) -> str:
    cat = messages.catalogue(language)
    by_control = _control_index(catalogs or [])
    labels = [f"{c.id}@{c.version}" for c in (catalogs or [])]
    summary = verdict.summarize(assertions)
    lines = [f"# {cat['report.title']}", ""]
    # The records lead the report (SPEC's evidence-first framing, 18.4): what happened, before how
    # it measures up. What the records can't show yet (18.5) comes right after (VALUE-PROP.md).
    if activity is not None:
        lines += _activity_md(activity, cat)
    if blind_spots is not None:
        lines += _blind_spots_md(blind_spots, cat)
    lines += _verdict_md(summary, cat)
    lines += [f"## {cat['report.summary_heading']}", ""]
    lines += [
        f"- `{sanitize_for_markdown(_outcome_label(cat, outcome))}`: {count}"
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
    catalog_text = (
        ", ".join(sanitize_for_html(c) for c in catalogs) if catalogs else "(none)"
    )
    return (
        '<section aria-labelledby="provenance"><h2 id="provenance">Provenance</h2><ul>'
        f"<li>Engine: {html.escape(ENGINE_NAME)} {html.escape(__version__)}</li>"
        f"<li>Catalog: {catalog_text}</li>"
        f"<li>Lenses available: {html.escape(_lenses_text())}</li>"
        f"<li>Reproduce: <code>{html.escape(_reproduce_command(invocation))}</code></li>"
        "</ul></section>"
    )


def _row_html(a: Assertion, spec: ControlSpec | None, cat: dict[str, str]) -> str:
    """One finding row: its title, severity, outcome, clause citation, and -- when present -- the
    evidence pointers, offending nodes, and remediation technique in a Details cell (SPEC §9.3), every
    field sanitised (via :func:`sanitize_for_html`, dropping bidi/zero-width characters as well as
    HTML-escaping) since a control title, an evidence ref, and a violation's focus node can all
    originate in evidence or a third-party catalog."""
    details: list[str] = []
    if a.evidence:
        refs = ", ".join(
            sanitize_for_html(e.ref, placeholder=_SANITIZE_EMPTY_FIELD_PLACEHOLDER)
            for e in a.evidence
        )
        details.append(f"{html.escape(cat['report.evidence_label'])}: {refs}")
    offending = [str(v.get("focus", "")) for v in a.violations if v.get("focus")]
    if offending:
        nodes = ", ".join(
            sanitize_for_html(node, placeholder=_SANITIZE_EMPTY_FIELD_PLACEHOLDER)
            for node in offending
        )
        details.append(f"{html.escape(cat['report.violations_label'])}: {nodes}")
    hints = _remediation_hints(spec)
    if hints:
        label = html.escape(cat["report.remediation_label"])
        details.append(f"{label}: {html.escape(', '.join(hints))}")
    return (
        f"<tr><td>{sanitize_for_html(_finding_title(a, spec))}</td>"
        f"<td>{sanitize_for_html(a.control)}</td><td>{sanitize_for_html(a.subject)}</td>"
        f"<td>{sanitize_for_html(spec.severity if spec else '')}</td>"
        f"<td>{sanitize_for_html(_outcome_label(cat, a.outcome))}</td>"
        f"<td>{'; '.join(sanitize_for_html(_crosswalk_text(e, cat)) for e in a.crosswalk)}</td>"
        f"<td>{'<br>'.join(details)}</td></tr>"
    )


def render_report_html(
    assertions: list[Assertion],
    counts: dict[str, int],
    *,
    language: str = messages.DEFAULT_LANGUAGE,
    catalogs: list[Catalog] | None = None,
    invocation: list[str] | None = None,
    activity: dict[str, Any] | None = None,
    blind_spots: dict[str, Any] | None = None,
) -> str:
    """Render a self-contained, escaped, WCAG 2.2 AA report page (SPEC §9.3): a strict CSP meta tag,
    no external references, one ``h1``, a ``main`` landmark, a print stylesheet, and every string that
    originates in evidence or declarations rendered as escaped text, never as markup."""
    cat = messages.catalogue(language)
    by_control = _control_index(catalogs or [])
    labels = [f"{c.id}@{c.version}" for c in (catalogs or [])]
    title = html.escape(cat["report.title"])
    summary = "".join(
        f"<li>{sanitize_for_html(_outcome_label(cat, o))}: {c}</li>"
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
        f"{_activity_html(activity, cat) if activity is not None else ''}"
        f"{_blind_spots_html(blind_spots, cat) if blind_spots is not None else ''}"
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

    observations: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    for a in ordered:
        # `control`/`subject` back both this assertion's observation and its finding, so convert each
        # once and reuse it rather than converting twice per assertion (`_uuid` would otherwise be
        # called with the same raw parts twice).
        safe_control = _hash_safe(a.control)
        safe_subject = _hash_safe(a.subject)
        # An observation backs every finding, evidence-bearing or not, so every finding resolves to
        # one (SPEC §9); only an evidence-bearing assertion's observation carries `relevant-evidence`.
        obs_uuid = _uuid_raw("observation", safe_control, safe_subject)
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

        finding: dict[str, Any] = {
            "uuid": _uuid_raw("finding", safe_control, safe_subject),
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
            "related-observations": [{"observation-uuid": obs_uuid}],
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
    evidence produce byte-identical fingerprints (no clock, host, or run counter). The hash input is
    hash-safe (`_hash_safe`, never the raw SARIF document's own field values, which stay byte-identical
    to the assertion, and never the Markdown-sanitised form, which TypeScript's and Java's own
    fingerprint hashing never apply either) since ``str.encode`` raises on a lone surrogate (SPEC §7
    injection hardening; `contracts/P18-18.21.md`)."""
    payload = "|".join(
        [
            _hash_safe(a.control),
            _hash_safe(a.subject),
            _hash_safe(a.outcome),
            _hash_safe(a.window[0]),
            _hash_safe(a.window[1]),
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
    subjects_text = ", ".join(f"`{sanitize_for_markdown(s)}`" for s in subjects)
    lines.append("Subjects: " + subjects_text if subjects else "Subjects: (none)")
    lines.append(
        "Catalogs: " + ", ".join(sanitize_for_markdown(c) for c in catalogs)
        if catalogs
        else "Catalogs: (unspecified)"
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
            f"| {sanitize_for_markdown(family)} | "
            + " | ".join(str(row[o]) for o in _STATEMENT_OUTCOMES)
            + " |"
        )
    lines += ["", "## Accepted deviations"]
    if deviations:
        lines.append(
            ", ".join(f"`{sanitize_for_markdown(d)}`" for d in sorted(deviations))
        )
    else:
        lines.append("None.")
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


#: Outcomes a remediation package reports as a finding (SPEC §7; Appendix A2 (A)): non-passing
#: verdicts only -- `conformant` has nothing to remediate, `not_applicable` was never in scope, and
#: `not_assessed` gets its own top-level array because there is no verdict yet to remediate toward.
_REMEDIATION_FINDING_OUTCOMES = frozenset(
    {"non-conformant", "partial", "insufficient_evidence"}
)
#: The fixed guardrail block every remediation finding cites by reference (never re-derived per
#: finding, so it can never drift control to control): don't over-claim, cite the clause with its
#: verification status, make the minimal change, and re-verify before considering it fixed.
_REMEDIATION_GUARDRAILS_REF = "remediation.guardrails.v1"
#: Cap applied when a record-derived string is sanitised for Markdown/terminal/HTML rendering (SPEC
#: §7 injection hardening): long enough to stay useful, short enough to bound a hostile payload.
_SANITIZE_CAP = 200
#: Shown in place of a name that neutralises to nothing (e.g. an event/agent name made entirely of
#: control characters): escaping, never erasure -- real activity is never silently dropped to "0".
_SANITIZE_EMPTY_NAME_PLACEHOLDER = "(unnamed)"
#: Shown in place of a subject id, evidence ref, or violation path that neutralises to nothing --
#: `_SANITIZE_EMPTY_NAME_PLACEHOLDER`'s "(unnamed)" reads oddly for a field that was never a name.
_SANITIZE_EMPTY_FIELD_PLACEHOLDER = "(empty)"

#: Unicode General Categories `_neutralize` replaces with a single literal space: control (`Cc`),
#: private-use (`Co`, an unpredictable glyph in most fonts), lone surrogate (`Cs`, must not crash the
#: sanitiser if one occurs), and the line/paragraph separators (`Zl`/`Zp`, which fall outside `C*`).
#: Each represents "the source intended a line break or a visible-but-unpredictable glyph here" -- a
#: space is an honest, safe stand-in.
_SPACE_LIKE_CATEGORIES = frozenset({"Cc", "Co", "Cs", "Zl", "Zp"})

#: The complete, current Unicode `Default_Ignorable_Code_Point` property, as (first, last) inclusive
#: codepoint ranges -- hard-coded once and identical across all three engines, independent of any
#: engine's own Unicode database version (`Cf` category alone misses variation selectors, CGJ, the
#: Mongolian free variation selectors, the Hangul fillers, and every reserved-for-future-use DICP
#: range). Union this with category `Cf` and drop the result entirely (never replace with a space --
#: these are, by definition, meant to be invisible/zero-width).
_DICP_RANGES: tuple[tuple[int, int], ...] = (
    (0x00AD, 0x00AD),
    (0x034F, 0x034F),
    (0x061C, 0x061C),
    (0x115F, 0x1160),
    (0x17B4, 0x17B5),
    (0x180B, 0x180F),
    (0x200B, 0x200F),
    (0x202A, 0x202E),
    (0x2060, 0x206F),
    (0x3164, 0x3164),
    (0xFE00, 0xFE0F),
    (0xFEFF, 0xFEFF),
    (0xFFA0, 0xFFA0),
    (0xFFF0, 0xFFF8),
    (0x1BCA0, 0x1BCA3),
    (0x1D173, 0x1D17A),
    (0xE0000, 0xE0FFF),
)


def _is_dicp_codepoint(codepoint: int) -> bool:
    return any(lo <= codepoint <= hi for lo, hi in _DICP_RANGES)


def has_invisible_codepoint(text: str) -> bool:
    """Whether any codepoint in ``text`` is in the drop-set :func:`_neutralize` uses (category `Cf`
    or the hard-coded ``Default_Ignorable_Code_Point`` table above) -- shared with
    ``commands._printable``'s widened trigger (SPEC §7 injection hardening), so a variation selector,
    CGJ, or Mongolian free variation selector is caught there too, not only by `str.isprintable()`
    (which treats those categories as printable)."""
    return any(
        unicodedata.category(ch) == "Cf" or _is_dicp_codepoint(ord(ch)) for ch in text
    )


def _neutralize(text: str, cap: int, placeholder: str) -> str:
    """The one shared core every sanitiser target calls first (SPEC §7 injection hardening): replace
    every `_SPACE_LIKE_CATEGORIES` codepoint or `Zs` (NBSP, ideographic space, ...) with a literal
    space, folding each run into one (one explicit, per-engine-identical definition of "collapsible
    whitespace", category-based rather than each language's own differing built-in ``\\s``/
    ``isspace()``, which let NBSP/U+3000 diverge across engines before); drop every `Cf`-or-
    ``Default_Ignorable_Code_Point`` codepoint entirely (never a space -- removing a zero-width
    character preserves the string's visual intent); trim leading and trailing whitespace; cap by
    codepoint (never a UTF-16 surrogate half, which Python's own per-codepoint string iteration
    already guarantees) at `cap`, computed here on the neutralized-but-not-yet-HTML-escaped text,
    before any HTML-entity expansion a caller applies on top; then render `placeholder` if the result
    is empty but `text` was not -- escaping, never erasure. `Cn` (unassigned) codepoints are
    deliberately not filtered: they carry no defined rendering behaviour to exploit, and the one real
    future-invisible-character risk is already closed permanently by the hard-coded, complete DICP
    table above."""
    kept: list[str] = []
    in_ws = False
    for ch in text:
        category = unicodedata.category(ch)
        if ch == " " or category in _SPACE_LIKE_CATEGORIES or category == "Zs":
            if not in_ws:
                kept.append(" ")
                in_ws = True
        elif category == "Cf" or _is_dicp_codepoint(ord(ch)):
            continue
        else:
            kept.append(ch)
            in_ws = False
    collapsed = "".join(kept).strip()
    if len(collapsed) > cap:
        collapsed = collapsed[: cap - 1] + "…"
    if not collapsed and text:
        collapsed = placeholder
    return collapsed


#: The six substitutions `sanitize_for_markdown`/`sanitize_for_terminal` apply on top of
#: `_neutralize`'s output: backtick to an inert lookalike (breaks a code-span escape); `<`/`>` to
#: fullwidth lookalikes (already-established P11 fix, breaks raw HTML); `[`/`]` to fullwidth
#: lookalikes (breaks Markdown link/image syntax, closing CommonMark's `[text](url)`/`![text](url)`
#: regardless of what surrounds them); `&` to a fullwidth lookalike (closes the HTML/XML
#: character-reference decoding path -- `&#x202E;`/`&zwj;`/etc. would otherwise survive `_neutralize`
#: as plain text and be decoded back into a live bidi/zero-width character by a downstream CommonMark
#: renderer). Deliberately not a broader "fullwidth every punctuation character" rule -- see
#: `contracts/P18-18.20.md`'s Design section for why emphasis/strikethrough/pipe-tables stay
#: unescaped (cosmetic, not a container break).
_MARKDOWN_SUBSTITUTIONS: tuple[tuple[str, str], ...] = (
    ("`", "'"),
    ("<", "‹"),
    (">", "›"),
    ("[", "［"),
    ("]", "］"),
    ("&", "＆"),
)


def sanitize_for_markdown(
    text: str,
    placeholder: str = _SANITIZE_EMPTY_NAME_PLACEHOLDER,
    cap: int = _SANITIZE_CAP,
) -> str:
    """Neutralise a record-derived string before it reaches `remediation.md`, a report's Markdown
    rendering, or the terminal (SPEC §7 injection hardening; see `contracts/P18-18.20.md`): call
    `_neutralize` first, then substitute the six Markdown-special characters `_MARKDOWN_SUBSTITUTIONS`
    names for visually similar but inert lookalikes, so the string can neither forge a heading/HTML
    element nor form link, image, autolink, or code-span syntax of its own."""
    neutralized = _neutralize(text, cap, placeholder)
    for old, new in _MARKDOWN_SUBSTITUTIONS:
        neutralized = neutralized.replace(old, new)
    return neutralized


#: `sanitize_for_terminal` is a documented alias for `sanitize_for_markdown`, not a second
#: implementation: every existing call site already computes one sanitised value and reuses it for
#: both `report.md` and the CLI lines, and terminal text never parses Markdown, so the substitutions
#: are harmless there. See `contracts/P18-18.20.md`'s Design section for the two checked, documented
#: residuals of this choice (neither a reason to change the design).
sanitize_for_terminal = sanitize_for_markdown


def sanitize_for_html(
    text: str,
    placeholder: str = _SANITIZE_EMPTY_NAME_PLACEHOLDER,
    cap: int = _SANITIZE_CAP,
) -> str:
    """Neutralise a record-derived string for HTML rendering (SPEC §7 injection hardening): call
    `_neutralize` first (closing the bidi/zero-width vector no independent `html.escape` alone
    closes), then the language's own HTML-escape on the result -- nothing else. Backtick/bracket
    characters are not HTML-special and pass through unchanged, which is correct: a literal backtick
    or bracket in HTML text content is inert."""
    return html.escape(_neutralize(text, cap, placeholder))


def _sanitize_field(text: str) -> str:
    """`sanitize_for_markdown` with the field placeholder (`_SANITIZE_EMPTY_FIELD_PLACEHOLDER`,
    "(empty)") rather than the name placeholder -- the one small convenience every call site that
    renders a subject id, evidence ref, or violation path uses, consolidating what were roughly ten
    separate `empty_placeholder=` call sites under one name."""
    return sanitize_for_markdown(text, placeholder=_SANITIZE_EMPTY_FIELD_PLACEHOLDER)


def _remediation_severity(spec: ControlSpec | None) -> str:
    return spec.severity if spec and spec.severity in _SEVERITY_LEVELS else "unrated"


def _remediation_clauses(spec: ControlSpec | None) -> list[dict[str, Any]]:
    """Every crosswalk clause exactly as the catalog carries it (SPEC §7.3): `verified_against_text`
    is read, never invented -- only a human with the licensed standard text may flip it (finding #21,
    human action H4)."""
    if spec is None:
        return []
    return [
        {
            "framework": str(entry.get("framework", "")),
            "clause": str(entry.get("clause", "")),
            "relation": str(entry.get("relation", "")),
            "verified_against_text": bool(entry.get("verified_against_text", False)),
        }
        for entry in spec.raw.get("crosswalk", []) or []
    ]


def _remediation_expectations(
    spec: ControlSpec | None, outcome: str
) -> list[dict[str, str]]:
    if spec is None:
        return []
    return [
        {"id": str(e.get("id", "")), "text": str(e.get("text", "")), "result": outcome}
        for e in spec.raw.get("expectations", []) or []
    ]


def _requirement_list(minimum: list[dict[str, str]]) -> list[dict[str, str]]:
    return [
        {"event": str(r.get("event", "")), "class": str(r.get("class", "any"))}
        for r in minimum
    ]


def _remediation_evidence_gap(
    spec: ControlSpec | None, outcome: str, subject_events: list[dict[str, Any]]
) -> dict[str, Any]:
    """What this control's own `minimum_evidence` requires, and which of those requirements this
    subject's own events did and did not satisfy -- the exact rule the assessment itself used to
    reach `outcome` (:func:`agentce.assess.requirement_met`). Reaching a structural verdict
    (`non-conformant`/`partial`) means every requirement was already met; only `insufficient_evidence`
    can have a real gap."""
    required = _requirement_list(spec.minimum_evidence if spec else [])
    if outcome == "insufficient_evidence":
        observed = [r for r in required if requirement_met(subject_events, r)]
        missing = [r for r in required if not requirement_met(subject_events, r)]
    else:
        observed, missing = list(required), []
    return {"required": required, "observed": observed, "missing": missing}


def _remediation_techniques(spec: ControlSpec | None) -> dict[str, Any]:
    """The control's technique pointers as the remediation block's `techniques` (Appendix A2 (A)) --
    the catalog's only machine-readable remediation hint when, as for every base `eu-ai-act` control
    today, `remediation_hint` itself is unset (mirrors `_remediation_hints`'s reading of the same
    field). `style` is `generic`: the catalog does not yet carry a technique per agent style."""
    if spec is None:
        return {"techniques": []}
    block: dict[str, Any] = {
        "techniques": [
            {
                "style": "generic",
                "ref": str(ref),
                "digest": "sha256:" + hashlib.sha256(str(ref).encode()).hexdigest(),
            }
            for ref in spec.raw.get("techniques", []) or []
        ]
    }
    hint = spec.raw.get("remediation_hint")
    if hint:
        block["hint"] = str(hint)
    return block


def _remediation_acceptance(
    spec: ControlSpec | None, outcome: str, reverify_command: list[str]
) -> dict[str, Any]:
    texts = [
        str(e.get("text", ""))
        for e in ((spec.raw.get("expectations", []) or []) if spec else [])
        if e.get("text")
    ]
    return {
        "expected_transition": {"from": outcome, "to": "conformant"},
        "criteria": "; ".join(texts)
        or "re-run and reach a conformant outcome for this control.",
        "reverify_command": list(reverify_command),
    }


def _remediation_finding(
    a: Assertion,
    spec: ControlSpec | None,
    subject_events: list[dict[str, Any]],
    reverify_command: list[str],
) -> dict[str, Any]:
    finding: dict[str, Any] = {
        "control": a.control,
        "control_version": a.control_version,
        "outcome": a.outcome,
        "rung": a.rung,
        "mode": a.mode,
        "severity": _remediation_severity(spec),
        "window": {"start": a.window[0], "end": a.window[1]},
        "title": _finding_title(a, spec),
        "clauses": _remediation_clauses(spec),
        "expectations": _remediation_expectations(spec, a.outcome),
        "evidence_gap": _remediation_evidence_gap(spec, a.outcome, subject_events),
        "violations": list(a.violations),
        "evidence": [e.to_json() for e in a.evidence],
        "remediation": _remediation_techniques(spec),
        "acceptance": _remediation_acceptance(spec, a.outcome, reverify_command),
        "guardrails_ref": _REMEDIATION_GUARDRAILS_REF,
    }
    description = spec.raw.get("description") if spec else None
    if description:
        finding["description"] = str(description)
    return finding


def _remediation_not_assessed_reason(spec: ControlSpec | None, a: Assertion) -> str:
    rung = spec.rung if spec else a.rung
    if rung != 2:
        return f"rung {rung} is not yet evaluated by this engine"
    return "no structural shape is defined for this control"


def _remediation_not_assessed(a: Assertion, spec: ControlSpec | None) -> dict[str, Any]:
    return {
        "control": a.control,
        "control_version": a.control_version,
        "title": _finding_title(a, spec),
        "severity": _remediation_severity(spec),
        "reason": _remediation_not_assessed_reason(spec, a),
    }


def render_remediation_package(
    subject: str,
    subject_assertions: list[Assertion],
    *,
    catalogs: list[Catalog],
    assertions_digest: str,
    subject_events: list[dict[str, Any]],
    reverify_command: list[str],
) -> dict[str, Any]:
    """The canonical, per-subject AI-actionable remediation package (SPEC §7; Appendix A2 (A)): a
    pure deterministic join of each non-passing control's catalog data (`control.raw` -- crosswalk,
    severity, expectation text, remediation techniques, minimum evidence) with that control's real
    assertion outcome (evidence, violations, evidence gap). No model call; RFC 8785 canonical
    (via :func:`agentce.canonical.canonicalize` at the call site); byte-identical across independent
    runs over the same assertions."""
    by_control = _control_index(catalogs)
    ordered = sorted(subject_assertions, key=lambda a: a.control)
    findings = [
        _remediation_finding(
            a, by_control.get(a.control), subject_events, reverify_command
        )
        for a in ordered
        if a.outcome in _REMEDIATION_FINDING_OUTCOMES
    ]
    not_assessed = [
        _remediation_not_assessed(a, by_control.get(a.control))
        for a in ordered
        if a.outcome == "not_assessed"
    ]
    deviations_applied = sorted(
        {a.deviation for a in subject_assertions if a.deviation}
    )
    body: dict[str, Any] = {
        "package_version": 1,
        "generated_from": {
            "assertions_digest": assertions_digest,
            "engine": {
                "impl": ENGINE_NAME,
                "version": __version__,
                "spec_version": SPEC_VERSION,
            },
            "catalogs": _catalog_refs(catalogs),
        },
        "subject": subject,
        "findings": findings,
        "not_assessed": not_assessed,
        "deviations_applied": deviations_applied,
    }
    # $id is content-derived (mirrors `_build_claim`'s `claim_id`): never a clock, a host, or a
    # random value, so two independent runs over the same assertions produce the same $id too.
    digest = hashlib.sha256(canonical.canonicalize(body)).hexdigest()
    return {
        "$schema": "https://agent-conformance.org/spec/report/remediation-package.schema.json",
        "$id": f"urn:agentce:remediation-package:sha256:{digest}",
        **body,
    }


def _sanitize_keys(d: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    """A rebuilt (never mutated-in-place) shallow copy of `d` with every field in `keys` sanitised
    (SPEC §7 injection hardening; `contracts/P18-18.21.md`) and every other field carried through
    unchanged."""
    return {**d, **{k: _sanitize_field(str(d.get(k, ""))) for k in keys}}


def _sanitize_evidence_gap(gap: dict[str, Any]) -> dict[str, Any]:
    """A rebuilt (never mutated-in-place) `evidence_gap`: every `event`/`class` name sanitised, in
    each of `required`/`observed`/`missing` (SPEC §7 injection hardening; `contracts/P18-18.21.md`)."""
    return {
        key: [_sanitize_keys(r, ("event", "class")) for r in gap.get(key, [])]
        for key in ("required", "observed", "missing")
    }


def _sanitize_finding(finding: dict[str, Any]) -> dict[str, Any]:
    """A render-only, rebuilt view of one remediation-package finding: every catalog- or
    evidence-derived string the two templates (`remediation.md.tmpl`, `skill-finding.md.tmpl`)
    interpolate is sanitised (SPEC §7 injection hardening; `contracts/P18-18.21.md`'s widened
    surface). Every nested structure is rebuilt (`_sanitize_keys`), never mutated in place, so the
    canonical `package` a caller still holds is untouched (a shallow-copy risk 18.20 already
    documented for the top-level dict; this closes it for nested dicts too)."""
    f = _sanitize_keys(finding, ("control", "title", "mode"))
    f["window"] = _sanitize_keys(finding.get("window") or {}, ("start", "end"))
    f["clauses"] = [
        _sanitize_keys(c, ("framework", "clause", "relation"))
        for c in finding.get("clauses", [])
    ]
    f["expectations"] = [
        _sanitize_keys(e, ("text",)) for e in finding.get("expectations", [])
    ]
    f["evidence_gap"] = _sanitize_evidence_gap(finding.get("evidence_gap") or {})
    f["evidence"] = [_sanitize_keys(e, ("ref",)) for e in finding.get("evidence", [])]
    f["violations"] = [
        _sanitize_keys(v, ("path", "constraint", "message_key"))
        for v in finding.get("violations", [])
    ]
    remediation = finding.get("remediation") or {}
    f["remediation"] = {
        **remediation,
        "techniques": [
            _sanitize_keys(t, ("ref",)) for t in remediation.get("techniques", [])
        ],
    }
    f["acceptance"] = _sanitize_keys(finding.get("acceptance") or {}, ("criteria",))
    return f


def _remediation_md_context(package: dict[str, Any]) -> dict[str, Any]:
    """A render-only view of `package`: every evidence- or catalog-derived string the template
    interpolates is sanitised (:func:`_sanitize_field`, :func:`_sanitize_finding`); the canonical
    `package` itself is never mutated -- only this copy feeds the template (SPEC §7 injection
    hardening)."""
    ctx = dict(package)
    ctx["subject"] = _sanitize_field(str(package.get("subject", "")))
    ctx["findings"] = [_sanitize_finding(f) for f in package.get("findings", [])]
    ctx["not_assessed"] = [
        {
            **n,
            "control": _sanitize_field(str(n.get("control", ""))),
            "title": _sanitize_field(str(n.get("title", ""))),
        }
        for n in package.get("not_assessed", [])
    ]
    ctx["deviations_applied"] = [
        _sanitize_field(str(d)) for d in package.get("deviations_applied", [])
    ]
    return ctx


def render_remediation_md(package: dict[str, Any]) -> str:
    """Render the derived Markdown prompt from a canonical remediation package (SPEC §7; Appendix A2
    (B)) by the one language-neutral template `spec/report/templates/remediation.md.tmpl` (vendored
    at `agentce/data/templates/`; `tests/test_bundled_data.py` holds the two byte-identical) -- never
    per-engine hardcoded string logic. A pure function of `package`'s content: no clock, no host, no
    absolute path, so two runs whose only difference is their `--out` directory render
    byte-identical bytes."""
    return templating.render(
        bundled.remediation_template(), _remediation_md_context(package)
    )


def _tool_names_for_subject(subject_events: list[dict[str, Any]]) -> list[str]:
    """Every distinct tool name this subject's own events name (SPEC §7 injection hardening: tool
    names are one of the named evidence-derived-string categories, alongside subject ids and paths),
    sanitised and length-capped with the same :func:`sanitize_for_markdown` the subject id and
    evidence refs already use, so a hostile or oversized tool name can neither hide instruction
    sentences nor blow up a rendered note. Scans every event this subject carries, not only those a
    specific finding cites as evidence -- a control can be `insufficient_evidence` (no evidence
    pointer at all) while the subject's raw events still show real tool activity worth surfacing as
    context. Deduplicated, in first-seen order."""
    names: list[str] = []
    seen: set[str] = set()
    for event in subject_events:
        data = event.get("data")
        if not isinstance(data, dict) or data.get("@type") != "ToolCall":
            continue
        tool = data.get("tool")
        name = tool.get("name") if isinstance(tool, dict) else None
        if not name:
            continue
        sanitized = sanitize_for_markdown(str(name))
        if sanitized not in seen:
            seen.add(sanitized)
            names.append(sanitized)
    return names


def _skill_finding_context(
    finding: dict[str, Any], tool_calls: list[str]
) -> dict[str, Any]:
    """A render-only view of one remediation-package finding for its ``findings/<control>--<n>.md``
    note: the same sanitising :func:`_sanitize_finding` applies per finding, plus the subject's
    already-sanitised tool names (SPEC §7). The canonical package itself is never mutated."""
    ctx = _sanitize_finding(finding)
    ctx["tool_calls"] = list(tool_calls)
    return ctx


def render_skill_finding_md(finding: dict[str, Any], tool_calls: list[str]) -> str:
    """Render one ``findings/<control>--<n>.md`` note (Appendix A2 (C)) from a single remediation
    finding, by the language-neutral template ``spec/report/templates/skill-finding.md.tmpl``.
    ``tool_calls`` is this finding's subject's escaped tool names (:func:`_tool_names_for_subject`)."""
    return templating.render(
        bundled.skill_finding_template(), _skill_finding_context(finding, tool_calls)
    )


def render_skill_md(
    *,
    spec_version: str,
    cli_version: str,
    catalog_version: str,
    assertions_digest: str,
) -> str:
    """Render the generated skill's ``SKILL.md`` (SPEC §13.3, skill rules S-1..S-10; Appendix A2
    (C)): fixed instructions from the one language-neutral template
    ``spec/report/templates/skill.md.tmpl`` plus this run's pinned versions and assertions digest --
    never per-run authored prose, so an instruction sentence can never be assembled from evidence.
    ``catalog_version`` is catalog-derived (a hostile ``--catalog-dir`` carries its own ``id``/
    ``version``) and lands in this file's own YAML frontmatter: sanitising alone is not sufficient (a
    brace, hash, leading quote, or `!!binary` still breaks ``yaml.safe_load`` on the sanitised value;
    `contracts/P18-18.21.md`'s Design section), so the sanitised value is re-emitted as a real,
    double-quoted YAML scalar (never the bare sanitised string the template's unquoted
    ``catalog_version: {{catalog_version}}`` line would otherwise interpolate)."""
    quoted_catalog_version = yaml.safe_dump(
        sanitize_for_markdown(catalog_version),
        default_style='"',
        allow_unicode=True,
        width=float("inf"),
    ).rstrip("\n")
    return templating.render(
        bundled.skill_template(),
        {
            "spec_version": spec_version,
            "cli_version": cli_version,
            "catalog_version": quoted_catalog_version,
            "assertions_digest": assertions_digest,
        },
    )


def render_reverify_md(argv: list[str]) -> str:
    """Render ``REVERIFY.md`` (SPEC §7): the exact, real re-verify command for this run, from the
    language-neutral template ``spec/report/templates/skill-reverify.md.tmpl``. ``argv`` is the full
    command line including the program name, e.g. ``["agentce", "assess", "--bundle", ...]``. Every
    token is sanitised before it reaches the template's triple-backtick fence: a fixed flag like
    ``--catalog-dir`` carries no backtick/newline, so this is a no-op there, but a hostile
    ``--catalog-dir`` *path* is attacker-influenced and could otherwise close the fence early (SPEC §7
    injection hardening; `contracts/P18-18.21.md`)."""
    return templating.render(
        bundled.skill_reverify_template(),
        {"argv": [sanitize_for_markdown(a) for a in argv]},
    )


def _skill_finding_filename(control: str, index: int) -> str:
    """``findings/<control>--<n>.md``'s filename: ``_safe`` keeps a hostile or unusual control id
    (never expected in practice -- control ids are catalog-defined -- but never trusted regardless)
    from escaping the ``findings/`` directory or colliding with another file."""
    return f"{_safe(control)}--{index}.md"


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
    applicability_profile_digest: str | None = None,
    domain_binding_digest: str | None = None,
) -> dict[str, Any]:
    package_digest = _package_digest()
    host = hashlib.sha256(
        f"{platform.system()}|{platform.machine()}|{package_digest}".encode()
    ).hexdigest()
    catalog_refs = _catalog_refs(catalogs)
    inputs: dict[str, Any] = {"bundle_digest": bundle_digest, "catalogs": catalog_refs}
    if applicability_profile_digest is not None:
        # SPEC §8.4's manifest schema already declares this property; every ``assess`` run now
        # populates it (18.8) so a report always names exactly which profile produced it, packaged
        # for re-running or not.
        inputs["applicability_profile_digest"] = applicability_profile_digest
    if domain_binding_digest is not None:
        inputs["domain_binding_digest"] = domain_binding_digest
    manifest: dict[str, Any] = {
        "agentce_manifest_version": 1,
        "engine": {
            "impl": ENGINE_NAME,
            "version": __version__,
            "spec_version": SPEC_VERSION,
            "package_digest": package_digest,
        },
        "inputs": inputs,
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
#: `report --format` has always rendered one at a time; the rest are new (SPEC §9). `remediation` and
#: `skill` are assess-only (Appendix A2 (C)): neither is ever added to `commands.REPORT_FORMATS`.
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
    "remediation",
    "skill",
)
#: What an `emit`-less `write_report` call renders (the engine's original fixed bundle, predating
#: `--emit`): every non-regression test pins this set exactly.
_LEGACY_EMIT = frozenset({"md", "html", "oscal", "sarif", "pack"})
#: `assess`'s own default when `--emit` is absent (RFC 0008 Sec.8): the legacy bundle plus the skill,
#: so the report a user actually runs always includes the fix-list. `write_report`'s own `emit=None`
#: contract (above) is unchanged -- this is resolved at the command layer, never inside `write_report`.
ASSESS_DEFAULT_EMIT = _LEGACY_EMIT | frozenset({"skill"})


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
    events: list[dict[str, Any]] | None = None,
    reverify_command: list[str] | None = None,
    extra_outputs: dict[str, bytes] | None = None,
    activity: dict[str, Any] | None = None,
    blind_spots: dict[str, Any] | None = None,
    applicability_profile_digest: str | None = None,
    domain_binding_digest: str | None = None,
    profile: Profile | None = None,
    declared_subject_ids: frozenset[str] | None = None,
    for_preset: str | None = None,
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
    rendering choice.

    ``events`` (the accepted, ingested events ``remediation`` was computed from) and
    ``reverify_command`` (the actual re-verify argv, SPEC §7) feed the ``remediation`` emission only;
    both default to a safe empty fallback so every other caller is unaffected.

    ``extra_outputs`` writes each ``name: bytes`` pair verbatim through the same accounting every
    other artifact uses (its digest lands in the returned manifest's ``outputs`` map), for artifacts
    a caller computes itself outside the formats above -- today only the incremental-state caller's
    ``runtime_drift.jsonl`` (SPEC.md:249 DC-9/HR-10). Never gated by ``emit``: like ``assertions.json``,
    a caller that passes one always gets it written.

    ``activity`` (:func:`agentce.activity.summarize_activity` over ``events`` and the resolved
    applicability profile) feeds ``activity.json`` and the "what your agents did" report section
    (18.4); computed by the caller, once, since it is also needed for the terminal summary and the
    ``--json`` envelope. A caller that leaves it out gets the honest answer for a profile that
    declares nothing.

    ``blind_spots`` (:func:`agentce.blind_spots.compute_blind_spots` over ``assertions``, the
    resolved profile, and ``catalogs``) feeds ``blind-spots.json`` and the not-enough-evidence report
    section (18.5); like ``activity``, computed once by the caller and reused for the terminal summary
    and the ``--json`` envelope. Unlike ``activity``, a caller that leaves it out gets the honest empty
    answer rather than a silent recomputation: an empty ``Profile()`` has zero subjects, which would
    fail ``compute_blind_spots``'s positional pairing against any non-empty ``assertions`` (RFC 0008
    Sec.6), so every real call site must compute and pass its own.

    ``applicability_profile_digest``/``domain_binding_digest`` (sha256 of the resolved profile/domain
    file bytes, computed by the caller) land in ``manifest.json``'s existing, previously-unpopulated
    schema properties of the same names (18.8) -- present on every run regardless of whether it was
    packaged for sharing.

    ``profile``/``declared_subject_ids`` (18.14, Hill 7): when ``profile`` names more than one
    subject, every agent's records go side by side -- ``project.md``/``.html``/``.json`` (the project
    view, :func:`agentce.project.compute_project_view`) plus each subject's own full report under
    ``agents/<dirname>/``, and the root ``report.md``/``.html`` become the project view. A single
    subject (or no ``profile``) writes exactly what this function always wrote. ``declared_subject_ids``
    mirrors :func:`agentce.activity.summarize_activity`'s own parameter and default (every subject in
    ``profile`` counts as declared when omitted).

    ``for_preset`` (18.16): ``"security"`` additionally writes ``security.md``/``.html``/``.json`` (the
    security view, :func:`agentce.security_view.compute_security_view`) -- a genuinely new mechanism,
    not a reuse of the project view's subject-count gate. ``security.json`` is written whenever
    ``for_preset == "security"``, the same "always written regardless of ``wants``" treatment
    ``project.json`` gets; ``security.md``/``.html`` still respect ``wants("md")``/``wants("html")``
    since the ``security`` preset's own emit set already selects both. Any other value, including
    ``None``, writes nothing new here."""
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
    if activity is None:
        activity = summarize_activity(events or [], Profile())
    write_json("activity.json", activity)
    if blind_spots is None:
        blind_spots = _EMPTY_BLIND_SPOTS
    write_json("blind-spots.json", blind_spots)

    for name, data in (extra_outputs or {}).items():
        write_bytes(name, data)

    # 18.14 C3: every agent's records side by side (Hill 7) -- only when `profile` names more than
    # one subject; a single subject (or no `profile`) leaves every byte below unchanged.
    project_view: dict[str, Any] | None = None
    project_activity_by_subject: dict[str, dict[str, Any]] = {}
    project_subject_ids: list[str] = []
    if profile is not None and len(profile.subjects) > 1:
        resolved_declared = (
            declared_subject_ids
            if declared_subject_ids is not None
            else frozenset(s.id for s in profile.subjects)
        )
        project_subject_ids = sorted(
            {a.subject for a in assertions} | {s.id for s in profile.subjects}
        )
        project_events_by_subject = index_by_subject(events or [])
        declared_subjects_by_id = {s.id: s for s in profile.subjects}
        for subject_id in project_subject_ids:
            declared_subject = declared_subjects_by_id.get(subject_id)
            subject_profile = Profile(
                subjects=[
                    declared_subject
                    if declared_subject is not None
                    else Subject(id=subject_id)
                ]
            )
            subject_declared_ids = (
                frozenset({subject_id})
                if subject_id in resolved_declared
                else frozenset()
            )
            project_activity_by_subject[subject_id] = summarize_activity(
                project_events_by_subject.get(subject_id, []),
                subject_profile,
                declared_subject_ids=subject_declared_ids,
            )
        project_view = compute_project_view(
            assertions,
            profile,
            resolved_declared,
            project_activity_by_subject,
            blind_spots,
        )
        write_json("project.json", project_view)
        gaps_by_subject = blind_spots_by_subject(blind_spots)
        no_pop_by_subject = no_population_by_subject(
            blind_spots.get("no_population", [])
        )
        assertions_by_subject: dict[str, list[Assertion]] = {}
        for assertion in assertions:
            assertions_by_subject.setdefault(assertion.subject, []).append(assertion)
        for subject_id in project_subject_ids:
            dirname = _agent_dirname(subject_id)
            (out_dir / "agents" / dirname).mkdir(parents=True, exist_ok=True)
            subject_assertions = assertions_by_subject.get(subject_id, [])
            subject_activity = project_activity_by_subject[subject_id]
            subject_blind_spots = {
                "blind_spots": gaps_by_subject.get(subject_id, []),
                "no_population": no_pop_by_subject.get(subject_id, []),
            }
            write_json(
                f"agents/{dirname}/assertions.json",
                [a.to_json() for a in subject_assertions],
            )
            write_json(f"agents/{dirname}/activity.json", subject_activity)
            write_json(f"agents/{dirname}/blind-spots.json", subject_blind_spots)
            if wants("md"):
                write_text(
                    f"agents/{dirname}/report.md",
                    render_report_md(
                        subject_assertions,
                        aggregate(subject_assertions),
                        language=report_language,
                        catalogs=catalogs,
                        invocation=invocation,
                        activity=subject_activity,
                        blind_spots=subject_blind_spots,
                    ),
                )
            if wants("html"):
                write_text(
                    f"agents/{dirname}/report.html",
                    render_report_html(
                        subject_assertions,
                        aggregate(subject_assertions),
                        language=report_language,
                        catalogs=catalogs,
                        invocation=invocation,
                        activity=subject_activity,
                        blind_spots=subject_blind_spots,
                    ),
                )

    if wants("md"):
        if project_view is not None:
            project_md = render_project_md(
                project_view, project_activity_by_subject, language=report_language
            )
            write_text("project.md", project_md)
            write_text("report.md", project_md)
        else:
            write_text(
                "report.md",
                render_report_md(
                    assertions,
                    counts,
                    language=report_language,
                    catalogs=catalogs,
                    invocation=invocation,
                    activity=activity,
                    blind_spots=blind_spots,
                ),
            )
    if wants("html"):
        if project_view is not None:
            project_html = render_project_html(
                project_view, project_activity_by_subject, language=report_language
            )
            write_text("project.html", project_html)
            write_text("report.html", project_html)
        else:
            write_text(
                "report.html",
                render_report_html(
                    assertions,
                    counts,
                    language=report_language,
                    catalogs=catalogs,
                    invocation=invocation,
                    activity=activity,
                    blind_spots=blind_spots,
                ),
            )

    # 18.16: the security view is gated on `for_preset`, not `emit` -- a genuinely new mechanism
    # (foundational_thinking, contracts/P18-18.16.md), independent of the project view's subject-count
    # gate above. `security.json` is always written for this preset, like `project.json` is always
    # written for a multi-subject profile; `.md`/`.html` still follow `wants()` since the `security`
    # preset's own emit set already selects both.
    if for_preset == "security":
        security_view = compute_security_view(activity, assertions)
        write_json("security.json", security_view)
        if wants("md"):
            write_text(
                "security.md",
                render_security_md(security_view, language=report_language),
            )
        if wants("html"):
            write_text(
                "security.html",
                render_security_html(security_view, language=report_language),
            )

    oscal_doc: dict[str, Any] | None = None
    if wants("oscal") or wants("oscal_xml"):
        oscal_doc = render_oscal(assertions)
    if (wants("oscal") or wants("oscal_xml")) and oscal_doc is not None:
        # oscal-ar.xml is a serialization of this same object (SPEC §9.4): write the JSON source
        # alongside it even when only `oscal_xml` was requested, so the two never diverge.
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

    if wants("remediation"):
        events_by_subject = index_by_subject(events or [])
        assertions_digest = outputs["assertions.json"]
        argv = list(reverify_command) if reverify_command else list(invocation or [])
        subjects = sorted({a.subject for a in assertions})
        for subject in subjects:
            package = render_remediation_package(
                subject,
                [a for a in assertions if a.subject == subject],
                catalogs=catalogs,
                assertions_digest=assertions_digest,
                subject_events=events_by_subject.get(subject, []),
                reverify_command=argv,
            )
            md = render_remediation_md(package)
            rel_dir = f"remediation/{_safe(subject)}"
            (out_dir / rel_dir).mkdir(parents=True, exist_ok=True)
            pkg_bytes = canonical.canonicalize(package)
            (out_dir / rel_dir / "remediation-package.json").write_bytes(pkg_bytes)
            outputs[f"{rel_dir}/remediation-package.json"] = _digest_bytes(pkg_bytes)
            md_bytes = md.encode("utf-8")
            (out_dir / rel_dir / "remediation.md").write_bytes(md_bytes)
            outputs[f"{rel_dir}/remediation.md"] = _digest_bytes(md_bytes)

    if wants("skill"):
        events_by_subject = index_by_subject(events or [])
        assertions_digest = outputs["assertions.json"]
        argv = list(reverify_command) if reverify_command else list(invocation or [])
        catalog_version = ", ".join(f"{c.id}@{c.version}" for c in catalogs)
        subjects = sorted({a.subject for a in assertions})
        for subject in subjects:
            subject_events = events_by_subject.get(subject, [])
            tool_calls = _tool_names_for_subject(subject_events)
            package = render_remediation_package(
                subject,
                [a for a in assertions if a.subject == subject],
                catalogs=catalogs,
                assertions_digest=assertions_digest,
                subject_events=subject_events,
                reverify_command=argv,
            )
            rel_dir = f"skill/{_safe(subject)}"
            skill_dir = out_dir / rel_dir
            (skill_dir / "findings").mkdir(parents=True, exist_ok=True)

            pkg_bytes = canonical.canonicalize(package)
            (skill_dir / "remediation-package.json").write_bytes(pkg_bytes)
            outputs[f"{rel_dir}/remediation-package.json"] = _digest_bytes(pkg_bytes)

            skill_md_bytes = render_skill_md(
                spec_version=SPEC_VERSION,
                cli_version=__version__,
                catalog_version=catalog_version,
                assertions_digest=assertions_digest,
            ).encode("utf-8")
            (skill_dir / "SKILL.md").write_bytes(skill_md_bytes)
            outputs[f"{rel_dir}/SKILL.md"] = _digest_bytes(skill_md_bytes)

            for i, finding in enumerate(package["findings"], start=1):
                note_bytes = render_skill_finding_md(finding, tool_calls).encode(
                    "utf-8"
                )
                name = _skill_finding_filename(str(finding["control"]), i)
                (skill_dir / "findings" / name).write_bytes(note_bytes)
                outputs[f"{rel_dir}/findings/{name}"] = _digest_bytes(note_bytes)

            reverify_bytes = render_reverify_md(["agentce", *argv]).encode("utf-8")
            (skill_dir / "REVERIFY.md").write_bytes(reverify_bytes)
            outputs[f"{rel_dir}/REVERIFY.md"] = _digest_bytes(reverify_bytes)

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
        applicability_profile_digest=applicability_profile_digest,
        domain_binding_digest=domain_binding_digest,
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


def _validate_jsonl(path: Path) -> list[str]:
    """Every non-blank line of ``path`` parses as its own JSON object."""
    problems = []
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            json.loads(line)
        except json.JSONDecodeError as exc:
            problems.append(f"{path.name}: line {i} is not valid JSON ({exc.msg})")
    return problems


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


#: The optional new artifacts (SPEC §9): validated when a run actually produced them (`--emit`, or for
#: `runtime_drift.jsonl`, `--state`), unlike `_ARTIFACT_SCHEMAS`'s entries, which every run has always
#: written and whose absence is itself a problem.
_OPTIONAL_ARTIFACTS = (
    "report.junit.xml",
    "oscal-ar.xml",
    "report.csv",
    "runtime_drift.jsonl",
)


def _recorded_outputs(out_dir: Path) -> dict[str, str]:
    """``manifest.json``'s own ``outputs`` map: the filenames *this run* actually wrote, recorded
    by ``write_report`` itself as it wrote them (SPEC §9) -- ground truth for what a narrower
    ``--emit`` did and did not request, independent of the directory's current contents."""
    manifest_path = out_dir / "manifest.json"
    if not manifest_path.is_file():
        return {}
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    outputs = manifest.get("outputs")
    return outputs if isinstance(outputs, dict) else {}


def validate_report(out_dir: Path) -> list[str]:
    """Validate every emitted artifact against its vendored schema; return a list of problems.

    `--emit` selects which formats a run writes, so only `_MANDATORY_ARTIFACT_SCHEMAS` (the run's
    structural core, always written) is unconditionally required; every format-specific artifact is
    additionally required when `manifest.json`'s own `outputs` map recorded that this run wrote it
    (so a run that emitted `report.html` but somehow lost it before `--validate` runs is still
    caught) and is validated when present regardless -- 'validate every emission' means every
    artifact the run actually emitted, not every format that exists."""
    problems: list[str] = []
    for filename in _MANDATORY_ARTIFACT_SCHEMAS:
        if not (out_dir / filename).is_file():
            problems.append(f"{filename}: missing")
    recorded = _recorded_outputs(out_dir)
    for filename in (
        set(_ARTIFACT_SCHEMAS) | {"report.md", "report.html"} | set(_OPTIONAL_ARTIFACTS)
    ):
        if filename in recorded and not (out_dir / filename).is_file():
            problems.append(
                f"{filename}: missing (recorded in manifest.json's outputs but not on disk)"
            )
    for filename, schema_name in _ARTIFACT_SCHEMAS.items():
        path = out_dir / filename
        if not path.is_file():
            continue  # mandatory absence was already reported above; optional absence is not a problem
        try:
            instance = json.loads(path.read_text(encoding="utf-8"))
            jsonschema.validate(instance, _load_schema(schema_name))
        except json.JSONDecodeError as exc:
            problems.append(f"{filename}: invalid JSON ({exc.msg})")
            continue
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
        if path.is_file() and not path.read_text(encoding="utf-8").strip():
            problems.append(f"{filename}: empty")
    for filename in _OPTIONAL_ARTIFACTS:
        path = out_dir / filename
        if not path.is_file():
            continue  # optional: only present, and only validated, when `--emit` requested it
        if filename == "report.csv":
            problems += _validate_csv(path)
        elif filename == "runtime_drift.jsonl":
            problems += _validate_jsonl(path)
        else:
            problems += _validate_xml_wellformed(path)
    return problems
