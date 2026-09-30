"""CLI dispatch, ``--json`` envelope, and per-command input handling for the skeleton."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import pytest

from agentce import cli

_REPO_ROOT = Path(__file__).resolve().parents[3]
_ENGINE = _REPO_ROOT / "engines" / "python"


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


def test_assess_names_the_catalogs_it_cannot_resolve(
    make_bundle: Callable[..., Path],
    example_events: list[dict[str, Any]],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    bundle = make_bundle(example_events)
    profile = tmp_path / "profile.yaml"
    profile.write_text("{}", encoding="utf-8")
    out = tmp_path / "o"
    code = cli.main(
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
        ]
    )
    envelope = json.loads(capsys.readouterr().out)
    assert code == 3
    assert envelope["error"]["key"] == "input.catalog_unresolved"
    assert "'a@1', 'b@2'" in envelope["error"]["cause"]
    assert not out.exists()


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
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    quickstart = _REPO_ROOT / "corpus" / "quickstart"
    out = tmp_path / "o"
    assert (
        cli.main(
            [
                "assess",
                "--bundle",
                str(quickstart / "evidence"),
                "--profile",
                str(quickstart / "applicability.yaml"),
                "--domain",
                str(quickstart / "domain.linkml.yaml"),
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


def _quickstart_assess_argv(out: Path, *extra: str) -> list[str]:
    quickstart = _REPO_ROOT / "corpus" / "quickstart"
    return [
        "assess",
        "--bundle",
        str(quickstart / "evidence"),
        "--profile",
        str(quickstart / "applicability.yaml"),
        "--domain",
        str(quickstart / "domain.linkml.yaml"),
        "--out",
        str(out),
        *extra,
    ]


def test_emit_formats_consistent_between_commands_and_report() -> None:
    from agentce import commands
    from agentce.report import EMIT_FORMATS as report_emit_formats

    assert set(commands.EMIT_FORMATS) == set(report_emit_formats)


def test_assess_without_emit_reproduces_the_fixed_bundle(tmp_path: Path) -> None:
    out = tmp_path / "o"
    assert cli.main(_quickstart_assess_argv(out)) == 0
    for name in (
        "assertions.json",
        "report.md",
        "report.html",
        "oscal-ar.json",
        "results.sarif",
        "manifest.json",
    ):
        assert (out / name).is_file(), name
    for name in (
        "report.junit.xml",
        "report.csv",
        "oscal-ar.xml",
        "report.pdf",
        "public-statement.md",
    ):
        assert not (out / name).exists(), name


def test_assess_emit_md_renders_only_report_md(tmp_path: Path) -> None:
    out = tmp_path / "o"
    assert cli.main(_quickstart_assess_argv(out, "--emit", "md")) == 0
    assert (out / "report.md").is_file()
    for name in (
        "report.html",
        "oscal-ar.json",
        "results.sarif",
        "report.junit.xml",
        "report.csv",
        "oscal-ar.xml",
        "report.pdf",
        "public-statement.md",
    ):
        assert not (out / name).exists(), name
    assert not (out / "packs").exists()


def test_assess_emit_new_formats_and_public(tmp_path: Path) -> None:
    out = tmp_path / "o"
    assert (
        cli.main(
            _quickstart_assess_argv(out, "--emit", "junit,csv,oscal_xml,pdf,public")
        )
        == 0
    )
    assert (out / "report.junit.xml").is_file()
    assert (out / "report.csv").is_file()
    assert (out / "oscal-ar.xml").is_file()
    assert (out / "report.pdf").read_bytes().startswith(b"%PDF-")
    matches = [
        p
        for p in out.rglob("*")
        if p.is_file()
        and "Public conformance statement"
        in p.read_text(encoding="utf-8", errors="ignore")
    ]
    assert matches, (
        "no file under the output directory carries the public statement heading"
    )


def test_assess_emit_invalid_token_is_rejected_before_writing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "o"
    code, env = run(
        _quickstart_assess_argv(out, "--emit", "junit,bogus") + ["--json"], capsys
    )
    assert code == 3
    assert env["error"]["key"] == "input.emit_format"
    assert "bogus" in env["error"]["cause"]
    assert not out.exists()


def test_collect_dry_run(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cfg = tmp_path / "collect.yaml"
    cfg.write_text(
        "job:\n"
        "  id: job-1\n"
        "  principal: spiffe://corp/jobs/c\n"
        "sources:\n"
        "  - id: urn:otel:x\n"
        "    adapter: otel-genai\n"
        "    credential: {secret_ref: 'vault://secret/x#token'}\n",
        encoding="utf-8",
    )
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
    # The dry-run bundle records the job identity and resolves no credential.
    assert (tmp_path / "b" / "manifest.json").is_file()


def _fake_adapter_run(events: list[dict[str, Any]]) -> Callable[..., Any]:
    def fake_run(cmd: list[str], **_kwargs: Any) -> Any:
        import subprocess as _subprocess

        return _subprocess.CompletedProcess(
            args=cmd, returncode=0, stdout=json.dumps({"events": events}), stderr=""
        )

    return fake_run


def test_ingest_writes_a_bundle_validate_accepts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from agentce import commands

    export = tmp_path / "export.json"
    export.write_text("{}", encoding="utf-8")
    event = {
        "@context": "https://agent-conformance.org/contexts/evidence/v1",
        "source": "urn:otel:credit-underwriter",
        "agentcesourceclass": "self_report",
    }
    monkeypatch.setattr(commands.subprocess, "run", _fake_adapter_run([event]))
    out = tmp_path / "bundle"
    code, env = run(
        [
            "ingest",
            "--in",
            str(export),
            "--out",
            str(out),
            "--adapter",
            "otel-genai",
            "--adapters-root",
            str(_REPO_ROOT / "adapters"),
            "--json",
        ],
        capsys,
    )
    assert code == 0
    assert env["events"] == 1
    assert (out / "manifest.json").is_file()
    assert (out / "events" / "stream.jsonl").is_file()


def test_ingest_missing_adapter_is_input_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    export = tmp_path / "export.json"
    export.write_text("{}", encoding="utf-8")
    code, env = run(
        ["ingest", "--in", str(export), "--out", str(tmp_path / "b"), "--json"], capsys
    )
    assert code == 3
    assert env["error"]["key"] == "input.adapter_missing"


def test_ingest_unknown_adapter_directory_is_input_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    export = tmp_path / "export.json"
    export.write_text("{}", encoding="utf-8")
    code, env = run(
        [
            "ingest",
            "--in",
            str(export),
            "--out",
            str(tmp_path / "b"),
            "--adapter",
            "no-such-adapter",
            "--adapters-root",
            str(tmp_path / "adapters"),
            "--json",
        ],
        capsys,
    )
    assert code == 3
    assert env["error"]["key"] == "input.adapter_not_found"


def test_collect_real_run_completes_a_source_with_an_export(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from agentce import commands

    export = tmp_path / "export.json"
    export.write_text("{}", encoding="utf-8")
    event = {"source": "urn:otel:x", "agentcesourceclass": "self_report"}
    monkeypatch.setattr(commands.subprocess, "run", _fake_adapter_run([event]))
    cfg = tmp_path / "collect.yaml"
    cfg.write_text(
        "job:\n  id: job-1\n  principal: spiffe://corp/jobs/c\n"
        f"sources:\n  - id: urn:otel:x\n    adapter: otel-genai\n    export: {export}\n",
        encoding="utf-8",
    )
    code, env = run(
        [
            "collect",
            "--config",
            str(cfg),
            "--out",
            str(tmp_path / "b"),
            "--adapters-root",
            str(_REPO_ROOT / "adapters"),
            "--json",
        ],
        capsys,
    )
    assert code == 0
    assert all(s["completeness"] == "complete" for s in env["sources"])


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


# --- 18.9 C4: `catalog lint --support-matrix` wiring (contracts/P18-18.9.md). ---


def test_catalog_lint_support_matrix_matches_catalog_support_view(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from agentce.blind_spots import catalog_support_view
    from agentce.catalog import load_catalog

    base = (
        Path(__file__).resolve().parents[3] / "spec" / "catalogs" / "base" / "eu-ai-act"
    )
    out = tmp_path / "sm.json"
    code, env = run(
        ["catalog", "lint", str(base), "--support-matrix", str(out), "--json"], capsys
    )
    assert code == 0, env
    assert env["support_matrix"] == str(out)
    written = json.loads(out.read_text(encoding="utf-8"))
    catalog = load_catalog(base)
    assert written == {
        "catalog": catalog.id,
        "version": catalog.version,
        "controls": catalog_support_view(catalog),
    }


def test_catalog_lint_support_matrix_refuses_multiple_catalogs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    base = (
        Path(__file__).resolve().parents[3] / "spec" / "catalogs" / "base" / "eu-ai-act"
    )
    parent = tmp_path / "many"
    for name in ("one", "two"):
        (parent / name).mkdir(parents=True)
        for entry in base.iterdir():
            if entry.is_dir():
                (parent / name / entry.name).symlink_to(entry, target_is_directory=True)
            else:
                (parent / name / entry.name).write_bytes(entry.read_bytes())
    out = tmp_path / "sm.json"
    code, env = run(
        ["catalog", "lint", str(parent), "--support-matrix", str(out), "--json"], capsys
    )
    assert code == 3, env
    assert env["error"]["key"] == "catalog.support_matrix_multi"
    assert not out.exists()


def test_catalog_lint_support_matrix_refuses_a_path_inside_the_catalog(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    base = (
        Path(__file__).resolve().parents[3] / "spec" / "catalogs" / "base" / "eu-ai-act"
    )
    catalog_dir = tmp_path / "cat"
    import shutil as _shutil

    _shutil.copytree(base, catalog_dir)
    out = catalog_dir / "sm.json"
    code, env = run(
        ["catalog", "lint", str(catalog_dir), "--support-matrix", str(out), "--json"],
        capsys,
    )
    assert code == 3, env
    assert env["error"]["key"] == "catalog.support_matrix_inside_catalog"
    assert not out.exists()


def test_conformance_run(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    corpus = tmp_path / "c"
    proj = corpus / "projects" / "credit/x/known-pass"
    events = proj / "evidence" / "events"
    events.mkdir(parents=True)
    event = {
        "specversion": "1.0",
        "id": "d1",
        "source": "urn:s",
        "type": "org.agent-conformance.evidence.Decision.v1",
        "time": "2026-05-01T09:00:00.000Z",
        "subject": "spiffe://corp/agents/a",
        "datacontenttype": "application/ld+json",
        "agentcesourceclass": "self_report",
        "data": {
            "@type": "Decision",
            "decision_type": "dom:CreditDecision",
            "agent": {"id": "spiffe://corp/agents/a"},
        },
    }
    stream = events / "s.jsonl"
    stream.write_text(json.dumps(event) + "\n", encoding="utf-8")
    (proj / "evidence" / "manifest.json").write_text(
        json.dumps(
            {
                "agentce_bundle_version": 1,
                "files": [
                    {
                        "path": "events/s.jsonl",
                        "sha256": hashlib.sha256(stream.read_bytes()).hexdigest(),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (proj / "applicability.yaml").write_text(
        'subjects:\n  - id: "spiffe://corp/agents/a"\n    role: "both"\n',
        encoding="utf-8",
    )
    (proj / "domain.linkml.yaml").write_text(
        'decision_types:\n  - id: "dom:CreditDecision"\n'
        '    subclass_of: "agentce:ConsequentialDecision"\n    consequential: true\n',
        encoding="utf-8",
    )
    (corpus / "corpus-manifest.json").write_text(
        json.dumps(
            {
                "corpus_version": "test",
                "projects": [{"id": "credit/x/known-pass", "events": 1}],
            }
        ),
        encoding="utf-8",
    )
    code, env = run(
        [
            "conformance",
            "run",
            "--engine",
            str(_ENGINE),
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
    assert env["claim"] == "full"
    assert env["no_ml"] == "pass"
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


def _diff_pair(
    tmp_path: Path, left_rows: list[dict[str, str]], right_rows: list[dict[str, str]]
) -> tuple[Path, Path]:
    left = tmp_path / "a.json"
    right = tmp_path / "b.json"
    left.write_text(json.dumps(left_rows), encoding="utf-8")
    right.write_text(json.dumps(right_rows), encoding="utf-8")
    return left, right


def test_diff_format_invalid_exits_3(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An unrecognised `--format` value is a keyed `input.diff_format` envelope error raised by
    `cmd_diff` itself, not argparse's own pre-envelope `choices=` usage error and not a silent
    fallback to `text` -- all three engines agree on this (TRADEOFFS.md, 2026-09-30; `report --format`
    is unchanged and still uses argparse's `choices=`, a disclosed, `diff`-only divergence)."""
    left, right = _diff_pair(tmp_path, [], [])
    code, env = run(
        ["diff", str(left), str(right), "--format", "xml", "--json"], capsys
    )
    assert code == 3
    assert env["error"]["key"] == "input.diff_format"


