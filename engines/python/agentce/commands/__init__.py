"""Command handlers for the AgentCE CLI (SPEC §8.5).

Each handler takes the parsed ``argparse.Namespace`` and returns a :class:`~agentce.result.CommandResult`.
Every command parses its arguments, validates its inputs (a missing or malformed input is an
``input_error``, exit code 3), emits ``--json`` output, and returns an exit code through the common
scheme. ``validate`` runs the ingest-and-validation stage (SPEC §8.1); ``version`` is fully
implemented; the remaining evaluation stages behind ``verify``, ``assess``, ``report``, ``collect``,
``catalog``, ``conformance``, ``diff``, and ``sign`` are layered on by the subsequent work items and
until then report ``status: not_implemented`` and exit ``ok``.
"""

from __future__ import annotations

import argparse
import getpass
import json
import re
from collections.abc import Iterable
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from .. import ENGINE_NAME, SPEC_VERSION, __version__, no_ml, readiness
from ..applicability import resolve as resolve_applicability
from ..assess import assess_subjects
from ..bundle import load_bundle
from ..catalog import lint_catalog, load_catalog
from ..collect import EnvSecretManager, load_config, run_collect
from ..config import resolve as resolve_config
from ..conformance import run_ecs
from ..coverage import compute_coverage
from ..coverage_matrix import MATRIX_FILE, check_matrix, write_matrix
from ..domain import DomainBinding
from ..errors import InputError
from ..exit_codes import ExitCode
from ..graph import build_graph
from ..ingest import ingest
from ..integrity import IntegrityStatus, verify_bundle
from ..logsetup import get_logger
from ..profile import Profile
from ..quarantine import counts_by_reason, write_quarantine
from ..assertions import Assertion, aggregate
from ..report import (
    render_evidence_pack,
    render_oscal,
    render_public_statement,
    render_report_html,
    render_report_md,
    render_sarif,
    validate_report,
    write_report,
)
from ..state import StateDir, window_end
from ..result import CommandResult
from ..store import GraphStore

_log = get_logger()

REPORT_FORMATS = ("md", "html", "oscal", "sarif", "public", "pack")
SIGN_ROLES = ("claimant", "assessor")
SIGN_PROFILES = ("sigstore-public", "sigstore-private", "kms")


def _opt_str(ns: argparse.Namespace, name: str) -> str | None:
    value = getattr(ns, name, None)
    return None if value is None else str(value)


def _flag(ns: argparse.Namespace, name: str) -> bool:
    return bool(getattr(ns, name, False))


def _require_dir(
    raw: str | None, *, key: str, what: str, fix: str | None = None
) -> Path:
    fix = fix or f"pass --{key.replace('_', '-')} <dir>."
    if raw is None:
        raise InputError(f"input.{key}_missing", f"{what} is required.", fix)
    path = Path(raw)
    if not path.is_dir():
        raise InputError(
            f"input.{key}_not_a_directory",
            f"{what} {raw!r} is not an existing directory.",
            fix,
        )
    return path


def _require_file(
    raw: str | None, *, key: str, what: str, fix: str | None = None
) -> Path:
    fix = fix or f"pass --{key.replace('_', '-')} <file>."
    if raw is None:
        raise InputError(f"input.{key}_missing", f"{what} is required.", fix)
    path = Path(raw)
    if not path.is_file():
        raise InputError(
            f"input.{key}_not_a_file",
            f"{what} {raw!r} is not an existing file.",
            fix,
        )
    return path


def _write_jsonl(records: Iterable[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, sort_keys=True, separators=(",", ":")))
            handle.write("\n")


def _pending(result: CommandResult, summary: str) -> CommandResult:
    """Mark a handler whose evaluation logic is not built yet; a clean, honest no-op (exit ok)."""
    result.data.setdefault("status", "not_implemented")
    result.data.setdefault("message_key", "skeleton.not_implemented")
    result.note(summary)
    _log.debug("command.pending", extra={"command": result.command})
    return result


