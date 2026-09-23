"""Render the report artifacts from assertions (SPEC §9).

``assertions.json`` is written in RFC 8785 canonical form (byte-identical across engines); the human
report (md/html), OSCAL Assessment Results, SARIF, and role-aware evidence packs are rendered from it,
and the reproducibility manifest records the digest of every input and output. DC-5 is enforced
before anything is written: a supporting verdict without an evidence pointer aborts the run.
Superseded reports are named in ``manifest.supersedes`` (HR-10). ``validate_report`` checks every
emitted artifact against its vendored schema.
"""

from __future__ import annotations

import hashlib
import html
import json
import platform
import uuid
from importlib import resources
from pathlib import Path
from typing import Any

import jsonschema

from . import ENGINE_NAME, SPEC_VERSION, __version__, canonical, messages, verdict
from .assertions import Assertion, aggregate, check_dc5
from .catalog import Catalog, catalog_provenance_digest

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


def render_report_md(
    assertions: list[Assertion],
    counts: dict[str, int],
    *,
    language: str = messages.DEFAULT_LANGUAGE,
    catalogs: list[str] | None = None,
    invocation: list[str] | None = None,
) -> str:
    cat = messages.catalogue(language)
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
    for a in sorted(assertions, key=lambda x: (x.subject, x.control)):
        lines.append(
            f"- `{a.control}` @ `{a.subject}` -> **{_outcome_label(cat, a.outcome)}** "
            f"(rung {a.rung}, {a.mode}; {a.population[1]}/{a.population[0]} failed)"
        )
        lines += [f"  - {_crosswalk_text(e, cat)}" for e in a.crosswalk]
    lines += [""] + _provenance_md(catalogs or [], invocation)
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


def render_report_html(
    assertions: list[Assertion],
    counts: dict[str, int],
    *,
    language: str = messages.DEFAULT_LANGUAGE,
    catalogs: list[str] | None = None,
    invocation: list[str] | None = None,
) -> str:
    """Render a self-contained, escaped, WCAG 2.2 AA report page (SPEC §9.3): a strict CSP meta tag,
    no external references, one ``h1``, a ``main`` landmark, a print stylesheet, and every string that
    originates in evidence or declarations rendered as escaped text, never as markup."""
    cat = messages.catalogue(language)
    title = html.escape(cat["report.title"])
    summary = "".join(
        f"<li>{html.escape(_outcome_label(cat, o))}: {c}</li>"
        for o, c in counts.items()
    )
    rows = "".join(
        f"<tr><td>{html.escape(a.control)}</td><td>{html.escape(a.subject)}</td>"
        f"<td>{html.escape(_outcome_label(cat, a.outcome))}</td>"
        f"<td>{'; '.join(html.escape(_crosswalk_text(e, cat)) for e in a.crosswalk)}</td></tr>"
        for a in sorted(assertions, key=lambda x: (x.subject, x.control))
    )
    body_rows = rows or (
        f'<tr><td colspan="4">{html.escape(cat["report.no_controls"])}</td></tr>'
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
        '<thead><tr><th scope="col">Control</th><th scope="col">Subject</th>'
        '<th scope="col">Outcome</th><th scope="col">Clause</th></tr></thead>'
        f"<tbody>{body_rows}</tbody></table></section>"
        f"{_provenance_html(catalogs or [], invocation)}"
        f"<footer><p>{html.escape(cat['report.affected_persons'])}</p></footer>"
        "</main></body></html>\n"
    )


def render_oscal(assertions: list[Assertion]) -> dict[str, Any]:
    findings = [
        {
            "uuid": _uuid("finding", a.control, a.subject),
            "title": f"{a.control} for {a.subject}",
            "target": {
                "type": "objective-id",
                "target-id": a.control,
                "status": {
                    "state": _OSCAL_STATE.get(a.outcome, "not-satisfied"),
                    "reason": a.outcome,
                },
            },
        }
        for a in sorted(assertions, key=lambda x: (x.subject, x.control))
    ]
    return {
        "assessment-results": {
            "uuid": _uuid("assessment-results"),
            "metadata": {
                "title": "AgentCE Assessment Results",
                "version": __version__,
                "oscal-version": "1.1.2",
            },
            "results": [
                {
                    "uuid": _uuid("result"),
                    "title": "AgentCE structural assessment",
                    "findings": findings,
                }
            ],
        }
    }


def render_sarif(assertions: list[Assertion]) -> dict[str, Any]:
    rules = [{"id": control} for control in sorted({a.control for a in assertions})]
    results = [
        {
            "ruleId": a.control,
            "level": _SARIF_LEVEL[a.outcome],
            "message": {"text": f"{a.control} on {a.subject}: {a.outcome}"},
        }
        for a in sorted(assertions, key=lambda x: (x.subject, x.control))
        if a.outcome in _SARIF_LEVEL
    ]
    return {
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
            "started_at": _now(),
            "operator": operator,
            "host_fingerprint": "sha256:" + host,
            "invocation": invocation,
            "report_language": report_language,
        },
    }
    if supersedes:
        manifest["supersedes"] = supersedes
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
    claim_id = "sha256:" + hashlib.sha256(canonical.canonicalize(body)).hexdigest()
    return {"claim_id": claim_id, **body}


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
) -> dict[str, Any]:
    """Write every report artifact for ``assertions`` and return the reproducibility manifest."""
    check_dc5(
        assertions
    )  # DC-5: refuse a supporting verdict without an evidence pointer
    out_dir.mkdir(parents=True, exist_ok=True)
    outputs: dict[str, str] = {}
    labels = [f"{c.id}@{c.version}" for c in catalogs]

    def write_json(name: str, obj: Any) -> None:
        data = canonical.canonicalize(obj)
        (out_dir / name).write_bytes(data)
        outputs[name] = _digest_bytes(data)

    def write_text(name: str, text: str) -> None:
        data = text.encode("utf-8")
        (out_dir / name).write_bytes(data)
        outputs[name] = _digest_bytes(data)

    counts = aggregate(assertions)
    write_json("assertions.json", [a.to_json() for a in assertions])
    write_text(
        "report.md",
        render_report_md(
            assertions,
            counts,
            language=report_language,
            catalogs=labels,
            invocation=invocation,
        ),
    )
    write_text(
        "report.html",
        render_report_html(
            assertions,
            counts,
            language=report_language,
            catalogs=labels,
            invocation=invocation,
        ),
    )
    write_json("oscal-ar.json", render_oscal(assertions))
    write_json("results.sarif", render_sarif(assertions))

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
        claim = _build_claim(assertions, catalogs=catalogs, operator=operator)
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
    for filename in ("report.md", "report.html"):
        path = out_dir / filename
        if not path.is_file() or not path.read_text(encoding="utf-8").strip():
            problems.append(f"{filename}: missing or empty")
    return problems
