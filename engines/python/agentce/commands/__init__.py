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
import hashlib
import json
import os
import re
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from .. import (
    ENGINE_NAME,
    SPEC_VERSION,
    __version__,
    bundled,
    messages,
    no_ml,
    readiness,
    verdict,
)
from ..applicability import resolve as resolve_applicability
from ..assess import assess_subjects, evaluated_nothing
from ..bundle import load_bundle
from ..catalog import Catalog, lint_catalog, load_catalog
from ..collect import EnvSecretManager, SourceSpec, load_config, run_collect
from ..config import resolve as resolve_config
from ..conformance import run_ecs
from ..coverage import compute_coverage
from ..coverage_matrix import MATRIX_FILE, check_matrix, write_matrix
from ..error_catalogue import MESSAGE_KEYS, render_errors_md
from ..domain import DomainBinding
from ..environment import inspect_environment
from ..errors import AgentceError, InputError
from ..exit_codes import ExitCode
from ..fail_on import parse_fail_on
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
from .. import signing
from ..canonical import canonical_string, canonicalize

_log = get_logger()

REPORT_FORMATS = ("md", "html", "oscal", "sarif", "public", "pack")
#: Every token `assess --emit` accepts: the six `report --format` has always rendered one at a time,
#: plus report.py's newer renderers (SPEC §9). Kept identical to `report.EMIT_FORMATS`; a test holds
#: the two equal. `remediation` and `skill` are assess-only (Appendix A2 (C)) -- deliberately not in
#: `REPORT_FORMATS`, so `report --format` never accepts either.
EMIT_FORMATS = REPORT_FORMATS + (
    "junit",
    "csv",
    "oscal_xml",
    "pdf",
    "remediation",
    "skill",
)
#: Where a command writes, or reads a project from, when the caller names no directory: the working
#: directory for a project (``init``, ``doctor``) and ``./out`` for a run's output (``assess``,
#: ``quickstart``), so the first command a newcomer types needs no flag.
DEFAULT_PROJECT_DIR = "."
DEFAULT_OUT_DIR = "out"
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
        catalog_dir = _require_dir(catalog, key="catalog", what="the catalog directory")
        return _verify_catalog(result, catalog_dir)
    release_path = Path(release)  # type: ignore[arg-type]
    if not release_path.exists():
        raise InputError(
            "input.release_missing",
            f"the release artifact {release!r} does not exist.",
            "pass --release <bundle-dir-or-envelope>.",
        )
    return _verify_release(result, release_path)


def _verify_catalog(result: CommandResult, catalog_dir: Path) -> CommandResult:
    """Verify a catalog directory's signature offline against the vendored trust root (SPEC §8.7)."""
    recomputed = signing.digest_tree(
        catalog_dir, exclude=frozenset({signing.CATALOG_SIGNATURE_NAME})
    )
    result.data.update({"catalog": str(catalog_dir), "digest": recomputed})
    try:
        verified = signing.verify_catalog_directory(
            catalog_dir, signing.vendored_trust()
        )
    except signing.UnsignedError as exc:
        result.data["verified"] = False
        result.data["reason"] = str(exc)
        result.note(f"catalog {catalog_dir.name}: unsigned")
        result.add_code(int(ExitCode.INPUT_ERROR))
        return result
    except (signing.VerificationError, ValueError, KeyError, IndexError) as exc:
        result.data["verified"] = False
        result.data["reason"] = str(exc)
        result.note(f"catalog {catalog_dir.name}: verification failed — {exc}")
        result.add_code(int(ExitCode.INPUT_ERROR))
        return result
    result.data.update(
        {
            "verified": True,
            "signer": verified.identity,
            "keyid": verified.keyid,
            "keyless": verified.keyless,
        }
    )
    result.note(f"verified catalog {catalog_dir.name}: signer {verified.identity}")
    return result


def _verify_release(result: CommandResult, release_path: Path) -> CommandResult:
    """Verify a release bundle (or a single DSSE envelope) offline against the vendored trust root."""
    trust = signing.vendored_trust()
    if release_path.is_file():
        envelope = json.loads(release_path.read_text("utf-8"))
        verified = signing.verify_envelope(envelope, trust)
        result.data.update(
            {
                "release": str(release_path),
                "verified": True,
                "signer": verified.identity,
                "keyid": verified.keyid,
            }
        )
        result.note(f"verified {release_path.name}: signer {verified.identity}")
        return result
    manifest_path = release_path / "release-manifest.json"
    signatures_path = release_path / "signatures.json"
    if not manifest_path.is_file() or not signatures_path.is_file():
        raise InputError(
            "input.release_bundle",
            f"{release_path} is not a release bundle (release-manifest.json/signatures.json).",
            "pass the --out directory produced by the release tooling.",
        )
    manifest = json.loads(manifest_path.read_text("utf-8"))
    manifest_digest = signing.sha256_prefixed(canonicalize(manifest))
    problems: list[str] = []
    for artifact in manifest.get("artifacts", []):
        artifact_file = release_path / artifact["name"]
        if not artifact_file.is_file():
            problems.append(f"missing artifact {artifact['name']}")
            continue
        actual = signing.sha256_prefixed(artifact_file.read_bytes())
        if actual != artifact.get("digest"):
            problems.append(f"digest mismatch for {artifact['name']}")
    signers: list[dict[str, Any]] = []
    for entry in json.loads(signatures_path.read_text("utf-8")):
        try:
            verified = signing.verify_envelope(entry["envelope"], trust)
            if (
                signing.statement_subject_digest(json.loads(verified.payload))
                != manifest_digest
            ):
                raise signing.VerificationError(
                    "signature does not cover the release manifest"
                )
            signers.append(
                {"profile": entry.get("profile"), "identity": verified.identity}
            )
        except (signing.VerificationError, ValueError, KeyError) as exc:
            problems.append(f"signature ({entry.get('profile')}): {exc}")
    ok = not problems
    result.data.update(
        {
            "release": str(release_path),
            "verified": ok,
            "manifest_digest": manifest_digest,
            "signers": signers,
        }
    )
    if not ok:
        result.data["reason"] = "; ".join(problems)
        result.note(f"release {release_path.name}: verification failed")
        result.add_code(int(ExitCode.INPUT_ERROR))
    else:
        result.note(f"verified release {release_path.name}: {len(signers)} signatures")
    return result


