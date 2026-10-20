"""Every catalog YAML file is one mapping read as the domain binding's unique-key loader reads it (18.184i).

A key written twice, an empty or list file, bytes that are not UTF-8, YAML the engine refuses to load, or a second
document are refused as ``input.catalog_unreadable`` naming the file, never read with the last value, as nothing, or
skipped; that refusal comes before the signature check, so ``--allow-unverified-catalog`` never waives it. A
``catalog.sig.json`` that is there but is not a regular file is unsigned with its own key, never called absent.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

import pytest

from agentce import cli, signing
from agentce.catalog import load_catalog
from agentce.errors import InputError

_REPO_ROOT = Path(__file__).resolve().parents[3]
_BASE = _REPO_ROOT / "spec" / "catalogs" / "base" / "eu-ai-act"
_QUICKSTART = _REPO_ROOT / "corpus" / "quickstart"
_CONTROL = "controls/DAT-01.yaml"
_SHAPE = "shapes/DAT-01.ttl"


def _catalog(tmp_path: Path) -> Path:
    directory = tmp_path / "cat"
    shutil.copytree(_BASE, directory)
    (directory / signing.CATALOG_SIGNATURE_NAME).unlink()
    return directory


def _edit(directory: Path, rel: str, old: str, new: str) -> None:
    path = directory / rel
    text = path.read_text("utf-8")
    assert old in text, old
    path.write_text(text.replace(old, new, 1), "utf-8")


# (case, file, change) -> the problem the refusal names for that file.
_CASES: dict[str, tuple[str, Any, str]] = {
    "control key twice, the weaker last": (
        _CONTROL,
        lambda d: _edit(
            d,
            _CONTROL,
            "  min_source_class: self_report\n",
            "  min_source_class: independent_system\n  min_source_class: self_report\n",
        ),
        "is not YAML the engine can load",
    ),
    "catalog.yaml key twice": (
        "catalog.yaml",
        lambda d: _edit(
            d,
            "catalog.yaml",
            'version: "2026.09"\n',
            'version: "2026.09"\nversion: "9"\n',
        ),
        "is not YAML the engine can load",
    ),
    "catalog.yaml empty": (
        "catalog.yaml",
        lambda d: (d / "catalog.yaml").write_bytes(b""),
        "does not hold one YAML mapping",
    ),
    "catalog.yaml a list": (
        "catalog.yaml",
        lambda d: (d / "catalog.yaml").write_text("- id: eu-ai-act\n", "utf-8"),
        "does not hold one YAML mapping",
    ),
    "control empty": (
        _CONTROL,
        lambda d: (d / _CONTROL).write_bytes(b""),
        "does not hold one YAML mapping",
    ),
    "control a list": (
        _CONTROL,
        lambda d: (d / _CONTROL).write_text("- id: DAT-01\n", "utf-8"),
        "does not hold one YAML mapping",
    ),
    "control not UTF-8": (
        _CONTROL,
        lambda d: (d / _CONTROL).write_bytes((d / _CONTROL).read_bytes() + b"# \xff\n"),
        "is not valid UTF-8",
    ),
    "shape not UTF-8": (
        _SHAPE,
        lambda d: (d / _SHAPE).write_bytes((d / _SHAPE).read_bytes() + b"# \xff\n"),
        "is not valid UTF-8",
    ),
    "control syntax error": (
        _CONTROL,
        lambda d: (d / _CONTROL).write_text("id: [DAT-01\n", "utf-8"),
        "is not YAML the engine can load",
    ),
    "control nested too deeply": (
        _CONTROL,
        lambda d: (d / _CONTROL).write_text(
            "x: " + "[" * 5000 + "]" * 5000 + "\n", "utf-8"
        ),
        "is nested too deeply to read safely",
    ),
    "control python tag": (
        _CONTROL,
        lambda d: (d / _CONTROL).write_text("id: !!python/object:object {}\n", "utf-8"),
        "is not YAML the engine can load",
    ),
    "control second document": (
        _CONTROL,
        lambda d: (d / _CONTROL).write_text(
            (d / _CONTROL).read_text("utf-8") + "---\nid: DAT-99\n", "utf-8"
        ),
        "is not YAML the engine can load",
    ),
}


@pytest.mark.parametrize("case", sorted(_CASES))
def test_a_catalog_file_that_cannot_be_loaded_is_refused_naming_it(
    tmp_path: Path, case: str
) -> None:
    rel, change, problem = _CASES[case]
    directory = _catalog(tmp_path)
    change(directory)
    with pytest.raises(InputError) as caught:
        load_catalog(directory)
    assert caught.value.key == "input.catalog_unreadable"
    assert f"holds a file that cannot be loaded: {rel} {problem}" in caught.value.cause
    assert rel in caught.value.fix


def test_a_calendar_invalid_unquoted_date_in_a_control_is_read() -> None:
    """TypeScript and Java read it as text; Python's safe_load crashed on it before 18.184i."""
    from agentce.safe_yaml import load_unique_keys

    assert load_unique_keys("since: 2026-02-30\n") == {"since": "2026-02-30"}


