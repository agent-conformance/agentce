"""A bare ``agentce init`` and a bare ``agentce_emit.auto()`` describe one agent, and an assessment
that judges nothing does not exit 0."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from agentce import cli
from agentce.assess import evaluated_nothing
from agentce.assertions import OUTCOMES, Assertion
from agentce.commands import DEFAULT_EMIT_SOURCE, DEFAULT_SUBJECT

_REPO_ROOT = Path(__file__).resolve().parents[3]
_EMIT_SOURCE = _REPO_ROOT / "engines" / "python-emit" / "agentce_emit" / "_emit.py"
_CATALOG_DIR = _REPO_ROOT / "spec" / "catalogs" / "base" / "eu-ai-act"


def _load_emitter() -> Any:
    """The emitter package's module, loaded from the checkout (the engine does not depend on it)."""
    spec = importlib.util.spec_from_file_location("agentce_emit_defaults", _EMIT_SOURCE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _run(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str, str]:
    code = cli.main(argv)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def _bare_init(out: Path, capsys: pytest.CaptureFixture[str]) -> dict[str, Any]:
    code, _, _ = _run(["init", "--non-interactive", "--out", str(out)], capsys)
    assert code == 0
    profile = yaml.safe_load(
        (out / "agentce" / "applicability.yaml").read_text(encoding="utf-8")
    )
    assert isinstance(profile, dict)
    return profile


def _bare_emit(out: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    module = _load_emitter()
    monkeypatch.setenv("AGENTCE_EMIT", "1")
    monkeypatch.setenv("AGENTCE_EMIT_OUT", str(out))
    monkeypatch.delenv("AGENTCE_EMIT_SUBJECT", raising=False)
    monkeypatch.delenv("AGENTCE_EMIT_SOURCE", raising=False)
    emitter = module.auto()
    emitter.emit_session_start()
    emitter.flush()
    return out


def test_init_and_emit_defaults_are_the_same_strings() -> None:
    module = _load_emitter()
    assert DEFAULT_SUBJECT == module.DEFAULT_SUBJECT
    assert DEFAULT_EMIT_SOURCE == module.DEFAULT_SOURCE


def test_bare_init_profile_matches_bare_emit_events(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    profile = _bare_init(tmp_path / "proj", capsys)
    bundle = _bare_emit(tmp_path / "emit", monkeypatch)
    subject = profile["subjects"][0]
    event = json.loads(
        (bundle / "events" / "stream.jsonl").read_text(encoding="utf-8").splitlines()[0]
    )
    assert subject["id"] == event["subject"]
    assert subject["evidence_sources"][0]["source"] == event["source"]


def test_init_with_own_subject_says_how_to_emit_under_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, _ = _run(
        [
            "init",
            "--non-interactive",
            "--subject",
            "spiffe://corp/agents/a",
            "--out",
            str(tmp_path),
        ],
        capsys,
    )
    assert code == 0
    assert "AGENTCE_EMIT_SUBJECT=spiffe://corp/agents/a" in out
    assert f"AGENTCE_EMIT_SOURCE={DEFAULT_EMIT_SOURCE}" in out


def test_init_for_an_otel_framework_keeps_a_source_to_fill_in(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _, _ = _run(
        [
            "init",
            "--non-interactive",
            "--framework",
            "langgraph",
            "--out",
            str(tmp_path),
        ],
        capsys,
    )
    assert code == 0
    profile = yaml.safe_load(
        (tmp_path / "agentce" / "applicability.yaml").read_text(encoding="utf-8")
    )
    source = profile["subjects"][0]["evidence_sources"][0]
    assert source["adapter"] == "otel-genai"
    assert source["source"] == "urn:agentce:source:langgraph:TODO"


def _assess_default_first_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> tuple[int, dict[str, Any], Path]:
    _bare_init(tmp_path / "proj", capsys)
    bundle = _bare_emit(tmp_path / "emit", monkeypatch)
    out = tmp_path / "report"
    code, stdout, _ = _run(
        [
            "assess",
            "--bundle",
            str(bundle),
            "--profile",
            str(tmp_path / "proj" / "agentce" / "applicability.yaml"),
            "--catalog",
            "eu-ai-act@2026.09",
            "--catalog-dir",
            str(_CATALOG_DIR),
            "--out",
            str(out),
            "--json",
        ],
        capsys,
    )
    return code, json.loads(stdout) if stdout.strip() else {}, out


def test_the_default_first_run_is_refused_not_passed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    code, envelope, out = _assess_default_first_run(tmp_path, capsys, monkeypatch)
    assert code == 3
    assert envelope["error"]["key"] == "input.nothing_evaluated"
    assert "judge nothing" in envelope["error"]["cause"]
    assert (out / "assertions.json").is_file()  # the reports are still written


def test_an_assessment_naming_the_wrong_subject_says_which_ids_differ(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _bare_init(tmp_path / "proj", capsys)
    profile_path = tmp_path / "proj" / "agentce" / "applicability.yaml"
    profile_path.write_text(
        profile_path.read_text(encoding="utf-8").replace(
            DEFAULT_SUBJECT, "spiffe://corp/agents/other"
        ),
        encoding="utf-8",
    )
    bundle = _bare_emit(tmp_path / "emit", monkeypatch)
    code, stdout, _ = _run(
        [
            "assess",
            "--bundle",
            str(bundle),
            "--profile",
            str(profile_path),
            "--catalog",
            "eu-ai-act@2026.09",
            "--catalog-dir",
            str(_CATALOG_DIR),
            "--out",
            str(tmp_path / "report"),
            "--json",
        ],
        capsys,
    )
    cause = json.loads(stdout)["error"]["cause"]
    assert code == 3
    assert "spiffe://corp/agents/other" in cause
    assert DEFAULT_SUBJECT in cause


def test_a_run_that_reaches_verdicts_still_exits_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, stdout, _ = _run(
        ["quickstart", "--out", str(tmp_path / "qs"), "--json"], capsys
    )
    assert code == 0
    assert json.loads(stdout)["quickstart"] == "ok"


def _assertion(outcome: str) -> Assertion:
    return Assertion(
        control="C-1",
        control_version="1",
        subject="s",
        rung=2,
        mode="deterministic",
        window=("2026-01-01T00:00:00Z", "2026-02-01T00:00:00Z"),
        outcome=outcome,
        population=(0, 0),
        severity="high",
        family="C",
    )


@pytest.mark.parametrize(
    ("outcomes", "expected"),
    [
        ([], True),
        (["not_applicable", "not_assessed"], True),
        (["not_applicable", "conformant"], False),
        (["not_assessed", "non-conformant"], False),
        (["not_applicable", "insufficient_evidence"], False),
    ],
)
def test_evaluated_nothing_needs_a_verdict_outcome(
    outcomes: list[str], expected: bool
) -> None:
    assert set(outcomes) <= set(OUTCOMES)
    assert evaluated_nothing([_assertion(o) for o in outcomes]) is expected