def cmd_validate(ns: argparse.Namespace) -> CommandResult:
    result = CommandResult(command="validate")
    bundle_dir = _require_dir(
        _opt_str(ns, "bundle"), key="bundle", what="the evidence bundle"
    )
    bundle = load_bundle(
        bundle_dir
    )  # raises InputError (exit 3) on a missing/mismatching manifest
    ingested = ingest(bundle)
    result.data.update(
        {
            "bundle": str(bundle_dir),
            "bundle_digest": bundle.digest,
            "accepted": len(ingested.accepted),
            "quarantined": len(ingested.quarantined),
            "quarantine_by_reason": counts_by_reason(ingested.quarantined),
        }
    )
    out = _opt_str(ns, "out")
    if out is not None:
        quarantine_path = Path(out) / "quarantine.jsonl"
        write_quarantine(ingested.quarantined, quarantine_path)
        result.data["quarantine_file"] = str(quarantine_path)
    result.note(
        f"validated {bundle_dir}: {len(ingested.accepted)} accepted, "
        f"{len(ingested.quarantined)} quarantined"
    )
    if ingested.quarantined:
        result.add_code(int(ExitCode.FINDINGS))
    return result


def cmd_verify(ns: argparse.Namespace) -> CommandResult:
    result = CommandResult(command="verify")
    bundle = _opt_str(ns, "bundle")
    catalog = _opt_str(ns, "catalog")
    release = _opt_str(ns, "release")
    chosen = [
        name
        for name, value in (
            ("bundle", bundle),
            ("catalog", catalog),
            ("release", release),
        )
        if value
    ]
    if len(chosen) != 1:
        raise InputError(
            "input.verify_target",
            "verify needs exactly one of --bundle, --catalog, or --release.",
            "pass exactly one target, e.g. `agentce verify --bundle <dir>`.",
        )
    if bundle is not None:
        bundle_dir = _require_dir(bundle, key="bundle", what="the evidence bundle")
        loaded = load_bundle(bundle_dir)
        ingested = ingest(loaded)
        results = verify_bundle(ingested.accepted, loaded.manifest, loaded.root)
        clean = {IntegrityStatus.VERIFIED.value, IntegrityStatus.VERIFIED_WEAK.value}
        broken = [r for r in results if r.status not in clean]
        result.data.update(
            {
                "bundle": str(bundle_dir),
                "streams": [r.to_json() for r in results],
                "stream_count": len(results),
                "broken_streams": len(broken),
            }
        )
        result.note(
            f"verified {bundle_dir}: {len(results)} streams, {len(broken)} broken"
        )
        if broken:
            result.add_code(int(ExitCode.FINDINGS))
        return result
    if catalog is not None:
        result.data["catalog"] = str(
            _require_dir(catalog, key="catalog", what="the catalog directory")
        )
    else:
        result.data["release"] = str(
            _require_file(release, key="release", what="the release artifact")
        )
    return _pending(
        result, "Catalog and release signature verification land in a later work item."
    )


