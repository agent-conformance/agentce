"""Scheduled evidence collection (SPEC §5.4 B8).

``agentce collect`` is a *job*, not a service: given a config that names each source, its adapter, its
endpoint, and a *reference* to a credential (never the credential itself), it pulls evidence and writes
a bundle. This module carries the deterministic, offline parts of that job:

* the config model and its loader;
* the ``SecretManager`` abstraction -- short-lived, read-only credentials are resolved from the
  enterprise's secret manager at run time and used only to authenticate; they are never written to the
  bundle, the state directory, or a log (SPEC §5.4);
* ``--dry-run`` planning: the job's identity and, per source, what *would* be collected -- with the
  credential shown only as its reference, never resolved -- so an operator can review a collection
  before it runs, offline and side-effect-free.

The collecting job's identity and per-source completeness are recorded in the manifest (SPEC §5.4,
§6.6). Live source connectors are out of scope for the reference collector; a real run therefore records
every source ``incomplete`` and exits non-zero rather than writing a clean bundle over a thin one
(SPEC §5.4, "collection failures are recorded, never hidden"). No network, no learned component.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import yaml

from .errors import InputError

#: How a source's credential is referenced (never its value).
_CREDENTIAL_VIAS = frozenset({"secret_ref", "env"})


@dataclass(frozen=True)
class CredentialRef:
    """A *reference* to a credential -- an external secret-manager path or an environment variable."""

    via: str
    ref: str

    def redacted(self) -> dict[str, Any]:
        """The reference as it may appear in a plan or manifest: pointer only, never resolved."""
        return {"via": self.via, "ref": self.ref, "resolved": False}


@dataclass(frozen=True)
class SourceSpec:
    """One source to collect from: an adapter, an endpoint, and a credential reference."""

    id: str
    adapter: str
    endpoint: str
    credential: CredentialRef | None
    source_class: str | None


@dataclass(frozen=True)
class JobSpec:
    """The collecting job's identity, recorded in the manifest (SPEC §5.4, §6.6)."""

    id: str
    principal: str
    window: dict[str, str]


@dataclass(frozen=True)
class CollectConfig:
    job: JobSpec
    sources: tuple[SourceSpec, ...]


class SecretManager(Protocol):
    """Resolves a credential reference to a short-lived, read-only credential at run time.

    Implementations must return the credential for use in authentication only; callers must never
    write the returned value to the bundle, the state directory, or a log (SPEC §5.4).
    """

    def resolve(self, credential: CredentialRef) -> str | None: ...


@dataclass
class EnvSecretManager:
    """Reference secret manager: resolves ``env`` references from the environment.

    ``secret_ref`` references point at an external secret manager that a real deployment injects; the
    reference collector cannot resolve them and returns ``None`` (the source is then ``incomplete``).
    """

    environ: dict[str, str] = field(default_factory=lambda: dict(os.environ))

    def resolve(self, credential: CredentialRef) -> str | None:
        if credential.via == "env":
            return self.environ.get(credential.ref)
        return None


def _credential(raw: object) -> CredentialRef | None:
    if not isinstance(raw, dict):
        return None
    for via in _CREDENTIAL_VIAS:
        ref = raw.get(via)
        if isinstance(ref, str) and ref:
            return CredentialRef(via=via, ref=ref)
    return None


def load_config(path: Path) -> CollectConfig:
    """Parse and validate a collection config (SPEC §5.4 B8); raise :class:`InputError` on any problem."""
    fix = "see docs/integrate.md for the collect config shape."
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise InputError(
            "input.collect_config", f"cannot read {path}: {exc}.", fix
        ) from exc
    if not isinstance(raw, dict):
        raise InputError("input.collect_config", "the config is not a mapping.", fix)

    job_raw = raw.get("job")
    if not isinstance(job_raw, dict) or not isinstance(job_raw.get("id"), str):
        raise InputError(
            "input.collect_config", "the config needs a job with an id.", fix
        )
    window_raw = job_raw.get("window")
    window = window_raw if isinstance(window_raw, dict) else {}
    job = JobSpec(
        id=str(job_raw["id"]),
        principal=str(job_raw.get("principal", "")),
        window={str(k): str(v) for k, v in window.items()},
    )

    sources_raw = raw.get("sources")
    if not isinstance(sources_raw, list) or not sources_raw:
        raise InputError(
            "input.collect_config", "the config needs a non-empty sources list.", fix
        )
    sources: list[SourceSpec] = []
    for entry in sources_raw:
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str):
            raise InputError("input.collect_config", "each source needs an id.", fix)
        source_class = entry.get("class")
        sources.append(
            SourceSpec(
                id=str(entry["id"]),
                adapter=str(entry.get("adapter", "")),
                endpoint=str(entry.get("endpoint", "")),
                credential=_credential(entry.get("credential")),
                source_class=str(source_class)
                if isinstance(source_class, str)
                else None,
            )
        )
    return CollectConfig(job=job, sources=tuple(sources))


