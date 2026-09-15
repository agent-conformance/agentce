"""Configuration resolution and precedence (SPEC §8.5, §13.4 AX-8)."""

from __future__ import annotations

from pathlib import Path

from agentce.config import resolve


def _by_key(values: list[dict[str, object]]) -> dict[str, dict[str, object]]:
    return {v["key"]: v for v in values}  # type: ignore[misc]


def test_every_value_has_a_source(tmp_path: Path) -> None:
    values = resolve(env={}, cwd=tmp_path)
    assert values
    assert all(v["source"] for v in values)
    assert all(v["source"] == "default" for v in values)  # nothing set here


def test_env_overrides_default() -> None:
    values = _by_key(
        resolve(env={"AGENTCE_REPORT_LANGUAGE": "fr"}, cwd=Path("/nonexistent"))
    )
    assert values["report_language"]["value"] == "fr"
    assert values["report_language"]["source"] == "env"


def test_flag_overrides_env() -> None:
    values = _by_key(
        resolve(
            env={"AGENTCE_REPORT_LANGUAGE": "fr"},
            flags={"report_language": "de"},
            cwd=Path("/nonexistent"),
        )
    )
    assert values["report_language"]["value"] == "de"
    assert values["report_language"]["source"] == "flag"


def test_file_overrides_default_and_casts(tmp_path: Path) -> None:
    (tmp_path / "agentce.toml").write_text(
        '[config]\nreport_language = "es"\nclock_skew_seconds = 60\n', encoding="utf-8"
    )
    values = _by_key(resolve(env={}, cwd=tmp_path))
    assert values["report_language"] == {
        "key": "report_language",
        "value": "es",
        "source": "file",
    }
    assert values["clock_skew_seconds"]["value"] == 60
    assert values["clock_skew_seconds"]["source"] == "file"


def test_env_overrides_file(tmp_path: Path) -> None:
    (tmp_path / "agentce.toml").write_text(
        "[config]\nclock_skew_seconds = 60\n", encoding="utf-8"
    )
    values = _by_key(resolve(env={"AGENTCE_CLOCK_SKEW_SECONDS": "90"}, cwd=tmp_path))
    assert values["clock_skew_seconds"]["value"] == 90
    assert values["clock_skew_seconds"]["source"] == "env"
