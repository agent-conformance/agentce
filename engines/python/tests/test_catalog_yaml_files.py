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
import yaml

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
# A tag PyYAML's SafeLoader constructs but the TypeScript and Java readers do not is refused in every engine (18.184o).
for _tag, _value in {
    "binary": "aGVsbG8=",
    "set": "{a: null}",
    "omap": "[{a: 1}]",
    "pairs": "[{a: 1}]",
}.items():
    _CASES[f"control !!{_tag}"] = (
        _CONTROL,
        lambda d, line=f"extra: !!{_tag} {_value}\n": _edit(
            d, _CONTROL, "id: DAT-01\n", "id: DAT-01\n" + line
        ),
        "is not YAML the engine can load",
    )
# A merge source is never constructed, so its tag is checked on its own, and a key it writes twice is refused (18.184o).
for _name, _merge in {
    "!!set": "!!set {min_source_class: self_report}",
    "!!omap": "!!omap [{min_source_class: self_report}]",
    "!!pairs": "!!pairs [{min_source_class: self_report}]",
    "!!set in a list": "[!!set {min_source_class: self_report}]",
    "!!merge": "!!merge {min_source_class: self_report}",
    "local tag": "!foo {min_source_class: self_report}",
    "repeated key": "{min_source_class: independent_system, min_source_class: self_report}",
    "repeated key in a list": "[{min_source_class: independent_system, min_source_class: self_report}]",
}.items():
    _CASES[f"control merge source {_name}"] = (
        _CONTROL,
        lambda d, line=f"extra:\n  <<: {_merge}\n": _edit(
            d, _CONTROL, "id: DAT-01\n", "id: DAT-01\n" + line
        ),
        "is not YAML the engine can load",
    )
_CASES["catalog.yaml !!binary"] = (
    "catalog.yaml",
    lambda d: _edit(
        d,
        "catalog.yaml",
        "id: eu-ai-act\n",
        "id: eu-ai-act\nextra: !!binary aGVsbG8=\n",
    ),
    "is not YAML the engine can load",
)


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
    from agentce.safe_yaml import load_catalog_yaml

    assert load_catalog_yaml("since: 2026-02-30\n") == {"since": "2026-02-30"}


_FLOOR = "  min_source_class: self_report\n"

# How evaluation.min_source_class is written -> the class every engine reads (PyYAML's merge rules, 18.184o): an
# explicit key beats a merged one wherever it is written, and the first source of a merge list wins.
_MERGES: dict[str, tuple[str, str, str]] = {
    "inline mapping": (
        "",
        "  <<: {min_source_class: independent_system}\n",
        "independent_system",
    ),
    "alias": (
        "x-b: &b {min_source_class: independent_system}\n",
        "  <<: *b\n",
        "independent_system",
    ),
    "list, stronger first": (
        "",
        "  <<: [{min_source_class: independent_system}, {min_source_class: self_report}]\n",
        "independent_system",
    ),
    "list, weaker first": (
        "",
        "  <<: [{min_source_class: self_report}, {min_source_class: independent_system}]\n",
        "self_report",
    ),
    "nested override": (
        "x-a: &a {min_source_class: self_report}\nx-b: &b {<<: *a, min_source_class: independent_system}\n",
        "  <<: *b\n",
        "independent_system",
    ),
    "explicit key after the merge": (
        "",
        "  <<: {min_source_class: independent_system}\n" + _FLOOR,
        "self_report",
    ),
    "explicit key before the merge": (
        "",
        "  min_source_class: independent_system\n  <<: {min_source_class: self_report}\n",
        "independent_system",
    ),
}


@pytest.mark.parametrize("case", sorted(_MERGES))
def test_a_merge_key_in_a_control_is_read_as_pyyaml_merges(
    tmp_path: Path, case: str
) -> None:
    head, floor, expected = _MERGES[case]
    directory = _catalog(tmp_path)
    path = directory / _CONTROL
    path.write_text(head + path.read_text("utf-8").replace(_FLOOR, floor), "utf-8")
    control = next(c for c in load_catalog(directory).controls if c.id == "DAT-01")
    assert control.min_source_class == expected


def test_a_merged_value_that_is_not_a_source_class_is_refused(tmp_path: Path) -> None:
    directory = _catalog(tmp_path)
    _edit(directory, _CONTROL, _FLOOR, "  <<: {min_source_class: trusted}\n")
    with pytest.raises(InputError) as caught:
        load_catalog(directory)
    assert caught.value.key == "catalog.control.min_source_class_unknown"


def test_a_merge_key_in_catalog_yaml_is_read(tmp_path: Path) -> None:
    directory = _catalog(tmp_path)
    _edit(
        directory, "catalog.yaml", 'version: "2026.09"\n', '<<: {version: "2026.09"}\n'
    )
    assert load_catalog(directory).version == "2026.09"


def test_explicit_core_tags_stay_readable(tmp_path: Path) -> None:
    directory = _catalog(tmp_path)
    _edit(
        directory,
        _CONTROL,
        "id: DAT-01\n",
        "id: !!str DAT-01\nsince: !!timestamp 2026-01-02\n",
    )
    assert any(c.id == "DAT-01" for c in load_catalog(directory).controls)


def test_a_mapping_merged_after_its_own_merge_is_read() -> None:
    """A source is checked for repeated keys before it is flattened, so a mapping that merged another and is merged in
    turn is not read as writing the merged key twice (18.184o)."""
    from agentce.safe_yaml import load_catalog_yaml

    text = "b: &b {m: 1}\na: &a {<<: *b, m: 2}\nc: {<<: *a}\nd: {<<: [*a, *b]}\n"
    assert load_catalog_yaml(text) == {
        "b": {"m": 1},
        "a": {"m": 2},
        "c": {"m": 2},
        "d": {"m": 2},
    }


def test_the_domain_binding_loader_refuses_a_key_written_twice_in_a_merge_source() -> (
    None
):
    """The unique-key loader checks a merge source too, as Java's rejectRepeatedKeys does for every unique-key file."""
    from agentce.safe_yaml import _UniqueKeySafeLoader

    with pytest.raises(
        yaml.constructor.ConstructorError, match="found the key 'm' twice"
    ):
        yaml.load("e:\n  <<: {m: a, m: b}\n", Loader=_UniqueKeySafeLoader)


def test_catalog_sign_refuses_a_control_with_a_safe_loader_only_tag(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    directory = _catalog(tmp_path)
    _CASES["control !!binary"][1](directory)
    argv = [
        "catalog",
        "sign",
        str(directory),
        "--new-key",
        str(tmp_path / "k.pem"),
        "--json",
    ]
    assert cli.main(argv) == 3
    assert (
        json.loads(capsys.readouterr().out)["error"]["key"]
        == "catalog.sign_lint_failed"
    )
    assert not (directory / signing.CATALOG_SIGNATURE_NAME).exists()


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