def plan(config: CollectConfig) -> dict[str, Any]:
    """Build the dry-run plan: the job identity and, per source, what would be collected.

    Credentials are shown only as references and are never resolved, so the plan is safe to print and
    to write and is produced with no network access.
    """
    sources = [
        {
            "id": source.id,
            "adapter": source.adapter,
            "endpoint": source.endpoint,
            **({"class": source.source_class} if source.source_class else {}),
            **(
                {"credential": source.credential.redacted()}
                if source.credential is not None
                else {}
            ),
            "completeness": "planned",
        }
        for source in config.sources
    ]
    return {
        "job": {
            "id": config.job.id,
            "principal": config.job.principal,
            "window": config.job.window,
        },
        "dry_run": True,
        "sources": sources,
    }


def _manifest(
    config: CollectConfig, sources: list[dict[str, Any]], files: list[dict[str, str]]
) -> dict[str, Any]:
    """A bundle manifest recording the collecting job's identity and per-source completeness (§5.4, §6.6)."""
    return {
        "agentce_bundle_version": 1,
        "job": {
            "id": config.job.id,
            "principal": config.job.principal,
            "window": config.job.window,
        },
        "sources": sources,
        "files": files,
    }


@dataclass
class CollectOutcome:
    """The result of a collection run: the report, whether it completed, and what it wrote."""

    report: dict[str, Any]
    complete: bool
    written: tuple[str, ...] = ()


def _write(out_dir: Path, name: str, data: dict[str, Any]) -> None:
    (out_dir / name).write_text(
        json.dumps(data, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )


def run_collect(
    config: CollectConfig,
    *,
    out_dir: Path | None,
    dry_run: bool,
    secret_manager: SecretManager,
) -> CollectOutcome:
    """Run (or, with ``dry_run``, plan) a collection.

    A dry run resolves no credentials and completes. A real run obtains a short-lived credential per
    source from ``secret_manager`` (used to authenticate, never written); because the reference
    collector ships no live source connectors, every source is recorded ``incomplete`` and the run does
    not complete (SPEC §5.4) -- but no credential value is ever written.
    """
    if dry_run:
        report = plan(config)
        written: tuple[str, ...] = ()
        if out_dir is not None:
            out_dir.mkdir(parents=True, exist_ok=True)
            _write(out_dir, "collect-plan.json", report)
            _write(
                out_dir,
                "manifest.json",
                _manifest(config, report["sources"], [{"path": "collect-plan.json"}]),
            )
            written = ("collect-plan.json", "manifest.json")
        return CollectOutcome(report=report, complete=True, written=written)

    sources: list[dict[str, Any]] = []
    for source in config.sources:
        # A short-lived credential is obtained only to authenticate the pull; its value is never
        # recorded. The reference collector has no connector for the source, so the pull cannot run.
        authenticated = (
            secret_manager.resolve(source.credential) is not None
            if source.credential is not None
            else False
        )
        sources.append(
            {
                "id": source.id,
                "adapter": source.adapter,
                "completeness": "incomplete",
                "reason": "no source connector in the reference collector",
                "authenticated": authenticated,
            }
        )
    report = {
        "job": {
            "id": config.job.id,
            "principal": config.job.principal,
            "window": config.job.window,
        },
        "dry_run": False,
        "sources": sources,
    }
    written = ()
    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
        _write(out_dir, "collect-report.json", report)
        _write(
            out_dir,
            "manifest.json",
            _manifest(config, sources, [{"path": "collect-report.json"}]),
        )
        written = ("collect-report.json", "manifest.json")
    return CollectOutcome(report=report, complete=False, written=written)