def cmd_assess(ns: argparse.Namespace) -> CommandResult:
    result = CommandResult(command="assess")
    bundle = _require_dir(
        _opt_str(ns, "bundle"), key="bundle", what="the evidence bundle"
    )
    profile = _require_file(
        _opt_str(ns, "profile"), key="profile", what="the applicability profile"
    )
    catalog = _opt_str(ns, "catalog")
    if not catalog:
        raise InputError(
            "input.catalog_missing",
            "at least one catalog id@version is required.",
            "pass --catalog <id@ver>[,<id@ver>...].",
        )
    out = _opt_str(ns, "out")
    if not out:
        raise InputError(
            "input.out_missing", "an output directory is required.", "pass --out <dir>."
        )
    # Stage 1: ingest and validate. A missing/mismatching manifest aborts with exit 3.
    loaded = load_bundle(bundle)
    ingested = ingest(loaded)
    out_dir = Path(out)
    write_quarantine(ingested.quarantined, out_dir / "quarantine.jsonl")
    # Stage 2: integrity verification, one IntegrityResult per stream.
    integrity_results = verify_bundle(ingested.accepted, loaded.manifest, loaded.root)
    _write_jsonl((r.to_json() for r in integrity_results), out_dir / "integrity.jsonl")
    # Stage 3: build the provenance graph into the on-disk store (ADR-0001).
    domain_path = _opt_str(ns, "domain")
    domain = (
        DomainBinding.load(
            _require_file(domain_path, key="domain", what="the domain binding")
        )
        if domain_path is not None
        else DomainBinding.empty()
    )
    graph_path = out_dir / "graph.sqlite"
    graph_path.unlink(missing_ok=True)
    graph_store = GraphStore(graph_path)
    build_graph(ingested.accepted, domain=domain, store=graph_store)
    graph_triples = graph_store.triple_count()
    graph_store.close()
    # Stage 4: coverage and reconciliation against independent denominators.
    profile_obj = Profile.load(profile)
    coverage = compute_coverage(ingested.accepted, profile_obj, loaded.root)
    (out_dir / "coverage.json").write_text(
        json.dumps(coverage, sort_keys=True, indent=2), encoding="utf-8"
    )
    # Stage 5: applicability resolution and drift (catalog controls arrive with item 1.9).
    statements = resolve_applicability(profile_obj, ingested.accepted)
    _write_jsonl(statements, out_dir / "applicability.jsonl")
    drift_findings = sum(len(s["drift"]) for s in statements)
    # Stage 6: catalog evaluation and report artifacts. Each --catalog-dir catalog is evaluated
    # against every subject to produce assertions; the report artifacts are rendered from them.
    catalog_dirs = [d for d in (getattr(ns, "catalog_dir", None) or [])]
    catalogs = [
        load_catalog(_require_dir(d, key="catalog-dir", what="the catalog directory"))
        for d in catalog_dirs
    ]
    evaluated = assess_subjects(ingested.accepted, profile_obj, catalogs, domain)
    # Stage 6a: incremental state (SPEC §5.4 B7, HR-10). With --state the engine detects a changed
    # bundle (late-arriving evidence lands here), supersedes the prior report, and counts late events.
    state_arg = _opt_str(ns, "state")
    state: StateDir | None = None
    supersedes: list[str] = []
    late_events: dict[str, int] = {}
    new_window_end = window_end(profile_obj, ingested.accepted)
    if state_arg is not None:
        state = StateDir.load(
            Path(state_arg)
        )  # incompatible state_version aborts with exit 3
        supersedes, late_events = state.plan(
            loaded.digest, ingested.accepted, new_window_end
        )
    write_report(
        out_dir,
        evaluated,
        bundle_digest=loaded.digest,
        catalogs=catalog.split(","),
        operator=_operator(),
        invocation=["assess", str(bundle), str(profile)],
        supersedes=supersedes,
    )
    if state is not None:
        state.record(loaded.digest, out_dir / "manifest.json", new_window_end)
    non_conformant = sum(1 for a in evaluated if a.outcome == "non-conformant")
    result.data.update(
        {
            "bundle": str(bundle),
            "bundle_digest": loaded.digest,
            "profile": str(profile),
            "catalogs": catalog.split(","),
            "out": out,
            "accepted": len(ingested.accepted),
            "quarantined": len(ingested.quarantined),
            "streams": len(integrity_results),
            "graph_triples": graph_triples,
            "subjects": len(coverage["subjects"]),
            "drift_findings": drift_findings,
            "assertions": len(evaluated),
        }
    )
    if state is not None:
        result.data["supersedes"] = supersedes
        result.data["late_events"] = late_events
    if non_conformant:
        result.add_code(int(ExitCode.FINDINGS))
    if not catalogs:
        return _pending(
            result,
            "Ingest through report complete; pass --catalog-dir to evaluate controls "
            "and populate assertions.",
        )
    result.note(
        f"assessed {len(evaluated)} (control, subject) pairs; {non_conformant} non-conformant"
    )
    return result