def _parse_emit(raw: str | None) -> frozenset[str] | None:
    """Parse and validate ``--emit``'s comma-separated token list (SPEC §9). ``None`` when the flag
    is absent, so ``write_report`` renders its legacy fixed bundle unchanged. Every token is validated
    here, before any output is written: an assessment that names a format it cannot produce must
    leave nothing behind that looks like a result (mirrors ``cmd_report``'s ``input.report_format``
    check for the single-format ``--format`` flag)."""
    if raw is None:
        return None
    tokens = [t.strip() for t in raw.split(",") if t.strip()]
    invalid = [t for t in tokens if t not in EMIT_FORMATS]
    if invalid:
        raise InputError(
            "input.emit_format",
            f"unknown --emit format(s): {', '.join(repr(t) for t in invalid)}.",
            f"choose from: {', '.join(EMIT_FORMATS)}.",
        )
    return frozenset(tokens)


def cmd_assess(ns: argparse.Namespace) -> CommandResult:
    result = CommandResult(command="assess")
    bundle = _require_dir(
        _opt_str(ns, "bundle"), key="bundle", what="the evidence bundle"
    )
    profile = _require_file(
        _opt_str(ns, "profile"), key="profile", what="the applicability profile"
    )
    catalog = _opt_str(ns, "catalog")
    out = _opt_str(ns, "out") or DEFAULT_OUT_DIR
    # --emit is validated before any output is written (below, before ingest/quarantine): an unknown
    # token must never leave a partial or misleading result behind.
    emit = _parse_emit(_opt_str(ns, "emit"))
    # --fail-on is parsed (never eval'd) before any output is written too: a hostile or malformed
    # expression is refused at exit 3 before any assertion is evaluated against it (SPEC §7).
    fail_on_raw = _opt_str(ns, "fail_on")
    fail_on_predicate = parse_fail_on(fail_on_raw) if fail_on_raw is not None else None
    # Resolve the catalogs before any output is written: a run that cannot name what it evaluates
    # against — or cannot verify it — must leave nothing behind that looks like a result.
    profile_obj = Profile.load(profile)
    catalogs, catalog_labels, limitations = _resolve_catalogs(
        catalog,
        profile_obj,
        list(getattr(ns, "catalog_dir", None) or []),
        _effective_trust_root(ns),
        allow_unverified=_flag(ns, "allow_unverified_catalog"),
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
    coverage = compute_coverage(ingested.accepted, profile_obj, loaded.root)
    (out_dir / "coverage.json").write_text(
        json.dumps(coverage, sort_keys=True, indent=2), encoding="utf-8"
    )
    # Stage 5: applicability resolution and drift (catalog controls arrive with item 1.9).
    statements = resolve_applicability(profile_obj, ingested.accepted)
    _write_jsonl(statements, out_dir / "applicability.jsonl")
    drift_findings = sum(len(s["drift"]) for s in statements)
    # Stage 6: catalog evaluation and report artifacts. Each resolved catalog is evaluated
    # against every subject to produce assertions; the report artifacts are rendered from them.
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
    command_name = _opt_str(ns, "command_name") or "assess"
    invocation = (
        ["quickstart"]
        if command_name == "quickstart"
        else ["assess", _scrub_path(bundle), _scrub_path(profile)]
    )
    # The actual re-verify invocation a remediation finding names (SPEC §7): unlike `invocation`
    # above (kept positional for backward compatibility with the manifest), this carries every flag
    # needed to reproduce this run's catalog resolution exactly -- `--catalog` (the resolved
    # id@version labels, always reproducible regardless of how they were originally supplied),
    # every `--catalog-dir`, and `--domain` when one was given.
    reverify_argv = [
        "assess",
        "--bundle",
        _scrub_path(bundle),
        "--profile",
        _scrub_path(profile),
    ]
    if catalog_labels:
        reverify_argv += ["--catalog", ",".join(catalog_labels)]
    for catalog_dir in list(getattr(ns, "catalog_dir", None) or []):
        reverify_argv += ["--catalog-dir", _scrub_path(catalog_dir)]
    if domain_path is not None:
        reverify_argv += ["--domain", _scrub_path(domain_path)]
    write_report(
        out_dir,
        evaluated,
        bundle_digest=loaded.digest,
        catalogs=catalogs,
        operator=_operator(),
        invocation=invocation,
        supersedes=supersedes,
        report_language=_opt_str(ns, "report_language") or "en",
        limitations=limitations,
        emit=emit,
        events=ingested.accepted,
        reverify_command=reverify_argv,
    )
    if state is not None:
        state.record(loaded.digest, out_dir / "manifest.json", new_window_end)
    non_conformant = sum(1 for a in evaluated if a.outcome == "non-conformant")
    summary = verdict.summarize(evaluated)
    result.data.update(
        {
            "bundle": str(bundle),
            "bundle_digest": loaded.digest,
            "profile": str(profile),
            "catalogs": catalog_labels,
            "out": out,
            "emit": sorted(emit)
            if emit is not None
            else ["html", "md", "oscal", "pack", "sarif"],
            "accepted": len(ingested.accepted),
            "quarantined": len(ingested.quarantined),
            "streams": len(integrity_results),
            "graph_triples": graph_triples,
            "subjects": len(coverage["subjects"]),
            "drift_findings": drift_findings,
            "assertions": len(evaluated),
            "summary": summary,
        }
    )
    if state is not None:
        result.data["supersedes"] = supersedes
        result.data["late_events"] = late_events
    if limitations:
        # The override is never silent: it is on stderr for the operator and in the manifest and the
        # claim for every later reader (SPEC §8.7).
        result.data["limitations"] = limitations
        for limitation in limitations:
            result.note(limitation)
    if fail_on_predicate is not None:
        # A policy-scoped gate replaces the default any-non-conformant rule: the exit code reflects
        # only the assertions the expression names, never the whole run (SPEC §8.5, finding #29).
        fail_on_matches = sum(1 for a in evaluated if fail_on_predicate(a))
        result.data["fail_on"] = {"expression": fail_on_raw, "matched": fail_on_matches}
        if fail_on_matches:
            result.add_code(int(ExitCode.FINDINGS))
    elif non_conformant:
        result.add_code(int(ExitCode.FINDINGS))
    if evaluated_nothing(evaluated):
        raise _nothing_evaluated(profile_obj, ingested.accepted, len(evaluated))
    for line in _verdict_lines(ns, summary, out):
        result.note(line)
    result.note(
        f"assessed {len(evaluated)} (control, subject) pairs; {non_conformant} non-conformant"
    )
    return result


def _verdict_lines(
    ns: argparse.Namespace, summary: dict[str, Any], out: str
) -> list[str]:
    """The verdict, tally, top gaps, and next step a run prints, in the report language."""
    catalogue = messages.catalogue(_opt_str(ns, "report_language") or "en")
    return verdict.cli_lines(summary, catalogue, report_dir=out)


def _vendored_catalogs() -> dict[str, Path]:
    """Every catalog the engine ships (base and sector overlays), keyed ``id@version``."""
    root = bundled.catalogs_dir()
    found: dict[str, Path] = {}
    for catalog_yaml in sorted(root.glob("*/*/catalog.yaml")):
        if catalog_yaml.parent.parent.name not in ("base", "overlays"):
            continue
        try:
            meta = yaml.safe_load(catalog_yaml.read_text(encoding="utf-8")) or {}
            found[f"{meta['id']}@{meta['version']}"] = catalog_yaml.parent
        except (OSError, yaml.YAMLError, KeyError, TypeError):
            continue
    return found


def _effective_trust_root(ns: argparse.Namespace) -> signing.TrustRoot:
    """The trust root catalog verification consults: ``--trust-root``, else ``AGENTCE_TRUST_ROOT``,
    else the trust root vendored in the engine (SPEC §8.7).

    The vendored development root is the default so the engine's own signed catalogs verify offline
    out of the box; a deployment that signs its catalogs with its own keys points either surface at
    its own root, and that root — never the vendored one — is what verification then uses."""
    raw = _opt_str(ns, "trust_root") or os.environ.get("AGENTCE_TRUST_ROOT")
    if raw is None or not raw.strip():
        return signing.vendored_trust()
    path = _require_file(raw, key="trust_root", what="the trust root")
    try:
        return signing.load_trust_root(path)
    except signing.VerificationError as exc:
        raise InputError(
            "input.trust_root_invalid",
            f"the trust root {str(path)!r} could not be loaded: {exc}",
            "pass --trust-root <file> (or set AGENTCE_TRUST_ROOT) to a trust root in the form of "
            "the engine's vendored data/trust/dev-root.json.",
        ) from exc


def _verify_catalog_dirs(
    loaded: list[Catalog], trust: signing.TrustRoot, *, allow_unverified: bool = False
) -> list[str]:
    """Refuse every ``--catalog-dir`` catalog whose signature does not verify (SPEC §8.7).

    A catalog an operator points the engine at is untrusted input: unsigned, signed by a key the
    effective trust root does not know, or signed over different bytes than the directory holds, it
    cannot be evaluated. The refusal happens before any control runs and before anything is written,
    so an unverifiable catalog never produces a report that looks as confident as a verified one.

    ``allow_unverified`` is the operator's explicit ``--allow-unverified-catalog`` override (SPEC
    §8.7): the run proceeds and each unverifiable catalog is returned as a limitation string, which
    the caller records in the manifest and the claim so no reader can mistake the report for one
    produced from verified inputs. The override covers *only* what this function checks — an absent
    or failing signature; the identity cross-check in :func:`_resolve_catalogs` has no override."""
    limitations: list[str] = []
    for catalog in loaded:
        try:
            signing.verify_catalog_directory(catalog.directory, trust)
        except signing.VerificationError as exc:
            if allow_unverified:
                limitations.append(
                    f"catalog {catalog.id}@{catalog.version} at "
                    f"{_scrub_path(catalog.directory)} was used unverified "
                    f"(--allow-unverified-catalog): {exc}"
                )
                continue
            raise InputError(
                "input.catalog_unverified",
                f"the catalog directory {_scrub_path(catalog.directory)} did not verify against "
                f"the effective trust root: {exc}",
                "point --catalog-dir at a catalog whose catalog.sig.json verifies, or pass "
                "--trust-root <file> (or set AGENTCE_TRUST_ROOT) for the root that signed it; "
                "--allow-unverified-catalog assesses it anyway and records the override as a "
                "limitation.",
            ) from exc
    return limitations


def _resolve_catalogs(
    requested: str | None,
    profile: Profile,
    catalog_dirs: list[str],
    trust: signing.TrustRoot,
    *,
    allow_unverified: bool = False,
) -> tuple[list[Catalog], list[str], list[str]]:
    """The catalogs an assessment evaluates, their ``id@version`` labels, and any limitations.

    Each ``--catalog-dir`` is loaded as given (the explicit override) and its signature verified
    against ``trust`` before anything else happens. Each requested id — the ``--catalog`` list, else
    the profile's declared ``catalogs`` when no directory was passed — must resolve to a directory
    that was passed or to a vendored catalog, and every directory that was passed must be one the
    request names. An id that resolves to nothing, a directory the request does not name, or a
    request that names no catalog at all, is an input error: an assessment must not proceed to judge
    nothing, nor to judge something other than what was asked for.

    The returned limitations are the signature checks ``--allow-unverified-catalog`` waived, for the
    manifest and the claim to record; the list is empty on an ordinary run."""
    loaded = [
        load_catalog(_require_dir(d, key="catalog-dir", what="the catalog directory"))
        for d in catalog_dirs
    ]
    limitations = _verify_catalog_dirs(loaded, trust, allow_unverified=allow_unverified)
    by_label = {f"{c.id}@{c.version}": c for c in loaded}
    if requested:
        ids = [i.strip() for i in requested.split(",") if i.strip()]
    elif catalog_dirs:
        ids = []
    else:
        ids = list(profile.catalogs)
    if not ids and not loaded:
        raise InputError(
            "input.catalog_missing",
            "no catalog to evaluate: --catalog and --catalog-dir were not passed and the profile "
            "declares no catalogs.",
            "pass --catalog <id@version>, or list the catalogs to apply under `catalogs:` in the "
            "profile.",
        )
    unresolved = [i for i in dict.fromkeys(ids) if i not in by_label]
    if unresolved:
        vendored = _vendored_catalogs()
        for label in [u for u in unresolved if u in vendored]:
            by_label[label] = load_catalog(vendored[label])
        unresolved = [u for u in unresolved if u not in vendored]
    if unresolved:
        available = sorted(set(by_label) | set(_vendored_catalogs()))
        raise InputError(
            "input.catalog_unresolved",
            f"no catalog directory resolves {', '.join(repr(u) for u in unresolved)} "
            f"(available: {', '.join(available) or 'none'}).",
            "use an available <id>@<version>, or pass --catalog-dir <dir> for a catalog on disk.",
        )
    # A verified signature proves the bytes were not altered; it does not prove the directory holds
    # the catalog that was asked for. When both a request and a directory were given, every directory
    # must carry an id@version the request names — a rebranded or swapped catalog is refused here even
    # though its own signature verifies. This step is deliberately separate from the signature check
    # above and has no override: SPEC §8.7's --allow-unverified-catalog waives "unsigned or
    # unverifiable", and a catalog whose signature verifies but whose identity is not the one
    # requested is neither, so it is refused whether or not the flag was passed.
    if ids and loaded:
        wanted = set(ids)
        unrequested = [
            f"{c.id}@{c.version}" for c in loaded if f"{c.id}@{c.version}" not in wanted
        ]
        if unrequested:
            raise InputError(
                "input.catalog_mismatch",
                f"a --catalog-dir carries {', '.join(repr(u) for u in unrequested)}, which "
                f"--catalog did not request ({', '.join(repr(i) for i in ids)}).",
                "pass --catalog-dir for the catalog you named, or name the id@version the "
                "directory carries.",
            )
    labels = list(dict.fromkeys([*ids, *(f"{c.id}@{c.version}" for c in loaded)]))
    return [by_label[label] for label in labels], labels, limitations


def _nothing_evaluated(
    profile: Profile, accepted: list[dict[str, Any]], pairs: int
) -> InputError:
    """The exit-3 error for a run whose every (control, subject) pair was inapplicable or unassessed."""
    declared = sorted(s.id for s in profile.subjects)
    seen = sorted({str(e["subject"]) for e in accepted if "subject" in e})
    matched = set(declared) & set(seen)
    if not accepted:
        why = "the bundle has no accepted events"
    elif not matched:
        why = (
            f"none of the {len(accepted)} accepted events is about a subject the profile declares "
            f"(profile: {_ids(declared)}; bundle: {_ids(seen)})"
        )
    else:
        why = (
            f"the {len(accepted)} accepted events give no control an applicable population "
            f"(subjects matched: {_ids(sorted(matched))})"
        )
    return InputError(
        "input.nothing_evaluated",
        f"assessed {pairs} (control, subject) pairs and none reached conformant, non-conformant, "
        f"or insufficient_evidence: {why}. The reports were written, but they judge nothing.",
        "emit under the subject and source the profile declares (agentce-emit reads "
        "AGENTCE_EMIT_SUBJECT and AGENTCE_EMIT_SOURCE), and record the evidence the catalog's "
        "controls apply to.",
    )


def _ids(ids: list[str], cap: int = 3) -> str:
    """A bounded, quoted rendering of identifiers, for a message that must stay one line."""
    shown = ", ".join(repr(i[:80]) for i in ids[:cap]) or "none"
    return shown + (f" and {len(ids) - cap} more" if len(ids) > cap else "")


def _operator() -> str:
    """The run's provenance identity (SPEC §8.4): a string the deployer controls via the
    ``operator`` config key (``AGENTCE_OPERATOR`` or ``agentce.toml``), never the invoking OS
    user by default — the OS username and home directory are not the deployer's to disclose."""
    return str(next(v["value"] for v in resolve_config() if v["key"] == "operator"))


def _scrub_path(path: str | Path) -> str:
    """A path safe to write into a shared artifact (SPEC §8.4: ``invocation`` records paths,
    never a person or their local filesystem layout). Never returns an absolute path: under the
    home directory it becomes ``~/…``; else under the current directory it becomes a relative
    path; anywhere else — a devcontainer's `/workspaces`, an `/opt` checkout, a path outside both
    — only the final path component is kept. Compares the stated path, not where a symlink might
    ultimately point, so a path reached through a symlink under the home directory is still
    caught."""
    p = Path(os.path.abspath(path))
    home = Path(os.path.abspath(Path.home()))
    try:
        return "~/" + p.relative_to(home).as_posix()
    except ValueError:
        pass
    cwd = Path(os.path.abspath(Path.cwd()))
    try:
        return p.relative_to(cwd).as_posix()
    except ValueError:
        pass
    return p.name


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
    language = _opt_str(ns, "language") or "en"
    rendering: str
    if fmt == "md":
        rendering = render_report_md(assertions, counts, language=language)
    elif fmt == "html":
        rendering = render_report_html(assertions, counts, language=language)
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


def _adapt_export(
    adapters_root: Path,
    adapter: str,
    export_path: Path,
    *,
    subject: str,
    source_class: str,
    source: str | None = None,
    engine: str | None = None,
) -> list[dict[str, Any]]:
    """Adapt ``export_path`` through ``adapter``, in that adapter's own environment.

    Every v1 adapter package shares the name ``agentce_adapters``, so it cannot be imported directly
    alongside another; this shells out to ``adapters/_ingest.py`` in the adapter's own ``uv`` project,
    mirroring the engine conformance suite's own subprocess pattern for the identical reason.
    """
    adapter_dir = adapters_root / adapter
    if not adapter_dir.is_dir():
        raise InputError(
            "input.adapter_not_found",
            f"no adapter directory at {adapter_dir}.",
            "pass --adapters-root pointing at the adapters checkout, or check the adapter name.",
        )
    script = adapters_root / "_ingest.py"
    cmd = [
        "uv",
        "run",
        "--project",
        str(adapter_dir),
        "--frozen",
        "--quiet",
        "python",
        str(script),
        str(adapter_dir),
        str(export_path),
        "--subject",
        subject,
        "--source-class",
        source_class,
    ]
    if source:
        cmd += ["--source", source]
    if engine:
        cmd += ["--engine", engine]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise InputError(
            "input.ingest_failed",
            f"adapter {adapter!r} could not adapt {export_path}: "
            f"{(proc.stderr or proc.stdout).strip()[-400:]}",
            "check the export file matches the adapter's expected shape.",
        )
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise InputError(
            "input.ingest_failed",
            f"adapter {adapter!r} printed a non-JSON result for {export_path}: {exc}.",
            "check the export file matches the adapter's expected shape.",
        ) from exc
    if "error" in payload:
        raise InputError(
            "input.ingest_failed",
            f"adapter {adapter!r}: {payload['error']}",
            "check the export file matches the adapter's expected shape.",
        )
    return list(payload["events"])


def _write_event_bundle(
    out_dir: Path, events: list[dict[str, Any]], *, adapter: str
) -> None:
    """Write ``events`` to an evidence bundle at ``out_dir`` (``events/*.jsonl`` + ``manifest.json``)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    events_dir = out_dir / "events"
    events_dir.mkdir(exist_ok=True)
    stream = events_dir / "stream.jsonl"
    stream.write_text(
        "".join(canonical_string(event) + "\n" for event in events), encoding="utf-8"
    )
    digest = hashlib.sha256(stream.read_bytes()).hexdigest()
    source_classes = {
        str(event["source"]): str(event["agentcesourceclass"]) for event in events
    }
    manifest = {
        "agentce_bundle_version": 1,
        "sources": [
            {"id": source, "adapter": adapter, "class": cls}
            for source, cls in sorted(source_classes.items())
        ],
        "files": [{"path": "events/stream.jsonl", "sha256": digest}],
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )


def cmd_ingest(ns: argparse.Namespace) -> CommandResult:
    result = CommandResult(command="ingest")
    in_path = _require_file(
        _opt_str(ns, "in_path"), key="in", what="the adapter export file"
    )
    out = _opt_str(ns, "out")
    if not out:
        raise InputError(
            "input.out_missing",
            "ingest needs an output bundle path.",
            "pass --out <bundle>.",
        )
    adapter = _opt_str(ns, "adapter")
    if not adapter:
        raise InputError(
            "input.adapter_missing",
            "ingest needs an adapter.",
            "pass --adapter, e.g. --adapter otel-genai.",
        )
    adapters_root = Path(_opt_str(ns, "adapters_root") or "adapters")
    subject = _opt_str(ns, "subject") or DEFAULT_SUBJECT
    source_class = _opt_str(ns, "source_class") or "self_report"
    source = _opt_str(ns, "source")
    engine = _opt_str(ns, "engine")

    events = _adapt_export(
        adapters_root,
        adapter,
        in_path,
        subject=subject,
        source_class=source_class,
        source=source,
        engine=engine,
    )
    if not events:
        raise InputError(
            "input.ingest_empty",
            f"adapter {adapter!r} produced no events from {in_path}.",
            "check the export file actually contains records the adapter recognizes.",
        )
    out_dir = Path(out)
    _write_event_bundle(out_dir, events, adapter=adapter)
    result.data.update(
        {
            "in": str(in_path),
            "out": str(out_dir),
            "adapter": adapter,
            "events": len(events),
        }
    )
    result.note(
        f"ingested {len(events)} event(s) from {in_path} via {adapter} into {out_dir}"
    )
    return result


@dataclass
class _SubprocessExportAdapter:
    """The engine's ``ExportAdapter`` (SPEC §5.4): runs a source's named adapter as a subprocess."""

    adapters_root: Path

    def adapt(self, source: SourceSpec, export_path: Path) -> list[dict[str, Any]]:
        return _adapt_export(
            self.adapters_root,
            source.adapter,
            export_path,
            subject=source.subject or DEFAULT_SUBJECT,
            source_class=source.source_class or "self_report",
            source=source.id,
            engine=source.engine,
        )


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
    adapters_root = Path(_opt_str(ns, "adapters_root") or "adapters")
    outcome = run_collect(
        config,
        out_dir=Path(out) if out else None,
        dry_run=dry_run,
        secret_manager=EnvSecretManager(),
        export_adapter=None if dry_run else _SubprocessExportAdapter(adapters_root),
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
        complete_count = sum(
            1 for s in outcome.report["sources"] if s.get("completeness") == "complete"
        )
        incomplete = sum(
            1
            for s in outcome.report["sources"]
            if s.get("completeness") == "incomplete"
        )
        result.note(
            f"collected {complete_count} of {len(config.sources)} source(s); {incomplete} incomplete"
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
    require_flags = _flag(ns, "require_verification_flags")
    require_prov = _flag(ns, "require_provenance")
    for cat_dir in catalog_dirs:
        prefix = "" if len(catalog_dirs) == 1 else f"{cat_dir.relative_to(directory)}: "
        problems.extend(
            f"{prefix}{p}"
            for p in lint_catalog(
                cat_dir,
                require_verification_flags=require_flags,
                require_provenance=require_prov,
            )
        )
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


def _diff_assertion_sets(
    a: list[dict[str, Any]], b: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """A real, content-keyed delta between two assertion sets: every ``(control, subject)`` whose
    outcome changed, added, or was removed -- keyed by identity, never by array position, so the
    result is independent of either input's element order."""

    def by_key(entries: list[dict[str, Any]]) -> dict[tuple[str, str], str]:
        return {
            (str(e["control"]), str(e["subject"])): str(e["outcome"]) for e in entries
        }

    left, right = by_key(a), by_key(b)
    changes = []
    for key in sorted(set(left) | set(right)):
        before, after = left.get(key), right.get(key)
        if before != after:
            control, subject = key
            changes.append(
                {"control": control, "subject": subject, "from": before, "to": after}
            )
    return changes


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
    a = json.loads(left.read_text("utf-8"))
    b = json.loads(right.read_text("utf-8"))
    changes = _diff_assertion_sets(a, b)
    result.data["changed"] = len(changes)
    result.data["diff"] = changes
    if changes:
        result.add_code(int(ExitCode.FINDINGS))
        result.note(f"{len(changes)} assertion(s) differ:")
        for c in changes:
            result.note(f"  {c['control']} @ {c['subject']}: {c['from']} -> {c['to']}")
    else:
        result.note("no differences")
    return result


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
    dry_run = _flag(ns, "dry_run")
    result.data.update(
        {
            "report_dir": str(report_dir),
            "as": role,
            "profile": profile,
            "dry_run": dry_run,
        }
    )

    # The engine refuses to sign a report that is not ready to publish (SPEC §8.5).
    verdict = readiness.compute_readiness(
        report_dir, severities=_readiness_severities(ns)
    )
    result.data["readiness"] = verdict["verdict"]
    if verdict["verdict"] == readiness.NOT_READY:
        raise InputError(
            "sign.not_ready",
            f"the report is {verdict['verdict']}: {'; '.join(verdict['reasons'])}.",
            "resolve the blocking reasons (agentce readiness <report-dir>) before signing.",
        )

    if dry_run:
        result.note(f"dry run: would sign the claim as {role} ({profile})")
        return result

    claim_path = report_dir / "claim.json"
    if not claim_path.is_file():
        raise InputError(
            "sign.no_claim",
            "the report directory has no claim.json to sign.",
            "produce the report first: `agentce assess … --out <report-dir>`.",
        )
    claim = json.loads(claim_path.read_text("utf-8"))

    signer = _sign_signer(ns, profile)
    subjects = _sign_subjects(report_dir, claim)
    statement = {
        "_type": signing.INTOTO_STATEMENT_TYPE,
        "subject": subjects,
        "predicateType": "https://agent-conformance.org/attestation/claim/v1",
        "predicate": {
            "role": role,
            "profile": profile,
            "statement": (
                "This report states conformance to the named catalogs as evaluated by the named "
                "engine over the named evidence. It is not a legal compliance determination."
            ),
        },
    }
    envelope = signing.sign_statement(statement, signer)
    record = {"role": role, "profile": profile, **envelope}
    claim.setdefault("signatures", []).append(record)
    claim_path.write_text(
        json.dumps(claim, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    sig_dir = report_dir / "signatures"
    sig_dir.mkdir(exist_ok=True)
    detached = sig_dir / f"{role}-{profile}.dsse.json"
    detached.write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    result.data.update(
        {
            "signature": str(detached),
            "keyid": signer.keyid,
            "signatures": len(claim["signatures"]),
        }
    )
    result.note(f"signed {claim_path.name} as {role} ({profile})")
    return result


def _sign_subjects(report_dir: Path, claim: dict[str, Any]) -> list[dict[str, Any]]:
    """The in-toto subjects a claim signature covers: the claim body and the manifest, by digest."""
    body = {k: v for k, v in claim.items() if k != "signatures"}
    subjects = [
        {
            "name": "claim.json",
            "digest": {"sha256": hashlib.sha256(canonicalize(body)).hexdigest()},
        }
    ]
    manifest_path = report_dir / "manifest.json"
    if manifest_path.is_file():
        subjects.append(
            {
                "name": "manifest.json",
                "digest": {
                    "sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest()
                },
            }
        )
    return subjects


def _sign_signer(ns: argparse.Namespace, profile: str) -> signing.Signer:
    """Resolve the operator's signing key for ``agentce sign`` (SPEC §9.1).

    ``kms`` signs with an operator-held key passed as ``--key`` (Ed25519 PEM); the engine holds no
    signing identity of its own. The keyless ``sigstore-*`` profiles obtain a short-lived certificate
    from a Fulcio instance, which needs network and is exercised by the release tooling; ``agentce
    sign`` reports the requirement rather than pretending to reach it offline.
    """
    key_path = _opt_str(ns, "key")
    if profile == "kms":
        if not key_path:
            raise InputError(
                "sign.kms_key_missing",
                "the kms profile signs with an operator-held key.",
                "pass --key <ed25519-private-key.pem>.",
            )
        from cryptography.hazmat.primitives.serialization import load_pem_private_key

        key_file = _require_file(key_path, key="key", what="the signing key")
        loaded = load_pem_private_key(key_file.read_bytes(), password=None)
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        if not isinstance(loaded, Ed25519PrivateKey):
            raise InputError(
                "sign.key_algorithm",
                "the signing key is not an Ed25519 private key.",
                "supply an Ed25519 key (the algorithm the engine signs with, SPEC §8.7).",
            )
        return signing.KmsSigner(private_key=loaded)
    raise InputError(
        "sign.keyless_offline",
        f"the {profile} profile is keyless and obtains a certificate from a Fulcio instance "
        "(network); the engine does not sign it offline.",
        "use --profile kms --key <file> offline, or run keyless signing where the Fulcio and "
        "Rekor endpoints are reachable.",
    )


def _readiness_severities(ns: argparse.Namespace) -> dict[str, str]:
    dirs = list(getattr(ns, "catalog_dir", None) or [])
    if not dirs:
        base = bundled.catalogs_dir() / "base"
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


def _doctor_problem(key: str, problem: str, fix: str | None = None) -> dict[str, str]:
    entry = MESSAGE_KEYS.get(key)
    return {"key": key, "problem": problem, "fix": fix or (entry.fix if entry else "")}


#: The project layout shared by ``init`` (which writes it), ``doctor`` (which checks it) and the
#: documentation: declarations live in ``<project>/agentce/``.
DECLARATIONS_DIR = "agentce"
PROFILE_FILE = "applicability.yaml"
DOMAIN_FILE = "domain.linkml.yaml"


def _declaration_path(project: Path, name: str) -> Path:
    """Where ``name`` lives in ``project``: ``agentce/<name>``, else the flat layout the vendored
    quickstart project uses (``<name>`` beside the evidence directory)."""
    nested = project / DECLARATIONS_DIR / name
    return (
        nested if nested.is_file() or not (project / name).is_file() else project / name
    )


def cmd_doctor(ns: argparse.Namespace) -> CommandResult:
    """Diagnose the interpreter, toolchain, and a project's declarations, sources, and bundle, naming
    the exact fix for each problem (SPEC §13.4 AX-5). With ``--write-errors`` it regenerates the message-key catalogue instead."""
    result = CommandResult(command="doctor")
    write_errors = _opt_str(ns, "write_errors")
    if write_errors:
        path = Path(write_errors)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render_errors_md(), encoding="utf-8")
        result.data.update({"errors_md": str(path)})
        result.note(f"wrote {path}")
        return result

    project = _require_dir(
        _opt_str(ns, "project") or DEFAULT_PROJECT_DIR,
        key="project",
        what="the project directory",
        fix="pass --project <dir> (a project from agentce quickstart or agentce init).",
    )
    environment, problems = inspect_environment()
    declared_sources: set[str] = set()

    profile_path = _declaration_path(project, PROFILE_FILE)
    if not profile_path.is_file():
        problems.append(
            _doctor_problem(
                "input.profile_missing",
                f"no {PROFILE_FILE} in {project / DECLARATIONS_DIR} or {project}",
            )
        )
    else:
        from ..tools.validate_profile import validate_profile

        profile_data = yaml.safe_load(profile_path.read_text("utf-8")) or {}
        for problem in validate_profile(profile_data):
            problems.append(
                _doctor_problem(
                    "schema_invalid",
                    problem,
                    "correct the profile to match applicability-profile.schema.json.",
                )
            )
        declared_sources = {
            str(source.get("source"))
            for subject in profile_data.get("subjects", [])
            for source in subject.get("evidence_sources", [])
            if source.get("source")
        }

    bundle_dir = project / "evidence"
    if not bundle_dir.is_dir():
        problems.append(
            _doctor_problem(
                "input.bundle_manifest_missing",
                f"no evidence bundle at {bundle_dir}",
                f"run your agent with the emitter on, `AGENTCE_EMIT=1 AGENTCE_EMIT_OUT={bundle_dir}` "
                "(see docs/integrate.md), or watch one built with "
                f"`examples/custom-loop/run.sh {bundle_dir}` from a checkout; then check it with "
                f"`agentce validate --bundle {bundle_dir}`.",
            )
        )
    else:
        try:
            bundle = load_bundle(bundle_dir)
            manifest_sources = {
                str(source.get("id")) for source in bundle.manifest.get("sources", [])
            }
            for source in sorted(declared_sources - manifest_sources):
                problems.append(
                    _doctor_problem(
                        "unknown_source",
                        f"source {source} is declared but not in the bundle manifest",
                        "add the source's stream and manifest entry, or remove the declaration.",
                    )
                )
        except AgentceError as err:
            problems.append(_doctor_problem(err.key, err.cause, err.fix))

    if not _declaration_path(project, DOMAIN_FILE).is_file():
        problems.append(
            _doctor_problem(
                "input.catalog_not_found",
                f"no domain binding {DOMAIN_FILE} in {project / DECLARATIONS_DIR} or {project}",
                f"add {DECLARATIONS_DIR}/{DOMAIN_FILE} (`agentce init --out {project}` writes a starter).",
            )
        )

    result.data.update(
        {
            "project": str(project),
            "doctor": "ok" if not problems else "problems",
            "healthy": not problems,
            "environment": environment,
            "problems": sorted(problems, key=lambda p: (p["key"], p["problem"])),
        }
    )
    for advice in environment["notes"]:
        result.note(f"[{advice['key']}] {advice['note']} -> {advice['fix']}")
    if problems:
        result.add_code(int(ExitCode.FINDINGS))
        result.note(f"{project}: {len(problems)} problem(s)")
        for issue in problems:
            result.note(f"  [{issue['key']}] {issue['problem']} -> {issue['fix']}")
    else:
        result.note(f"{project}: healthy")
    return result


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
#: The identities ``agentce_emit.auto()`` emits under when nothing is configured (its
#: ``DEFAULT_SUBJECT`` and ``DEFAULT_SOURCE``); ``init`` writes the same strings so a bare init and a
#: bare ``auto()`` describe one subject and one source. A test holds the two packages equal.
DEFAULT_SUBJECT = "agentce:subject/local"
DEFAULT_EMIT_SOURCE = "urn:agentce:emit:local"


def cmd_quickstart(ns: argparse.Namespace) -> CommandResult:
    """Assess the bundled quickstart project end to end — one command, offline (SPEC §13.4 AX-1)."""
    result = CommandResult(command="quickstart")
    out = _opt_str(ns, "out") or DEFAULT_OUT_DIR
    quickstart = bundled.quickstart_dir()
    catalog_dir = bundled.catalogs_dir() / "base" / "eu-ai-act"
    if not quickstart.is_dir():
        raise InputError(
            "input.quickstart_missing",
            f"the quickstart project is missing at {quickstart}.",
            "reinstall the engine: the quickstart project ships inside the package.",
        )
    assess_ns = argparse.Namespace(
        bundle=str(quickstart / "evidence"),
        profile=str(quickstart / "applicability.yaml"),
        domain=str(quickstart / "domain.linkml.yaml"),
        catalog="eu-ai-act@2026.09",
        catalog_dir=[str(catalog_dir)],
        out=out,
        command_name="quickstart",
    )
    assess = cmd_assess(assess_ns)
    result.data.update(assess.data)
    result.data["quickstart"] = "ok"
    for code in assess.codes:
        result.add_code(code)
    for line in _verdict_lines(assess_ns, assess.data["summary"], out):
        result.note(line)
    result.note(
        f"quickstart complete: {assess.data.get('assertions', 0)} assertions; report in {out}"
    )
    return result


def cmd_init(ns: argparse.Namespace) -> CommandResult:
    """Write a starter applicability profile for an adopter (SPEC §13.4 AX-2)."""
    result = CommandResult(command="init")
    out = _opt_str(ns, "out") or DEFAULT_PROJECT_DIR
    subject = _opt_str(ns, "subject") or DEFAULT_SUBJECT
    role = _opt_str(ns, "role") or "deployer"
    if role not in INIT_ROLES:
        raise InputError(
            "input.init_role",
            f"--role must be one of {', '.join(INIT_ROLES)}.",
            "pass --role deployer|provider|both.",
        )
    framework = _opt_str(ns, "framework") or "custom-loop"
    adapter = _FRAMEWORK_ADAPTER.get(framework, "otel-genai")

    source = (
        DEFAULT_EMIT_SOURCE
        if adapter == "agentce-emit"
        else f"urn:agentce:source:{framework}:TODO"
    )
    profile = _render_starter_profile(
        subject=subject,
        role=role,
        framework=framework,
        adapter=adapter,
        source=source,
    )
    profile_path = Path(out) / DECLARATIONS_DIR / PROFILE_FILE
    domain_path = Path(out) / DECLARATIONS_DIR / DOMAIN_FILE
    existing = [str(p) for p in (profile_path, domain_path) if p.exists()]
    if existing and not _flag(ns, "force"):
        raise InputError(
            "input.init_exists",
            f"init would overwrite {', '.join(existing)}.",
            "pass --force to overwrite, or --out <dir> to write somewhere else.",
        )
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    profile_path.write_text(profile, encoding="utf-8")
    domain_path.write_text(_STARTER_DOMAIN_BINDING, encoding="utf-8")
    result.data.update(
        {
            "out": out,
            "profile": str(profile_path),
            "domain": str(domain_path),
            "framework": framework,
            "subject": subject,
            "source": source,
            "role": role,
        }
    )
    result.note(
        f"wrote a starter profile to {profile_path} and a domain binding to {domain_path}; "
        f"fill in the TODOs, then run `agentce doctor --project {out}`."
    )
    if adapter == "agentce-emit" and (
        subject != DEFAULT_SUBJECT or source != DEFAULT_EMIT_SOURCE
    ):
        result.note(
            f"emit under this subject with AGENTCE_EMIT_SUBJECT={subject} "
            f"AGENTCE_EMIT_SOURCE={source}; without them auto() emits under its own defaults."
        )
    return result


#: An empty binding is valid (SPEC §6.5): no decision type is declared consequential until the
#: adopter names one. The comment shows the shape the graph builder reads.
_STARTER_DOMAIN_BINDING = (
    "# Domain ontology binding generated by `agentce init` (SPEC §6.5). Empty is valid; add the\n"
    "# decision types your agent executes so the graph can flag consequential ones, for example:\n"
    "#\n"
    "# decision_types:\n"
    '#   - id: "dom:CreditDecision"\n'
    '#     subclass_of: "agentce:ConsequentialDecision"\n'
    "#     consequential: true\n"
    '#     required_oversight_modality: "review_before"\n'
    "decision_types: []\n"
    "context_classes: []\n"
)


def _render_starter_profile(
    *, subject: str, role: str, framework: str, adapter: str, source: str
) -> str:
    source_todo = (
        "" if source == DEFAULT_EMIT_SOURCE else "  # TODO: your evidence source id"
    )
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
        f'        source: "{source}"{source_todo}\n'
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
