"""Catalog-write guards and containment judged by folder identity (contracts/P18-18.184l.md, C3).

catalog sign and lint --require-provenance refuse a covered file reached through a folder linked
outside (inbox row 397); the inside-catalog guards and bundle containment judge by folder identity
whatever the letter case (census line 56); --out inside an unenterable records folder is the records
collision (inbox row 421); --package-for-sharing refuses a link in --out that leads outside (ruling
R7); doctor names an unreadable or malformed applicability profile. Case-variant tests skip where the
filesystem tells names apart by case (KD-441)."""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
)

from agentce import cli, signing
from agentce.bundle import confine_to_root

_REPO_ROOT = Path(__file__).resolve().parents[3]
_QUICKSTART = _REPO_ROOT / "corpus" / "quickstart"
_EU_DIR = _REPO_ROOT / "spec" / "catalogs" / "base" / "eu-ai-act"
_FIXTURES = _REPO_ROOT / "adapters" / "otel-genai" / "fixtures"


def _run(
    argv: list[str], capsys: pytest.CaptureFixture[str]
) -> tuple[int, dict[str, Any]]:
    capsys.readouterr()
    code = cli.main([*argv, "--json"])
    return code, json.loads(capsys.readouterr().out)


def _catalog(dest: Path) -> Path:
    """A copy of the committed base catalog with its own signature removed."""
    shutil.copytree(_EU_DIR, dest)
    (dest / signing.CATALOG_SIGNATURE_NAME).unlink()
    return dest


def _key(path: Path) -> Path:
    path.write_bytes(
        Ed25519PrivateKey.generate().private_bytes(
            Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()
        )
    )
    return path


def _folded(path: Path) -> Path:
    """``path`` spelled in upper case; skips the test where that is another folder."""
    upper = Path(str(path).upper())
    if upper == path or not upper.exists():
        pytest.skip("needs a case-insensitive filesystem")
    return upper


@contextmanager
def _mode(path: Path, mode: int) -> Iterator[Path]:
    """``path`` at ``mode``; skips where this user reads through it anyway (root)."""
    path.chmod(mode)
    try:
        if mode & 0o400 == 0 and os.access(path, os.R_OK):
            pytest.skip("this user reads through mode 000")
        yield path
    finally:
        path.chmod(0o700 if path.is_dir() else 0o600)


# --- catalog sign and lint --require-provenance: a linked folder (inbox row 397) ---


def _link_folder_out(catalog: Path, rel: str, outside: Path) -> None:
    """Move the folder ``rel`` out of ``catalog`` and link it back: the same bytes, kept elsewhere."""
    (catalog / rel).rename(outside)
    (catalog / rel).symlink_to(outside, target_is_directory=True)


@pytest.mark.parametrize("rel", ["controls", "test"])
def test_a_folder_linked_outside_is_refused_by_sign_and_provenance_lint(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], rel: str
) -> None:
    catalog = _catalog(tmp_path / "testcat")
    _link_folder_out(catalog, rel, tmp_path / "elsewhere")
    files = signing.read_catalog_files(catalog)
    assert files.links_out == frozenset()
    assert files.outside == frozenset({rel})
    code, env = _run(
        ["catalog", "sign", str(catalog), "--new-key", str(tmp_path / "k.pem")], capsys
    )
    assert (code, env["error"]["key"]) == (3, "catalog.link_outside")
    assert f"the catalog file or folder {rel} is a link" in env["error"]["cause"]
    assert not (catalog / signing.CATALOG_SIGNATURE_NAME).exists()
    assert not (tmp_path / "k.pem").exists()
    code, env = _run(["catalog", "lint", str(catalog), "--require-provenance"], capsys)
    assert code == 1
    assert [p for p in env["problems"] if p.startswith("catalog.link_outside")] == [
        f"catalog.link_outside: the catalog file or folder {rel} is a link to a place outside"
        " the catalog folder. A signed catalog must hold its own files, because a file kept"
        " elsewhere is not handed over with the catalog."
    ]
    code, env = _run(["catalog", "lint", str(catalog)], capsys)
    assert code == 0, env