def _operator() -> str:
    try:
        return getpass.getuser()
    except Exception:  # noqa: BLE001 - environments without a resolvable user
        return "unknown"


def cmd_report(ns: argparse.Namespace) -> CommandResult:
    result = CommandResult(command="report")
    if _flag(ns, "validate"):
        report_dir = _require_dir(
            _opt_str(ns, "validate"), key="validate", what="the report directory"
        )
        problems = validate_report(report_dir)
        result.data.update(
            {"report_dir": str(report_dir), "valid": not problems, "problems": problems}
        )
        if problems:
            result.add_code(int(ExitCode.INPUT_ERROR))
            result.note(f"{report_dir}: {len(problems)} artifact(s) failed validation")
        else:
            result.note(f"{report_dir}: all artifacts valid")
        return result
    source = _require_file(
        _opt_str(ns, "from_"), key="from", what="the assertions file"
    )
    fmt = _opt_str(ns, "format") or "md"
    if fmt not in REPORT_FORMATS:
        raise InputError(
            "input.report_format",
            f"unknown report format {fmt!r}.",
            f"choose one of: {', '.join(REPORT_FORMATS)}.",
        )
    assertions = [Assertion.from_json(a) for a in json.loads(source.read_text("utf-8"))]
    counts = aggregate(assertions)
    catalogs = [c for c in (_opt_str(ns, "catalog") or "").split(",") if c] or None
    rendering: str
    if fmt == "md":
        rendering = render_report_md(assertions, counts)
    elif fmt == "html":
        rendering = render_report_html(assertions, counts)
    elif fmt == "oscal":
        rendering = json.dumps(render_oscal(assertions), sort_keys=True, indent=2)
    elif fmt == "sarif":
        rendering = json.dumps(render_sarif(assertions), sort_keys=True, indent=2)
    elif fmt == "public":
        rendering = render_public_statement(assertions, catalogs=catalogs)
    else:  # pack
        role = _opt_str(ns, "role")
        by_subject: dict[str, list[Assertion]] = {}
        for assertion in assertions:
            by_subject.setdefault(assertion.subject, []).append(assertion)
        packs = {
            subject: render_evidence_pack(subject, subject_assertions, role=role)
            for subject, subject_assertions in sorted(by_subject.items())
        }
        rendering = json.dumps(packs, sort_keys=True, indent=2)
    result.data.update({"from": str(source), "format": fmt, "rendering": rendering})
    out = _opt_str(ns, "out")
    if out is not None:
        Path(out).write_text(rendering, encoding="utf-8")
        result.data["out"] = out
    result.note(rendering)
    return result


def cmd_collect(ns: argparse.Namespace) -> CommandResult:
    result = CommandResult(command="collect")
    config_path = _require_file(
        _opt_str(ns, "config"), key="config", what="the collection config"
    )
    out = _opt_str(ns, "out")
    dry_run = _flag(ns, "dry_run")
    if not dry_run and not out:
        raise InputError(
            "input.out_missing",
            "a real collection needs an output bundle path.",
            "pass --out <bundle>, or --dry-run to plan only.",
        )
    config = load_config(config_path)
    outcome = run_collect(
        config,
        out_dir=Path(out) if out else None,
        dry_run=dry_run,
        secret_manager=EnvSecretManager(),
    )
    result.data.update({"config": str(config_path), "dry_run": dry_run})
    if out is not None:
        result.data["out"] = out
        result.data["written"] = list(outcome.written)
    result.data.update(outcome.report)
    if dry_run:
        result.note(
            f"COLLECT DRY-RUN OK: planned {len(config.sources)} source(s), offline, "
            "no credentials resolved"
        )
    else:
        incomplete = sum(
            1
            for s in outcome.report["sources"]
            if s.get("completeness") == "incomplete"
        )
        result.note(
            f"collected 0 of {len(config.sources)} source(s); {incomplete} incomplete"
        )
    if not outcome.complete:
        result.add_code(int(ExitCode.FINDINGS))
    return result


