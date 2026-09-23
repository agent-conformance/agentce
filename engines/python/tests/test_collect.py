"""Scheduled collection: dry-run planning, the secret-manager abstraction, and no credential leak
(SPEC §5.4 B8)."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentce.collect import (
    CredentialRef,
    EnvSecretManager,
    load_config,
    plan,
    run_collect,
)
from agentce.errors import InputError

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DRY_RUN_CONFIG = _REPO_ROOT / "adapters" / "fixtures" / "collect-dry-run.yaml"


def _write_config(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    return path


_MINIMAL = """\
job:
  id: job-1
  principal: spiffe://corp/jobs/c
  window: {start: "2026-05-01T00:00:00.000Z", end: "2026-05-02T00:00:00.000Z"}
sources:
  - id: urn:otel:x
    adapter: otel-genai
    endpoint: https://collector/otlp
    class: self_report
    credential: {secret_ref: "vault://secret/x#token"}
"""


class _RaisingSecretManager:
    def resolve(self, credential: CredentialRef) -> str | None:
        raise AssertionError("a dry run must not resolve credentials")


class _FixedSecretManager:
    def __init__(self, value: str) -> None:
        self.value = value

    def resolve(self, credential: CredentialRef) -> str | None:
        return self.value


def test_load_config_parses_job_and_sources(tmp_path: Path) -> None:
    config = load_config(_write_config(tmp_path / "c.yaml", _MINIMAL))
    assert config.job.id == "job-1"
    assert config.job.window["start"] == "2026-05-01T00:00:00.000Z"
    assert len(config.sources) == 1
    source = config.sources[0]
    assert source.adapter == "otel-genai"
    assert source.credential == CredentialRef(
        via="secret_ref", ref="vault://secret/x#token"
    )


def test_load_config_rejects_malformed(tmp_path: Path) -> None:
    for body in ["[]", "job: {}\nsources: []\n", "job: {id: j}\n"]:
        with pytest.raises(InputError):
            load_config(_write_config(tmp_path / "bad.yaml", body))


def test_load_config_refuses_a_too_deeply_nested_config(tmp_path: Path) -> None:
    """The config load goes through the same hardened YAML loader ``domain.py``/``profile.py`` use
    for untrusted, bundle-adjacent input (loophole L13.1): a config nested past Python's recursion
    limit is a deliberate ``input.collect_config`` refusal, never an uncaught ``RecursionError``."""
    nested = "a: " + "[" * 6000 + "]" * 6000 + "\n"
    with pytest.raises(InputError, match="input.collect_config"):
        load_config(_write_config(tmp_path / "deep.yaml", nested))


def test_plan_shows_credential_reference_never_resolved() -> None:
    config = load_config(_DRY_RUN_CONFIG)
    p = plan(config)
    assert p["dry_run"] is True
    assert p["job"]["id"] == "collect-credit-eu-1"
    for source in p["sources"]:
        assert source["completeness"] == "planned"
        if "credential" in source:
            assert source["credential"]["resolved"] is False
            assert "ref" in source["credential"]


def test_dry_run_does_not_resolve_credentials() -> None:
    config = load_config(_DRY_RUN_CONFIG)
    outcome = run_collect(
        config, out_dir=None, dry_run=True, secret_manager=_RaisingSecretManager()
    )
    assert outcome.complete is True
    assert outcome.written == ()


def test_dry_run_writes_plan_and_manifest_with_job_identity(tmp_path: Path) -> None:
    config = load_config(_DRY_RUN_CONFIG)
    outcome = run_collect(
        config,
        out_dir=tmp_path / "b",
        dry_run=True,
        secret_manager=_RaisingSecretManager(),
    )
    assert set(outcome.written) == {"collect-plan.json", "manifest.json"}
    manifest = (tmp_path / "b" / "manifest.json").read_text(encoding="utf-8")
    assert (
        "collect-credit-eu-1" in manifest
    )  # the job identity is recorded (SPEC §5.4, §6.6)


def test_real_run_records_incomplete_and_does_not_complete(tmp_path: Path) -> None:
    config = load_config(_DRY_RUN_CONFIG)
    outcome = run_collect(
        config,
        out_dir=tmp_path / "b",
        dry_run=False,
        secret_manager=EnvSecretManager(environ={}),
    )
    assert outcome.complete is False
    assert all(s["completeness"] == "incomplete" for s in outcome.report["sources"])
    assert (tmp_path / "b" / "collect-report.json").is_file()


def test_a_credential_value_is_never_written(tmp_path: Path) -> None:
    config = load_config(_DRY_RUN_CONFIG)
    secret = "s3cr3t-token-do-not-write-me"
    run_collect(
        config,
        out_dir=tmp_path / "b",
        dry_run=False,
        secret_manager=_FixedSecretManager(secret),
    )
    for path in (tmp_path / "b").rglob("*"):
        if path.is_file():
            assert secret not in path.read_text(encoding="utf-8")


_EXPORT_CONFIG = """\
job:
  id: job-1
  principal: spiffe://corp/jobs/c
