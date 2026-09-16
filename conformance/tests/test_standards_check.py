"""Standards-alignment gate (SPEC §2.1, §7.3, P5.4): the OSCAL component definition and the obligation
crosswalks validate, and every clause reference is flagged unverified until a human confirms it.

These tests are hermetic: the real-artifact assertions read the committed files, and the validator
tests build synthetic crosswalks in a temporary directory.
"""

from __future__ import annotations

from pathlib import Path

import standards_check

_GOOD_CONTROL = "REC-04"
_GOOD_ELEMENT = "Decision"


def test_real_artifacts_validate() -> None:
    result = standards_check.run_standards_check()
    assert result["artifacts_valid"] is True, result["problems"]
    assert result["refs_flagged"] is True
    assert result["crosswalks"] >= 7


def test_oscal_matches_a_fresh_generation() -> None:
    committed = standards_check.OSCAL_INSTANCE.read_text("utf-8")
    regenerated = standards_check.oscal_builder.render(
        standards_check.oscal_builder.build()
    )
    assert committed == regenerated


def _write_crosswalk(directory: Path, entry: str) -> Path:
    path = directory / "synthetic.yaml"
    path.write_text(
        'framework: synthetic\nversion: "1"\nobligations:\n' + entry,
        encoding="utf-8",
    )
    return path


def test_validator_accepts_a_well_formed_crosswalk(tmp_path: Path) -> None:
    entry = (
        f'  - ref: "X"\n    obligation: A statement.\n'
        f"    schema_elements: [{_GOOD_ELEMENT}]\n    rungs: [2]\n"
        f"    controls: [{_GOOD_CONTROL}]\n    verified_against_text: false\n"
    )
    path = _write_crosswalk(tmp_path, entry)
    problems = standards_check._check_crosswalk(
        path, standards_check._event_types(), standards_check._control_ids()
    )
    assert problems == []
    assert standards_check._all_flagged(path) is True


def test_validator_flags_unknown_element_and_control(tmp_path: Path) -> None:
    entry = (
        '  - ref: "X"\n    obligation: A statement.\n'
        "    schema_elements: [NotAnEvent]\n    controls: [ZZZ-99]\n"
        "    verified_against_text: false\n"
    )
    path = _write_crosswalk(tmp_path, entry)
    problems = standards_check._check_crosswalk(
        path, standards_check._event_types(), standards_check._control_ids()
    )
    assert any("unknown schema element NotAnEvent" in p for p in problems)
    assert any("unknown control ZZZ-99" in p for p in problems)


def test_validator_catches_a_verified_reference(tmp_path: Path) -> None:
    entry = (
        '  - ref: "X"\n    obligation: A statement.\n'
        f"    schema_elements: [{_GOOD_ELEMENT}]\n    verified_against_text: true\n"
    )
    path = _write_crosswalk(tmp_path, entry)
    # Structurally valid (the flag is a boolean), but presented as verified without human review.
    assert (
        standards_check._check_crosswalk(
            path, standards_check._event_types(), standards_check._control_ids()
        )
        == []
    )
    assert standards_check._all_flagged(path) is False


def test_validator_requires_the_verification_flag(tmp_path: Path) -> None:
    entry = '  - ref: "X"\n    obligation: A statement.\n'
    path = _write_crosswalk(tmp_path, entry)
    problems = standards_check._check_crosswalk(
        path, standards_check._event_types(), standards_check._control_ids()
    )
    assert any("verified_against_text must be a boolean" in p for p in problems)
    assert standards_check._all_flagged(path) is False