def cmd_catalog(ns: argparse.Namespace) -> CommandResult:
    result = CommandResult(command="catalog")
    action = _opt_str(ns, "catalog_action")
    if action == "coverage-matrix":
        return _cmd_coverage_matrix(ns)
    if action != "lint":
        raise InputError(
            "input.catalog_action",
            "the catalog actions are `lint` and `coverage-matrix`.",
            "run `agentce catalog lint <dir>` or `agentce catalog coverage-matrix <dir>`.",
        )
    directory = _require_dir(
        _opt_str(ns, "dir"),
        key="dir",
        what="the catalog directory",
        fix="pass the catalog directory: `agentce catalog lint <dir>`.",
    )
    # Lint the catalog at `directory`, or every catalog beneath it (so `catalog lint spec/catalogs`
    # lints the whole tree, not just a directory that itself holds a catalog.yaml).
    if (directory / "catalog.yaml").is_file():
        catalog_dirs = [directory]
    else:
        catalog_dirs = sorted({p.parent for p in directory.rglob("catalog.yaml")})
    problems: list[str] = []
    if not catalog_dirs:
        problems.append(f"{directory}: no catalog.yaml (and none beneath it)")
    for cat_dir in catalog_dirs:
        prefix = "" if len(catalog_dirs) == 1 else f"{cat_dir.relative_to(directory)}: "
        problems.extend(f"{prefix}{p}" for p in lint_catalog(cat_dir))
    result.data.update(
        {
            "action": "lint",
            "dir": str(directory),
            "catalogs": [str(d) for d in catalog_dirs],
            "clean": not problems,
            "problems": problems,
        }
    )
    if problems:
        result.add_code(int(ExitCode.FINDINGS))
        result.note(f"CATALOG FAILED: {directory}: {len(problems)} problem(s)")
        for problem in problems:
            result.note(f"  {problem}")
    else:
        result.note(f"CATALOG OK: {directory}")
    return result


def _cmd_coverage_matrix(ns: argparse.Namespace) -> CommandResult:
    result = CommandResult(command="catalog")
    directory = _require_dir(
        _opt_str(ns, "dir"),
        key="dir",
        what="the catalog directory",
        fix="pass the catalog directory: `agentce catalog coverage-matrix <dir>`.",
    )
    if _flag(ns, "check"):
        ok, message = check_matrix(directory)
        result.data.update(
            {"action": "coverage-matrix", "dir": str(directory), "matrix_ok": ok}
        )
        if ok:
            result.note(f"MATRIX OK: {directory}")
        else:
            result.add_code(int(ExitCode.FINDINGS))
            result.note(f"MATRIX FAILED: {message}")
        return result
    written = write_matrix(directory)
    result.data.update(
        {"action": "coverage-matrix", "dir": str(directory), "written": str(written)}
    )
    result.note(f"wrote {MATRIX_FILE} for {directory}")
    return result


