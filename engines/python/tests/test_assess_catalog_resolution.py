"""``assess`` resolves every catalog it is asked to evaluate, and never succeeds having judged nothing.

An ``id@version`` — typed with ``--catalog`` or declared in the profile — resolves to a
``--catalog-dir`` that was passed or to a catalog vendored under ``spec/catalogs``; an id that
resolves to neither, or a run that names no catalog at all, is an input error (exit 3) that writes
nothing. The assess examples the documentation shows must run as written.
"""

from __future__ import annotations

import json
import re
import shlex
from pathlib import Path
from typing import Any

import pytest
import yaml

from agentce import cli

_REPO_ROOT = Path(__file__).resolve().parents[3]
_QUICKSTART = _REPO_ROOT / "corpus" / "quickstart"
_EU_DIR = _REPO_ROOT / "spec" / "catalogs" / "base" / "eu-ai-act"
_EU_LABEL = "eu-ai-act@2026.09"
_DOC_PAGES = (
    _REPO_ROOT
    / "website"
    / "src"
    / "content"
    / "docs"
    / "docs"
    / "running-assessments.md",
    _REPO_ROOT / "website" / "src" / "content" / "docs" / "docs" / "ci-integration.md",
)


def _profile(tmp_path: Path, catalogs: list[str] | None) -> Path:
    """The quickstart profile with its ``catalogs:`` list replaced (removed when ``None``)."""
    data: dict[str, Any] = yaml.safe_load(
        (_QUICKSTART / "applicability.yaml").read_text(encoding="utf-8")
    )
    if catalogs is None:
        data.pop("catalogs", None)
    else:
        data["catalogs"] = catalogs
    path = tmp_path / "profile.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def _assess(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    profile: Path,
    *extra: str,
) -> tuple[int, dict[str, Any], Path]:
    out = tmp_path / "out"
    code = cli.main(
        [
            "assess",
            "--bundle",
            str(_QUICKSTART / "evidence"),
            "--profile",
            str(profile),
            "--domain",
            str(_QUICKSTART / "domain.linkml.yaml"),
            *extra,
            "--out",
            str(out),
            "--json",
        ]
    )
    stdout = capsys.readouterr().out
    return code, json.loads(stdout) if stdout.strip() else {}, out


def _assertion_count(out: Path) -> int:
    return len(json.loads((out / "assertions.json").read_text(encoding="utf-8")))


def test_an_unresolvable_catalog_id_is_refused_and_writes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, envelope, out = _assess(
        tmp_path,
        capsys,
        _profile(tmp_path, [_EU_LABEL]),
        "--catalog",
        "does-not-exist@9.9",
    )
    assert code == 3
    assert envelope["error"]["key"] == "input.catalog_unresolved"
    assert "'does-not-exist@9.9'" in envelope["error"]["cause"]
    assert _EU_LABEL in envelope["error"]["cause"]  # the ids that do resolve are named
    assert not out.exists()


@pytest.mark.parametrize(
    "requested",
    [
        "eu-ai-act@1999.01",  # a real id, a version no catalog carries
        "eu-ai-act",  # no version
        "@2026.09",
        "eu-ai-act@2026.09,does-not-exist@9.9",  # one good id does not excuse a bad one
        "bad\nid@1'\"",  # a hostile string is reported, not interpreted
    ],
)
def test_no_route_to_an_unresolved_catalog_exits_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], requested: str
) -> None:
    code, envelope, out = _assess(
        tmp_path, capsys, _profile(tmp_path, [_EU_LABEL]), "--catalog", requested
    )
    assert code == 3
    assert envelope["error"]["key"] == "input.catalog_unresolved"
    assert not out.exists()


