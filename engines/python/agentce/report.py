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
import json
import platform
import uuid
from importlib import resources
from pathlib import Path
from typing import Any

import jsonschema

from . import ENGINE_NAME, SPEC_VERSION, __version__, canonical
from .assertions import Assertion, aggregate, check_dc5

_ZERO_DIGEST = "sha256:" + "0" * 64
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


def render_report_md(assertions: list[Assertion], counts: dict[str, int]) -> str:
    lines = ["# AgentCE conformance report", "", "## Outcome summary", ""]
    lines += [f"- {outcome}: {count}" for outcome, count in counts.items()]
    lines += ["", "## Assertions", ""]
    if not assertions:
        lines.append("_No controls were evaluated._")
    for a in sorted(assertions, key=lambda x: (x.subject, x.control)):
        lines.append(
            f"- `{a.control}` @ `{a.subject}` -> **{a.outcome}** "
            f"(rung {a.rung}, {a.mode}; {a.population[1]}/{a.population[0]} failed)"
        )
    return "\n".join(lines) + "\n"


def render_report_html(assertions: list[Assertion], counts: dict[str, int]) -> str:
    summary = "".join(f"<li>{o}: {c}</li>" for o, c in counts.items())
    rows = "".join(
        f"<tr><td>{a.control}</td><td>{a.subject}</td><td>{a.outcome}</td></tr>"
        for a in sorted(assertions, key=lambda x: (x.subject, x.control))
    )
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        "<title>AgentCE conformance report</title></head><body>"
        "<h1>AgentCE conformance report</h1>"
        f"<h2>Outcome summary</h2><ul>{summary}</ul>"
        f"<h2>Assertions</h2><table><tr><th>Control</th><th>Subject</th><th>Outcome</th></tr>{rows}</table>"
        "</body></html>\n"
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


def render_evidence_pack(subject: str, assertions: list[Assertion]) -> dict[str, Any]:
    return {
        "subject": subject,
        "assertions": [
            {"control": a.control, "outcome": a.outcome, "mode": a.mode}
            for a in assertions
        ],
        "evidence": sorted({e.ref for a in assertions for e in a.evidence}),
    }


def build_manifest(
    *,
    bundle_digest: str,
    catalogs: list[str],
    outputs: dict[str, str],
    operator: str,
    invocation: list[str],
    supersedes: list[str],
) -> dict[str, Any]:
    package_digest = _package_digest()
    host = hashlib.sha256(
        f"{platform.system()}|{platform.machine()}|{package_digest}".encode()
    ).hexdigest()
    catalog_refs = []
    for entry in catalogs:
        cid, _, version = entry.partition("@")
        catalog_refs.append(
            {"id": cid, "version": version or "0", "digest": _ZERO_DIGEST}
        )
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
        },
    }
    if supersedes:
        manifest["supersedes"] = supersedes
    return manifest


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_report(
    out_dir: Path,
    assertions: list[Assertion],
    *,
    bundle_digest: str,
    catalogs: list[str],
    operator: str = "unknown",
    invocation: list[str] | None = None,
    supersedes: list[str] | None = None,
) -> dict[str, Any]:
    """Write every report artifact for ``assertions`` and return the reproducibility manifest."""
    check_dc5(
        assertions
    )  # DC-5: refuse a supporting verdict without an evidence pointer
    out_dir.mkdir(parents=True, exist_ok=True)
    outputs: dict[str, str] = {}

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
    write_text("report.md", render_report_md(assertions, counts))
    write_text("report.html", render_report_html(assertions, counts))
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

    manifest = build_manifest(
        bundle_digest=bundle_digest,
        catalogs=catalogs,
        outputs=outputs,
        operator=operator,
        invocation=invocation or [],
        supersedes=supersedes or [],
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