sources:
  - id: urn:otel:x
    adapter: otel-genai
    export: {export}
  - id: urn:otel:y
    adapter: otel-genai
"""


class _FakeExportAdapter:
    def __init__(self, events: list[dict[str, object]]) -> None:
        self.events = events
        self.calls: list[tuple[str, Path]] = []

    def adapt(self, source: object, export_path: Path) -> list[dict[str, object]]:
        self.calls.append((source.id, export_path))  # type: ignore[attr-defined]
        return self.events


def test_real_run_completes_a_source_with_an_export(tmp_path: Path) -> None:
    export = tmp_path / "export.json"
    export.write_text("{}", encoding="utf-8")
    config = load_config(
        _write_config(tmp_path / "c.yaml", _EXPORT_CONFIG.format(export=export))
    )
    event: dict[str, object] = {
        "source": "urn:otel:x",
        "agentcesourceclass": "self_report",
        "@type": "SessionStart",
    }
    adapter = _FakeExportAdapter([event])
    outcome = run_collect(
        config,
        out_dir=tmp_path / "b",
        dry_run=False,
        secret_manager=EnvSecretManager(environ={}),
        export_adapter=adapter,
    )
    by_id = {s["id"]: s for s in outcome.report["sources"]}
    assert by_id["urn:otel:x"]["completeness"] == "complete"
    assert by_id["urn:otel:x"]["events"] == 1
    # A source with no `export` still has no connector: recorded incomplete, never hidden.
    assert by_id["urn:otel:y"]["completeness"] == "incomplete"
    assert outcome.complete is False  # one source is still incomplete
    assert adapter.calls == [("urn:otel:x", export)]
    stream = (tmp_path / "b" / "events" / "stream.jsonl").read_text(encoding="utf-8")
    assert "urn:otel:x" in stream


def test_real_run_all_sources_with_exports_completes(tmp_path: Path) -> None:
    export = tmp_path / "export.json"
    export.write_text("{}", encoding="utf-8")
    config = load_config(
        _write_config(
            tmp_path / "c.yaml",
            "job:\n  id: job-1\n  principal: spiffe://corp/jobs/c\n"
            f"sources:\n  - id: urn:otel:x\n    adapter: otel-genai\n    export: {export}\n",
        )
    )
    adapter = _FakeExportAdapter(
        [{"source": "urn:otel:x", "agentcesourceclass": "self_report"}]
    )
    outcome = run_collect(
        config,
        out_dir=tmp_path / "b",
        dry_run=False,
        secret_manager=EnvSecretManager(environ={}),
        export_adapter=adapter,
    )
    assert outcome.complete is True


def test_real_run_export_missing_stays_incomplete(tmp_path: Path) -> None:
    config = load_config(
        _write_config(
            tmp_path / "c.yaml",
            "job:\n  id: job-1\n  principal: spiffe://corp/jobs/c\n"
            "sources:\n  - id: urn:otel:x\n    adapter: otel-genai\n"
            f"    export: {tmp_path / 'nope.json'}\n",
        )
    )
    outcome = run_collect(
        config,
        out_dir=tmp_path / "b",
        dry_run=False,
        secret_manager=EnvSecretManager(environ={}),
        export_adapter=_FakeExportAdapter(
            [{"source": "x", "agentcesourceclass": "self_report"}]
        ),
    )
    source = outcome.report["sources"][0]
    assert source["completeness"] == "incomplete"
    assert "not found" in source["reason"]


def test_real_run_adapter_failure_stays_incomplete(tmp_path: Path) -> None:
    export = tmp_path / "export.json"
    export.write_text("{}", encoding="utf-8")
    config = load_config(
        _write_config(
            tmp_path / "c.yaml",
            "job:\n  id: job-1\n  principal: spiffe://corp/jobs/c\n"
            f"sources:\n  - id: urn:otel:x\n    adapter: otel-genai\n    export: {export}\n",
        )
    )

    class _RaisingAdapter:
        def adapt(self, source: object, export_path: Path) -> list[dict[str, object]]:
            raise ValueError("bad export")

    outcome = run_collect(
        config,
        out_dir=tmp_path / "b",
        dry_run=False,
        secret_manager=EnvSecretManager(environ={}),
        export_adapter=_RaisingAdapter(),
    )
    source = outcome.report["sources"][0]
    assert source["completeness"] == "incomplete"
    assert "bad export" in source["reason"]


def test_env_secret_manager_resolves_env_only() -> None:
    sm = EnvSecretManager(environ={"TOK": "value"})
    assert sm.resolve(CredentialRef(via="env", ref="TOK")) == "value"
    assert sm.resolve(CredentialRef(via="env", ref="MISSING")) is None
    # A secret_ref points at an external secret manager the reference collector cannot resolve.
    assert sm.resolve(CredentialRef(via="secret_ref", ref="vault://x")) is None