def test_the_catalog_defaults_from_the_profile_and_evaluates(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, envelope, out = _assess(tmp_path, capsys, _profile(tmp_path, [_EU_LABEL]))
    assert code == 0
    assert envelope["catalogs"] == [_EU_LABEL]
    assert _assertion_count(out) > 0
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert [f"{c['id']}@{c['version']}" for c in manifest["inputs"]["catalogs"]] == [
        _EU_LABEL
    ]


def test_an_explicit_catalog_id_resolves_without_a_catalog_dir(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _, out = _assess(
        tmp_path, capsys, _profile(tmp_path, None), "--catalog", _EU_LABEL
    )
    assert code == 0
    assert _assertion_count(out) > 0


def test_a_profile_that_declares_no_catalog_and_none_passed_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    declared_cases: list[list[str] | None] = [None, []]
    for declared in declared_cases:
        sub = tmp_path / f"case-{declared}"
        sub.mkdir()
        code, envelope, out = _assess(sub, capsys, _profile(sub, declared))
        assert code == 3
        assert envelope["error"]["key"] == "input.catalog_missing"
        assert not out.exists()


def test_a_profile_declaring_an_unresolvable_catalog_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, envelope, _ = _assess(
        tmp_path, capsys, _profile(tmp_path, ["not-a-catalog@1"])
    )
    assert code == 3
    assert envelope["error"]["key"] == "input.catalog_unresolved"


def test_a_catalog_dir_alone_is_an_explicit_override(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, envelope, out = _assess(
        tmp_path,
        capsys,
        _profile(tmp_path, ["not-a-catalog@1"]),
        "--catalog-dir",
        str(_EU_DIR),
    )
    assert code == 0
    assert envelope["catalogs"] == [_EU_LABEL]
    assert _assertion_count(out) > 0


def test_a_label_that_names_a_different_catalog_than_the_dir_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``--catalog`` is no longer a free-form label riding beside ``--catalog-dir``."""
    code, envelope, _ = _assess(
        tmp_path,
        capsys,
        _profile(tmp_path, [_EU_LABEL]),
        "--catalog",
        "someone-elses@1",
        "--catalog-dir",
        str(_EU_DIR),
    )
    assert code == 3
    assert envelope["error"]["key"] == "input.catalog_unresolved"


def test_a_catalog_dir_supplies_the_id_it_carries(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, envelope, _ = _assess(
        tmp_path,
        capsys,
        _profile(tmp_path, [_EU_LABEL]),
        "--catalog",
        _EU_LABEL,
        "--catalog-dir",
        str(_EU_DIR),
    )
    assert code == 0
    assert envelope["catalogs"] == [_EU_LABEL]  # one catalog, not evaluated twice


def _documented_assess_commands() -> list[tuple[str, list[str]]]:
    commands: list[tuple[str, list[str]]] = []
    for page in _DOC_PAGES:
        text = page.read_text(encoding="utf-8")
        for block in re.findall(r"```bash\n(.*?)```", text, flags=re.DOTALL):
            joined = re.sub(r"\\\n\s*", " ", block)
            for line in joined.splitlines():
                if "agentce assess" in line:
                    words = shlex.split(line.split("agentce", 1)[1])
                    commands.append((page.name, words))
    return commands


def test_the_documented_assess_examples_are_found() -> None:
    assert {page for page, _ in _documented_assess_commands()} == {
        page.name for page in _DOC_PAGES
    }


@pytest.mark.parametrize(
    ("page", "words"),
    _documented_assess_commands(),
    ids=lambda v: v if isinstance(v, str) else "",
)
def test_the_documented_assess_example_evaluates_as_written(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    page: str,
    words: list[str],
) -> None:
    """Stage the paths the page uses (an evidence bundle and the profile and domain ``init``
    writes), run the command exactly as documented, and require real assertions."""
    (tmp_path / "bundle").symlink_to(_QUICKSTART / "evidence")
    declarations = tmp_path / "my-assessment" / "agentce"
    declarations.mkdir(parents=True)
    for name in ("applicability.yaml", "domain.linkml.yaml"):
        (declarations / name).write_text(
            (_QUICKSTART / name).read_text(encoding="utf-8"), encoding="utf-8"
        )
    monkeypatch.chdir(tmp_path)
    assert cli.main([*words, "--json"]) == 0, capsys.readouterr()
    capsys.readouterr()
    out = tmp_path / words[words.index("--out") + 1]
    assert _assertion_count(out) > 0
