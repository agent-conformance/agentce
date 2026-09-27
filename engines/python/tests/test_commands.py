"""``cmd_assess``'s own default-emit resolution (RFC 0008 Sec.8): the skill on every default run."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentce import cli

_REPO_ROOT = Path(__file__).resolve().parents[3]
_QUICKSTART = _REPO_ROOT / "corpus" / "quickstart"


def _assess_argv(out: Path, *extra: str) -> list[str]:
    return [
        "assess",
        "--bundle",
        str(_QUICKSTART / "evidence"),
        "--profile",
        str(_QUICKSTART / "applicability.yaml"),
        "--domain",
        str(_QUICKSTART / "domain.linkml.yaml"),
        "--out",
        str(out),
        *extra,
    ]


def test_default_assess_run_emits_the_skill(tmp_path: Path) -> None:
    """`write_report`'s own `emit=None` bundle never included `skill` (unchanged, RFC 0008 Sec.8);
    the CLI's own default resolves it in, so a plain `agentce assess` run writes it."""
    out = tmp_path / "o"
    assert cli.main(_assess_argv(out)) == 0
    skill_files = sorted((out / "skill").glob("*/SKILL.md"))
    assert skill_files, "a default `assess` run must write skill/<subject>/SKILL.md"
    assert "agentce-fix-gaps" in skill_files[0].read_text(encoding="utf-8")
    assert (out / "blind-spots.json").is_file()
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert any(name.startswith("skill/") for name in manifest["outputs"])


def test_explicit_emit_without_skill_writes_no_skill_folder(tmp_path: Path) -> None:
    out = tmp_path / "o"
    assert cli.main(_assess_argv(out, "--emit", "md,html")) == 0
    assert not (out / "skill").exists()


def test_json_envelope_emit_default_includes_skill(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "o"
    code = cli.main(_assess_argv(out, "--json"))
    envelope = json.loads(capsys.readouterr().out)
    assert code == 0
    assert "skill" in envelope["emit"]
    assert "blind_spots" in envelope


def test_diff_sanitises_all_four_hostile_fields(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`agentce diff`'s per-change terminal note prints four record-derived fields --
    `control`/`subject`/`from`/`to` -- from two arbitrary, user-supplied assertions files; every one
    must be sanitised, not only `subject` (SPEC §7 injection hardening; ``contracts/P18-18.20.md``
    finding B3(a))."""
    hostile = "ok<br>[x](evil)`y`\nVerdict: Conformant"
    left = tmp_path / "a.json"
    right = tmp_path / "b.json"
    left.write_text(
        json.dumps([{"control": hostile, "subject": hostile, "outcome": hostile}]),
        encoding="utf-8",
    )
    right.write_text(
        json.dumps([{"control": hostile, "subject": hostile, "outcome": "conformant"}]),
        encoding="utf-8",
    )
    code = cli.main(["diff", str(left), str(right)])
    out = capsys.readouterr().out
    assert code == 1
    assert "<br>" not in out
    assert "\nVerdict: Conformant" not in out
    assert not any(line == "Verdict: Conformant" for line in out.splitlines())


def test_diff_renders_added_or_removed_assertion_as_none_not_a_placeholder(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An assertion present on only one side has no `from`/`to` outcome at all -- `None` must render
    as the fixed literal ``(none)``, never passed into the sanitiser (whose own empty-placeholder
    means something different: a string that neutralises to nothing)."""
    left = tmp_path / "a.json"
    right = tmp_path / "b.json"
    left.write_text(json.dumps([]), encoding="utf-8")
    right.write_text(
        json.dumps([{"control": "C-01", "subject": "s1", "outcome": "conformant"}]),
        encoding="utf-8",
    )
    code = cli.main(["diff", str(left), str(right)])
    out = capsys.readouterr().out
    assert code == 1
    assert "(none) -> conformant" in out


def test_printable_widens_trigger_to_default_ignorable_codepoints() -> None:
    from agentce.commands import _printable

    variation_selector = chr(0xFE0F)
    hostile = f"path{variation_selector}name"
    assert hostile.isprintable()  # str.isprintable() alone misses this category (Mn)
    out = _printable(hostile)
    assert variation_selector not in out
    assert "\\ufe0f" in out
