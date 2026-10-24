"""The report-file reader contract's shared case table (SPEC 8.7, ``spec/report/reader-cases.json``).

One full report folder (every ``--emit`` token) is written once by this engine's own ``assess`` over
the quickstart corpus; each case copies it, applies its edits to one file, and runs its command
through the real CLI entry point. Exit code, message key and the named rule must hold, as they must
in the TypeScript and Java engines.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
)

from agentce import cli

_REPO_ROOT = Path(__file__).resolve().parents[3]
_QUICKSTART = _REPO_ROOT / "corpus" / "quickstart"
_CASES = json.loads(
    (_REPO_ROOT / "spec" / "report" / "reader-cases.json").read_text("utf-8")
)["cases"]
_EMIT = "md,html,oscal,sarif,pack,junit,csv,oscal_xml"


@pytest.fixture(scope="module")
def base_report(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("reader") / "base"
    code = cli.main(
        [
            "assess",
            "--bundle",
            str(_QUICKSTART / "evidence"),
            "--profile",
            str(_QUICKSTART / "applicability.yaml"),
            "--domain",
            str(_QUICKSTART / "domain.linkml.yaml"),
            "--catalog",
            "eu-ai-act@2026.09",
            "--catalog-dir",
            str(_REPO_ROOT / "spec" / "catalogs" / "base" / "eu-ai-act"),
            "--emit",
            _EMIT,
            "--out",
            str(out),
        ]
    )
    assert code == 0
    return out


@pytest.fixture(scope="module")
def sign_key(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("key") / "claimant.pem"
    path.write_bytes(
        Ed25519PrivateKey.generate().private_bytes(
            Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()
        )
    )
    return path


def _apply(path: Path, edits: list[dict[str, str]]) -> None:
    for edit in edits:
        data = path.read_bytes() if path.exists() else b""
        new = bytes.fromhex(edit["hex"])
        op = edit["op"]
        if op == "write":
            data = new
        elif op == "prepend":
            data = new + data
        elif op == "append":
            data = data + new
        else:
            find = bytes.fromhex(edit["find_hex"])
            assert find in data, f"{path.name} does not hold the bytes to replace"
            data = data.replace(find, new, 1 if op == "replace" else -1)
        path.write_bytes(data)


def _snapshot(report: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(report)): p.read_bytes()
        for p in sorted(report.rglob("*"))
        if p.is_file()
    }


def _run(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, Any]:
    capsys.readouterr()
    code = cli.main(argv)
    out = capsys.readouterr().out
    return code, json.loads(out) if "--json" in argv else None


@pytest.mark.parametrize("case", _CASES, ids=[c["id"] for c in _CASES])
def test_reader_case(
    case: dict[str, Any],
    base_report: Path,
    sign_key: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    report = tmp_path / "case"
    shutil.copytree(base_report, report)
    _apply(report / case["file"], case["edits"])
    before = _snapshot(report)
    command = case["command"]
    if command == "report-validate":
        code, env = _run(["report", "--validate", str(report), "--json"], capsys)
        key = env.get("message_key")
        texts = env.get("problems", [])
    elif command == "readiness":
        code, env = _run(["readiness", str(report), "--json"], capsys)
    elif command == "sign":
        argv = ["sign", str(report), "--as", "claimant", "--profile", "kms"]
        code, env = _run([*argv, "--key", str(sign_key), "--json"], capsys)
    else:
        assert command == "diff"
        argv = [str(base_report / "assertions.json"), str(report / "assertions.json")]
        code, env = _run(["diff", *argv, "--json"], capsys)
    if command != "report-validate":
        error = env.get("error") or {}
        key = error.get("key")
        texts = [error.get("cause", "")]
    assert code == case["exit"], (code, env)
    if case["exit"] == 0:
        return
    assert key == case["key"], env
    assert any(case["names"] in text for text in texts), texts
    if command in ("readiness", "sign"):
        # A refusal writes nothing: no readiness file, no signature, claim.json unchanged.
        assert _snapshot(report) == before
