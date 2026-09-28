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


#: All 42 off-diagonal `(before, after)` pairs over `{None} + assertions.OUTCOMES` (item 18.6, C1(a)):
#: written by hand from `verdict.GAP_OUTCOMES = ("non-conformant", "partial", "insufficient_evidence",
#: "not_assessed")`, never re-derived by calling `_classify_change`'s own two conditions again, so this
#: table pins the intended behaviour rather than tautologically restating the implementation.
_DIFF_CLASSIFY_PAIRS: tuple[tuple[str | None, str | None, str], ...] = (
    # before=None: never opened/closed regardless of after (an added assertion).
    (None, "conformant", "other"),
    (None, "non-conformant", "other"),
    (None, "partial", "other"),
    (None, "not_applicable", "other"),
    (None, "not_assessed", "other"),
    (None, "insufficient_evidence", "other"),
    # before="conformant": opened iff after is a gap outcome.
    ("conformant", None, "other"),
    ("conformant", "non-conformant", "opened"),
    ("conformant", "partial", "opened"),
    ("conformant", "not_applicable", "other"),
    ("conformant", "not_assessed", "opened"),
    ("conformant", "insufficient_evidence", "opened"),
    # before="non-conformant" (a gap): closed iff after=="conformant".
    ("non-conformant", None, "other"),
    ("non-conformant", "conformant", "closed"),
    ("non-conformant", "partial", "other"),
    ("non-conformant", "not_applicable", "other"),
    ("non-conformant", "not_assessed", "other"),
    ("non-conformant", "insufficient_evidence", "other"),
    # before="partial" (a gap): closed iff after=="conformant".
    ("partial", None, "other"),
    ("partial", "conformant", "closed"),
    ("partial", "non-conformant", "other"),
    ("partial", "not_applicable", "other"),
    ("partial", "not_assessed", "other"),
    ("partial", "insufficient_evidence", "other"),
    # before="not_applicable": never a gap and never conformant, so always other.
    ("not_applicable", None, "other"),
    ("not_applicable", "conformant", "other"),
    ("not_applicable", "non-conformant", "other"),
    ("not_applicable", "partial", "other"),
    ("not_applicable", "not_assessed", "other"),
    ("not_applicable", "insufficient_evidence", "other"),
    # before="not_assessed" (a gap): closed iff after=="conformant".
    ("not_assessed", None, "other"),
    ("not_assessed", "conformant", "closed"),
    ("not_assessed", "non-conformant", "other"),
    ("not_assessed", "partial", "other"),
    ("not_assessed", "not_applicable", "other"),
    ("not_assessed", "insufficient_evidence", "other"),
    # before="insufficient_evidence" (a gap): closed iff after=="conformant".
    ("insufficient_evidence", None, "other"),
    ("insufficient_evidence", "conformant", "closed"),
    ("insufficient_evidence", "non-conformant", "other"),
    ("insufficient_evidence", "partial", "other"),
    ("insufficient_evidence", "not_applicable", "other"),
    ("insufficient_evidence", "not_assessed", "other"),
)


def test_diff_classify_change_pairs_cover_all_42() -> None:
    assert len(_DIFF_CLASSIFY_PAIRS) == 42
    assert len({(b, a) for b, a, _ in _DIFF_CLASSIFY_PAIRS}) == 42
    universe = (
        None,
        "conformant",
        "non-conformant",
        "partial",
        "not_applicable",
        "not_assessed",
        "insufficient_evidence",
    )
    assert len(universe) == 7
    for before in universe:
        for after in universe:
            if before != after:
                assert (before, after) in {(b, a) for b, a, _ in _DIFF_CLASSIFY_PAIRS}


@pytest.mark.parametrize("before,after,expected", _DIFF_CLASSIFY_PAIRS)
def test_diff_classify_change_pairs(
    before: str | None, after: str | None, expected: str
) -> None:
    from agentce.commands import _classify_change

    assert _classify_change(before, after) == expected


@pytest.mark.parametrize(
    "before,after",
    [
        ("Conformant", "non-conformant"),
        ("non-conformant", "Conformant"),
        ("CONFORMANT", "conformant"),
    ],
)
def test_diff_classify_change_unknown_outcome(before: str, after: str) -> None:
    """An out-of-vocabulary outcome string (wrong case, or otherwise not in the six-outcome
    vocabulary) lands in `other` by the same plain two-condition rule -- neither `in GAP_OUTCOMES` nor
    `== "conformant"` matches it -- with no special-casing needed in the implementation."""
    from agentce.commands import _classify_change

    assert _classify_change(before, after) == "other"


def test_diff_what_changed_always_has_all_three_keys_sorted_within_group() -> None:
    """`_what_changed`'s three keys are always present, even when empty (the cross-engine output
    contract for item 18.24), and entries within a group keep `_diff_assertion_sets`'s own
    `(control, subject)` order."""
    from agentce.commands import _what_changed

    empty = _what_changed([])
    assert set(empty) == {"closed", "opened", "other"}
    assert empty == {"closed": [], "opened": [], "other": []}

    changes = [
        {
            "control": "C-02",
            "subject": "s1",
            "from": "non-conformant",
            "to": "conformant",
        },
        {
            "control": "C-01",
            "subject": "s1",
            "from": "non-conformant",
            "to": "conformant",
        },
    ]
    grouped = _what_changed(changes)
    assert [c["control"] for c in grouped["closed"]] == ["C-02", "C-01"]


def test_printable_widens_trigger_to_default_ignorable_codepoints() -> None:
    from agentce.commands import _printable

    variation_selector = chr(0xFE0F)
    hostile = f"path{variation_selector}name"
    assert hostile.isprintable()  # str.isprintable() alone misses this category (Mn)
    out = _printable(hostile)
    assert variation_selector not in out
    assert "\\ufe0f" in out