def cmd_conformance(ns: argparse.Namespace) -> CommandResult:
    result = CommandResult(command="conformance")
    action = _opt_str(ns, "conformance_action")
    if action != "run":
        raise InputError(
            "input.conformance_action",
            "the only conformance action is `run`.",
            "run `agentce conformance run ...`.",
        )
    engine = _require_dir(_opt_str(ns, "engine"), key="engine", what="the engine path")
    corpus = _require_dir(
        _opt_str(ns, "corpus"), key="corpus", what="the corpus directory"
    )
    out = _opt_str(ns, "out")
    adapters = _opt_str(ns, "adapters")
    adapters_dir = (
        _require_dir(adapters, key="adapters", what="the adapters directory")
        if adapters is not None
        else None
    )
    report = run_ecs(
        engine_path=engine,
        corpus_dir=corpus,
        out_dir=Path(out) if out else None,
        adapters_dir=adapters_dir,
    )
    result.data.update({"action": "run", "engine": str(engine), "corpus": str(corpus)})
    if out is not None:
        result.data["out"] = out
    result.data.update(report)
    result.note(
        f"ECS: {report['projects']['identical']}/{report['projects']['total']} identical; "
        f"claim {report['claim']}; no_ml {report['no_ml']}"
    )
    adapters_claim = report.get("adapters")
    detail = report.get("adapter_conformance")
    if isinstance(adapters_claim, str) and isinstance(detail, dict):
        result.note(
            f"adapters: {detail.get('identical')}/{detail.get('total')} identical; "
            f"round_trip {detail.get('round_trip')}; claim {adapters_claim}"
        )
    if report["claim"] != "full":
        result.add_code(int(ExitCode.FINDINGS))
    if adapters_claim is not None and adapters_claim != "full":
        result.add_code(int(ExitCode.FINDINGS))
    return result


def cmd_diff(ns: argparse.Namespace) -> CommandResult:
    result = CommandResult(command="diff")
    fix = "pass two assertion files: `agentce diff <report-a> <report-b>`."
    left = _require_file(
        _opt_str(ns, "report_a"),
        key="report_a",
        what="the first assertion set",
        fix=fix,
    )
    right = _require_file(
        _opt_str(ns, "report_b"),
        key="report_b",
        what="the second assertion set",
        fix=fix,
    )
    result.data.update({"report_a": str(left), "report_b": str(right)})
    return _pending(result, "Deterministic report diffing lands in a later work item.")


def cmd_sign(ns: argparse.Namespace) -> CommandResult:
    result = CommandResult(command="sign")
    report_dir = _require_dir(
        _opt_str(ns, "report_dir"),
        key="report_dir",
        what="the report directory",
        fix="pass the report directory: `agentce sign <report-dir> --as claimant|assessor`.",
    )
    role = _opt_str(ns, "as_role")
    if role not in SIGN_ROLES:
        raise InputError(
            "input.sign_role",
            "--as must be `claimant` or `assessor`.",
            "pass --as claimant|assessor.",
        )
    profile = _opt_str(ns, "profile") or "sigstore-public"
    if profile not in SIGN_PROFILES:
        raise InputError(
            "input.sign_profile",
            f"unknown signing profile {profile!r}.",
            f"choose one of: {', '.join(SIGN_PROFILES)}.",
        )
    result.data.update(
        {
            "report_dir": str(report_dir),
            "as": role,
            "profile": profile,
            "dry_run": _flag(ns, "dry_run"),
        }
    )
    return _pending(result, "Signing lands with the release-tooling work item.")


def _readiness_severities(ns: argparse.Namespace) -> dict[str, str]:
    dirs = list(getattr(ns, "catalog_dir", None) or [])
    if not dirs:
        base = _repo_root() / "spec" / "catalogs" / "base"
        dirs = [str(p.parent) for p in sorted(base.glob("*/catalog.yaml"))]
    severities: dict[str, str] = {}
    for directory in dirs:
        catalog = load_catalog(
            _require_dir(directory, key="catalog-dir", what="a catalog directory")
        )
        for control in catalog.controls:
            severities[control.id] = control.severity
    return severities


