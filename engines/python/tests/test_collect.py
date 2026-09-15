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


def test_env_secret_manager_resolves_env_only() -> None:
    sm = EnvSecretManager(environ={"TOK": "value"})
    assert sm.resolve(CredentialRef(via="env", ref="TOK")) == "value"
    assert sm.resolve(CredentialRef(via="env", ref="MISSING")) is None
    # A secret_ref points at an external secret manager the reference collector cannot resolve.
    assert sm.resolve(CredentialRef(via="secret_ref", ref="vault://x")) is None
