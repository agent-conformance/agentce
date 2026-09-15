"""CLI dispatch, ``--json`` envelope, and per-command input handling for the skeleton."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import pytest

from agentce import cli


def run(
    argv: Sequence[str], capsys: pytest.CaptureFixture[str]
) -> tuple[int, dict[str, Any]]:
    code = cli.main(list(argv))
    return code, json.loads(capsys.readouterr().out)


def test_no_command_prints_help(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main([]) == 0
    assert "<command>" in capsys.readouterr().out


def test_help_lists_commands(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--help"]) == 0
    out = capsys.readouterr().out
    for name in ("validate", "assess", "catalog", "conformance", "version"):
        assert name in out


def test_top_level_version_flag(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--version"]) == 0
    assert "agentce" in capsys.readouterr().out


def test_version_json(capsys: pytest.CaptureFixture[str]) -> None:
    code, env = run(["version", "--json"], capsys)
    assert code == 0
    assert env["engine"] == "agentce-py"
    assert env["command"] == "version"
    assert env["no_ml"] in {"pass", "fail"}
    assert env["spec_version"] == "0.6"


def test_version_human(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["version"]) == 0
    out = capsys.readouterr().out
    assert "agentce-py" in out
    assert "no_ml" in out


def test_validate_missing_bundle(capsys: pytest.CaptureFixture[str]) -> None:
    code, env = run(["validate", "--json"], capsys)
    assert code == 3
    assert env["error"]["key"] == "input.bundle_missing"
    assert env["error"]["fix"]


def test_validate_bundle_not_a_directory(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, env = run(["validate", "--bundle", str(tmp_path / "nope"), "--json"], capsys)
    assert code == 3
    assert env["error"]["key"] == "input.bundle_not_a_directory"


def test_validate_good_bundle(
    make_bundle: Callable[..., Path],
    example_events: list[dict[str, Any]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    bundle = make_bundle(example_events)
    code, env = run(["validate", "--bundle", str(bundle), "--json"], capsys)
    assert code == 0
    assert env["accepted"] == 2
    assert env["quarantined"] == 0
    assert env["bundle_digest"].startswith("sha256:")


def test_validate_quarantine_exits_one(
    make_bundle: Callable[..., Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    bundle = make_bundle(["{not json"])
    code, env = run(["validate", "--bundle", str(bundle), "--json"], capsys)
    assert code == 1
    assert env["quarantined"] == 1
    assert env["quarantine_by_reason"] == {"schema_invalid": 1}


def test_validate_missing_manifest_exits_three(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    empty = tmp_path / "no_manifest"
    empty.mkdir()
    code, env = run(["validate", "--bundle", str(empty), "--json"], capsys)
    assert code == 3
    assert env["error"]["key"] == "input.bundle_manifest_missing"


def test_validate_writes_quarantine_file(
    make_bundle: Callable[..., Path],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    bundle = make_bundle(["{not json"])
    out = tmp_path / "out"
    code, env = run(
        ["validate", "--bundle", str(bundle), "--out", str(out), "--json"], capsys
    )
    assert code == 1
    assert (out / "quarantine.jsonl").is_file()
    assert env["quarantine_file"] == str(out / "quarantine.jsonl")


def test_verify_requires_exactly_one_target(capsys: pytest.CaptureFixture[str]) -> None:
    code, env = run(["verify", "--json"], capsys)
    assert code == 3
    assert env["error"]["key"] == "input.verify_target"


def test_verify_bundle(
    make_bundle: Callable[..., Path],
    example_event: dict[str, Any],
    clone: Callable[[dict[str, Any]], dict[str, Any]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    from agentce.integrity import GENESIS_PREV, recompute_hash

    first = clone(example_event)
    first["data"]["integrity"]["strength"] = "export_chained"
    first["data"]["integrity"]["prev"] = GENESIS_PREV
    first["data"]["integrity"]["hash"] = recompute_hash(first)
    second = clone(example_event)
    second["id"] = "f" * 64
    second["time"] = "2026-07-14T09:30:00.000Z"
    second["data"]["integrity"]["strength"] = "export_chained"
    second["data"]["integrity"]["prev"] = first["data"]["integrity"]["hash"]
    second["data"]["integrity"]["hash"] = recompute_hash(second)

    bundle = make_bundle([first, second])
    code, env = run(["verify", "--bundle", str(bundle), "--json"], capsys)
    assert code == 0
    assert env["stream_count"] == 1
    assert env["streams"][0]["status"] == "verified_weak"


def test_assess_missing_profile(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, env = run(
        [
            "assess",
            "--bundle",
            str(tmp_path),
            "--catalog",
            "base@1",
            "--out",
            str(tmp_path / "o"),
            "--json",
        ],
        capsys,
    )
    assert code == 3
    assert env["error"]["key"] == "input.profile_missing"


def test_assess_ingests_then_pending(
    make_bundle: Callable[..., Path],
    example_events: list[dict[str, Any]],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    bundle = make_bundle(example_events)
    profile = tmp_path / "profile.yaml"
    profile.write_text("{}", encoding="utf-8")
    out = tmp_path / "o"
    code, env = run(
        [
            "assess",
            "--bundle",
            str(bundle),
            "--catalog",
            "a@1,b@2",
            "--profile",
            str(profile),
            "--out",
            str(out),
            "--json",
        ],
        capsys,
    )
    assert code == 0
    assert env["catalogs"] == ["a@1", "b@2"]
    assert env["accepted"] == 2
    assert env["status"] == "not_implemented"
    assert (out / "quarantine.jsonl").is_file()


def test_report_from(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    src = tmp_path / "assertions.json"
    src.write_text("{}", encoding="utf-8")
    code, env = run(
        ["report", "--from", str(src), "--format", "oscal", "--json"], capsys
    )
    assert code == 0
    assert env["format"] == "oscal"


def test_report_bad_format_is_usage_error() -> None:
    assert cli.main(["report", "--from", "x", "--format", "xml"]) == 3


def test_report_validate_empty_dir_is_invalid(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, env = run(["report", "--validate", str(tmp_path), "--json"], capsys)
    assert code == 3
    assert env["valid"] is False
    assert env["problems"]


def test_report_validate_after_assess(
    make_bundle: Callable[..., Path],
    example_events: list[dict[str, Any]],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    bundle = make_bundle(example_events)
    profile = tmp_path / "profile.yaml"
    profile.write_text("{}", encoding="utf-8")
    out = tmp_path / "o"
    assert (
        cli.main(
            [
                "assess",
                "--bundle",
                str(bundle),
                "--catalog",
                "c@1",
                "--profile",
                str(profile),
                "--out",
                str(out),
            ]
        )
        == 0
    )
    capsys.readouterr()
    code, env = run(["report", "--validate", str(out), "--json"], capsys)
    assert code == 0
    assert env["valid"] is True


def test_collect_dry_run(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cfg = tmp_path / "collect.yaml"
    cfg.write_text("{}", encoding="utf-8")
    code, env = run(
        [
            "collect",
            "--config",
            str(cfg),
            "--out",
            str(tmp_path / "b"),
            "--dry-run",
            "--json",
        ],
        capsys,
    )
    assert code == 0
    assert env["dry_run"] is True


def test_catalog_no_action(capsys: pytest.CaptureFixture[str]) -> None:
    code, env = run(["catalog", "--json"], capsys)
    assert code == 3
    assert env["error"]["key"] == "input.catalog_action"


def test_catalog_lint_missing_dir(capsys: pytest.CaptureFixture[str]) -> None:
    code, env = run(["catalog", "lint", "--json"], capsys)
    assert code == 3
    assert env["error"]["key"] == "input.dir_missing"


def test_catalog_lint_base_is_clean(capsys: pytest.CaptureFixture[str]) -> None:
    base = (
        Path(__file__).resolve().parents[3] / "spec" / "catalogs" / "base" / "eu-ai-act"
    )
    code, env = run(["catalog", "lint", str(base), "--json"], capsys)
    assert code == 0
    assert env["clean"] is True
    assert env["problems"] == []


def test_catalog_lint_no_catalog_yaml(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, env = run(["catalog", "lint", str(tmp_path), "--json"], capsys)
    assert code == 1
    assert env["clean"] is False


def test_conformance_run(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    engine = tmp_path / "e"
    corpus = tmp_path / "c"
    engine.mkdir()
    corpus.mkdir()
    code, env = run(
        [
            "conformance",
            "run",
            "--engine",
            str(engine),
            "--corpus",
            str(corpus),
            "--out",
            str(tmp_path / "o"),
            "--json",
        ],
        capsys,
    )
    assert code == 0
    assert env["action"] == "run"
    assert env["out"] == str(tmp_path / "o")


def test_conformance_run_missing_corpus(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    engine = tmp_path / "e"
    engine.mkdir()
    code, env = run(["conformance", "run", "--engine", str(engine), "--json"], capsys)
    assert code == 3
    assert env["error"]["key"] == "input.corpus_missing"


def test_diff_ok(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    left = tmp_path / "a.json"
    right = tmp_path / "b.json"
    left.write_text("{}", encoding="utf-8")
    right.write_text("{}", encoding="utf-8")
    code, env = run(["diff", str(left), str(right), "--json"], capsys)
    assert code == 0
    assert env["report_a"] == str(left)


def test_diff_missing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    left = tmp_path / "a.json"
    left.write_text("{}", encoding="utf-8")
    code, env = run(
        ["diff", str(left), str(tmp_path / "missing.json"), "--json"], capsys
    )
    assert code == 3
    assert env["error"]["key"] == "input.report_b_not_a_file"


def test_sign_ok(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, env = run(
        ["sign", str(tmp_path), "--as", "claimant", "--dry-run", "--json"], capsys
    )
    assert code == 0
    assert env["as"] == "claimant"
    assert env["dry_run"] is True
    assert env["profile"] == "sigstore-public"


def test_sign_missing_dir(capsys: pytest.CaptureFixture[str]) -> None:
    code, env = run(["sign", "--as", "claimant", "--json"], capsys)
    assert code == 3
    assert env["error"]["key"] == "input.report_dir_missing"


def test_sign_bad_role_is_usage_error() -> None:
    assert cli.main(["sign", "/tmp", "--as", "nobody"]) == 3


def test_human_output_without_json(
    make_bundle: Callable[..., Path],
    example_events: list[dict[str, Any]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    bundle = make_bundle(example_events)
    code = cli.main(["validate", "--bundle", str(bundle)])
    out = capsys.readouterr().out
    assert code == 0
    assert "accepted" in out
