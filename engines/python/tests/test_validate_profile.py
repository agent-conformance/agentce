"""The applicability-profile validator tool and its vendored schema (SPEC §6.5, §13.4)."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentce.tools import validate_profile as vp

_REPO_ROOT = Path(__file__).resolve().parents[3]
_VENDORED = (
    _REPO_ROOT
    / "engines"
    / "python"
    / "agentce"
    / "data"
    / "schemas"
    / "applicability-profile.schema.json"
)
_GENERATED = (
    _REPO_ROOT
    / "spec"
    / "model"
    / "generated"
    / "json-schema"
    / "applicability-profile.schema.json"
)

_VALID_PROFILE = """\
profile_version: 1
observation_window:
  start: "2026-01-01T00:00:00Z"
  end: "2026-04-01T00:00:00Z"
catalogs:
  - "eu-ai-act@2026.09"
subjects:
  - id: "spiffe://corp/agents/a"
    role: "deployer"
"""


def test_vendored_schema_matches_generated() -> None:
    # The engine vendors the profile schema so the tool validates identically to the spec driver; a
    # drift would let one accept a profile the other rejects, so the copy stays byte-identical.
    assert _VENDORED.read_text(encoding="utf-8") == _GENERATED.read_text(
        encoding="utf-8"
    )


def test_valid_profile_has_no_errors() -> None:
    import yaml

    assert vp.validate_profile(yaml.safe_load(_VALID_PROFILE)) == []


def test_missing_required_field_is_reported() -> None:
    import yaml

    profile = yaml.safe_load(_VALID_PROFILE)
    del profile["catalogs"]
    errors = vp.validate_profile(profile)
    assert errors and "catalogs" in errors[0]


def test_main_ok(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "p.yaml"
    path.write_text(_VALID_PROFILE, encoding="utf-8")
    assert vp.main([str(path)]) == 0
    assert "PROFILE OK" in capsys.readouterr().out


def test_main_invalid_exits_one(tmp_path: Path) -> None:
    path = tmp_path / "p.yaml"
    path.write_text("profile_version: 1\n", encoding="utf-8")  # missing required fields
    assert vp.main([str(path)]) == 1


def test_main_missing_file_exits_one(tmp_path: Path) -> None:
    assert vp.main([str(tmp_path / "nope.yaml")]) == 1


def test_main_usage_error_exits_two() -> None:
    assert vp.main([]) == 2
