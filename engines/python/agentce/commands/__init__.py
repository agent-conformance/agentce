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
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .. import ENGINE_NAME, SPEC_VERSION, __version__, no_ml
from ..bundle import load_bundle
from ..coverage import compute_coverage
from ..domain import DomainBinding
from ..errors import InputError
from ..exit_codes import ExitCode
from ..graph import build_graph
from ..ingest import ingest
from ..integrity import IntegrityStatus, verify_bundle
from ..logsetup import get_logger
from ..profile import Profile
from ..quarantine import counts_by_reason, write_quarantine
from ..result import CommandResult
from ..store import GraphStore

_log = get_logger()

REPORT_FORMATS = ("md", "html", "oscal", "sarif", "public")
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
        }
    )
    return _pending(
        result,
        "Ingest, integrity, graph, and coverage complete; the applicability, "
        "evaluation, and report stages land across the later work items.",
    )


def cmd_report(ns: argparse.Namespace) -> CommandResult:
    result = CommandResult(command="report")
    if _flag(ns, "validate"):
        report_dir = _require_dir(
            _opt_str(ns, "validate"), key="validate", what="the report directory"
        )
        result.data["report_dir"] = str(report_dir)
        return _pending(
            result, "Report-artifact schema validation lands in a later work item."
        )
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
    result.data.update({"from": str(source), "format": fmt})
    return _pending(result, "Report rendering lands in a later work item.")


def cmd_collect(ns: argparse.Namespace) -> CommandResult:
    result = CommandResult(command="collect")
    config = _require_file(
        _opt_str(ns, "config"), key="config", what="the collection config"
    )
    out = _opt_str(ns, "out")
    if not out:
        raise InputError(
            "input.out_missing",
            "an output bundle path is required.",
            "pass --out <bundle>.",
        )
    result.data.update(
        {"config": str(config), "out": out, "dry_run": _flag(ns, "dry_run")}
    )
    return _pending(
        result, "Scheduled collection via adapters lands in a later work item."
    )


def cmd_catalog(ns: argparse.Namespace) -> CommandResult:
    result = CommandResult(command="catalog")
    action = _opt_str(ns, "catalog_action")
    if action != "lint":
        raise InputError(
            "input.catalog_action",
            "the only catalog action is `lint`.",
            "run `agentce catalog lint <dir>`.",
        )
    directory = _require_dir(
        _opt_str(ns, "dir"),
        key="dir",
        what="the catalog directory",
        fix="pass the catalog directory: `agentce catalog lint <dir>`.",
    )
    result.data.update({"action": "lint", "dir": str(directory)})
    return _pending(result, "Catalog linting lands with the base catalog work item.")


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
    result.data.update({"action": "run", "engine": str(engine), "corpus": str(corpus)})
    if (out := _opt_str(ns, "out")) is not None:
        result.data["out"] = out
    return _pending(
        result, "The Engine Conformance Suite runner lands in a later work item."
    )


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
