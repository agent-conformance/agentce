"""Policy-as-code: severity/family on every assertion, and ``--fail-on`` gates the exit code on a
tiny, deterministic expression -- never ``eval`` (SPEC §8.5, §7; finding #29's CI guard).

Runs the real CLI in-process against the committed quickstart bundle. The hostile-
expression cases prove the parser itself never reaches Python's ``eval``/``exec``/``os.system``: a
sentinel file is created only if the injected code actually ran, and no shell is involved at all
(argv is passed as a Python list), so a passing test here means the engine's own parser -- not shell
quoting -- refused the payload.
"""

from __future__ import annotations

import glob
import json
from pathlib import Path

import pytest
import yaml

from agentce import cli

_REPO_ROOT = Path(__file__).resolve().parents[3]
_BUNDLE = _REPO_ROOT / "corpus" / "quickstart" / "evidence"
_PROFILE = _REPO_ROOT / "corpus" / "quickstart" / "applicability.yaml"
_DOMAIN = _REPO_ROOT / "corpus" / "quickstart" / "domain.linkml.yaml"
_CATALOG_DIR = _REPO_ROOT / "spec" / "catalogs" / "base" / "eu-ai-act"


def _assess(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], *extra: str
) -> tuple[int, dict]:
    out = tmp_path / f"out-{len(extra)}-{abs(hash(extra))}"
    argv = [
        "assess",
        "--bundle",
        str(_BUNDLE),
        "--profile",
        str(_PROFILE),
        "--domain",
        str(_DOMAIN),
        "--catalog",
        "eu-ai-act@2026.09",
        "--catalog-dir",
        str(_CATALOG_DIR),
        "--out",
        str(out),
        "--json",
        *extra,
    ]
    code = cli.main(argv)
    data = json.loads(capsys.readouterr().out)
    return code, data


def test_baseline_assess_succeeds_and_every_assertion_carries_real_severity_and_family(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, data = _assess(tmp_path, capsys)
    assert code == 0

    out_dir = Path(data["out"])
    assertions = json.loads((out_dir / "assertions.json").read_text(encoding="utf-8"))
    assert assertions, "the quickstart bundle must produce at least one assertion"

    ground_truth: dict[str, str | None] = {}
    for control_file in glob.glob(str(_CATALOG_DIR / "controls" / "*.yaml")):
        control = yaml.safe_load(Path(control_file).read_text(encoding="utf-8"))
        ground_truth[control["id"]] = control.get("severity")

    for record in assertions:
        assert record["severity"] in ("low", "medium", "high"), record
        expected_severity = ground_truth.get(record["control"])
        if expected_severity is not None:
            assert record["severity"] == expected_severity, record
        expected_family = (
            record["control"].split("-", 1)[0]
            if "-" in record["control"]
            else record["control"]
        )
        assert record["family"] == expected_family, record


def test_fail_on_severity_and_family_gate_the_exit_code_and_are_deterministic(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Zero real matches in the quickstart bundle: must not trigger the gate.
    code, _ = _assess(
        tmp_path,
        capsys,
        "--fail-on",
        'outcome=="insufficient_evidence" and severity=="high"',
    )
    assert code == 0
    code, _ = _assess(
        tmp_path,
        capsys,
        "--fail-on",
        'outcome=="insufficient_evidence" and family=="INT"',
    )
    assert code == 0

    # Real matches: must trigger the gate (nonzero, never the input-error code 3), deterministically.
    code_a, _ = _assess(
        tmp_path,
        capsys,
        "--fail-on",
        'outcome=="insufficient_evidence" and severity=="medium"',
    )
    code_b, _ = _assess(
        tmp_path,
        capsys,
        "--fail-on",
        'outcome=="insufficient_evidence" and severity=="medium"',
    )
    assert code_a != 0
    assert code_a != 3
    assert code_a == code_b

    code, _ = _assess(
        tmp_path,
        capsys,
        "--fail-on",
        'outcome=="insufficient_evidence" and family=="DAT"',
    )
    assert code != 0
    assert code != 3


def test_fail_on_rejects_hostile_expressions_without_executing_them(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    for label, expr_of in {
        "dunder": lambda s: f"__import__('os').system('touch {s}')",
        "ossystem": lambda s: f"os.system('touch {s}')",
        "backtick": lambda s: f'severity=="high" `touch {s}`',
        "trailing": lambda s: f"severity==\"high\" or __import__('os').system('touch {s}')==0",
    }.items():
        sentinel = tmp_path / f"pwned-{label}"
        sentinel.unlink(missing_ok=True)
        code, data = _assess(tmp_path, capsys, "--fail-on", expr_of(sentinel))
        assert code == 3, (label, code, data)
        assert data["error"]["key"].startswith("input.fail_on"), (label, data)
        assert not sentinel.exists(), (
            f"{label}: hostile expression executed (sentinel file created)"
        )

    # Control arm: a benign, well-formed expression is not caught by the same rejection path.
    code, data = _assess(
        tmp_path,
        capsys,
        "--fail-on",
        'outcome=="insufficient_evidence" and severity=="medium"',
    )
    assert "error" not in data or not str(data["error"].get("key", "")).startswith(
        "input.fail_on"
    )


def test_policy_examples_exist_and_are_scoped_to_severity_and_family() -> None:
    rego = _REPO_ROOT / "spec/report/examples/policy/conftest/agentce.rego"
    content = rego.read_text(encoding="utf-8")
    assert rego.stat().st_size > 0
    for token in ("package", "severity", "family", "ssertion"):
        assert token in content, f"{rego}: missing {token!r}"
    assert "deny" in content or "violation" in content

    kyverno = _REPO_ROOT / "spec/report/examples/policy/kyverno/policy.yaml"
    text = kyverno.read_text(encoding="utf-8")
    doc = yaml.safe_load(text)
    assert isinstance(doc, dict)
    assert str(doc.get("apiVersion", "")).startswith("kyverno.io/")
    assert doc.get("kind") in ("ClusterPolicy", "Policy")
    for token in ("severity", "family", "ssertion"):
        assert token in text, f"{kyverno}: missing {token!r}"