def cmd_readiness(ns: argparse.Namespace) -> CommandResult:
    """Compute the report-readiness verdict (SPEC §13.3.4 stage 4). Exit 0 for READY and READY WITH
    LIMITATIONS, 1 for NOT READY; the verdict logic lives in the engine, never in a skill."""
    result = CommandResult(command="readiness")
    report_dir = _require_dir(
        _opt_str(ns, "report_dir"),
        key="report_dir",
        what="the report directory",
        fix="pass the report directory: `agentce readiness <report-dir>`.",
    )
    gaps: set[str] = set()
    gaps_path = _opt_str(ns, "gaps")
    if gaps_path:
        text = _require_file(gaps_path, key="gaps", what="the gaps file").read_text(
            "utf-8"
        )
        gaps = set(re.findall(r"\b[A-Z]{2,4}-[0-9]{2}\b", text))
    deviations: list[dict[str, Any]] = []
    dev_path = _opt_str(ns, "deviations")
    if dev_path:
        loaded = yaml.safe_load(
            _require_file(
                dev_path, key="deviations", what="the deviation register"
            ).read_text("utf-8")
        )
        deviations = list((loaded or {}).get("deviations", []))
    verdict = readiness.compute_readiness(
        report_dir,
        severities=_readiness_severities(ns),
        deviations=deviations,
        gaps=gaps,
    )
    result.data.update(verdict)
    out = report_dir / f"report-readiness-{date.today().isoformat()}.md"
    lines = [f"# Report readiness — {verdict['verdict']}", ""]
    if verdict["reasons"]:
        lines += ["## Blocking reasons", *[f"- {r}" for r in verdict["reasons"]], ""]
    if verdict["limitations"]:
        lines += [
            "## Limitations",
            *[f"- {limit}" for limit in verdict["limitations"]],
            "",
        ]
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    result.data["report"] = str(out)
    if verdict["verdict"] == readiness.NOT_READY:
        result.add_code(int(ExitCode.FINDINGS))
    return result


def _repo_root() -> Path:
    """The repository root, from the engine package location (Phase-1 dev layout)."""
    return Path(__file__).resolve().parents[4]


#: Framework → the evidence-source adapter an adopter most likely starts with (SPEC §12).
_FRAMEWORK_ADAPTER = {
    "langgraph": "otel-genai",
    "openai-agents": "otel-genai",
    "claude-agent-sdk": "otel-genai",
    "google-adk": "otel-genai",
    "crewai": "otel-genai",
    "custom-loop": "agentce-emit",
}
INIT_ROLES = ("deployer", "provider", "both")


def cmd_quickstart(ns: argparse.Namespace) -> CommandResult:
    """Assess the bundled quickstart project end to end — one command, offline (SPEC §13.4 AX-1)."""
    result = CommandResult(command="quickstart")
    out = _opt_str(ns, "out")
    if not out:
        raise InputError(
            "input.out_missing", "an output directory is required.", "pass --out <dir>."
        )
    quickstart = _repo_root() / "corpus" / "quickstart"
    catalog_dir = _repo_root() / "spec" / "catalogs" / "base" / "eu-ai-act"
    if not quickstart.is_dir():
        raise InputError(
            "input.quickstart_missing",
            f"the quickstart project is missing at {quickstart}.",
            "reinstall the engine, or run from a checkout that carries corpus/quickstart.",
        )
    assess_ns = argparse.Namespace(
        bundle=str(quickstart / "evidence"),
        profile=str(quickstart / "applicability.yaml"),
        domain=str(quickstart / "domain.linkml.yaml"),
        catalog="eu-ai-act@2026.09",
        catalog_dir=[str(catalog_dir)],
        out=out,
    )
    assess = cmd_assess(assess_ns)
    result.data.update(assess.data)
    result.data["quickstart"] = "ok"
    for code in assess.codes:
        result.add_code(code)
    result.note(
        f"quickstart complete: {assess.data.get('assertions', 0)} assertions; report in {out}"
    )
    return result