def _assess(
    capsys: pytest.CaptureFixture[str], out: Path, *extra: str
) -> tuple[int, dict[str, Any]]:
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
            *extra,
            "--out",
            str(out),
            "--json",
        ]
    )
    return code, json.loads(capsys.readouterr().out)


@pytest.mark.parametrize("waived", [False, True])
def test_a_load_refusal_comes_before_the_signature_and_is_never_waived(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], waived: bool
) -> None:
    rel, change, _ = _CASES["control key twice, the weaker last"]
    directory = _catalog(tmp_path)
    change(directory)
    extra = ["--allow-unverified-catalog"] if waived else []
    code, envelope = _assess(
        capsys, tmp_path / "out", "--catalog-dir", str(directory), *extra
    )
    assert code == 3
    assert envelope["error"]["key"] == "input.catalog_unreadable"
    assert rel in envelope["error"]["cause"]
    assert not (tmp_path / "out").exists()


def test_catalog_lint_fails_a_repeated_key(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    directory = _catalog(tmp_path)
    _CASES["control key twice, the weaker last"][1](directory)
    code = cli.main(["catalog", "lint", str(directory)])
    err = capsys.readouterr()
    text = err.out + err.err
    assert code != 0
    assert "CATALOG FAILED" in text
    assert "input.catalog_unreadable" in text
    assert _CONTROL in text


def _not_a_file(directory: Path, kind: str) -> None:
    sig = directory / signing.CATALOG_SIGNATURE_NAME
    if kind == "fifo":
        os.mkfifo(sig)
    elif kind == "folder":
        sig.mkdir()
    else:
        sig.symlink_to(directory / "no-such-signature.json")


_KINDS = ["fifo", "folder", "dangling link"]


@pytest.mark.parametrize("kind", _KINDS)
def test_a_signature_that_is_not_a_file_is_unsigned_and_never_called_absent(
    tmp_path: Path, kind: str
) -> None:
    if kind == "fifo" and not hasattr(os, "mkfifo"):
        pytest.skip("no mkfifo on this platform")
    directory = _catalog(tmp_path)
    _not_a_file(directory, kind)
    trust = signing.vendored_trust()
    with pytest.raises(signing.UnsignedError) as caught:
        signing.verify_catalog_directory(directory, trust)
    assert caught.value.keys == ("verify.catalog_signature_not_a_file",)
    assert "is absent" not in str(caught.value)
    assert "is there but is not a regular file" in str(caught.value)


def test_an_absent_signature_keeps_its_key(tmp_path: Path) -> None:
    directory = _catalog(tmp_path)
    with pytest.raises(signing.UnsignedError) as caught:
        signing.verify_catalog_directory(directory, signing.vendored_trust())
    assert caught.value.keys == ("verify.catalog_unsigned",)


@pytest.mark.parametrize("kind", _KINDS)
def test_a_signature_that_is_not_a_file_is_waived_as_unsigned(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], kind: str
) -> None:
    if kind == "fifo" and not hasattr(os, "mkfifo"):
        pytest.skip("no mkfifo on this platform")
    directory = _catalog(tmp_path)
    _not_a_file(directory, kind)
    code, envelope = _assess(capsys, tmp_path / "out", "--catalog-dir", str(directory))
    assert code == 3
    assert envelope["error"]["key"] == "input.catalog_unverified"
    assert "is absent" not in envelope["error"]["cause"]
    code, envelope = _assess(
        capsys,
        tmp_path / "out",
        "--catalog-dir",
        str(directory),
        "--allow-unverified-catalog",
    )
    assert code in (0, 1), envelope
    assert len(envelope["limitations"]) == 1
