"""``cmd_assess``'s own default-emit resolution (RFC 0008 Sec.8): the skill on every default run."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentce import cli
from agentce.report import ASSESS_DEFAULT_EMIT, validate_report

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


# `--for` audience presets (contracts/P18-18.7.md). Every table below is hand-written, never derived
# from `commands.PRESET_EMIT`, so a typo in the shipped mapping cannot also hide in its own test.
_PRESET_PAIRS: dict[str, frozenset[str]] = {
    "engineering": frozenset({"md", "html", "skill", "remediation"}),
    "compliance": frozenset({"oscal", "oscal_xml", "public", "pack", "csv"}),
    "security": frozenset({"sarif", "md", "html"}),
    "ci": frozenset({"sarif", "junit"}),
    "share": frozenset({"md", "html", "pdf", "public", "pack"}),
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
    assert cli.main(argv) == 0
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
