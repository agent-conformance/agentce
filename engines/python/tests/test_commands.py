"""``cmd_assess``'s own default-emit resolution (RFC 0008 Sec.8): the skill on every default run."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agentce import cli
from agentce.report import ASSESS_DEFAULT_EMIT, validate_report

_REPO_ROOT = Path(__file__).resolve().parents[3]
_QUICKSTART = _REPO_ROOT / "corpus" / "quickstart"


def _assess_argv(out: Path, *extra: str, domain: bool = True) -> list[str]:
    return [
        "assess",
        "--bundle",
        str(_QUICKSTART / "evidence"),
        "--profile",
        str(_QUICKSTART / "applicability.yaml"),
        *(["--domain", str(_QUICKSTART / "domain.linkml.yaml")] if domain else []),
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


# `--for` audience presets (contracts/P18-18.7.md). Every table below is hand-written, never derived
# from `commands.PRESET_EMIT`, so a typo in the shipped mapping cannot also hide in its own test.
_PRESET_PAIRS: dict[str, frozenset[str]] = {
    "engineering": frozenset({"md", "html", "skill", "remediation"}),
    "compliance": frozenset({"oscal", "oscal_xml", "public", "pack", "csv"}),
    "security": frozenset({"sarif", "md", "html"}),
    "ci": frozenset({"sarif", "junit"}),
    "share": frozenset({"md", "html", "pdf", "public", "pack"}),
    "risk-lead": frozenset({"md", "html"}),
    "auditor": frozenset({"oscal", "oscal_xml", "pack"}),
    "buyer": frozenset({"pack"}),
}

#: Every file a run writes regardless of `--emit`/`--for` (contracts/P18-18.7.md C1).
_ALWAYS_FILES = frozenset(
    {
        "assertions.json",
        "activity.json",
        "blind-spots.json",
        "manifest.json",
        "claim.json",
        "quarantine.jsonl",
        "integrity.jsonl",
        "graph.sqlite",
        "coverage.json",
        "applicability.jsonl",
        "packaging.json",
    }
)

#: Per-token files, relative to `--out`, one glob pattern per file (a `*` stands for the one subject
#: id the quickstart fixture assesses).
_TOKEN_FILES: dict[str, tuple[str, ...]] = {
    "md": ("report.md",),
    "html": ("report.html",),
    "oscal": ("oscal-ar.json",),
    "oscal_xml": ("oscal-ar.xml", "oscal-ar.json"),
    "sarif": ("results.sarif",),
    "public": ("public-statement.md",),
    "pack": ("packs/*/pack.json",),
    "junit": ("report.junit.xml",),
    "csv": ("report.csv",),
    "pdf": ("report.pdf",),
    "remediation": (
        "remediation/*/remediation-package.json",
        "remediation/*/remediation.md",
    ),
    "skill": (
        "skill/*/SKILL.md",
        "skill/*/REVERIFY.md",
        "skill/*/remediation-package.json",
        "skill/*/findings/*.md",
    ),
}

#: Files a preset gates outside `--emit` entirely (18.16's `for_preset` mechanism,
#: contracts/P18-18.16.md `foundational_thinking`) -- kept separate from `_TOKEN_FILES`/`_PRESET_PAIRS`
#: precisely because `commands.PRESET_EMIT` (asserted equal to `_PRESET_PAIRS` above) must stay
#: `{"sarif", "md", "html"}` for `security`, unchanged by this item (TRADEOFFS row 18). RED if a future
#: change drops one of the three files from the `security` preset's output.
_PRESET_VIEW_FILES: dict[str, tuple[str, ...]] = {
    "security": ("security.md", "security.html", "security.json"),
    "auditor": ("auditor.md", "auditor.html", "auditor.json"),
    "buyer": ("buyer.md", "buyer.html", "buyer.json"),
}


def test_for_preset_matches_documented_set_pairs() -> None:
    from agentce import commands

    assert commands.PRESET_EMIT == _PRESET_PAIRS


def test_for_unknown_preset_exits_3(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "o"
    code = cli.main(_assess_argv(out, "--for", "bogus", "--json"))
    envelope = json.loads(capsys.readouterr().out)
    assert code == 3
    assert envelope["error"]["key"] == "input.for_preset"
    assert not out.exists()


def test_for_and_emit_conflict_exits_3(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "o"
    code = cli.main(_assess_argv(out, "--for", "engineering", "--emit", "md", "--json"))
    envelope = json.loads(capsys.readouterr().out)
    assert code == 3
    assert envelope["error"]["key"] == "input.for_emit_ambiguous"
    assert not out.exists()


@pytest.mark.parametrize(
    ("argv_extra", "expected_key"),
    [
        (("--for", "CI"), "input.for_preset"),
        (("--for", "Engineering"), "input.for_preset"),
        (("--for", "ci,security"), "input.for_preset"),
        (("--for", ""), "input.for_preset"),
        (("--emit", "", "--for", "engineering"), "input.for_emit_ambiguous"),
    ],
)
def test_for_case_and_list_negative_cases(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    argv_extra: tuple[str, ...],
    expected_key: str,
) -> None:
    """No case-folding, no list parsing: `--for` matches exactly one of `PRESET_EMIT`'s keys, and an
    explicitly empty `--emit` still counts as "given" against `--for`."""
    out = tmp_path / "o"
    code = cli.main(_assess_argv(out, *argv_extra, "--json"))
    envelope = json.loads(capsys.readouterr().out)
    assert code == 3
    assert envelope["error"]["key"] == expected_key
    assert not out.exists()


def test_for_absent_no_ci_env_unchanged(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`CI` is unset by the autouse `_no_ci_env` fixture (conftest.py)."""
    out = tmp_path / "o"
    code = cli.main(_assess_argv(out, "--json"))
    envelope = json.loads(capsys.readouterr().out)
    assert code == 0
    assert set(envelope["emit"]) == set(ASSESS_DEFAULT_EMIT)
    assert envelope["preset"] is None
    assert envelope["preset_source"] is None


@pytest.mark.parametrize("value", ["false", "0", "", "FALSE"])
def test_for_ci_falsy_values_treated_as_unset(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    value: str,
) -> None:
    monkeypatch.setenv("CI", value)
    out = tmp_path / "o"
    code = cli.main(_assess_argv(out, "--json"))
    envelope = json.loads(capsys.readouterr().out)
    assert code == 0
    assert set(envelope["emit"]) == set(ASSESS_DEFAULT_EMIT)
    assert envelope["preset_source"] is None