def test_diff_extra_argument_is_keyed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A third positional argument is a keyed `input.diff_extra_argument` envelope error, not
    argparse's own "unrecognized arguments" usage error (TRADEOFFS.md, 2026-09-30)."""
    left, right = _diff_pair(tmp_path, [], [])
    code, env = run(["diff", str(left), str(right), str(left), "--json"], capsys)
    assert code == 3
    assert env["error"]["key"] == "input.diff_extra_argument"


def test_diff_field_not_string_is_keyed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A record whose `control`/`subject`/`outcome` is present but not a string is a keyed
    `input.diff_field_not_string` error, never a silent `str()` coercion (TRADEOFFS.md, 2026-09-30
    -- this row is now decided the opposite way for all three engines: refuse, don't coerce)."""
    left = tmp_path / "a.json"
    right = tmp_path / "b.json"
    left.write_text(
        json.dumps([{"control": 1, "subject": "s1", "outcome": "conformant"}]),
        encoding="utf-8",
    )
    right.write_text("[]", encoding="utf-8")
    code, env = run(["diff", str(left), str(right), "--json"], capsys)
    assert code == 3
    assert env["error"]["key"] == "input.diff_field_not_string"


def test_diff_format_text_unchanged(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--format text` (and no `--format` at all) is today's exact human-note rendering, byte for
    byte, unaffected by the new `what_changed` grouping (round 2 finding: pin the literal lines)."""
    left, right = _diff_pair(
        tmp_path,
        [{"control": "C-01", "subject": "s1", "outcome": "non-conformant"}],
        [{"control": "C-01", "subject": "s1", "outcome": "conformant"}],
    )
    code = cli.main(["diff", str(left), str(right)])
    out = capsys.readouterr().out
    assert code == 1
    assert out == "1 assertion(s) differ:\n  C-01 @ s1: non-conformant -> conformant\n"
    for fmt in (None, "text"):
        left2, right2 = _diff_pair(tmp_path, [], [])
        argv = ["diff", str(left2), str(right2)]
        if fmt:
            argv += ["--format", fmt]
        code2 = cli.main(argv)
        assert code2 == 0
        assert capsys.readouterr().out == "no differences\n"


def test_diff_format_md_empty_group_omitted(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--format md` prints a subsection only for a non-empty group; a run with only a closed change
    has no `### Opened` or `### Other changes` heading at all."""
    left, right = _diff_pair(
        tmp_path,
        [{"control": "C-01", "subject": "s1", "outcome": "non-conformant"}],
        [{"control": "C-01", "subject": "s1", "outcome": "conformant"}],
    )
    code = cli.main(["diff", str(left), str(right), "--format", "md"])
    out = capsys.readouterr().out
    assert code == 1
    assert out == (
        "## What changed\n\n### Closed (1)\n- C-01 @ s1: non-conformant -> conformant\n"
    )
    assert "Opened" not in out
    assert "Other changes" not in out


def test_diff_format_md_no_differences(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    left, right = _diff_pair(tmp_path, [], [])
    code = cli.main(["diff", str(left), str(right), "--format", "md"])
    out = capsys.readouterr().out
    assert code == 0
    assert out == "## What changed\n\nno differences.\n"


def test_diff_md_hostile_id(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A hostile control id survives `--format md` rendering exactly as every other
    Markdown-rendering surface in this repo does: backtick (code-span escape), `<`/`>` (raw HTML),
    and `[`/`]` (link/image syntax) are neutralised to inert lookalikes -- the same fields
    `test_diff_sanitises_all_four_hostile_fields` already exercises for `--format text`."""
    hostile = "ok<br>[x](evil)`y`"
    left, right = _diff_pair(
        tmp_path,
        [{"control": hostile, "subject": "s1", "outcome": "non-conformant"}],
        [{"control": hostile, "subject": "s1", "outcome": "conformant"}],
    )
    code = cli.main(["diff", str(left), str(right), "--format", "md"])
    out = capsys.readouterr().out
    assert code == 1

    from agentce.report import sanitize_for_terminal

    expected_control = sanitize_for_terminal(hostile)
    assert out == (
        "## What changed\n"
        "\n"
        "### Closed (1)\n"
        f"- {expected_control} @ s1: non-conformant -> conformant\n"
    )
    assert "<br>" not in out
    assert "[x](evil)" not in out
    assert "`y`" not in out


@pytest.mark.parametrize("fmt", ["md", "json"])
def test_diff_json_envelope_wins_over_format(
    fmt: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--json` always wins over `--format`: `_emit_result` (`cli.py:391-399`) prints only the JSON
    envelope and ignores every `result.note()` call, so `--format md --json` and `--format json --json`
    each print exactly one JSON document (round 2 finding 5)."""
    left, right = _diff_pair(
        tmp_path,
        [{"control": "C-01", "subject": "s1", "outcome": "non-conformant"}],
        [{"control": "C-01", "subject": "s1", "outcome": "conformant"}],
    )
    code = cli.main(["diff", str(left), str(right), "--format", fmt, "--json"])
    out = capsys.readouterr().out
    assert code == 1
    parsed = json.loads(out)  # exactly one JSON document on stdout, or this raises
    assert parsed["command"] == "diff"
    assert parsed["what_changed"]["closed"][0]["control"] == "C-01"


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


def test_sign_bad_role_gives_keyed_envelope(capsys: pytest.CaptureFixture[str]) -> None:
    """`cli.py`'s `sign` subparser used to declare `choices=SIGN_ROLES`, so an invalid `--as` value
    never reached `cmd_sign`'s own `input.sign_role` check -- argparse raised its own bare usage error
    first (no JSON envelope). This is now a keyed `InputError`, matching `diff --format`'s existing
    no-`choices=` pattern (18.26 round-1/round-2 critic correction, MAINTAINER-NOTES 2026-09-30)."""
    code, env = run(["sign", "/tmp", "--as", "bogus", "--json"], capsys)
    assert code == 3
    assert env["error"]["key"] == "input.sign_role"
    assert env["error"]["cause"] == "--as must be `claimant` or `assessor`."
    assert env["error"]["fix"] == "pass --as claimant|assessor."


def test_sign_bad_profile_gives_keyed_envelope(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, env = run(
        ["sign", "/tmp", "--as", "claimant", "--profile", "bogus", "--json"], capsys
    )
    assert code == 3
    assert env["error"]["key"] == "input.sign_profile"
    assert env["error"]["cause"] == "unknown signing profile 'bogus'."
    assert (
        env["error"]["fix"] == "choose one of: sigstore-public, sigstore-private, kms."
    )


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


def test_quickstart(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, env = run(["quickstart", "--out", str(tmp_path / "qs"), "--json"], capsys)
    assert code == 0
    assert env["quickstart"] == "ok"
    assert (tmp_path / "qs" / "assertions.json").is_file()


def test_init_writes_a_valid_profile(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from agentce.tools.validate_profile import main as validate_main

    code, env = run(
        [
            "init",
            "--non-interactive",
            "--framework",
            "custom-loop",
            "--subject",
            "demo",
            "--role",
            "deployer",
            "--out",
            str(tmp_path / "init"),
            "--json",
        ],
        capsys,
    )
    assert code == 0
    profile = tmp_path / "init" / "agentce" / "applicability.yaml"
    assert env["profile"] == str(profile)
    assert env["domain"] == str(tmp_path / "init" / "agentce" / "domain.linkml.yaml")
    assert validate_main([str(profile)]) == 0  # the generated profile is schema-valid


def test_config_show_reports_sources(capsys: pytest.CaptureFixture[str]) -> None:
    code, env = run(["config", "show", "--json"], capsys)
    assert code == 0
    assert env["values"]
    assert all(v["source"] for v in env["values"])


def test_config_no_action_is_input_error(capsys: pytest.CaptureFixture[str]) -> None:
    code, env = run(["config", "--json"], capsys)
    assert code == 3
    assert env["error"]["key"] == "input.config_action"