def test_a_folder_linked_inside_the_catalog_is_not_a_link_outside(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A link to a folder inside the catalog keeps nothing outside it; sign still refuses it, as at
    base, because the walk reaches that folder twice (input.catalog_unreadable), never as a link out."""
    catalog = _catalog(tmp_path / "testcat")
    (catalog / "controls").rename(catalog / "ctl-real")
    (catalog / "controls").symlink_to("ctl-real", target_is_directory=True)
    assert not signing.inside_folder(catalog, tmp_path / "elsewhere")
    assert signing.inside_folder(catalog, catalog / "controls" / "DOC-01.yaml")
    code, env = _run(
        ["catalog", "sign", str(catalog), "--new-key", str(tmp_path / "k.pem")], capsys
    )
    assert (code, env["error"]["key"]) == (3, "catalog.sign_lint_failed")
    assert "catalog.link_outside" not in env["error"]["cause"]
    assert not (catalog / signing.CATALOG_SIGNATURE_NAME).exists()


# --- the inside-catalog guards by folder identity (census line 56) ---


@pytest.mark.parametrize(
    ("flag", "name", "key"),
    [
        ("--key", "k.pem", "catalog.sign_key_inside_catalog"),
        ("--new-key", "new.pem", "catalog.sign_key_inside_catalog"),
        ("--new-key", "missing/sub/new.pem", "catalog.sign_key_inside_catalog"),
        ("--write-trust-root", "tr.json", "catalog.sign_trust_root_inside_catalog"),
        (
            "--write-trust-root",
            "missing/sub/tr.json",
            "catalog.sign_trust_root_inside_catalog",
        ),
        ("--write-trust-root", "", "catalog.sign_trust_root_inside_catalog"),
    ],
)
def test_catalog_sign_guards_judge_a_case_variant_by_identity(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], flag: str, name: str, key: str
) -> None:
    catalog = _catalog(tmp_path / "catcopy")
    upper = _folded(catalog)
    value = upper / name if name else upper
    argv = ["catalog", "sign", str(catalog)]
    if flag == "--key":
        _key(catalog / name)
        argv += ["--key", str(value)]
    elif flag == "--new-key":
        argv += ["--new-key", str(value)]
    else:
        argv += ["--new-key", str(tmp_path / "k.pem"), "--write-trust-root", str(value)]
    code, env = _run(argv, capsys)
    assert (code, env["error"]["key"]) == (3, key)
    assert not (catalog / signing.CATALOG_SIGNATURE_NAME).exists()
    assert not (tmp_path / "k.pem").exists()
    if name and flag != "--key":
        assert not (catalog / name).exists()


def test_catalog_sign_writes_a_trust_root_and_key_outside_the_catalog(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    catalog = _catalog(tmp_path / "catcopy")
    code, env = _run(
        [
            "catalog",
            "sign",
            str(catalog),
            "--key",
            str(_key(tmp_path / "k.pem")),
            "--write-trust-root",
            str(tmp_path / "tr.json"),
        ],
        capsys,
    )
    assert code == 0, env
    assert (tmp_path / "tr.json").exists()


def test_lint_support_matrix_judges_a_case_variant_by_identity(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    catalog = _catalog(tmp_path / "catcopy")
    inside = _folded(catalog) / "support-matrix.md"
    code, env = _run(
        ["catalog", "lint", str(catalog), "--support-matrix", str(inside)], capsys
    )
    assert (code, env["error"]["key"]) == (3, "catalog.support_matrix_inside_catalog")
    assert not (catalog / "support-matrix.md").exists()
    code, env = _run(
        ["catalog", "lint", str(catalog), "--support-matrix", str(tmp_path / "sm.md")],
        capsys,
    )
    assert code == 0, env


def test_inside_folder_judges_a_path_not_written_yet_by_its_existing_parent(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    assert signing.inside_folder(root, root / "a" / "b" / "c.json")
    assert signing.inside_folder(root, root, or_root=True)
    assert not signing.inside_folder(root, root)
    assert not signing.inside_folder(root, tmp_path / "rootless" / "c.json")
    assert not signing.inside_folder(tmp_path / "gone", tmp_path / "gone" / "x")


# --- bundle containment by folder identity ---


def _bundle_with_link(tmp_path: Path, target: Path) -> Path:
    bundle = tmp_path / "bundle"
    (bundle / "moved-inside").mkdir(parents=True)
    (bundle / "moved-inside" / "x.jsonl").write_text("{}\n", encoding="utf-8")
    (bundle / "x.jsonl").symlink_to(target)
    return bundle


def test_a_bundle_member_linked_inside_through_a_case_variant_is_inside(
    tmp_path: Path,
) -> None:
    bundle = _bundle_with_link(
        tmp_path, Path(str(tmp_path / "bundle").upper()) / "moved-inside" / "x.jsonl"
    )
    _folded(bundle)
    assert confine_to_root(bundle, "x.jsonl") == bundle / "x.jsonl"


@pytest.mark.parametrize("where", ["parent", "sibling"])
def test_a_bundle_member_linked_to_the_parent_or_a_sibling_is_outside(
    tmp_path: Path, where: str
) -> None:
    (tmp_path / "bundle2").mkdir()
    (tmp_path / "bundle2" / "x.jsonl").write_text("{}\n", encoding="utf-8")
    (tmp_path / "up.jsonl").write_text("{}\n", encoding="utf-8")
    target = tmp_path / ("up.jsonl" if where == "parent" else "bundle2/x.jsonl")
    bundle = _bundle_with_link(tmp_path, Path(str(target).upper()))
    _folded(bundle)
    assert confine_to_root(bundle, "x.jsonl") is None


# --- records --out inside the records folder, not enterable (inbox row 421) ---


def _records(folder: Path) -> Path:
    folder.mkdir(parents=True)
    shutil.copy(
        _FIXTURES / "otel-genai-agent-session" / "input.json", folder / "session.json"
    )
    return folder


@pytest.mark.parametrize(
    ("mode", "holds", "key"),
    [
        (0o600, True, "input.records_out_collides"),
        (0o000, True, "input.records_out_collides"),
        (0o000, False, "input.records_out_collides"),
        (0o600, False, "input.out_dir_unwritable"),
    ],
)
def test_an_unenterable_out_inside_the_records_folder(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], mode: int, holds: bool, key: str
) -> None:
    records = _records(tmp_path / "records")
    out = records / "out"
    out.mkdir()
    if holds:
        (out / "kept.txt").write_text("kept\n", encoding="utf-8")
    with _mode(out, mode):
        if mode == 0o600 and os.access(out, os.X_OK):
            pytest.skip("this user enters a mode-600 folder")
        code, env = _run(["assess", str(records), "--out", str(out)], capsys)
        assert (code, env["error"]["key"]) == (3, key)
    assert sorted(p.name for p in out.iterdir()) == (["kept.txt"] if holds else [])


# --- --package-for-sharing: a link in --out leading outside (ruling R7) ---


def _package(
    out: Path, capsys: pytest.CaptureFixture[str]
) -> tuple[int, dict[str, Any]]:
    return _run(
        [
            "assess",
            "--bundle",
            str(_QUICKSTART / "evidence"),
            "--profile",
            str(_QUICKSTART / "applicability.yaml"),
            "--domain",
            str(_QUICKSTART / "domain.linkml.yaml"),
            "--out",
            str(out),
            "--package-for-sharing",
        ],
        capsys,
    )


@pytest.mark.parametrize(
    ("entry", "target", "folder"),
    [
        ("claim.json", "outside.json", False),
        ("report.md", "outside.md", False),
        ("bundle", "outside-dir", True),
        ("claim.json", "missing.json", False),
        ("bundle", "missing-dir", True),
    ],
)
def test_package_refuses_a_link_in_out_leading_outside(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    entry: str,
    target: str,
    folder: bool,
) -> None:
    outside = tmp_path / target
    if not target.startswith("missing"):
        if folder:
            outside.mkdir()
            (outside / "kept.txt").write_text("kept\n", encoding="utf-8")
        else:
            outside.write_text("kept\n", encoding="utf-8")
    out = tmp_path / "pkg"
    out.mkdir()
    (out / entry).symlink_to(outside, target_is_directory=folder)
    code, env = _package(out, capsys)
    assert (code, env["error"]["key"]) == (3, "input.package_link_outside")
    assert f"{entry} in --out links outside the output folder" in env["error"]["cause"]
    assert sorted(p.name for p in out.iterdir()) == [entry]
    if target.startswith("missing"):
        assert not os.path.lexists(outside)
    elif folder:
        assert sorted(p.name for p in outside.iterdir()) == ["kept.txt"]
    else:
        assert outside.read_text(encoding="utf-8") == "kept\n"


def test_package_accepts_a_link_inside_out(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "pkg"
    out.mkdir()
    (out / "notes.json").write_text("{}", encoding="utf-8")
    (out / "alias.json").symlink_to("notes.json")
    code, env = _package(out, capsys)
    assert code == 0, env
    assert (out / "claim.json").is_file()


# --- doctor reads the applicability profile with assess's reader ---


def _project(tmp_path: Path, profile: bytes) -> Path:
    project = tmp_path / "project"
    (project / "agentce").mkdir(parents=True)
    (project / "agentce" / "applicability.yaml").write_bytes(profile)
    return project


@pytest.mark.parametrize(
    ("profile", "key"),
    [
        (b"\xff\xfe not utf-8\n", "input.profile_invalid"),
        (b"subjects: [\n", "input.profile_invalid"),
        (b"- a\n", "schema_invalid"),
    ],
)
def test_doctor_names_a_malformed_profile(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], profile: bytes, key: str
) -> None:
    code, env = _run(["doctor", "--project", str(_project(tmp_path, profile))], capsys)
    assert code == 1
    assert key in {p["key"] for p in env["problems"]}


def test_doctor_names_an_unreadable_profile(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = _project(tmp_path, (_QUICKSTART / "applicability.yaml").read_bytes())
    with _mode(project / "agentce" / "applicability.yaml", 0o000):
        code, env = _run(["doctor", "--project", str(project)], capsys)
    assert code == 1
    problems = {p["key"]: p for p in env["problems"]}
    assert "input.profile_unreadable" in problems
    assert "applicability.yaml" in problems["input.profile_unreadable"]["problem"]