def cmd_init(ns: argparse.Namespace) -> CommandResult:
    """Write a starter applicability profile for an adopter (SPEC §13.4 AX-2)."""
    result = CommandResult(command="init")
    if not _flag(ns, "non_interactive"):
        raise InputError(
            "input.init_interactive",
            "interactive init is not available; pass --non-interactive with --subject and --role.",
            "run `agentce init --non-interactive --framework <fw> --subject <id> --role <role> --out <dir>`.",
        )
    out = _opt_str(ns, "out")
    if not out:
        raise InputError(
            "input.out_missing", "an output directory is required.", "pass --out <dir>."
        )
    subject = _opt_str(ns, "subject") or "spiffe://example/agents/my-agent"
    role = _opt_str(ns, "role") or "deployer"
    if role not in INIT_ROLES:
        raise InputError(
            "input.init_role",
            f"--role must be one of {', '.join(INIT_ROLES)}.",
            "pass --role deployer|provider|both.",
        )
    framework = _opt_str(ns, "framework") or "custom-loop"
    adapter = _FRAMEWORK_ADAPTER.get(framework, "otel-genai")

    profile = _render_starter_profile(
        subject=subject, role=role, framework=framework, adapter=adapter
    )
    profile_path = Path(out) / "agentce" / "applicability-profile.yaml"
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    profile_path.write_text(profile, encoding="utf-8")
    result.data.update(
        {
            "out": out,
            "profile": str(profile_path),
            "framework": framework,
            "subject": subject,
            "role": role,
        }
    )
    result.note(
        f"wrote a starter profile to {profile_path}; fill in the TODOs, then run `agentce assess`."
    )
    return result


def _render_starter_profile(
    *, subject: str, role: str, framework: str, adapter: str
) -> str:
    return (
        f"# Starter applicability profile generated by `agentce init` for a {framework} agent "
        "(SPEC §6.5).\n"
        "# Replace every TODO, then assess with `agentce assess --profile this-file ...`.\n"
        "profile_version: 1\n"
        "observation_window:\n"
        '  start: "2026-01-01T00:00:00Z"  # TODO: the start of your assessment window\n'
        '  end: "2026-04-01T00:00:00Z"    # TODO: the end of your assessment window\n'
        "catalogs:\n"
        '  - "eu-ai-act@2026.09"\n'
        "subjects:\n"
        f'  - id: "{subject}"\n'
        f'    role: "{role}"\n'
        "    evidence_sources:\n"
        f'      - adapter: "{adapter}"\n'
        f'        source: "urn:agentce:source:{framework}:TODO"  # TODO: your evidence source id\n'
        '        class: "self_report"\n'
        '        class_justification: "TODO: justify this source\'s trust class (SPEC §5.2)"\n'
    )


def cmd_config(ns: argparse.Namespace) -> CommandResult:
    """Show the resolved engine configuration and each value's source (SPEC §13.4 AX-8)."""
    result = CommandResult(command="config")
    action = _opt_str(ns, "config_action")
    if action != "show":
        raise InputError(
            "input.config_action",
            "the only config action is `show`.",
            "run `agentce config show`.",
        )
    values = resolve_config()
    result.data["values"] = values
    for entry in values:
        result.note(f"{entry['key']} = {entry['value']}  ({entry['source']})")
    return result


def cmd_version(ns: argparse.Namespace) -> CommandResult:
    result = CommandResult(command="version")
    scan = no_ml.evaluate()
    payload: dict[str, Any] = {
        "engine": ENGINE_NAME,
        "engine_version": __version__,
        "spec_version": SPEC_VERSION,
        "supported_catalogs": [],
        "no_ml": scan["result"],
        "no_ml_detail": scan,
    }
    result.data.update(payload)
    for line in (
        f"{ENGINE_NAME} {__version__} (spec {SPEC_VERSION})",
        f"no_ml: {scan['result']}",
    ):
        result.note(line)
    if scan["result"] != "pass":
        # A learned component is present: an input/environment error for a model-free engine.
        result.add_code(3)
    return result