def test_ci_detected_extends_default_and_keeps_existing_consumers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`CI=true` (GitHub Actions' and GitLab CI's real default) must ADD `report.junit.xml` to the
    legacy default, never replace it -- every existing no-`--emit` caller (`.github/actions/assess`,
    `.gitlab/components/agentce-assess`, `cmd_quickstart`, the quick-tier gates) keeps every file it
    gets today (contracts/P18-18.7.md D1)."""
    monkeypatch.setenv("CI", "true")
    out = tmp_path / "o"
    assert cli.main(_assess_argv(out)) == 0
    for name in ("report.md", "results.sarif", "report.junit.xml"):
        assert (out / name).is_file(), name
    assert list((out / "skill").glob("*/SKILL.md")), "skill/ must still be written"


def test_for_explicit_overrides_ci_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """An explicit `--for` is authoritative: `CI=true` never adds `junit` on top of it."""
    monkeypatch.setenv("CI", "true")
    out = tmp_path / "o"
    code = cli.main(_assess_argv(out, "--for", "engineering", "--json"))
    envelope = json.loads(capsys.readouterr().out)
    assert code == 0
    assert set(envelope["emit"]) == _PRESET_PAIRS["engineering"]
    assert envelope["preset"] == "engineering"
    assert envelope["preset_source"] == "flag"


@pytest.mark.parametrize("preset", sorted(_PRESET_PAIRS))
def test_for_each_preset_writes_exactly_its_set(tmp_path: Path, preset: str) -> None:
    """Every preset writes exactly the always-present files plus its own per-token files -- never
    extra, never missing (contracts/P18-18.7.md C1). `CI` is unset by the autouse `_no_ci_env`
    fixture (conftest.py)."""
    out = tmp_path / "o"
    assert cli.main(_assess_argv(out, "--for", preset)) == 0
    expected: set[str] = set(_ALWAYS_FILES)
    for token in _PRESET_PAIRS[preset]:
        for pattern in _TOKEN_FILES[token]:
            matches = sorted(out.glob(pattern))
            assert matches, f"{preset}: no file matches {pattern!r}"
            for match in matches:
                expected.add(str(match.relative_to(out)))
    for pattern in _PRESET_VIEW_FILES.get(preset, ()):
        matches = sorted(out.glob(pattern))
        assert matches, f"{preset}: no file matches {pattern!r}"
        for match in matches:
            expected.add(str(match.relative_to(out)))
    actual = {str(p.relative_to(out)) for p in out.rglob("*") if p.is_file()}
    assert actual == expected, (
        preset,
        sorted(actual - expected),
        sorted(expected - actual),
    )


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


@pytest.mark.parametrize("value", [1, True, None, {}, []])
def test_diff_assertion_sets_rejects_non_string_field(value: object) -> None:
    """A `control`/`subject`/`outcome` present but not a JSON string raises the keyed
    `input.diff_field_not_string`, never Python's own former silent `str()` coercion -- all three
    engines refuse this the same way (TRADEOFFS.md, 2026-09-30)."""
    from agentce.commands import _diff_assertion_sets
    from agentce.errors import InputError

    with pytest.raises(InputError) as exc_info:
        _diff_assertion_sets(
            [{"control": value, "subject": "s1", "outcome": "conformant"}], []
        )
    assert exc_info.value.key == "input.diff_field_not_string"


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


# --- 18.8 C1: `--package-for-sharing` (contracts/P18-18.8.md). ---

_AUD_FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "verification"
    / "gates"
    / "fixtures"
    / "audience_presets"
)


def test_package_for_sharing_refuses_records_folder(tmp_path: Path) -> None:
    records = tmp_path / "records"
    records.mkdir()
    (records / "trace.json").write_text("{}", encoding="utf-8")
    out = tmp_path / "o"
    code = cli.main(
        ["assess", str(records), "--out", str(out), "--package-for-sharing"]
    )
    assert code == 3
    assert not out.exists() or not any(out.iterdir())


def _copied_quickstart_inputs(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    """A private, tmp_path-local copy of the quickstart fixture: overlap tests write ``--out``
    inside/around these paths, and must never touch the real repo fixture."""
    import shutil as _shutil

    root = tmp_path / "inputs"
    _shutil.copytree(_QUICKSTART / "evidence", root / "evidence")
    _shutil.copy2(_QUICKSTART / "applicability.yaml", root / "applicability.yaml")
    _shutil.copy2(_QUICKSTART / "domain.linkml.yaml", root / "domain.linkml.yaml")
    _shutil.copytree(_AUD_FIXTURE / "catalog", root / "catalog")
    return (
        root / "evidence",
        root / "applicability.yaml",
        root / "domain.linkml.yaml",
        root / "catalog",
    )


@pytest.mark.parametrize("which", ["bundle", "profile", "domain", "catalog_dir"])
@pytest.mark.parametrize("direction", ["out_inside_input", "input_inside_out"])
def test_package_for_sharing_refuses_path_overlap(
    tmp_path: Path, which: str, direction: str
) -> None:
    bundle, profile, domain, catalog_dir = _copied_quickstart_inputs(tmp_path)
    inputs = {
        "bundle": bundle,
        "profile": profile,
        "domain": domain,
        "catalog_dir": catalog_dir,
    }
    if direction == "out_inside_input":
        out = inputs[which] / "nested-out"
    else:
        out = tmp_path / "o"
        out.mkdir()
        inputs[which] = out / (
            "target-dir" if which in ("bundle", "catalog_dir") else "target-file"
        )
        if which in ("bundle", "catalog_dir"):
            import shutil as _shutil

            _shutil.copytree(
                bundle if which == "bundle" else catalog_dir, inputs[which]
            )
        else:
            inputs[which].write_bytes(
                (profile if which == "profile" else domain).read_bytes()
            )
    argv = [
        "assess",
        "--bundle",
        str(inputs["bundle"]),
        "--profile",
        str(inputs["profile"]),
        "--domain",
        str(inputs["domain"]),
        "--catalog-dir",
        str(inputs["catalog_dir"]),
        "--allow-unverified-catalog",
        "--out",
        str(out),
        "--package-for-sharing",
    ]
    code = cli.main(argv)
    assert code == 3
    if direction == "out_inside_input":
        assert not out.exists()


def test_package_for_sharing_writes_self_contained_bundle(tmp_path: Path) -> None:
    out = tmp_path / "o"
    assert cli.main(_assess_argv(out, "--package-for-sharing")) == 0
    src_files = sorted(
        p.relative_to(_QUICKSTART / "evidence")
        for p in (_QUICKSTART / "evidence").rglob("*")
        if p.is_file()
    )
    dst_files = sorted(
        p.relative_to(out / "bundle" / "evidence")
        for p in (out / "bundle" / "evidence").rglob("*")
        if p.is_file()
    )
    assert src_files == dst_files
    for rel in src_files:
        assert (out / "bundle" / "evidence" / rel).read_bytes() == (
            _QUICKSTART / "evidence" / rel
        ).read_bytes()
    assert (out / "bundle" / "applicability.yaml").read_bytes() == (
        _QUICKSTART / "applicability.yaml"
    ).read_bytes()
    assert (out / "bundle" / "domain.linkml.yaml").read_bytes() == (
        _QUICKSTART / "domain.linkml.yaml"
    ).read_bytes()
    packaging = json.loads((out / "packaging.json").read_text())
    assert packaging["packaged"] is True
    assert packaging["catalog_dir_order"] == []
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["inputs"]["applicability_profile_digest"].startswith("sha256:")
    assert manifest["inputs"]["domain_binding_digest"].startswith("sha256:")
    for name in (
        "integrity.jsonl",
        "applicability.jsonl",
        "quarantine.jsonl",
        "coverage.json",
        "packaging.json",
    ):
        digest = manifest["outputs"][name]
        actual = (
            "sha256:"
            + __import__("hashlib").sha256((out / name).read_bytes()).hexdigest()
        )
        assert digest == actual, name
    assert validate_report(out) == []

    # A second run without --domain overwrites cleanly (no stale domain.linkml.yaml left behind).
    argv_no_domain = [
        "assess",
        "--bundle",
        str(_QUICKSTART / "evidence"),
        "--profile",
        str(_QUICKSTART / "applicability.yaml"),
        "--out",
        str(out),
        "--package-for-sharing",
    ]
    assert cli.main(argv_no_domain) == 0
    assert not (out / "bundle" / "domain.linkml.yaml").exists()


def test_package_for_sharing_off_by_default_writes_no_bundle(tmp_path: Path) -> None:
    out = tmp_path / "o"
    assert cli.main(_assess_argv(out)) == 0
    assert not (out / "bundle").exists()
    packaging = json.loads((out / "packaging.json").read_text())
    assert packaging["packaged"] is False
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["inputs"]["applicability_profile_digest"].startswith("sha256:")
    assert validate_report(out) == []


def test_package_for_sharing_catalog_dir_is_digested_and_copied(tmp_path: Path) -> None:
    out = tmp_path / "o"
    argv = [
        "assess",
        "--bundle",
        str(_AUD_FIXTURE / "evidence"),
        "--profile",
        str(_AUD_FIXTURE / "applicability.yaml"),
        "--domain",
        str(_AUD_FIXTURE / "domain.linkml.yaml"),
        "--catalog-dir",
        str(_AUD_FIXTURE / "catalog"),
        "--allow-unverified-catalog",
        "--out",
        str(out),
        "--package-for-sharing",
    ]
    # AUD-01 is severity: high and insufficient_evidence by fixture design, so this exits 2
    # (SPEC.md:1076, item 18.30), not 0.
    assert cli.main(argv) == 2
    packaging = json.loads((out / "packaging.json").read_text())
    assert len(packaging["catalog_dir_order"]) == 1
    label = packaging["catalog_dir_order"][0]
    assert label in packaging["catalog_dir_digests"]
    from agentce import signing

    assert packaging["catalog_dir_digests"][label] == signing.digest_tree(
        _AUD_FIXTURE / "catalog"
    )
    assert (out / "bundle" / "catalog" / "0" / "catalog.yaml").is_file()
    assert validate_report(out) == []


# --- 18.30 C2: assess exits 2 for insufficient evidence on a severity: high control
# (contracts/P18-18.30.md, SPEC.md:1076). ---


def _aud_argv(out: Path, *extra: str) -> list[str]:
    return [
        "assess",
        "--bundle",
        str(_AUD_FIXTURE / "evidence"),
        "--profile",
        str(_AUD_FIXTURE / "applicability.yaml"),
        "--domain",
        str(_AUD_FIXTURE / "domain.linkml.yaml"),
        "--catalog-dir",
        str(_AUD_FIXTURE / "catalog"),
        "--allow-unverified-catalog",
        "--out",
        str(out),
        *extra,
    ]


def test_assess_exits_insufficient_evidence_on_a_severity_high_control(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """AUD-01 is severity: high and insufficient_evidence by fixture design: the run exits 2, and
    the JSON envelope's ``exit_status`` names the code (``["insufficient_evidence"]``, no other code
    applying here since this fixture's one control has no other failure mode)."""
    out = tmp_path / "o"
    code = cli.main([*_aud_argv(out), "--json"])
    envelope = json.loads(capsys.readouterr().out)
    assert code == 2
    assert envelope["exit_code"] == 2
    assert envelope["exit_status"] == ["insufficient_evidence"]


def test_assess_exit_code_2_is_not_tripped_by_a_medium_severity_gap(
    tmp_path: Path,
) -> None:
    """``corpus/quickstart`` has 19 ``insufficient_evidence`` assertions, all severity: medium, so it
    must stay exit 0 -- the check is scoped to severity: high, not to the outcome alone."""
    out = tmp_path / "o"
    assert cli.main(_assess_argv(out)) == 0


