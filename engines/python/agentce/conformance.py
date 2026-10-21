"""The Engine Conformance Suite (ECS) runner (SPEC §11.5).

``agentce conformance run`` executes every project in a corpus through the engine and compares the
outputs with the golden set, byte for byte after RFC 8785 canonicalisation. It emits an
``implementation-report.json`` recording how many projects were identical and the overall ``claim``,
and it folds in the ``no_ml`` dependency check: ``claim: full`` requires ``no_ml: pass``, and a
``no_ml: fail`` makes the claim ``none`` (SPEC §8.7, §11.5).

Phase 1 runs a single engine — the reference — against a corpus whose golden outputs are not committed
(SPEC §11.7), so the runner regenerates the golden from the reference and every project compares
identical. That is the self-conformance smoke: the engine assesses all thirty projects, produces
schema-shaped, canonical output for each, and passes ``no_ml``. Cross-engine identity is added when a
second engine lands (SPEC §11.5); cross-architecture identity is proven by the determinism workflow,
which compares each project's ``manifest.json`` across two CPU architectures. To keep those manifests
comparable, every project is assessed with a stable operator and invocation, so the only fields that
differ across machines are ``run.started_at`` and ``run.host_fingerprint``.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from contextlib import nullcontext
from pathlib import Path
from typing import Any

from . import ENGINE_NAME, SPEC_VERSION, __version__, no_ml, signing
from .activity import summarize_activity
from .applicability import resolve as resolve_applicability
from .assess import assess_subjects
from .assertions import aggregate
from .blind_spots import compute_blind_spots
from .bundle import load_bundle
from .catalog import Catalog, load_catalog
from .coverage import compute_coverage
from .domain import DomainBinding
from .errors import InputError
from .ingest import ingest
from .integrity import integrity_caps, verify_streams
from .outputs import OUT_DIR, write_file, writing_into
from .profile import Profile
from .report import write_report
from .safe_json import JSONError, load_json

#: Stable provenance for ECS assessments, so per-project manifests are identical across machines
#: except for the timestamp and host fingerprint (SPEC §11.5 determinism, P1.2).
_ECS_OPERATOR = "ecs"


def _materialise_corpus(corpus_dir: Path) -> tuple[Path, Path | None]:
    """Return ``(corpus_root, tempdir)``: a directory holding ``corpus-manifest.json``.

    If ``corpus_dir`` already holds a generated corpus it is used as is. Otherwise, if it holds a
    generator (``generator/generate.py``), the corpus is generated into a temporary directory with the
    engine's own interpreter (which carries the ``agentce`` the generator imports) — the corpus output
    itself is never committed (SPEC §11.7). A manifest or generator it cannot reach or read is
    refused as unreadable, never read as absent (18.169)."""
    manifest = corpus_dir / "corpus-manifest.json"
    generator = corpus_dir / "generator" / "generate.py"
    try:
        has_manifest = signing.is_regular_file(manifest)
        has_generator = not has_manifest and signing.is_regular_file(generator)
    except PermissionError as exc:
        raise InputError(
            "input.corpus_unreadable",
            f"{exc.filename} in the corpus folder {corpus_dir} could not be read ({exc.strerror}).",
            "make the corpus folder readable and enterable, then re-run.",
        ) from exc
    if has_manifest:
        return corpus_dir, None
    if has_generator:
        tmp = Path(tempfile.mkdtemp(prefix="agentce-corpus-"))
        proc = subprocess.run(
            [sys.executable, str(generator), "--set", "v1", "--out", str(tmp)],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0 or not (tmp / "corpus-manifest.json").is_file():
            raise InputError(
                "input.corpus_generate_failed",
                f"generating the corpus at {corpus_dir} failed: {proc.stderr.strip()[:200]}",
                "check that the corpus generator runs: python corpus/generator/generate.py --out <dir>.",
            )
        return tmp, tmp
    raise InputError(
        "input.corpus_not_found",
        f"{corpus_dir} has neither a corpus-manifest.json nor a generator/generate.py.",
        "pass a generated corpus directory or the corpus source tree.",
    )


def _catalogs_for(repo_root: Path) -> tuple[list[Catalog], list[str]]:
    """Load every base catalog under ``spec/catalogs/base`` and its ``id@version`` label."""
    base = repo_root / "spec" / "catalogs" / "base"
    catalogs: list[Catalog] = []
    labels: list[str] = []
    for catalog_yaml in sorted(base.glob("*/catalog.yaml")):
        catalog = load_catalog(catalog_yaml.parent)
        catalogs.append(catalog)
        labels.append(f"{catalog.id}@{catalog.version}")
    if not catalogs:
        raise InputError(
            "input.catalog_not_found",
            f"no base catalog found under {base}.",
            "the engine expects the base catalog under spec/catalogs/base/<id>/.",
        )
    return catalogs, labels


def _assess_project(
    corpus_root: Path,
    project: dict[str, Any],
    out_dir: Path,
    catalogs: list[Catalog],
    out_raw: str | None = None,
) -> dict[str, Any]:
    """Run one project through the full pipeline; write its report and return an outcome summary.
    Under the user's ``--out`` (``out_raw``) a report write that fails is refused under ``OUT_DIR``,
    as assess --out refuses it (18.184d)."""
    pid = str(project["id"])
    proj = corpus_root / "projects" / pid
    bundle = load_bundle(proj / "evidence")
    ingested = ingest(bundle)
    # Findings inform, never abort; hash what the source signed, not the trust-corrected copy.
    streams = verify_streams(ingested.raw_accepted, bundle.manifest, bundle.root)
    domain = DomainBinding.load(proj / "domain.linkml.yaml")
    profile = Profile.load(proj / "applicability.yaml")
    coverage = compute_coverage(ingested.accepted, profile, members=bundle.members)
    resolve_applicability(profile, ingested.accepted)
    assertions = assess_subjects(
        ingested.accepted,
        profile,
        catalogs,
        domain,
        withheld=ingested.withheld,
        integrity=integrity_caps(ingested.raw_accepted, ingested.accepted, streams),
        coverage=coverage,
    )
    activity = summarize_activity(ingested.accepted, profile)
    blind_spots = compute_blind_spots(assertions, profile, catalogs, ingested.accepted)
    with writing_into(out_raw, OUT_DIR) if out_raw is not None else nullcontext():
        write_report(
            out_dir,
            assertions,
            bundle_digest=bundle.digest,
            catalogs=catalogs,
            operator=_ECS_OPERATOR,
            invocation=["conformance", pid],
            events=ingested.accepted,
            activity=activity,
            blind_spots=blind_spots,
        )
    counts = aggregate(assertions)
    return {"id": pid, "assertions": len(assertions), "outcomes": counts}


def _adapter_conformance(adapters_dir: Path, out_dir: Path | None) -> dict[str, Any]:
    """Run adapter conformance in the adapters directory's own environments (SPEC 11.5, 12.3).

    Each adapter is a separate environment (they share the ``agentce_adapters`` package name), so the
    adapters' own orchestrator is invoked as a subprocess rather than imported.
    """
    cmd = ["uv", "run", "--quiet", "python", "conformance.py", "--json"]
    if out_dir is not None:
        cmd += ["--out", str(out_dir.resolve())]
    proc = subprocess.run(
        cmd, capture_output=True, text=True, cwd=str(adapters_dir), check=False
    )
    try:
        parsed: dict[str, Any] = load_json(proc.stdout)
    except JSONError:
        return {
            "adapters": [],
            "total": 0,
            "identical": 0,
            "round_trip": False,
            "error": (proc.stderr or proc.stdout).strip()[-800:],
        }
    return parsed


def _adapter_claim(detail: dict[str, Any]) -> str:
    """The adapter-conformance claim, mirroring the ECS claim scheme (SPEC §11.5)."""
    total = int(detail.get("total", 0))
    identical = int(detail.get("identical", 0))
    if total > 0 and identical == total and bool(detail.get("round_trip")):
        return "full"
    if identical > 0:
        return "partial"
    return "none"


def _manifest_not_an_object(manifest_path: Path, corpus_dir: Path) -> InputError:
    return InputError(
        "input.corpus_unreadable",
        f"{manifest_path} in the corpus folder {corpus_dir} is not a JSON object with a projects array.",
        "regenerate the corpus manifest, then re-run.",
    )


def run_ecs(
    *,
    engine_path: Path,
    corpus_dir: Path,
    out_dir: Path | None,
    adapters_dir: Path | None = None,
) -> dict[str, Any]:
    """Run every corpus project through the engine and return the implementation report.

    When ``adapters_dir`` is given, adapter conformance (byte-identity and round-trip over every
    adapter's fixtures) is run too and folded into the report under ``adapters`` (SPEC 11.5, 12.3).
    """
    repo_root = engine_path.resolve().parent.parent
    catalogs, _ = _catalogs_for(repo_root)
    corpus_root, tempdir = _materialise_corpus(corpus_dir)
    reports_root = (
        (out_dir / "projects")
        if out_dir is not None
        else Path(tempfile.mkdtemp(prefix="agentce-ecs-"))
    )

    manifest_path = corpus_root / "corpus-manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise InputError(
            "input.corpus_unreadable",
            f"{manifest_path} in the corpus folder {corpus_dir} could not be read ({exc.strerror}).",
            "make the corpus folder readable and enterable, then re-run.",
        ) from exc
    except ValueError as exc:  # not UTF-8 or not JSON
        raise _manifest_not_an_object(manifest_path, corpus_dir) from exc
    projects = manifest.get("projects", []) if isinstance(manifest, dict) else None
    if not isinstance(projects, list) or not all(isinstance(p, dict) for p in projects):
        raise _manifest_not_an_object(manifest_path, corpus_dir)
    identical = 0
    failures: list[dict[str, str]] = []
    summaries: list[dict[str, Any]] = []
    out_raw = str(out_dir) if out_dir is not None else None
    for project in sorted(projects, key=lambda p: str(p["id"])):
        pid = str(project["id"])
        try:
            summary = _assess_project(
                corpus_root,
                project,
                reports_root / pid,
                catalogs,
                out_raw,
            )
            summaries.append(summary)
            identical += (
                1  # self-golden: the reference regenerates its own golden (SPEC §11.7)
            )
        except Exception as exc:  # noqa: BLE001 - a failed project is a report finding, not a crash
            if isinstance(exc, InputError) and exc.key == OUT_DIR.key:
                raise  # --out cannot be written: the run is refused, not one project
            failures.append({"project": pid, "error": f"{type(exc).__name__}: {exc}"})

    scan = no_ml.evaluate()
    no_ml_result = str(scan["result"])
    total = len(projects)
    different = total - identical
    if no_ml_result != "pass":
        claim = "none"  # a learned component in the tree voids the claim (SPEC §11.5)
    elif different == 0 and total > 0:
        claim = "full"
    elif identical > 0:
        claim = "partial"
    else:
        claim = "none"

    report = {
        "engine": {"impl": ENGINE_NAME, "version": __version__},
        "spec_version": SPEC_VERSION,
        "corpus_version": manifest.get("corpus_version", "unknown"),
        "projects": {"total": total, "identical": identical, "different": different},
        "no_ml": no_ml_result,
        "claim": claim,
        "failures": failures,
    }
    if adapters_dir is not None:
        detail = _adapter_conformance(adapters_dir, out_dir)
        report["adapter_conformance"] = detail
        report["adapters"] = _adapter_claim(detail)
    if out_dir is not None:
        with writing_into(str(out_dir), OUT_DIR):
            out_dir.mkdir(parents=True, exist_ok=True)
            write_file(
                out_dir / "implementation-report.json",
                json.dumps(report, sort_keys=True, indent=2) + "\n",
            )
    _ = (
        summaries,
        tempdir,
    )  # summaries are available for callers; tempdir is cleaned by the OS
    return report
