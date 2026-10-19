"""A path through a symlink is read where the kernel reads it: ``lnk/..`` is the parent of the
link's target, never the folder the link sits in (SPEC §8.7; item 18.182). Python is the reference;
these tests pin it, with a decoy at the lexical location, absolute and relative."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentce import cli

_QUICKSTART = Path(__file__).resolve().parents[3] / "corpus" / "quickstart"
_ASSESS = [
    "assess",
    "--bundle",
    str(_QUICKSTART / "evidence"),
    "--profile",
    str(_QUICKSTART / "applicability.yaml"),
    "--domain",
    str(_QUICKSTART / "domain.linkml.yaml"),
]


@pytest.fixture
def w(tmp_path: Path) -> Path:
    """``w/lnk -> w/far/deep``, so the kernel reads ``w/lnk/..`` as ``w/far``."""
    root = tmp_path.resolve()
    (root / "far" / "deep").mkdir(parents=True)
    (root / "lnk").symlink_to(root / "far" / "deep")
    (root / "target").mkdir()  # the decoy: must stay empty
    return root


def _readiness_in(folder: Path) -> bool:
    return any(folder.glob("report-readiness-*.md"))


@pytest.mark.parametrize("relative", [False, True])
def test_assess_out_and_readiness_land_in_the_kernel_folder(
    w: Path,
    relative: bool,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    target = "lnk/../target" if relative else f"{w}/lnk/../target"
    if relative:
        monkeypatch.chdir(w)
    assert cli.main([*_ASSESS, "--out", target]) == 0
    assert (w / "far" / "target" / "assertions.json").is_file()
    assert cli.main(["readiness", target]) in (0, 1)
    capsys.readouterr()
    assert _readiness_in(w / "far" / "target")
    assert list((w / "target").iterdir()) == []