def test_assess_a_deviation_on_a_high_severity_insufficient_evidence_control_is_refused_before_exit_code_2_applies(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A deviation can never apply to an ``insufficient_evidence`` outcome in the first place
    (SPEC §13.3.4, ``readiness.deviation_lint``: "insufficient_evidence is an evidence gap, not a
    risk acceptance"), so there is no way to use ``--deviations`` to suppress exit code 2 -- the
    register naming AUD-01 is refused up front (exit 3, ``input.deviation_invalid``), before a
    report is even written."""
    import yaml

    dev = tmp_path / "deviations.yaml"
    dev.write_text(
        yaml.safe_dump(
            {
                "deviations": [
                    {
                        "control": "AUD-01",
                        "rationale": "test rationale",
                        "compensating_control": "manual review",
                        "owner": "user:owner@example.com",
                        "approver": "user:approver@example.com",
                        "granted": "2026-01-01T00:00:00.000Z",
                        "expiry": "2026-06-01T00:00:00.000Z",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    out = tmp_path / "o"
    code = cli.main([*_aud_argv(out, "--deviations", str(dev)), "--json"])
    envelope = json.loads(capsys.readouterr().out)
    assert code == 3
    assert envelope["error"]["key"] == "input.deviation_invalid"


def test_assess_fail_on_does_not_suppress_exit_code_2(tmp_path: Path) -> None:
    """``--fail-on`` narrows which assertions count toward exit code 1, but SPEC's exit-2 clause has
    no such scoping: a ``--fail-on`` expression matching nothing still leaves exit 2 in force."""
    out = tmp_path / "o"
    code = cli.main(_aud_argv(out, "--fail-on", 'control=="DOES-NOT-MATCH-ANYTHING"'))
    assert code == 2


def test_assess_records_folder_scan_never_trips_exit_code_2(tmp_path: Path) -> None:
    """A records-folder ``assess <folder>`` scan (``scanned is not None``) is excluded from the
    exit-2 check even though its derived, incomplete profile routinely has severity-high
    ``insufficient_evidence`` gaps by design (contracts/P18-18.30.md Dispositions)."""
    import shutil

    fixtures = _REPO_ROOT / "adapters" / "otel-genai" / "fixtures"
    folder = tmp_path / "records"
    folder.mkdir(parents=True, exist_ok=True)
    shutil.copy(
        fixtures / "otel-genai-agent-session" / "input.json", folder / "session.json"
    )
    shutil.copy(fixtures / "openinference-rag" / "input.json", folder / "rag.json")
    document = json.loads(
        (fixtures / "otel-genai-chat" / "input.json").read_text(encoding="utf-8")
    )
    (folder / "chat.jsonl").write_text(
        json.dumps(document) + "\n\n" + json.dumps(document) + "\n", encoding="utf-8"
    )
    out = tmp_path / "o"
    code = cli.main(["assess", str(folder), "--out", str(out), "--json"])
    assertions = json.loads((out / "assertions.json").read_text())
    high_insufficient = [
        a
        for a in assertions
        if a["outcome"] == "insufficient_evidence" and a["severity"] == "high"
    ]
    assert (
        high_insufficient
    )  # the fixture does carry a severity-high gap (baseline@2026.09)
    assert code != 2


# --- 18.8 C2: `sign --write-trust-root` (contracts/P18-18.8.md). ---


def _write_kms_key(path: Path) -> Ed25519PrivateKey:
    from cryptography.hazmat.primitives.serialization import (
        Encoding,
        NoEncryption,
        PrivateFormat,
    )

    key = Ed25519PrivateKey.generate()
    path.write_bytes(
        key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
    )
    return key


def test_write_trust_root_key_verifies_the_signed_claim(tmp_path: Path) -> None:
    from agentce import signing

    out = tmp_path / "o"
    assert cli.main(_assess_argv(out)) == 0
    key_path = tmp_path / "claimant.pem"
    key = _write_kms_key(key_path)
    code = cli.main(
        [
            "sign",
            str(out),
            "--as",
            "claimant",
            "--profile",
            "kms",
            "--key",
            str(key_path),
            "--write-trust-root",
        ]
    )
    assert code == 0
    trust_root_path = out / "trust-root.json"
    assert trust_root_path.is_file()
    trust = signing.load_trust_root(trust_root_path)
    keyid = signing.keyid_for(key.public_key())
    assert keyid in trust.keys
    assert signing.public_ed25519_b64(trust.keys[keyid]) == signing.public_ed25519_b64(
        key.public_key()
    )
    claim = json.loads((out / "claim.json").read_text())
    record = next(r for r in claim["signatures"] if r["role"] == "claimant")
    verified = signing.verify_envelope(record, trust)
    assert verified.identity is not None


def test_write_trust_root_refuses_non_kms_profile(tmp_path: Path) -> None:
    out = tmp_path / "o"
    assert cli.main(_assess_argv(out)) == 0
    claim_before = (out / "claim.json").read_bytes()
    code = cli.main(
        [
            "sign",
            str(out),
            "--as",
            "claimant",
            "--profile",
            "sigstore-public",
            "--write-trust-root",
        ]
    )
    assert code == 3
    assert (out / "claim.json").read_bytes() == claim_before
    assert not (out / "trust-root.json").exists()


def test_write_trust_root_dry_run_writes_nothing(tmp_path: Path) -> None:
    out = tmp_path / "o"
    assert cli.main(_assess_argv(out)) == 0
    key_path = tmp_path / "claimant.pem"
    _write_kms_key(key_path)
    before = sorted(out.rglob("*"))
    code = cli.main(
        [
            "sign",
            str(out),
            "--as",
            "claimant",
            "--profile",
            "kms",
            "--key",
            str(key_path),
            "--dry-run",
            "--write-trust-root",
        ]
    )
    assert code == 0
    assert sorted(out.rglob("*")) == before
    assert not (out / "trust-root.json").exists()


# --- 18.8 C3: `agentce verify --report` (contracts/P18-18.8.md). ---


def _packaged_and_signed(
    tmp_path: Path, *assess_extra: str, domain: bool = True
) -> tuple[Path, Ed25519PrivateKey]:
    """A `--package-for-sharing` quickstart report, signed as claimant with `--write-trust-root`."""
    out = tmp_path / "o"
    assert (
        cli.main(
            _assess_argv(out, "--package-for-sharing", *assess_extra, domain=domain)
        )
        == 0
    )
    key_path = tmp_path / "claimant.pem"
    key = _write_kms_key(key_path)
    assert (
        cli.main(
            [
                "sign",
                str(out),
                "--as",
                "claimant",
                "--profile",
                "kms",
                "--key",
                str(key_path),
                "--write-trust-root",
            ]
        )
        == 0
    )
    return out, key


def _verify_report_json(
    capsys: pytest.CaptureFixture[str], report_dir: str, *extra: str
) -> tuple[int, dict[str, Any]]:
    """Run `agentce verify --report <dir> --json`, discarding stdout any earlier setup call
    (assess, sign) emitted first, and parse the resulting JSON envelope."""
    capsys.readouterr()  # discard setup output
    code = cli.main(["verify", "--report", report_dir, *extra, "--json"])
    return code, json.loads(capsys.readouterr().out)


def test_verify_report_happy_path_reproduces(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    out, _key = _packaged_and_signed(tmp_path)
    summary_path = tmp_path / "step-summary.md"
    summary_path.write_text("before\n", encoding="utf-8")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary_path))
    code, envelope = _verify_report_json(capsys, str(out))
    assert code == 0
    assert envelope["reproduced"] is True
    # The re-run must not append a second, fake step summary (N2).
    assert summary_path.read_text(encoding="utf-8") == "before\n"


def _corrupt_claim_no_claim(out: Path) -> None:
    (out / "claim.json").unlink()


def _corrupt_claim_malformed(out: Path) -> None:
    (out / "claim.json").write_text("not json", encoding="utf-8")


def _corrupt_claim_not_object(out: Path) -> None:
    """Well-formed JSON that is not an object -- distinct from `_corrupt_claim_malformed`'s
    syntax error, this exercises the `isinstance(parsed_claim, dict)` guard a non-object claim.json
    needs to avoid crashing on its first `.get()` call (18.65 F1-class fix)."""
    (out / "claim.json").write_text("[]", encoding="utf-8")


def _corrupt_claim_unsigned(out: Path) -> None:
    claim = json.loads((out / "claim.json").read_text(encoding="utf-8"))
    claim["signatures"] = []
    (out / "claim.json").write_text(
        json.dumps(claim, indent=2, sort_keys=True), encoding="utf-8"
    )


@pytest.mark.parametrize(
    ("corrupt", "expected_key"),
    [
        (_corrupt_claim_no_claim, "verify.report_no_claim"),
        (_corrupt_claim_malformed, "verify.report_claim_malformed"),
        (_corrupt_claim_not_object, "verify.report_claim_malformed"),
        (_corrupt_claim_unsigned, "verify.report_unsigned"),
    ],
)
def test_verify_report_claim_stage_tamper_cases(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    corrupt: Callable[[Path], None],
    expected_key: str,
) -> None:
    out = tmp_path / "o"
    assert cli.main(_assess_argv(out, "--package-for-sharing")) == 0
    corrupt(out)
    code, envelope = _verify_report_json(capsys, str(out))
    assert code == 3
    assert envelope["error"]["key"] == expected_key


def _corrupt_signed_signatures_not_list(out: Path) -> None:
    """`claim.json`'s `signatures` field set to a non-list -- a plain edit to an already-signed
    report, no re-signing needed since it is read before any signature check (verifier round 1,
    18.65: `candidates = [s for s in signatures ...]` crashed `AttributeError`/`TypeError` iterating
    a string or an int)."""
    claim = json.loads((out / "claim.json").read_text(encoding="utf-8"))
    claim["signatures"] = "x"
    (out / "claim.json").write_text(
        json.dumps(claim, indent=2, sort_keys=True), encoding="utf-8"
    )


def _corrupt_signed_signature_entry_not_dict(out: Path) -> None:
    """One `signatures[]` entry replaced with a non-object; the list comprehension's `s.get(...)`
    crashed `AttributeError` before the `isinstance(s, dict)` guard (verifier round 1, 18.65)."""
    claim = json.loads((out / "claim.json").read_text(encoding="utf-8"))
    claim["signatures"][0] = 1
    (out / "claim.json").write_text(
        json.dumps(claim, indent=2, sort_keys=True), encoding="utf-8"
    )


@pytest.mark.parametrize(
    ("corrupt", "expected_key"),
    [
        (_corrupt_signed_signatures_not_list, "verify.report_claim_malformed"),
        (_corrupt_signed_signature_entry_not_dict, "verify.report_signature_invalid"),
    ],
)
def test_verify_report_signed_signatures_shape_tamper(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    corrupt: Callable[[Path], None],
    expected_key: str,
) -> None:
    """A plain tamper of an already-*signed* report's `signatures` shape refuses cleanly with a
    stable key instead of crashing `internal.unexpected` (verifier round 1, 18.65): a non-list
    `signatures` is the same claim-shape refusal as claim.json's own top level; one malformed entry
    in an otherwise-valid list is dropped, leaving no claimant candidate -- the same clean outcome an
    unsigned report gets."""
    out, _key = _packaged_and_signed(tmp_path)
    corrupt(out)
    code, envelope = _verify_report_json(capsys, str(out))
    assert code == 3
    assert envelope["error"]["key"] == expected_key


def test_verify_report_claim_body_noncanonical_number_is_tampered_not_a_crash(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A float spliced into an already-*signed* claim.json's body (no re-signing): `canonicalize`
    refuses any non-integer number, and `_verify_report` called it unguarded while recomputing the
    claim digest, crashing `internal.unexpected: CanonicalizationError` instead of refusing with the
    same `verify.report_claim_tampered` key a digest mismatch already uses (verifier round 2,
    18.65) -- a tampered body that cannot even be canonicalized can never match the recorded digest
    either way."""
    out, _key = _packaged_and_signed(tmp_path)
    claim = json.loads((out / "claim.json").read_text(encoding="utf-8"))
    claim["not_canonical"] = 1.5
    (out / "claim.json").write_text(json.dumps(claim), encoding="utf-8")
    code, envelope = _verify_report_json(capsys, str(out))
    assert code == 3
    assert envelope["error"]["key"] == "verify.report_claim_tampered"


@pytest.mark.parametrize("tampered_file", ["report.md", "packaging.json"])
def test_verify_report_output_tampered(
    tmp_path: Path, tampered_file: str, capsys: pytest.CaptureFixture[str]
) -> None:
    out, _key = _packaged_and_signed(tmp_path)
    target = out / tampered_file
    target.write_bytes(target.read_bytes() + b"TAMPER")
    code, envelope = _verify_report_json(capsys, str(out))
    assert code == 3
    assert envelope["error"]["key"] == "verify.report_output_tampered"


@pytest.mark.parametrize(
    "relative_path",
    [
        "bundle/evidence/events",
        "bundle/applicability.yaml",
        "bundle/domain.linkml.yaml",
    ],
)
def test_verify_report_evidence_tampered(
    tmp_path: Path, relative_path: str, capsys: pytest.CaptureFixture[str]
) -> None:
    out, _key = _packaged_and_signed(tmp_path)
    target = out / relative_path
    tampered_file = next(target.glob("*.jsonl")) if target.is_dir() else target
    tampered_file.write_bytes(tampered_file.read_bytes() + b"TAMPER")
    code, envelope = _verify_report_json(capsys, str(out))
    assert code == 3
    assert envelope["error"]["key"] == "verify.report_evidence_tampered"


def test_verify_report_bundle_digest_self_consistent_edit_is_still_tampered(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A tamperer who edits an event file AND recomputes its own manifest entry's sha256 (so
    `load_bundle`'s per-file check -- the one every other case above trips -- stays green) still
    changes the manifest's own bytes, so the outer `bundle_digest` equality at
    commands/__init__.py:769 (distinct from `load_bundle`'s per-file checks, and otherwise never
    exercised by any test) must catch it on its own (verifier round 3, 18.8, mutation M8)."""
    out, _key = _packaged_and_signed(tmp_path)
    evidence = out / "bundle" / "evidence"
    event_file = next((evidence / "events").glob("*.jsonl"))
    event_file.write_bytes(event_file.read_bytes() + b"\n")
    manifest_path = evidence / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    rel = str(event_file.relative_to(evidence))
    new_sha256 = hashlib.sha256(event_file.read_bytes()).hexdigest()
    entry = next(e for e in manifest["files"] if e["path"] == rel)
    assert entry["sha256"] != new_sha256, (
        "the edit must actually change the file's digest"
    )
    entry["sha256"] = new_sha256
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    code, envelope = _verify_report_json(capsys, str(out))
    assert code == 3
    assert envelope["error"]["key"] == "verify.report_evidence_tampered"


def test_verify_report_bundle_digest_canonicalization_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`canonical.sha256_hex` raises `CanonicalizationError` for any float value anywhere in the
    manifest it canonicalizes; `load_bundle` itself never rejects an unrecognised top-level manifest
    key, so this is reachable only through the lazy `Bundle.digest` property, hitting
    commands/__init__.py:764-768's except branch -- distinct from the ordinary mismatch path at 769
    the self-consistent-edit test above exercises (18.8.R1 branch-coverage gate)."""
    out, _key = _packaged_and_signed(tmp_path)
    manifest_path = out / "bundle" / "evidence" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["agentce_bundle_extra_float"] = 1.5
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    code, envelope = _verify_report_json(capsys, str(out))
    assert code == 3
    assert envelope["error"]["key"] == "verify.report_evidence_tampered"


def test_verify_report_rerun_missing_compare_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """commands/__init__.py:895-901's "a compare file is simply missing" branch is distinct from
    "present but byte-different" (902-903), which the reproduction-mismatch test below exercises
    exclusively. A compare-file name present in neither the shipped report nor the re-run's own
    output hits the missing-file branch directly (18.8.R1 branch-coverage gate)."""
    from agentce import commands

    out, _key = _packaged_and_signed(tmp_path)
    monkeypatch.setattr(
        commands,
        "_RERUN_COMPARE_FILES",
        commands._RERUN_COMPARE_FILES + ("does-not-exist.json",),
    )
    code, envelope = _verify_report_json(capsys, str(out))
    assert code == 3
    assert envelope["error"]["key"] == "verify.report_reproduction_mismatch"


def test_verify_report_catalog_dir_digest_tampered(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A packaged BYO `--catalog-dir` copy tampered after signing -- step 7's
    `catalog_dir_digests` check, distinct from the evidence/profile/domain cases above, must catch
    it on its own (a mutation that drops the catalog-dir loop leaves this case unhandled)."""
    out, _key = _packaged_and_signed(
        tmp_path,
        "--catalog-dir",
        str(_AUD_FIXTURE / "catalog"),
        "--allow-unverified-catalog",
    )
    catalog_copy = out / "bundle" / "catalog" / "0"
    catalog_file = next(catalog_copy.glob("controls/*.yaml"))
    catalog_file.write_bytes(catalog_file.read_bytes() + b"\n# TAMPER\n")
    code, envelope = _verify_report_json(capsys, str(out))
    assert code == 3
    assert envelope["error"]["key"] == "verify.report_evidence_tampered"


def _patch_path_method(
    monkeypatch: pytest.MonkeyPatch, name: str, target: Path, fake: Callable[[], Any]
) -> None:
    """Monkeypatch `Path.<name>` so calls on `target` run `fake()`; every other path keeps the real
    method."""
    real = getattr(Path, name)

    def _wrapped(self: Path, *args: Any, **kwargs: Any) -> Any:
        if self == target:
            return fake()
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, name, _wrapped)


def test_verify_report_claim_unreadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`claim_path.is_file()` can be true while `read_bytes()` still raises `OSError` (a permission
    change, a device error) -- commands/__init__.py:531-536, distinct from the malformed-JSON case,
    which shares the same `verify.report_claim_malformed` key (18.8.R1 branch-coverage gate)."""
    out, _key = _packaged_and_signed(tmp_path)
    claim_path = out / "claim.json"

    def _raise_oserror() -> bytes:
        raise OSError("simulated unreadable claim.json")

    _patch_path_method(monkeypatch, "read_bytes", claim_path, _raise_oserror)
    code, envelope = _verify_report_json(capsys, str(out))
    assert code == 3
    assert envelope["error"]["key"] == "verify.report_claim_malformed"


def test_verify_report_signature_shaped_wrong_falls_through(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A claimant signature that verifies cryptographically but whose signed payload is not shaped
    like the statement `agentce sign` writes (commands/__init__.py:607-611) must be skipped, not
    accepted -- distinct from the no-candidate-verified case at 621-626, which shares the same
    `verify.report_signature_invalid` key (18.8.R1 branch-coverage gate)."""
    from agentce import signing

    out, key = _packaged_and_signed(tmp_path)
    claim_path = out / "claim.json"
    claim = json.loads(claim_path.read_text(encoding="utf-8"))
    bad_statement = {"predicate": {"role": "claimant"}, "subject": "not-a-list"}
    envelope = signing.sign_statement(bad_statement, signing.KmsSigner(private_key=key))
    claim["signatures"] = [{"role": "claimant", "profile": "kms", **envelope}]
    claim_path.write_text(json.dumps(claim), encoding="utf-8")
    code, envelope_out = _verify_report_json(capsys, str(out))
    assert code == 3
    assert envelope_out["error"]["key"] == "verify.report_signature_invalid"


def test_verify_report_manifest_missing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """manifest.json literally absent (not merely tampered content, the pre-existing
    `verify.report_output_tampered` case) -- commands/__init__.py:645-651 (18.8.R1 branch-coverage
    gate)."""
    out, _key = _packaged_and_signed(tmp_path)
    (out / "manifest.json").unlink()
    code, envelope = _verify_report_json(capsys, str(out))
    assert code == 3
    assert envelope["error"]["key"] == "verify.report_output_tampered"


def test_verify_report_output_file_missing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A manifest-tracked output literally missing from disk -- commands/__init__.py:711-713,
    distinct from the digest-mismatch branch at 714-716 that every other output-tampered test here
    exercises by appending bytes rather than deleting (18.8.R1 branch-coverage gate)."""
    out, _key = _packaged_and_signed(tmp_path)
    (out / "report.md").unlink()
    code, envelope = _verify_report_json(capsys, str(out))
    assert code == 3
    assert envelope["error"]["key"] == "verify.report_output_tampered"
    assert "report.md" in envelope["error"]["cause"]


def test_verify_report_packaging_missing_at_direct_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`packaging.json` is itself manifest-tracked, so a plain delete is caught by the outputs loop
    first (commands/__init__.py:708-722); the dedicated `{report_dir} has no packaging.json` check
    at 726-727 -- same `verify.report_output_tampered` key -- is reachable only if packaging.json
    goes missing between that loop and this later, separate check (a TOCTOU race a real filesystem
    could also produce). Simulated by making only the second `is_file()` call on that path return
    False (18.8.R1 branch-coverage gate)."""
    out, _key = _packaged_and_signed(tmp_path)
    packaging_path = out / "packaging.json"
    calls: list[None] = []

    def _true_once_then_false() -> bool:
        calls.append(None)
        return len(calls) == 1

    _patch_path_method(monkeypatch, "is_file", packaging_path, _true_once_then_false)
    code, envelope = _verify_report_json(capsys, str(out))
    assert code == 3
    assert envelope["error"]["key"] == "verify.report_output_tampered"
    assert "no packaging.json" in envelope["error"]["cause"]


def test_verify_report_without_domain_binding_still_reproduces(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A bundle assessed with no `--domain` binding leaves `domain_binding_digest` absent from
    manifest.json's inputs, so the domain-tampered check at commands/__init__.py:786-796 and the
    re-run's own `--domain` arg-building at 855-856 both take their `is not None` condition's False
    branch, never exercised by any other test here (every other case uses the quickstart fixture's
    domain binding) (18.8.R1 branch-coverage gate)."""
    out, _key = _packaged_and_signed(tmp_path, domain=False)
    assert not (out / "bundle" / "domain.linkml.yaml").exists()
    code, envelope = _verify_report_json(capsys, str(out))
    assert code == 0
    assert envelope["reproduced"] is True


def test_verify_report_deviation_register_tampered(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A packaged deviation register tampered after signing -- step 7's dedicated
    `deviation_register_digest` check at commands/__init__.py:797-808, distinct from the
    evidence/profile/domain/catalog cases above, and only reachable when `--deviations` was actually
    passed (18.8.R1 branch-coverage gate)."""
    deviations_path = tmp_path / "deviations.yaml"
    deviations_path.write_text("deviations: []\n", encoding="utf-8")
    out, _key = _packaged_and_signed(tmp_path, "--deviations", str(deviations_path))
    packaged_deviations = out / "bundle" / "deviations.yaml"
    assert packaged_deviations.is_file()
    packaged_deviations.write_text("deviations: []\n# TAMPER\n", encoding="utf-8")
    code, envelope = _verify_report_json(capsys, str(out))
    assert code == 3
    assert envelope["error"]["key"] == "verify.report_evidence_tampered"


def test_verify_report_no_trust_root(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """No embedded `trust-root.json` and no `--signer-trust-root` -- stage 2's own dedicated exit,
    distinct from every stage-1 claim case above and from `input.trust_root_invalid` (an unreadable
    or forged file, tested elsewhere)."""
    out, _key = _packaged_and_signed(tmp_path)
    (out / "trust-root.json").unlink()
    code, envelope = _verify_report_json(capsys, str(out))
    assert code == 3
    assert envelope["error"]["key"] == "verify.report_no_trust_root"


def test_verify_report_unpackaged_reports_null(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "o"
    assert cli.main(_assess_argv(out)) == 0
    key_path = tmp_path / "claimant.pem"
    _write_kms_key(key_path)
    assert (
        cli.main(
            [
                "sign",
                str(out),
                "--as",
                "claimant",
                "--profile",
                "kms",
                "--key",
                str(key_path),
                "--write-trust-root",
            ]
        )
        == 0
    )
    code, envelope = _verify_report_json(capsys, str(out))
    assert code == 0
    # The unpackaged branch (commands/__init__.py:744-752) is distinct from the reproduced-True
    # happy path: nothing asserted `reproduced`/`reason` here before, so a mutation that always set
    # `reproduced: True` would pass this test undetected (verifier round 3, 18.8, mutation M20).
    assert envelope["reproduced"] is None
    assert envelope["reason"] == "this report was not packaged for re-running"


def test_verify_report_byo_catalog_dir_round_trip(tmp_path: Path) -> None:
    # quickstart's own evidence/profile/domain (known to reach READY, D8) plus the audience-presets
    # fixture's unsigned catalog directory (D7/round-2 defect 5) -- only the catalog is reused from
    # that fixture, not its evidence, which has its own known integrity gap unrelated to this test.
    argv = [
        "assess",
        "--bundle",
        str(_QUICKSTART / "evidence"),
        "--profile",
        str(_QUICKSTART / "applicability.yaml"),
        "--domain",
        str(_QUICKSTART / "domain.linkml.yaml"),
        "--catalog-dir",
        str(_AUD_FIXTURE / "catalog"),
        "--allow-unverified-catalog",
        "--out",
        str(tmp_path / "o"),
        "--package-for-sharing",
    ]
    assert cli.main(argv) == 0
    out = tmp_path / "o"
    key_path = tmp_path / "claimant.pem"
    _write_kms_key(key_path)
    assert (
        cli.main(
            [
                "sign",
                str(out),
                "--as",
                "claimant",
                "--profile",
                "kms",
                "--key",
                str(key_path),
                "--write-trust-root",
            ]
        )
        == 0
    )
    # The recipient's own ambient trust config must not matter: unset it, still succeeds because
    # step 8 builds its own scratch trust root from the report's own signer key.
    old_env = os.environ.pop("AGENTCE_TRUST_ROOT", None)
    try:
        code = cli.main(["verify", "--report", str(out), "--json"])
    finally:
        if old_env is not None:
            os.environ["AGENTCE_TRUST_ROOT"] = old_env
    assert code == 0


def test_verify_report_byo_catalog_dir_trusts_only_its_own_scratch_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Step 8's catalog-dir re-run (commands/__init__.py:825-874) must trust only a scratch trust
    root built from THIS report's own verified signer key, never the recipient's ambient
    `AGENTCE_TRUST_ROOT`. `test_verify_report_byo_catalog_dir_round_trip` always packages with
    `--allow-unverified-catalog`, so the catalog-dir's own signature is never actually checked at
    re-run time (verifier round 3, 18.8). Here the catalog IS signed -- with the same key that signs
    the report -- and packaged WITHOUT the override; the recipient's `AGENTCE_TRUST_ROOT` is pointed
    at the vendored dev-root, which does not know this key, to prove the scratch root is what is
    actually consulted, not a coincidentally-permissive ambient one."""
    import shutil as _shutil

    from agentce import signing

    key_path = tmp_path / "claimant.pem"
    key = _write_kms_key(key_path)
    keyid = signing.keyid_for(key.public_key())
    claimant_trust_path = tmp_path / "claimant-trust-root.json"
    claimant_trust_path.write_text(
        json.dumps(
            signing.TrustRoot.document(
                keyid, signing.public_ed25519_b64(key.public_key()), "claimant"
            )
        ),
        encoding="utf-8",
    )
    catalog_dir = tmp_path / "catalog"
    _shutil.copytree(_AUD_FIXTURE / "catalog", catalog_dir)
    statement = signing.intoto_statement(
        subject_name=catalog_dir.name,
        digest=signing.digest_tree(
            catalog_dir, exclude=frozenset({signing.CATALOG_SIGNATURE_NAME})
        ),
        predicate_type="https://agent-conformance.org/attestation/catalog/v1",
        predicate={"kind": "catalog", "id": catalog_dir.name},
    )
    envelope = signing.sign_statement(statement, signing.KmsSigner(private_key=key))
    (catalog_dir / signing.CATALOG_SIGNATURE_NAME).write_text(
        json.dumps(envelope), encoding="utf-8"
    )

    out = tmp_path / "o"
    argv = [
        "assess",
        "--bundle",
        str(_QUICKSTART / "evidence"),
        "--profile",
        str(_QUICKSTART / "applicability.yaml"),
        "--domain",
        str(_QUICKSTART / "domain.linkml.yaml"),
        "--catalog-dir",
        str(catalog_dir),
        "--trust-root",
        str(claimant_trust_path),
        "--out",
        str(out),
        "--package-for-sharing",
    ]
    assert cli.main(argv) == 0
    assert (
        cli.main(
            [
                "sign",
                str(out),
                "--as",
                "claimant",
                "--profile",
                "kms",
                "--key",
                str(key_path),
                "--write-trust-root",
            ]
        )
        == 0
    )

    monkeypatch.setenv("AGENTCE_TRUST_ROOT", str(signing.vendored_trust_path()))
    code, envelope = _verify_report_json(capsys, str(out))
    assert code == 0
    assert envelope["reproduced"] is True


def test_verify_report_expect_keyid_match_and_mismatch(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from agentce import signing

    out, key = _packaged_and_signed(tmp_path)
    real_keyid = signing.keyid_for(key.public_key())
    assert cli.main(["verify", "--report", str(out), "--expect-keyid", real_keyid]) == 0
    code, envelope = _verify_report_json(
        capsys, str(out), "--expect-keyid", "sha256:bogus"
    )
    assert code == 3
    assert envelope["error"]["key"] == "verify.report_keyid_mismatch"


def test_verify_report_forged_keyid_trust_root_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from agentce import signing

    out, key = _packaged_and_signed(tmp_path)
    real_keyid = signing.keyid_for(key.public_key())
    attacker_key = Ed25519PrivateKey.generate()
    forged = {
        "keys": {
            real_keyid: {
                "public_key": signing.public_ed25519_b64(attacker_key.public_key()),
                "identity": "attacker",
            }
        }
    }
    (out / "trust-root.json").write_text(json.dumps(forged), encoding="utf-8")
    code, envelope = _verify_report_json(capsys, str(out), "--expect-keyid", real_keyid)
    assert code == 3
    # `TrustRoot.from_dict`'s own content-addressed invariant must refuse this before
    # verification even starts -- assert THIS exact key, not `verify.report_signature_invalid`
    # (round-2 critic defect 2, the live-forged-signature bypass).
    assert envelope["error"]["key"] == "input.trust_root_invalid"


def test_verify_report_trust_root_malformed_keys_field(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A trust root whose `keys` field is a string, not an object: `TrustRoot.from_dict` calls
    `(data.get("keys") or {}).items()`, so a truthy non-dict reaches `.items()` directly and crashed
    `internal.unexpected: AttributeError` instead of refusing with `input.trust_root_invalid`, the
    key the docstring already promises for every malformed trust root (verifier round 2, 18.65)."""
    out, _key = _packaged_and_signed(tmp_path)
    tampered = json.loads((out / "trust-root.json").read_text(encoding="utf-8"))
    tampered["keys"] = "x"
    (out / "trust-root.json").write_text(json.dumps(tampered), encoding="utf-8")
    code, envelope = _verify_report_json(capsys, str(out))
    assert code == 3
    assert envelope["error"]["key"] == "input.trust_root_invalid"


def test_verify_report_external_vs_embedded_trust_source(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out, _key = _packaged_and_signed(tmp_path)
    trust_root_copy = tmp_path / "external-trust-root.json"
    trust_root_copy.write_bytes((out / "trust-root.json").read_bytes())
    code, embedded = _verify_report_json(capsys, str(out))
    assert code == 0
    assert embedded["trust_source"] == "embedded"
    code, external = _verify_report_json(
        capsys, str(out), "--signer-trust-root", str(trust_root_copy)
    )
    assert code == 0
    assert external["trust_source"] == "external"


def test_verify_report_re_sign_append_is_manifest_tampered(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`agentce sign` APPENDS a new signature; re-signing after a tamper leaves the original,
    still-verifying signature in place, so manifest.json's own digest mismatch (step 4) is what
    catches the tamper, never `signature_invalid` (round-2 defect 8)."""
    out, _key = _packaged_and_signed(tmp_path)
    trust_root_copy = tmp_path / "external-trust-root.json"
    trust_root_copy.write_bytes((out / "trust-root.json").read_bytes())
    (out / "report.md").write_bytes((out / "report.md").read_bytes() + b"TAMPER")
    manifest = json.loads((out / "manifest.json").read_text())
    manifest["outputs"]["report.md"] = (
        "sha256:" + hashlib.sha256((out / "report.md").read_bytes()).hexdigest()
    )
    (out / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
    )
    key2_path = tmp_path / "attacker.pem"
    _write_kms_key(key2_path)
    assert (
        cli.main(
            [
                "sign",
                str(out),
                "--as",
                "claimant",
                "--profile",
                "kms",
                "--key",
                str(key2_path),
            ]
        )
        == 0
    )
    code, envelope = _verify_report_json(
        capsys, str(out), "--signer-trust-root", str(trust_root_copy)
    )
    assert code == 3
    assert envelope["error"]["key"] == "verify.report_manifest_tampered"


def test_verify_report_claim_tampered(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out, _key = _packaged_and_signed(tmp_path)
    claim = json.loads((out / "claim.json").read_text(encoding="utf-8"))
    claim["claimant"] = {"org": "tampered-after-signing"}
    (out / "claim.json").write_text(
        json.dumps(claim, indent=2, sort_keys=True), encoding="utf-8"
    )
    code, envelope = _verify_report_json(capsys, str(out))
    assert code == 3
    assert envelope["error"]["key"] == "verify.report_claim_tampered"


def test_verify_report_subject_missing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A signature whose subject list omits `manifest.json` entirely -- built by signing over a
    hand-trimmed subject list, not by editing a file post-signature (round-2 defect 7)."""
    from agentce import signing
    from agentce.canonical import canonicalize

    out = tmp_path / "o"
    assert cli.main(_assess_argv(out, "--package-for-sharing")) == 0
    key_path = tmp_path / "claimant.pem"
    key = _write_kms_key(key_path)
    signer = signing.KmsSigner(private_key=key)

    claim = json.loads((out / "claim.json").read_text(encoding="utf-8"))
    body = {k: v for k, v in claim.items() if k != "signatures"}
    subjects = [
        {
            "name": "claim.json",
            "digest": {"sha256": hashlib.sha256(canonicalize(body)).hexdigest()},
        }
        # manifest.json deliberately omitted.
    ]
    statement = {
        "_type": signing.INTOTO_STATEMENT_TYPE,
        "subject": subjects,
        "predicateType": "https://agent-conformance.org/attestation/claim/v1",
        "predicate": {
            "role": "claimant",
            "profile": "kms",
            "statement": "hand-trimmed subject list for the subject_missing test.",
        },
    }
    envelope = signing.sign_statement(statement, signer)
    record = {"role": "claimant", "profile": "kms", **envelope}
    claim.setdefault("signatures", []).append(record)
    (out / "claim.json").write_text(
        json.dumps(claim, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (out / "trust-root.json").write_text(
        json.dumps(
            signing.TrustRoot.document(signer.keyid, signer.public_key_b64, "unset"),
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    code, envelope = _verify_report_json(capsys, str(out))
    assert code == 3
    assert envelope["error"]["key"] == "verify.report_subject_missing"


def test_verify_report_claimant_predicate_role_required(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A signature that cryptographically verifies and whose outer, UNSIGNED `role` field says
    "claimant", but whose verified predicate carries a different role -- step 3 must re-check the
    role inside the verified predicate itself, not trust the outer field alone (N5). Built by
    signing a hand-crafted statement, the same technique as the subject_missing test above."""
    from agentce import signing
    from agentce.canonical import canonicalize

    out = tmp_path / "o"
    assert cli.main(_assess_argv(out, "--package-for-sharing")) == 0
    key_path = tmp_path / "claimant.pem"
    key = _write_kms_key(key_path)
    signer = signing.KmsSigner(private_key=key)

    claim = json.loads((out / "claim.json").read_text(encoding="utf-8"))
    body = {k: v for k, v in claim.items() if k != "signatures"}
    manifest_bytes = (out / "manifest.json").read_bytes()
    subjects = [
        {
            "name": "manifest.json",
            "digest": {"sha256": hashlib.sha256(manifest_bytes).hexdigest()},
        },
        {
            "name": "claim.json",
            "digest": {"sha256": hashlib.sha256(canonicalize(body)).hexdigest()},
        },
    ]
    statement = {
        "_type": signing.INTOTO_STATEMENT_TYPE,
        "subject": subjects,
        "predicateType": "https://agent-conformance.org/attestation/claim/v1",
        "predicate": {
            "role": "reviewer",
            "profile": "kms",
            "statement": "predicate role mismatch for the claimant-role test.",
        },
    }
    envelope = signing.sign_statement(statement, signer)
    record = {"role": "claimant", "profile": "kms", **envelope}
    claim.setdefault("signatures", []).append(record)
    (out / "claim.json").write_text(
        json.dumps(claim, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (out / "trust-root.json").write_text(
        json.dumps(
            signing.TrustRoot.document(signer.keyid, signer.public_key_b64, "unset"),
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    code, envelope = _verify_report_json(capsys, str(out))
    assert code == 3
    assert envelope["error"]["key"] == "verify.report_signature_invalid"


def test_verify_report_engine_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Simulates the RECIPIENT running a different engine build than the one that produced the
    manifest -- patched on the recipient side right before the re-run, never by editing
    manifest.json directly (that would trip the manifest-digest check first, N11)."""
    from agentce import commands

    out, _key = _packaged_and_signed(tmp_path)
    monkeypatch.setattr(commands, "__version__", "9.9.9-recipient")
    code, envelope = _verify_report_json(capsys, str(out))
    assert code == 3
    assert envelope["error"]["key"] == "verify.report_engine_mismatch"


def test_verify_report_full_re_sign_strips_original_is_signature_invalid(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A genuine full re-sign (the original signature entry is REMOVED, not appended alongside a
    new one) leaves nothing in `signatures[]` that verifies against the original external trust
    root, so this is `verify.report_signature_invalid`, distinct from the append-only re-sign
    case above, which manifest.json's own digest check catches instead."""
    out, _key = _packaged_and_signed(tmp_path)
    trust_root_copy = tmp_path / "external-trust-root.json"
    trust_root_copy.write_bytes((out / "trust-root.json").read_bytes())

    (out / "report.md").write_bytes((out / "report.md").read_bytes() + b"TAMPER")
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    manifest["outputs"]["report.md"] = (
        "sha256:" + hashlib.sha256((out / "report.md").read_bytes()).hexdigest()
    )
    (out / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
    )

    claim = json.loads((out / "claim.json").read_text(encoding="utf-8"))
    claim["signatures"] = []
    (out / "claim.json").write_text(
        json.dumps(claim, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    key2_path = tmp_path / "attacker.pem"
    _write_kms_key(key2_path)
    assert (
        cli.main(
            [
                "sign",
                str(out),
                "--as",
                "claimant",
                "--profile",
                "kms",
                "--key",
                str(key2_path),
            ]
        )
        == 0
    )
    code, envelope = _verify_report_json(
        capsys, str(out), "--signer-trust-root", str(trust_root_copy)
    )
    assert code == 3
    assert envelope["error"]["key"] == "verify.report_signature_invalid"


def test_verify_report_reproduction_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Step 8's byte-compare must catch a genuine reproduction failure, not just tampering
    already caught by steps 6-7 -- built by mutating a control's severity on the RECIPIENT side
    only, after packaging and signing, so every prior stage (subjects, manifest, evidence, all
    digest-verified unchanged) still passes and only the re-run's own output differs."""
    from agentce import commands

    out, _key = _packaged_and_signed(tmp_path)
    real_load_catalog = commands.load_catalog

    def mutated_load_catalog(directory: Path) -> Any:
        catalog = real_load_catalog(directory)
        for control in catalog.controls:
            control.severity = "critical" if control.severity != "critical" else "low"
        return catalog

    monkeypatch.setattr(commands, "load_catalog", mutated_load_catalog)
    code, envelope = _verify_report_json(capsys, str(out))
    assert code == 3
    assert envelope["error"]["key"] == "verify.report_reproduction_mismatch"


# --- 18.9 C2: `agentce catalog sign`, and the shared `_load_ed25519_private_key` helper it and
# `agentce sign --profile kms` both now call (contracts/P18-18.9.md). ---


def _ready_report(tmp_path: Path) -> Path:
    out = tmp_path / "o"
    assert cli.main(_assess_argv(out)) == 0
    return out


def _garbage_key(path: Path) -> Path:
    path.write_text("not a key\n", encoding="utf-8")
    return path


def _ec_key(path: Path) -> Path:
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.serialization import (
        Encoding,
        NoEncryption,
        PrivateFormat,
    )

    key = ec.generate_private_key(ec.SECP256R1())
    path.write_bytes(
        key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
    )
    return path


def _password_protected_key(path: Path) -> Path:
    from cryptography.hazmat.primitives.serialization import (
        BestAvailableEncryption,
        Encoding,
        PrivateFormat,
    )

    key = Ed25519PrivateKey.generate()
    path.write_bytes(
        key.private_bytes(
            Encoding.PEM, PrivateFormat.PKCS8, BestAvailableEncryption(b"secret")
        )
    )
    return path


def _openssh_key(path: Path) -> Path:
    from cryptography.hazmat.primitives.serialization import (
        Encoding,
        NoEncryption,
        PrivateFormat,
    )

    key = Ed25519PrivateKey.generate()
    path.write_bytes(
        key.private_bytes(Encoding.PEM, PrivateFormat.OpenSSH, NoEncryption())
    )
    return path


def _catalog_init(
    directory: Path, capsys: pytest.CaptureFixture[str], *extra: str
) -> str:
    capsys.readouterr()
    code = cli.main(["catalog", "init", str(directory), "--json", *extra])
    env = json.loads(capsys.readouterr().out)
    assert code == 0, env
    return str(env["family"])


def _catalog_sign_run(
    argv: list[str], capsys: pytest.CaptureFixture[str]
) -> tuple[int, dict[str, Any]]:
    capsys.readouterr()
    code = cli.main([*argv, "--json"])
    return code, json.loads(capsys.readouterr().out)


# Characterisation: pinned GREEN both before and after the `_load_ed25519_private_key` extraction
# (blast-radius proof, commits cited in `evidence/P18-18.9/simplify.md`). Round 2 (F1) found no
# existing test pinned any of these three -- they are the first real guard on the refactor.


def test_sign_kms_without_key_exits_kms_key_missing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = _ready_report(tmp_path)
    capsys.readouterr()
    code = cli.main(
        ["sign", str(out), "--as", "claimant", "--profile", "kms", "--json"]
    )
    envelope = json.loads(capsys.readouterr().out)
    assert code == 3
    assert envelope["error"]["key"] == "sign.kms_key_missing"


def test_sign_kms_missing_key_path_exits_input_key_not_a_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = _ready_report(tmp_path)
    capsys.readouterr()
    code = cli.main(
        [
            "sign",
            str(out),
            "--as",
            "claimant",
            "--profile",
            "kms",
            "--key",
            str(tmp_path / "missing.pem"),
            "--json",
        ]
    )
    envelope = json.loads(capsys.readouterr().out)
    assert code == 3
    assert envelope["error"]["key"] == "input.key_not_a_file"


def test_sign_kms_non_ed25519_key_exits_sign_key_algorithm(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = _ready_report(tmp_path)
    key_path = _ec_key(tmp_path / "ec.pem")
    capsys.readouterr()
    code = cli.main(
        [
            "sign",
            str(out),
            "--as",
            "claimant",
            "--profile",
            "kms",
            "--key",
            str(key_path),
            "--json",
        ]
    )
    envelope = json.loads(capsys.readouterr().out)
    assert code == 3
    assert envelope["error"]["key"] == "sign.key_algorithm"


# New behaviour (18.9 C2): before the extraction, each of these crashed to `internal.unexpected`.


def test_sign_kms_password_protected_key_exits_sign_key_unreadable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = _ready_report(tmp_path)
    key_path = _password_protected_key(tmp_path / "protected.pem")
    capsys.readouterr()
    code = cli.main(
        [
            "sign",
            str(out),
            "--as",
            "claimant",
            "--profile",
            "kms",
            "--key",
            str(key_path),
            "--json",
        ]
    )
    envelope = json.loads(capsys.readouterr().out)
    assert code == 3
    assert envelope["error"]["key"] == "sign.key_unreadable"


def test_sign_kms_openssh_format_key_exits_sign_key_unreadable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = _ready_report(tmp_path)
    key_path = _openssh_key(tmp_path / "openssh.pem")
    capsys.readouterr()
    code = cli.main(
        [
            "sign",
            str(out),
            "--as",
            "claimant",
            "--profile",
            "kms",
            "--key",
            str(key_path),
            "--json",
        ]
    )
    envelope = json.loads(capsys.readouterr().out)
    assert code == 3
    assert envelope["error"]["key"] == "sign.key_unreadable"


def test_catalog_sign_new_key_round_trips_through_the_real_verifier(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from agentce import signing

    directory = tmp_path / "cat"
    _catalog_init(directory, capsys)
    key_path = tmp_path / "new.pem"
    trust_path = tmp_path / "trust.json"
    code, env = _catalog_sign_run(
        [
            "catalog",
            "sign",
            str(directory),
            "--new-key",
            str(key_path),
            "--write-trust-root",
            str(trust_path),
        ],
        capsys,
    )
    assert code == 0, env
    assert env["catalog_id"] == directory.resolve().name
    assert oct(key_path.stat().st_mode)[-3:] == "600"
    sig_path = directory / signing.CATALOG_SIGNATURE_NAME
    assert sig_path.is_file()
    trust = signing.load_trust_root(trust_path)
    verified = signing.verify_catalog_directory(directory, trust)
    assert verified.identity == "unset"


def test_catalog_sign_pre_existing_key_happy_path(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from agentce import signing

    directory = tmp_path / "cat"
    _catalog_init(directory, capsys)
    key_path = tmp_path / "existing.pem"
    key = _write_kms_key(key_path)
    before = key_path.read_bytes()
    code, env = _catalog_sign_run(
        ["catalog", "sign", str(directory), "--key", str(key_path)], capsys
    )
    assert code == 0, env
    assert key_path.read_bytes() == before
    assert env["keyid"] == signing.keyid_for(key.public_key())


def test_catalog_sign_key_and_new_key_together_is_an_argparse_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Asserted via `cli.main`'s own argparse exit path (exit 3, `_Parser.error`), not a `cmd_catalog`
    unit call -- `--key`/`--new-key` are `add_mutually_exclusive_group(required=True)`."""
    from agentce import signing

    directory = tmp_path / "cat"
    _catalog_init(directory, capsys)
    key_path = _write_kms_key(tmp_path / "existing.pem")
    new_key_path = tmp_path / "new.pem"
    capsys.readouterr()
    code = cli.main(
        [
            "catalog",
            "sign",
            str(directory),
            "--key",
            str(key_path),
            "--new-key",
            str(new_key_path),
        ]
    )
    assert code == 3
    assert not new_key_path.exists()
    assert not (directory / signing.CATALOG_SIGNATURE_NAME).exists()


def test_catalog_sign_garbage_key_exits_key_unreadable_at_both_call_sites(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The blast-radius proof that the shared `_load_ed25519_private_key` helper fixes the same
    garbage-key file at both call sites: `agentce catalog sign` (new) and `agentce sign --profile
    kms` (behaviour CHANGE -- previously `internal.unexpected`)."""
    from agentce import signing

    directory = tmp_path / "cat"
    _catalog_init(directory, capsys)
    key_path = _garbage_key(tmp_path / "garbage.pem")

    code, env = _catalog_sign_run(
        ["catalog", "sign", str(directory), "--key", str(key_path)], capsys
    )
    assert code == 3, env
    assert env["error"]["key"] == "catalog.sign_key_unreadable"
    assert not (directory / signing.CATALOG_SIGNATURE_NAME).exists()

    out = _ready_report(tmp_path)
    capsys.readouterr()
    code = cli.main(
        [
            "sign",
            str(out),
            "--as",
            "claimant",
            "--profile",
            "kms",
            "--key",
            str(key_path),
            "--json",
        ]
    )
    envelope = json.loads(capsys.readouterr().out)
    assert code == 3
    assert envelope["error"]["key"] == "sign.key_unreadable"


def test_catalog_sign_non_ed25519_key_exits_catalog_sign_key_algorithm(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    directory = tmp_path / "cat"
    _catalog_init(directory, capsys)
    key_path = _ec_key(tmp_path / "ec.pem")
    code, env = _catalog_sign_run(
        ["catalog", "sign", str(directory), "--key", str(key_path)], capsys
    )
    assert code == 3, env
    assert env["error"]["key"] == "catalog.sign_key_algorithm"


def test_catalog_sign_new_key_existing_path_exits_new_key_exists(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    directory = tmp_path / "cat"
    _catalog_init(directory, capsys)
    key_path = tmp_path / "taken.pem"
    key_path.write_text("already here\n", encoding="utf-8")
    before = key_path.read_bytes()
    code, env = _catalog_sign_run(
        ["catalog", "sign", str(directory), "--new-key", str(key_path)], capsys
    )
    assert code == 3, env
    assert env["error"]["key"] == "catalog.sign_new_key_exists"
    assert key_path.read_bytes() == before


def test_catalog_sign_not_a_catalog(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    directory = tmp_path / "not-a-cat"
    directory.mkdir()
    code, env = _catalog_sign_run(
        ["catalog", "sign", str(directory), "--new-key", str(tmp_path / "k.pem")],
        capsys,
    )
    assert code == 3, env
    assert env["error"]["key"] == "catalog.sign_not_a_catalog"
    assert not (tmp_path / "k.pem").exists()


def test_catalog_sign_lint_failed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    directory = tmp_path / "cat"
    family = _catalog_init(directory, capsys)
    control = directory / "controls" / f"{family}-01.yaml"
    control.write_text(
        control.read_text(encoding="utf-8").replace(
            "severity: high", "severity: catastrophic"
        ),
        encoding="utf-8",
    )
    code, env = _catalog_sign_run(
        ["catalog", "sign", str(directory), "--new-key", str(tmp_path / "k.pem")],
        capsys,
    )
    assert code == 3, env
    assert env["error"]["key"] == "catalog.sign_lint_failed"
    assert not (tmp_path / "k.pem").exists()


def test_catalog_sign_key_inside_catalog_dir(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from agentce import signing

    directory = tmp_path / "cat"
    _catalog_init(directory, capsys)
    inside_key = directory / "signing-key.pem"
    code, env = _catalog_sign_run(
        ["catalog", "sign", str(directory), "--new-key", str(inside_key)], capsys
    )
    assert code == 3, env
    assert env["error"]["key"] == "catalog.sign_key_inside_catalog"
    assert not inside_key.exists()
    assert not (directory / signing.CATALOG_SIGNATURE_NAME).exists()


def test_catalog_sign_exists_guard_and_force(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from agentce import signing

    directory = tmp_path / "cat"
    _catalog_init(directory, capsys)
    key = _write_kms_key(tmp_path / "key.pem")
    code, env = _catalog_sign_run(
        ["catalog", "sign", str(directory), "--key", str(tmp_path / "key.pem")], capsys
    )
    assert code == 0, env
    sig_path = directory / signing.CATALOG_SIGNATURE_NAME
    before = sig_path.read_bytes()

    code, env = _catalog_sign_run(
        ["catalog", "sign", str(directory), "--key", str(tmp_path / "key.pem")], capsys
    )
    assert code == 3, env
    assert env["error"]["key"] == "catalog.sign_exists"
    assert sig_path.read_bytes() == before

    code, env = _catalog_sign_run(
        [
            "catalog",
            "sign",
            str(directory),
            "--key",
            str(tmp_path / "key.pem"),
            "--force",
        ],
        capsys,
    )
    assert code == 0, env
    trust = signing.TrustRoot(
        keys={signing.keyid_for(key.public_key()): key.public_key()}
    )
    verified = signing.verify_catalog_directory(directory, trust)
    assert verified.identity is not None


def test_catalog_sign_write_trust_root_inside_catalog_dir(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from agentce import signing

    directory = tmp_path / "cat"
    _catalog_init(directory, capsys)
    inside_trust_root = directory / "trust-root.json"
    code, env = _catalog_sign_run(
        [
            "catalog",
            "sign",
            str(directory),
            "--new-key",
            str(tmp_path / "k.pem"),
            "--write-trust-root",
            str(inside_trust_root),
        ],
        capsys,
    )
    assert code == 3, env
    assert env["error"]["key"] == "catalog.sign_trust_root_inside_catalog"
    assert not (directory / signing.CATALOG_SIGNATURE_NAME).exists()
    assert not inside_trust_root.exists()
    assert not (tmp_path / "k.pem").exists()


def test_catalog_sign_dot_gives_a_non_empty_resolved_catalog_id(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "own-rules-cat"
    _catalog_init(directory, capsys)
    monkeypatch.chdir(directory)
    code, env = _catalog_sign_run(
        ["catalog", "sign", ".", "--new-key", str(tmp_path / "k.pem")], capsys
    )
    assert code == 0, env
    assert env["catalog_id"] == directory.resolve().name
    assert env["catalog_id"] != ""


def test_catalog_sign_tamper_after_signing_fails_verification(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from agentce import signing

    directory = tmp_path / "cat"
    family = _catalog_init(directory, capsys)
    key_path = tmp_path / "k.pem"
    trust_path = tmp_path / "trust.json"
    code, env = _catalog_sign_run(
        [
            "catalog",
            "sign",
            str(directory),
            "--new-key",
            str(key_path),
            "--write-trust-root",
            str(trust_path),
        ],
        capsys,
    )
    assert code == 0, env
    control = directory / "controls" / f"{family}-01.yaml"
    control.write_text(control.read_text(encoding="utf-8") + "\n# tampered\n")
    trust = signing.load_trust_root(trust_path)
    with pytest.raises(signing.VerificationError):
        signing.verify_catalog_directory(directory, trust)


# --- 18.17 C1: `--deviations` applies deviations for real (contracts/P18-18.17.md). ---

_DEV_FIXTURE = (
    _AUD_FIXTURE  # same audience_presets fixture the C1 package-for-sharing tests use.
)
_DEV_CONTROL = "REC-04"  # non-conformant in this fixture, outside the INT family.


def _deviations_yaml(tmp_path: Path, **over: str) -> Path:
    import yaml

    fields = {
        "control": _DEV_CONTROL,
        "rationale": "test rationale",
        "compensating_control": "manual review",
        "owner": "user:owner@example.com",
        "approver": "user:approver@example.com",
        "granted": "2026-01-01T00:00:00.000Z",
        "expiry": "2026-06-01T00:00:00.000Z",
    }
    fields.update(over)
    path = tmp_path / "deviations.yaml"
    path.write_text(yaml.safe_dump({"deviations": [fields]}), encoding="utf-8")
    return path


def _dev_argv(
    out: Path, deviations: Path, *extra: str, bundle: Path | None = None
) -> list[str]:
    return [
        "assess",
        "--bundle",
        str(bundle if bundle is not None else _DEV_FIXTURE / "evidence"),
        "--profile",
        str(_DEV_FIXTURE / "applicability.yaml"),
        "--domain",
        str(_DEV_FIXTURE / "domain.linkml.yaml"),
        "--deviations",
        str(deviations),
        "--out",
        str(out),
        *extra,
    ]


def _dev_assertion(out: Path, control: str = _DEV_CONTROL) -> dict[str, Any]:
    assertions = json.loads((out / "assertions.json").read_text())
    return next(a for a in assertions if a["control"] == control)


def _signable_dev_bundle(tmp_path: Path) -> Path:
    """A copy of `_DEV_FIXTURE`'s single-event bundle with a real, verifying integrity chain
    (the shipped fixture's own event carries none, by design, so its stream is `failed` and
    `sign`/`verify --report` always refuse it -- unrelated to deviations). Everything else
    (the control outcomes 18.17 deviates) is unchanged."""
    from conftest import write_bundle

    from agentce.integrity import GENESIS_PREV, recompute_hash

    event = json.loads(
        (_DEV_FIXTURE / "evidence" / "events" / "subject.jsonl")
        .read_text(encoding="utf-8")
        .strip()
    )
    event["data"]["integrity"] = {
        "prev": GENESIS_PREV,
        "stream": f"{event['source']}|{event['subject']}",
        "strength": "export_chained",
    }
    event["data"]["integrity"]["hash"] = recompute_hash(event)
    return write_bundle(tmp_path / "signable-evidence", [json.dumps(event)])


def test_assess_with_deviations_flag_produces_partial_outcome_end_to_end(
    tmp_path: Path,
) -> None:
    dev = _deviations_yaml(tmp_path)
    out = tmp_path / "o"
    cli.main(_dev_argv(out, dev))
    a = _dev_assertion(out)
    assert a["outcome"] == "partial"
    assert a["deviation"] == _DEV_CONTROL


def test_assess_refuses_an_invalid_deviation_register(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    dev = tmp_path / "deviations.yaml"
    dev.write_text("deviations:\n  - control: ZZZ-99\n", encoding="utf-8")
    out = tmp_path / "o"
    code = cli.main(_dev_argv(out, dev, "--json"))
    envelope = json.loads(capsys.readouterr().out)
    assert code == 3
    assert envelope["error"]["key"] == "input.deviation_invalid"


def test_assess_refuses_a_deviations_file_that_is_not_valid_yaml(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    dev = tmp_path / "deviations.yaml"
    dev.write_text("not: a: mapping: at: all: [", encoding="utf-8")
    out = tmp_path / "o"
    code = cli.main(_dev_argv(out, dev, "--json"))
    envelope = json.loads(capsys.readouterr().out)
    assert code == 3
    assert envelope["error"]["key"] == "input.deviation_invalid"


def test_assess_refuses_a_deviations_register_whose_deviations_key_is_not_a_list(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    dev = tmp_path / "deviations.yaml"
    dev.write_text("deviations:\n  x: 1\n", encoding="utf-8")
    out = tmp_path / "o"
    code = cli.main(_dev_argv(out, dev, "--json"))
    envelope = json.loads(capsys.readouterr().out)
    assert code == 3
    assert envelope["error"]["key"] == "input.deviation_invalid"


@pytest.mark.parametrize(
    "content",
    [f"deviations: {_DEV_CONTROL}\n", f"deviations: [{_DEV_CONTROL}]\n"],
)
def test_assess_refuses_a_deviations_register_whose_deviations_key_is_a_scalar_or_a_list_of_scalars(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], content: str
) -> None:
    dev = tmp_path / "deviations.yaml"
    dev.write_text(content, encoding="utf-8")
    out = tmp_path / "o"
    code = cli.main(_dev_argv(out, dev, "--json"))
    envelope = json.loads(capsys.readouterr().out)
    assert code == 3
    assert envelope["error"]["key"] == "input.deviation_invalid"


def test_assess_an_unquoted_yaml_expiry_date_is_still_checked_against_as_of(
    tmp_path: Path,
) -> None:
    """An unquoted YAML date (parsed by the YAML loader as a `date`, not a `str`) must be
    normalized before comparison, not silently treated as absent (round 2 B4)."""
    dev = tmp_path / "deviations.yaml"
    dev.write_text(
        "deviations:\n"
        f"  - control: {_DEV_CONTROL}\n"
        '    rationale: "r"\n'
        '    compensating_control: "c"\n'
        '    owner: "user:owner@example.com"\n'
        '    approver: "user:approver@example.com"\n'
        '    granted: "2025-08-01T00:00:00.000Z"\n'
        "    expiry: 2025-12-01\n",  # unquoted -- a YAML date, long expired vs. the fixture's window
        encoding="utf-8",
    )
    out = tmp_path / "o"
    cli.main(_dev_argv(out, dev))
    a = _dev_assertion(out)
    assert a["outcome"] == "non-conformant"
    assert a.get("deviation") is None
    manifest = json.loads((out / "manifest.json").read_text())
    assert any(_DEV_CONTROL in limit for limit in manifest.get("limitations", []))


def test_assess_refuses_a_deviation_whose_expiry_is_not_a_valid_date_rather_than_silently_applying_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An expiry that is present but unparseable (a bare integer with no separators, or a garbage
    string) must refuse the run, not silently apply the deviation with its expiry check skipped."""
    dev = tmp_path / "deviations.yaml"
    dev.write_text(
        "deviations:\n"
        f"  - control: {_DEV_CONTROL}\n"
        '    rationale: "r"\n'
        '    compensating_control: "c"\n'
        '    owner: "user:owner@example.com"\n'
        '    approver: "user:approver@example.com"\n'
        '    granted: "2025-08-01T00:00:00.000Z"\n'
        "    expiry: 20210101\n",  # a bare int, not a YAML date -- unparseable, not a known-good shape
        encoding="utf-8",
    )
    out = tmp_path / "o"
    code = cli.main(_dev_argv(out, dev, "--json"))
    envelope = json.loads(capsys.readouterr().out)
    assert code == 3
    assert envelope["error"]["key"] == "input.deviation_invalid"


def test_assess_a_calendar_invalid_unquoted_yaml_expiry_date_is_refused_as_input_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A YAML-syntax-valid but calendar-invalid unquoted expiry (``2026-02-30``) used to crash
    ``load_untrusted_yaml`` with a bare ``ValueError`` -- uncaught, it reached the CLI's top-level
    catch-all as ``internal.unexpected`` (exit 3), never this control's own ``input.deviation_invalid``
    key (18.17c). Exit code alone cannot discriminate the fix: both keys are exit 3 today, so the
    assertion is the error key, not the exit code alone."""
    dev = tmp_path / "deviations.yaml"
    dev.write_text(
        "deviations:\n"
        f"  - control: {_DEV_CONTROL}\n"
        '    rationale: "r"\n'
        '    compensating_control: "c"\n'
        '    owner: "user:owner@example.com"\n'
        '    approver: "user:approver@example.com"\n'
        '    granted: "2025-08-01T00:00:00.000Z"\n'
        "    expiry: 2026-02-30\n",  # unquoted, calendar-invalid -- shape-valid, not a real date
        encoding="utf-8",
    )
    out = tmp_path / "o"
    code = cli.main(_dev_argv(out, dev, "--json"))
    envelope = json.loads(capsys.readouterr().out)
    assert code == 3
    assert envelope["error"]["key"] == "input.deviation_invalid"


def test_assess_an_explicit_non_timestamp_tagged_expiry_is_refused_as_input_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An explicit ``!!timestamp`` tag on a value that is not timestamp-shaped at all (``!!timestamp
    foo``) bypasses the resolver's own shape gate and reaches ``construct_yaml_timestamp`` with no
    regex match, which raises ``AttributeError`` rather than ``ValueError`` -- a second, narrower
    crash the calendar-invalid fallback's first ``except ValueError`` alone did not catch (18.17c
    verifier round 1)."""
    dev = tmp_path / "deviations.yaml"
    dev.write_text(
        "deviations:\n"
        f"  - control: {_DEV_CONTROL}\n"
        '    rationale: "r"\n'
        '    compensating_control: "c"\n'
        '    owner: "user:owner@example.com"\n'
        '    approver: "user:approver@example.com"\n'
        '    granted: "2025-08-01T00:00:00.000Z"\n'
        "    expiry: !!timestamp foo\n",
        encoding="utf-8",
    )
    out = tmp_path / "o"
    code = cli.main(_dev_argv(out, dev, "--json"))
    envelope = json.loads(capsys.readouterr().out)
    assert code == 3
    assert envelope["error"]["key"] == "input.deviation_invalid"


def test_assess_an_explicit_int_tagged_empty_expiry_is_refused_as_input_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``!!int ''`` bypasses the resolver's own shape gate the same way ``!!timestamp foo`` does, but
    PyYAML's ``construct_yaml_int`` raises ``IndexError`` (``value[0]`` on an empty string), a third
    distinct exception type the round-1 fix's narrower ``except (ValueError, AttributeError)`` did
    not catch (18.17c verifier round 2) -- closed generally by catching any construction exception,
    not one more enumerated type."""
    dev = tmp_path / "deviations.yaml"
    dev.write_text(
        "deviations:\n"
        f"  - control: {_DEV_CONTROL}\n"
        '    rationale: "r"\n'
        '    compensating_control: "c"\n'
        '    owner: "user:owner@example.com"\n'
        '    approver: "user:approver@example.com"\n'
        '    granted: "2025-08-01T00:00:00.000Z"\n'
        "    expiry: !!int ''\n",
        encoding="utf-8",
    )
    out = tmp_path / "o"
    code = cli.main(_dev_argv(out, dev, "--json"))
    envelope = json.loads(capsys.readouterr().out)
    assert code == 3
    assert envelope["error"]["key"] == "input.deviation_invalid"


def test_readiness_a_calendar_invalid_unquoted_yaml_expiry_date_gives_not_ready_not_a_crash(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``agentce readiness`` shares ``_load_deviation_register`` with ``assess`` -- the same
    calendar-invalid expiry must reach ``readiness.deviation_lint``'s own not-a-valid-RFC-3339-date
    refusal (NOT READY, exit 1), never ``internal.unexpected`` (exit 3), matching what TypeScript's
    and Java's own calendar-invalid-timestamp handling already give today (18.17c; cross-engine
    parity is pinned by ``tools/readiness_parity_check.py`` scenario 7)."""
    report = tmp_path / "report"
    report.mkdir()
    (report / "assertions.json").write_text(
        json.dumps(
            [{"control": _DEV_CONTROL, "outcome": "conformant", "subject": "s"}]
        ),
        encoding="utf-8",
    )
    (report / "integrity.jsonl").write_text("", encoding="utf-8")
    dev = tmp_path / "deviations.yaml"
    dev.write_text(
        "deviations:\n"
        f"  - control: {_DEV_CONTROL}\n"
        '    rationale: "r"\n'
        '    compensating_control: "c"\n'
        '    owner: "user:owner@example.com"\n'
        '    approver: "user:approver@example.com"\n'
        '    granted: "2026-01-01T00:00:00.000Z"\n'
        "    expiry: 2026-02-30\n",
        encoding="utf-8",
    )
    code = cli.main(["readiness", str(report), "--deviations", str(dev), "--json"])
    envelope = json.loads(capsys.readouterr().out)
    assert code == 1
    assert envelope["verdict"] == "NOT READY"
    assert "error" not in envelope


def test_assess_ignores_and_reports_an_expired_deviation(tmp_path: Path) -> None:
    dev = _deviations_yaml(
        tmp_path, granted="2025-08-01T00:00:00.000Z", expiry="2025-12-01T00:00:00.000Z"
    )
    out = tmp_path / "o"
    cli.main(_dev_argv(out, dev))
    a = _dev_assertion(out)
    assert a["outcome"] == "non-conformant"
    assert a.get("deviation") is None
    manifest = json.loads((out / "manifest.json").read_text())
    assert any(_DEV_CONTROL in limit for limit in manifest.get("limitations", []))


def test_readiness_accepts_a_report_after_assess_applied_a_deviation_end_to_end(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The finding-1 regression, at the CLI level: `readiness` must not reject the very
    `outcome: partial` state a real `assess --deviations` run just produced. Uses the signable
    bundle (real integrity chain) since readiness blocks on integrity regardless of deviations,
    and that is not what this test is proving."""
    bundle = _signable_dev_bundle(tmp_path)
    dev = _deviations_yaml(tmp_path)
    out = tmp_path / "o"
    cli.main(_dev_argv(out, dev, bundle=bundle))
    capsys.readouterr()
    code = cli.main(["readiness", str(out), "--deviations", str(dev), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["verdict"] != "NOT READY"
    assert code == 0


def test_assess_deviations_flag_is_recorded_in_manifest_and_reverify_argv(
    tmp_path: Path,
) -> None:
    from agentce import signing

    dev = _deviations_yaml(tmp_path)
    out = tmp_path / "o"
    cli.main(_dev_argv(out, dev, "--emit", "remediation"))
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["inputs"]["deviation_register_digest"] == signing.sha256_prefixed(
        dev.read_bytes()
    )
    packages = list(out.glob("remediation/*/remediation-package.json"))
    assert packages
    package = json.loads(packages[0].read_text())
    finding = next(f for f in package["findings"] if f["control"] == "DOC-01")
    assert "--deviations" in finding["acceptance"]["reverify_command"]


def test_assess_package_for_sharing_copies_the_deviation_register(
    tmp_path: Path,
) -> None:
    dev = _deviations_yaml(tmp_path)
    out = tmp_path / "o"
    assert cli.main(_dev_argv(out, dev, "--package-for-sharing")) in (0, 1)
    copied = out / "bundle" / "deviations.yaml"
    assert copied.is_file()
    assert copied.read_bytes() == dev.read_bytes()


def test_verify_report_reruns_with_the_packaged_deviation_register_and_reproduces_partial(
    tmp_path: Path,
) -> None:
    bundle = _signable_dev_bundle(tmp_path)
    dev = _deviations_yaml(tmp_path)
    out = tmp_path / "o"
    cli.main(_dev_argv(out, dev, "--package-for-sharing", bundle=bundle))
    a = _dev_assertion(out)
    assert a["outcome"] == "partial"
    key_path = tmp_path / "claimant.pem"
    _write_kms_key(key_path)
    assert (
        cli.main(
            [
                "sign",
                str(out),
                "--as",
                "claimant",
                "--profile",
                "kms",
                "--key",
                str(key_path),
                "--write-trust-root",
            ]
        )
        == 0
    )
    assert cli.main(["verify", "--report", str(out)]) == 0


def test_report_public_statement_lists_accepted_deviations(tmp_path: Path) -> None:
    dev = _deviations_yaml(tmp_path)
    out = tmp_path / "o"
    cli.main(_dev_argv(out, dev, "--emit", "public"))
    statement = (out / "public-statement.md").read_text(encoding="utf-8")
    assert _DEV_CONTROL in statement


def test_report_claim_lists_accepted_deviations(tmp_path: Path) -> None:
    dev = _deviations_yaml(tmp_path)
    out = tmp_path / "o"
    cli.main(_dev_argv(out, dev))
    claim = json.loads((out / "claim.json").read_text())
    assert claim["deviations"] == [_DEV_CONTROL]


def test_deviation_partial_outcome_is_consistent_across_report_consumers(
    tmp_path: Path,
) -> None:
    """Cross-consumer consistency (blast_radius, contracts/P18-18.17.md): every consumer of a
    deviated assertion agrees it is `partial` -- `assertions.json` itself, a fresh `aggregate()`
    recount, `claim.json`'s accepted-deviations list, and `oscal-ar.json`'s matching finding."""
    from agentce.assertions import Assertion, aggregate

    dev = _deviations_yaml(tmp_path)
    out = tmp_path / "o"
    cli.main(_dev_argv(out, dev, "--for", "compliance"))
    assertions_json = json.loads((out / "assertions.json").read_text())
    dev_entry = next(a for a in assertions_json if a["control"] == _DEV_CONTROL)
    assert dev_entry["outcome"] == "partial"

    counts = aggregate([Assertion.from_json(a) for a in assertions_json])
    assert counts["partial"] >= 1

    claim = json.loads((out / "claim.json").read_text())
    assert _DEV_CONTROL in claim["deviations"]

    oscal = json.loads((out / "oscal-ar.json").read_text())
    finding = next(
        f
        for f in oscal["assessment-results"]["results"][0]["findings"]
        if f["title"].startswith(_DEV_CONTROL)
    )
    assert finding["target"]["status"]["reason"] == "partial"
    assert finding.get("related-risks")


def _resign_as_claimant(out: Path, tmp_path: Path) -> None:
    """Drop every signature and sign again with the key the embedded trust-root.json pins, the
    way anyone holding a bundle can once it carries its own trust root (18.65 round 3)."""
    claim = json.loads((out / "claim.json").read_text(encoding="utf-8"))
    claim.pop("signatures", None)
    (out / "claim.json").write_text(
        json.dumps(claim, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    for old in (out / "signatures").glob("*"):
        old.unlink()
    argv = ["sign", str(out), "--as", "claimant", "--profile", "kms"]
    assert cli.main([*argv, "--key", str(tmp_path / "claimant.pem")]) == 0


def _manifest_output_escapes(out: Path) -> None:
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    manifest["outputs"]["../escape.md"] = "sha256:" + "0" * 64
    (out / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
    )


def _manifest_limitations_not_list(out: Path) -> None:
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    manifest["limitations"] = "none"
    (out / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
    )


def _packaging_order_not_list(out: Path) -> None:
    packaging = json.loads((out / "packaging.json").read_text(encoding="utf-8"))
    packaging["catalog_dir_order"] = "x"
    (out / "packaging.json").write_text(
        json.dumps(packaging, indent=2, sort_keys=True), encoding="utf-8"
    )
    # packaging.json is a manifest-tracked output: record its new digest so only its shape is wrong.
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    manifest["outputs"]["packaging.json"] = (
        "sha256:" + hashlib.sha256((out / "packaging.json").read_bytes()).hexdigest()
    )
    (out / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
    )


@pytest.mark.parametrize(
    ("corrupt", "cause"),
    [
        (
            _manifest_output_escapes,
            "manifest.json is not shaped like the one `agentce assess` writes.",
        ),
        (
            _manifest_limitations_not_list,
            "manifest.json is not shaped like the one `agentce assess` writes.",
        ),
        (
            _packaging_order_not_list,
            "packaging.json is not shaped like the one `agentce assess` writes.",
        ),
    ],
)
def test_verify_report_resigned_document_shape_is_refused_not_a_crash(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    corrupt: Callable[[Path], None],
    cause: str,
) -> None:
    """Under an embedded trust root a signature that verifies says nothing about the documents'
    shape: a re-signed manifest.json or packaging.json of the wrong shape is refused with its key,
    never `internal.unexpected` and never a read outside the report directory (18.65 round 3)."""
    out, _key = _packaged_and_signed(tmp_path)
    corrupt(out)
    _resign_as_claimant(out, tmp_path)
    code, envelope = _verify_report_json(capsys, str(out))
    assert code == 3
    assert (envelope["error"]["key"], envelope["error"]["cause"]) == (
        "verify.report_output_tampered",
        cause,
    )
