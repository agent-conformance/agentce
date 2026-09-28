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
    tmp_path: Path, *assess_extra: str
) -> tuple[Path, Ed25519PrivateKey]:
    """A `--package-for-sharing` quickstart report, signed as claimant with `--write-trust-root`."""
    out = tmp_path / "o"
    assert cli.main(_assess_argv(out, "--package-for-sharing", *assess_extra)) == 0
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


def test_verify_report_unpackaged_reports_null(tmp_path: Path) -> None:
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
    code = cli.main(["verify", "--report", str(out), "--json"])
    assert code == 0


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
