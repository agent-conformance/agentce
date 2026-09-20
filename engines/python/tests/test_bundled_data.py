"""The data the engine ships resolves from the package, not from a checkout (SPEC §13.4 AX-1).

The catalogs and the quickstart project are vendored under ``agentce/data/`` and must stay
byte-identical to their ``spec/`` and ``corpus/`` originals. ``test_installed_wheel.py`` proves the
built wheel and sdist carry them.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentce import bundled

_REPO_ROOT = Path(__file__).resolve().parents[3]

#: (vendored tree, authoritative tree) pairs the engine carries.
_TREES: list[tuple[Path, Path]] = [
    (bundled.catalogs_dir() / "base", _REPO_ROOT / "spec" / "catalogs" / "base"),
    (
        bundled.catalogs_dir() / "overlays",
        _REPO_ROOT / "spec" / "catalogs" / "overlays",
    ),
    (bundled.quickstart_dir(), _REPO_ROOT / "corpus" / "quickstart"),
]


def _files(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
        and path.name != ".DS_Store"
        and "__pycache__" not in path.parts
    }


@pytest.mark.parametrize(("vendored", "authoritative"), _TREES, ids=lambda p: p.name)
def test_vendored_tree_matches_its_original(
    vendored: Path, authoritative: Path
) -> None:
    # Overlays carry a README of their own at the tree root; the engine vendors catalog directories only.
    want = {
        name: data
        for name, data in _files(authoritative).items()
        if name != "README.md" or authoritative.name != "overlays"
    }
    got = _files(vendored)
    assert sorted(got) == sorted(want), (
        f"{vendored} and {authoritative} list different files; re-sync with "
        f"`rsync -a --delete {authoritative}/ {vendored}/`"
    )
    drifted = [name for name in want if got[name] != want[name]]
    assert not drifted, f"vendored bytes drifted from {authoritative}: {drifted[:5]}"


def test_bundled_data_resolves_through_the_package_not_the_checkout() -> None:
    assert (bundled.catalogs_dir() / "base" / "eu-ai-act" / "catalog.yaml").is_file()
    assert (bundled.quickstart_dir() / "applicability.yaml").is_file()
    assert bundled.catalogs_dir().parent == Path(bundled.__file__).parent / "data"


def test_default_catalog_severities_come_from_the_bundled_catalogs() -> None:
    from argparse import Namespace

    from agentce.commands import _readiness_severities, _vendored_catalogs

    severities = _readiness_severities(Namespace(catalog_dir=None))
    assert len(severities) > 10
    assert "eu-ai-act@2026.09" in _vendored_catalogs()
    assert {"conduct", "employment", "finance", "insurance"} <= {
        d.name for d in (bundled.catalogs_dir() / "overlays").iterdir()
    }
