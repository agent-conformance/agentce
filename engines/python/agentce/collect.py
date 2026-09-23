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

A source may name an ``export``: a local path already written by its own pipeline (an OpenTelemetry
Collector's file or object-storage exporter, SPEC §5.4 -- "the OTel Collector exports traces to files
or object storage ... AgentCE reads these exports"). A real run adapts that export through the
``ExportAdapter`` it is given and records the source ``complete``; without one, or without an export
declared, the source stays ``incomplete`` -- recorded, never hidden, never silently skipped (SPEC §5.4,
"collection failures are recorded, never hidden"). No network, no learned component.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from .canonical import canonical_string
from .errors import InputError
from .safe_yaml import load_untrusted_yaml

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
    """One source to collect from: an adapter, an endpoint, and a credential reference.

    ``export``, ``engine``, and ``subject`` are optional and back the file-based path: ``export`` is a
    local path this source's evidence was already written to (SPEC §5.4); ``engine`` is passed through
    to an adapter that needs to know which underlying system produced the export (e.g. ``policy-engines``
    needs ``opa``/``cedar``/...); ``subject`` is the assessed subject id the adapter attributes the
    export to (default: the reference collector's default subject).
    """

    id: str
    adapter: str
    endpoint: str
    credential: CredentialRef | None
    source_class: str | None
    export: str | None = None
    engine: str | None = None
    subject: str | None = None


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


class ExportAdapter(Protocol):
    """Adapts one source's already-written export into canonical evidence events (SPEC §5.4, §12).

    Given to :func:`run_collect` by the caller so this module stays offline and adapter-agnostic: the
    CLI layer's implementation runs the named adapter in its own environment, because every v1 adapter
    package shares the name ``agentce_adapters`` (mirrors the engine conformance suite's own
    subprocess pattern for the same reason).
    """

    def adapt(self, source: SourceSpec, export_path: Path) -> list[dict[str, Any]]: ...


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
        raw = load_untrusted_yaml(
            path, key="input.collect_config", what="the collect config"
        )
    except OSError as exc:
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
        export = entry.get("export")
        engine = entry.get("engine")
        subject = entry.get("subject")
        sources.append(
            SourceSpec(
                id=str(entry["id"]),
                adapter=str(entry.get("adapter", "")),
                endpoint=str(entry.get("endpoint", "")),
                credential=_credential(entry.get("credential")),
                source_class=str(source_class)
                if isinstance(source_class, str)
                else None,
                export=str(export) if isinstance(export, str) and export else None,
                engine=str(engine) if isinstance(engine, str) and engine else None,
                subject=str(subject) if isinstance(subject, str) and subject else None,
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
    export_adapter: ExportAdapter | None = None,
) -> CollectOutcome:
    """Run (or, with ``dry_run``, plan) a collection.

    A dry run resolves no credentials and completes. A real run obtains a short-lived credential per
    source from ``secret_manager`` (used to authenticate, never written). A source that names an
    ``export`` is adapted through ``export_adapter``, when given, and recorded ``complete`` with its
    events folded into the bundle; a source with no ``export``, or when no ``export_adapter`` is given,
    or whose export cannot be read or adapted, is recorded ``incomplete`` (SPEC §5.4) -- but no
    credential value is ever written.
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
    all_events: list[dict[str, Any]] = []
    for source in config.sources:
        # A short-lived credential is obtained only to authenticate the pull; its value is never
        # recorded, whichever branch below resolves the source.
        authenticated = (
            secret_manager.resolve(source.credential) is not None
            if source.credential is not None
            else False
        )
        if source.export and export_adapter is not None:
            export_path = Path(source.export)
            if not export_path.is_file():
                sources.append(
                    {
                        "id": source.id,
                        "adapter": source.adapter,
                        "completeness": "incomplete",
                        "reason": f"export not found: {source.export}",
                        "authenticated": authenticated,
                    }
                )
                continue
            try:
                events = export_adapter.adapt(source, export_path)
            except Exception as exc:  # noqa: BLE001 - any adapter failure is a recorded incompleteness
                sources.append(
                    {
                        "id": source.id,
                        "adapter": source.adapter,
                        "completeness": "incomplete",
                        "reason": f"export could not be adapted: {exc}",
                        "authenticated": authenticated,
                    }
                )
                continue
            if not events:
                sources.append(
                    {
                        "id": source.id,
                        "adapter": source.adapter,
                        "completeness": "incomplete",
                        "reason": "export produced no events",
                        "authenticated": authenticated,
                    }
                )
                continue
            all_events.extend(events)
            sources.append(
                {
                    "id": source.id,
                    "adapter": source.adapter,
                    "completeness": "complete",
                    "events": len(events),
                    "authenticated": authenticated,
                }
            )
            continue
        # No export declared (or no adapter given to resolve one): the reference collector has no live
        # connector for this source, so the pull cannot run.
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
    complete = bool(sources) and all(s["completeness"] != "incomplete" for s in sources)
    written = ()
    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
        files: list[dict[str, str]] = [{"path": "collect-report.json"}]
        written_names = ["collect-report.json"]
        if all_events:
            events_dir = out_dir / "events"
            events_dir.mkdir(exist_ok=True)
            stream = events_dir / "stream.jsonl"
            stream.write_text(
                "".join(canonical_string(event) + "\n" for event in all_events),
                encoding="utf-8",
            )
            digest = hashlib.sha256(stream.read_bytes()).hexdigest()
            files.append({"path": "events/stream.jsonl", "sha256": digest})
            written_names.append("events/stream.jsonl")
        _write(out_dir, "collect-report.json", report)
        _write(out_dir, "manifest.json", _manifest(config, sources, files))
        written_names.append("manifest.json")
        written = tuple(written_names)
    return CollectOutcome(report=report, complete=complete, written=written)
