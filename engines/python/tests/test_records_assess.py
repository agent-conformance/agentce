"""``agentce assess <folder>``: a folder of trace exports in, a report out, no other flag (SPEC §13.4 AX-1).

The folder holds OpenTelemetry GenAI and OpenInference exports (one OTLP/JSON document per ``.json`` file,
one per line in ``.jsonl``). The engine reads them through the vendored ``otel-genai`` adapter, derives an
evidence bundle and a default profile, and evaluates the baseline lens. What it cannot read is listed with a
reason, never dropped, and a folder it can read nothing from is refused before anything is written.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

import pytest

from agentce import bundle, cli
from agentce.tools.validate_profile import validate_profile

_REPO_ROOT = Path(__file__).resolve().parents[3]
_FIXTURES = _REPO_ROOT / "adapters" / "otel-genai" / "fixtures"
_ADAPTER_SOURCE = (
    _REPO_ROOT
    / "adapters"
    / "otel-genai"
    / "src"
    / "agentce_adapters"
    / "otel_genai.py"
)
_VENDORED = Path(cli.__file__).resolve().parent / "records" / "otel_genai.py"
_BASELINE = "baseline@2026.09"


def _run(
    argv: list[str], capsys: pytest.CaptureFixture[str]
) -> tuple[int, dict[str, Any]]:
    code = cli.main([*argv, "--json"])
    return code, json.loads(capsys.readouterr().out)


def _records(folder: Path) -> Path:
    """A records folder: an OTel GenAI export (.json), an OpenInference export (.json), and the same
    OTel export again as JSON Lines (one document per line)."""
    folder.mkdir(parents=True, exist_ok=True)
    shutil.copy(
        _FIXTURES / "otel-genai-agent-session" / "input.json", folder / "session.json"
    )
    shutil.copy(_FIXTURES / "openinference-rag" / "input.json", folder / "rag.json")
    document = json.loads(
        (_FIXTURES / "otel-genai-chat" / "input.json").read_text(encoding="utf-8")
    )
    (folder / "chat.jsonl").write_text(
        json.dumps(document) + "\n\n" + json.dumps(document) + "\n", encoding="utf-8"
    )
    return folder


def _assertions(out: Path) -> list[dict[str, Any]]:
    loaded: list[dict[str, Any]] = json.loads(
        (out / "assertions.json").read_text(encoding="utf-8")
    )
    return loaded


def _manifest(out: Path) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads((out / "manifest.json").read_text("utf-8"))
    return loaded


def test_a_records_folder_is_assessed_with_no_flag(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "out"
    code, env = _run(
        ["assess", str(_records(tmp_path / "records")), "--out", str(out)], capsys
    )

    assert code == 0
    records = env["records"]
    assert [f["path"] for f in records["files"]] == [
        "chat.jsonl",
        "rag.json",
        "session.json",
    ]
    assert records["unrecognised"] == []
    assert records["events"] == env["accepted"] > 0 and env["quarantined"] == 0
    assert {"otel-genai:1.36", "openinference:0.1.14"} <= set(records["conventions"])
    assert [
        c["id"] + "@" + c["version"] for c in _manifest(out)["inputs"]["catalogs"]
    ] == [_BASELINE]
    for name in ("report.md", "report.html", "assertions.json", "applicability.yaml"):
        assert (out / name).is_file(), name
    assert (out / "records-bundle" / "manifest.json").is_file()


def test_the_repeated_json_lines_document_is_counted_once(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = _records(tmp_path / "records")
    code, env = _run(["assess", str(folder), "--out", str(tmp_path / "out")], capsys)
    assert code == 0 and env["quarantined"] == 0
    assert env["records"]["duplicates"] > 0


def test_records_alone_do_not_declare_what_the_agent_decides(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "out"
    _run(["assess", str(_records(tmp_path / "records")), "--out", str(out)], capsys)

    outcomes = {a["outcome"] for a in _assertions(out)}
    assert outcomes == {"insufficient_evidence"}, (
        "an empty population is not 'not applicable'"
    )
    limitations = _manifest(out)["limitations"]
    assert len(limitations) == 1 and "trace records only" in limitations[0]
    assert (
        "insufficient evidence"
        in (out / "report.md").read_text(encoding="utf-8").lower()
    )


def test_the_default_profile_is_schema_valid_and_names_the_baseline(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import yaml

    out = tmp_path / "out"
    _run(["assess", str(_records(tmp_path / "records")), "--out", str(out)], capsys)
    profile = yaml.safe_load((out / "applicability.yaml").read_text(encoding="utf-8"))

    assert profile["catalogs"] == [_BASELINE]
    assert profile["subjects"][0]["evidence_sources"], (
        "every source the records name is declared"
    )
    assert {s["class"] for s in profile["subjects"][0]["evidence_sources"]} == {
        "self_report"
    }
    assert validate_profile(profile) == []


def test_files_that_are_not_records_are_listed_with_a_reason(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = _records(tmp_path / "records")
    (folder / "notes.json").write_text('{"hello": "world"}', encoding="utf-8")
    (folder / "broken.json").write_text("{not json", encoding="utf-8")
    (folder / "list.json").write_text("[1, 2]", encoding="utf-8")
    (folder / "deep.json").write_text("[" * 100000 + "]" * 100000, encoding="utf-8")
    (folder / "binary.jsonl").write_bytes(b"\xff\xfe\x00\x01\n")
    (folder / "readme.txt").write_text("not a candidate", encoding="utf-8")
    (folder / ".hidden.json").write_text("{}", encoding="utf-8")

    code, env = _run(["assess", str(folder), "--out", str(tmp_path / "out")], capsys)

    assert code == 0
    unread = {u["path"]: u["reason"] for u in env["records"]["unrecognised"]}
    assert sorted(unread) == [
        "binary.jsonl",
        "broken.json",
        "deep.json",
        "list.json",
        "notes.json",
    ]
    assert all(unread.values())
    assert "nested too deeply" in unread["deep.json"]
    assert len(env["records"]["files"]) == 3


def test_a_folder_with_no_recognised_record_is_refused_and_writes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = tmp_path / "records"
    folder.mkdir()
    (folder / "notes.json").write_text('{"hello": "world"}', encoding="utf-8")
    out = tmp_path / "out"

    code, env = _run(["assess", str(folder), "--out", str(out)], capsys)

    assert code == 3 and env["error"]["key"] == "input.records_none_recognised"
    assert not out.exists()


def test_an_empty_folder_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "records").mkdir()
    code, env = _run(
        ["assess", str(tmp_path / "records"), "--out", str(tmp_path / "out")], capsys
    )
    assert code == 3 and env["error"]["key"] == "input.records_none_recognised"


def test_spans_the_adapter_does_not_map_are_not_a_recognised_record(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = tmp_path / "records"
    folder.mkdir()
    document = {
        "resourceSpans": [
            {
                "resource": {"attributes": []},
                "scopeSpans": [
                    {
                        "spans": [
                            {
                                "traceId": "aa" * 16,
                                "spanId": "bb" * 8,
                                "name": "GET /health",
                                "startTimeUnixNano": "1734200000000000000",
                                "endTimeUnixNano": "1734200001000000000",
                            }
                        ]
                    }
                ],
            }
        ]
    }
    (folder / "http.json").write_text(json.dumps(document), encoding="utf-8")
    out = tmp_path / "out"
    code, env = _run(["assess", str(folder), "--out", str(out)], capsys)
    assert code == 3 and env["error"]["key"] == "input.records_no_genai_spans"
    assert "GenAI" in env["error"]["fix"] and not out.exists()


@pytest.mark.parametrize("target", ["missing", "a-file"])
def test_a_folder_that_is_not_a_directory_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], target: str
) -> None:
    path = tmp_path / target
    if target == "a-file":
        path.write_text("{}", encoding="utf-8")
    code, env = _run(["assess", str(path), "--out", str(tmp_path / "out")], capsys)
    assert code == 3 and env["error"]["key"] == "input.records_not_a_directory"


def test_a_folder_and_a_bundle_together_are_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "out"
    code, env = _run(
        [
            "assess",
            str(_records(tmp_path / "records")),
            "--bundle",
            str(tmp_path),
            "--out",
            str(out),
        ],
        capsys,
    )
    assert code == 3 and env["error"]["key"] == "input.records_source_ambiguous"
    assert not out.exists()


def test_a_run_refused_after_reading_the_folder_writes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "out"
    folder = str(_records(tmp_path / "records"))
    for extra in (
        ["--catalog", "no-such@1"],
        ["--emit", "nope"],
        ["--fail-on", "x ~ y"],
    ):
        code, _ = _run(["assess", folder, "--out", str(out), *extra], capsys)
        assert code == 3, extra
        assert not out.exists(), extra


def test_a_symlink_that_leaves_the_folder_is_not_read(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = _records(tmp_path / "records")
    outside = tmp_path / "outside.json"
    shutil.copy(_FIXTURES / "otel-genai-chat" / "input.json", outside)
    (folder / "link.json").symlink_to(outside)

    code, env = _run(["assess", str(folder), "--out", str(tmp_path / "out")], capsys)

    assert code == 0
    assert [u["path"] for u in env["records"]["unrecognised"]] == ["link.json"]
    assert "link.json" not in [f["path"] for f in env["records"]["files"]]


def test_a_file_over_the_size_limit_is_not_read(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = _records(tmp_path / "records")
    big = folder / "big.json"
    shutil.copy(_FIXTURES / "otel-genai-agent-session" / "input.json", big)
    monkeypatch.setattr(
        bundle, "DEFAULT_MAX_MANIFEST_FILE_BYTES", big.stat().st_size - 1
    )

    code, env = _run(["assess", str(folder), "--out", str(tmp_path / "out")], capsys)

    unread = {u["path"]: u["reason"] for u in env["records"]["unrecognised"]}
    assert code == 0
    assert "over the" in unread["big.json"]


def test_the_same_records_give_the_same_result_wherever_the_folder_lives(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    first = _records(tmp_path / "one")
    second = tmp_path / "elsewhere" / "two"
    shutil.copytree(first, second)
    _, env_one = _run(["assess", str(first), "--out", str(tmp_path / "out1")], capsys)
    _, env_two = _run(["assess", str(second), "--out", str(tmp_path / "out2")], capsys)

    assert env_one["bundle_digest"] == env_two["bundle_digest"]
    assert (tmp_path / "out1" / "assertions.json").read_bytes() == (
        tmp_path / "out2" / "assertions.json"
    ).read_bytes()


def test_an_output_folder_inside_the_records_is_not_read_back_as_records(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = _records(tmp_path / "records")
    monkeypatch.chdir(folder)
    _, first = _run(["assess", "."], capsys)
    code, second = _run(["assess", "."], capsys)

    assert code == 0 and (folder / "out" / "report.md").is_file()
    assert second["records"] == first["records"]
    assert second["records"]["unrecognised"] == []


def test_a_named_catalog_and_an_explicit_profile_are_honoured(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = str(_records(tmp_path / "records"))
    out = tmp_path / "out"
    _run(["assess", folder, "--out", str(out)], capsys)

    named = tmp_path / "named"
    code, _ = _run(
        ["assess", folder, "--catalog", "eu-ai-act@2026.09", "--out", str(named)],
        capsys,
    )
    assert code == 0
    assert [c["id"] for c in _manifest(named)["inputs"]["catalogs"]] == ["eu-ai-act"]

    # A profile the adopter supplies is theirs: passed back it is neither rewritten nor turned into
    # "not applicable" verdicts over records that hold no decisions.
    declared = tmp_path / "mine.yaml"
    declared.write_bytes((out / "applicability.yaml").read_bytes())
    mine = tmp_path / "mine"
    code, _ = _run(
        ["assess", folder, "--profile", str(declared), "--out", str(mine)], capsys
    )
    assert code == 0
    assert not (mine / "applicability.yaml").exists()
    assert {a["outcome"] for a in _assertions(mine)} == {"insufficient_evidence"}
    assert "Conformant —" not in (mine / "report.md").read_text(encoding="utf-8")
    manifest = _manifest(mine)
    assert len(manifest["limitations"]) == 1
    assert manifest["run"]["invocation"][:2] == ["assess", "records"]


def test_passing_the_derived_profile_back_leaves_it_as_it_was(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = _records(tmp_path / "records")
    monkeypatch.chdir(tmp_path)
    _run(["assess", str(folder)], capsys)
    profile = tmp_path / "out" / "applicability.yaml"
    profile.write_text(
        profile.read_text(encoding="utf-8").replace("Agent records", "My edited agent"),
        encoding="utf-8",
    )
    before = profile.read_bytes()

    code, _ = _run(["assess", str(folder), "--profile", str(profile)], capsys)

    assert code == 0 and profile.read_bytes() == before


def test_the_folder_argument_leaves_the_bundle_and_profile_flags_working(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    quick = _REPO_ROOT / "corpus" / "quickstart"
    code, env = _run(
        [
            "assess",
            "--bundle",
            str(quick / "evidence"),
            "--profile",
            str(quick / "applicability.yaml"),
            "--domain",
            str(quick / "domain.linkml.yaml"),
            "--out",
            str(tmp_path / "out"),
        ],
        capsys,
    )
    assert code in (0, 1) and "records" not in env
    code, env = _run(["assess", "--out", str(tmp_path / "out2")], capsys)
    assert code == 3 and env["error"]["key"] == "input.bundle_missing"


def test_the_vendored_adapter_is_identical_to_the_adapter_source() -> None:
    assert _VENDORED.read_bytes() == _ADAPTER_SOURCE.read_bytes(), (
        "re-sync with `cp adapters/otel-genai/src/agentce_adapters/otel_genai.py "
        "engines/python/agentce/records/otel_genai.py`"
    )


def _with_service_name(folder: Path, name: str) -> None:
    document = json.loads(
        (_FIXTURES / "otel-genai-chat" / "input.json").read_text(encoding="utf-8")
    )
    for resource in document["resourceSpans"]:
        resource["resource"]["attributes"] = [
            {"key": "service.name", "value": {"stringValue": name}}
        ]
    (folder / "hostile.json").write_text(json.dumps(document), encoding="utf-8")


@pytest.mark.parametrize(
    "name", ["../../../evil", "a/b\\c", "x" * 5000, "\u0000nul", ".."]
)
def test_a_hostile_service_name_cannot_choose_where_the_bundle_writes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], name: str
) -> None:
    folder = tmp_path / "records"
    folder.mkdir()
    _with_service_name(folder, name)
    out = tmp_path / "deep" / "out"
    code, _ = _run(["assess", str(folder), "--out", str(out)], capsys)

    assert code == 0
    written = sorted(
        p for p in (tmp_path).rglob("*") if p.is_file() and "records" != p.parent.name
    )
    assert all(out in p.parents for p in written), [
        str(p) for p in written if out not in p.parents
    ]
    assert all(len(p.name) < 100 for p in written)


def test_the_headline_never_claims_conformance_for_records_that_judge_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "out"
    code, _ = _run(
        ["assess", str(_records(tmp_path / "records")), "--out", str(out)], capsys
    )
    report = (out / "report.md").read_text(encoding="utf-8")

    assert code == 0
    assert "Conformant —" not in report
    assert "Incomplete" in report


def test_a_profile_with_one_subject_gives_the_records_that_subject(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import yaml

    folder = str(_records(tmp_path / "records"))
    first = tmp_path / "first"
    _run(["assess", folder, "--out", str(first)], capsys)
    profile = yaml.safe_load((first / "applicability.yaml").read_text(encoding="utf-8"))
    profile["subjects"][0]["id"] = "spiffe://corp/agents/mine"
    mine = tmp_path / "mine.yaml"
    mine.write_text(yaml.safe_dump(profile), encoding="utf-8")

    out = tmp_path / "out"
    _run(["assess", folder, "--profile", str(mine), "--out", str(out)], capsys)

    events = [
        json.loads(line)
        for path in (out / "records-bundle" / "events").glob("*.jsonl")
        for line in path.read_text(encoding="utf-8").splitlines()
    ]
    assert {e["subject"] for e in events} == {"spiffe://corp/agents/mine"}
    assert {a["subject"] for a in _assertions(out)} == {"spiffe://corp/agents/mine"}


def test_a_profile_with_several_subjects_is_refused_for_a_records_folder(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import yaml

    folder = str(_records(tmp_path / "records"))
    first = tmp_path / "first"
    _run(["assess", folder, "--out", str(first)], capsys)
    profile = yaml.safe_load((first / "applicability.yaml").read_text(encoding="utf-8"))
    profile["subjects"].append(
        {**profile["subjects"][0], "id": "spiffe://corp/agents/other"}
    )
    two = tmp_path / "two.yaml"
    two.write_text(yaml.safe_dump(profile), encoding="utf-8")

    out = tmp_path / "out"
    code, env = _run(
        ["assess", folder, "--profile", str(two), "--out", str(out)], capsys
    )
    assert code == 3 and env["error"]["key"] == "input.records_subject_ambiguous"
    assert not out.exists()


def test_a_file_with_a_byte_order_mark_is_read(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = tmp_path / "records"
    folder.mkdir()
    raw = (_FIXTURES / "otel-genai-chat" / "input.json").read_bytes()
    (folder / "bom.json").write_bytes(b"\xef\xbb\xbf" + raw)
    code, env = _run(["assess", str(folder), "--out", str(tmp_path / "out")], capsys)
    assert code == 0 and [f["path"] for f in env["records"]["files"]] == ["bom.json"]


def test_a_file_that_cannot_be_read_is_listed_not_fatal(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = _records(tmp_path / "records")
    locked = folder / "locked.json"
    shutil.copy(folder / "rag.json", locked)
    locked.chmod(0)
    try:
        if locked.stat().st_mode & 0o444 or os.access(locked, os.R_OK):
            pytest.skip("this user can read a mode-000 file")
        code, env = _run(
            ["assess", str(folder), "--out", str(tmp_path / "out")], capsys
        )
    finally:
        locked.chmod(0o644)
    assert code == 0
    unread = {u["path"]: u["reason"] for u in env["records"]["unrecognised"]}
    assert "could not be read" in unread["locked.json"]


def test_an_output_folder_that_is_the_records_folder_or_above_it_still_finds_the_records(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    traces = _records(project / "traces")
    monkeypatch.chdir(project)
    code, env = _run(["assess", "traces", "--out", "."], capsys)
    assert code == 0 and len(env["records"]["files"]) == 3

    same = tmp_path / "same"
    _records(same)
    code, env = _run(["assess", str(same), "--out", str(same)], capsys)
    assert code == 0 and len(env["records"]["files"]) == 3
    assert traces.is_dir()


def test_hidden_directories_are_not_walked(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = _records(tmp_path / "records")
    for hidden in (".venv", ".git"):
        (folder / hidden).mkdir()
        shutil.copy(folder / "rag.json", folder / hidden / "trace.json")
        (folder / hidden / "package.json").write_text("{}", encoding="utf-8")
    _, env = _run(["assess", str(folder), "--out", str(tmp_path / "out")], capsys)
    assert [f["path"] for f in env["records"]["files"]] == [
        "chat.jsonl",
        "rag.json",
        "session.json",
    ]
    assert env["records"]["unrecognised"] == []


def test_a_rerun_into_the_same_output_leaves_no_stale_bundle_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "out"
    _run(["assess", str(_records(tmp_path / "many")), "--out", str(out)], capsys)
    single = tmp_path / "single"
    single.mkdir()
    shutil.copy(_FIXTURES / "openinference-rag" / "input.json", single / "rag.json")
    _run(["assess", str(single), "--out", str(out)], capsys)

    listed = {f["path"] for f in _manifest_of_bundle(out)["files"]}
    on_disk = {
        p.relative_to(out / "records-bundle").as_posix()
        for p in (out / "records-bundle" / "events").glob("*")
    }
    assert listed == on_disk and len(listed) == 1


def _manifest_of_bundle(out: Path) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(
        (out / "records-bundle" / "manifest.json").read_text(encoding="utf-8")
    )
    return loaded


def test_bad_lines_of_a_json_lines_file_are_named_with_their_line_number(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = tmp_path / "records"
    folder.mkdir()
    document = (_FIXTURES / "otel-genai-chat" / "input.json").read_text(
        encoding="utf-8"
    )
    (folder / "mixed.jsonl").write_text(
        "not json\n" + json.dumps(json.loads(document)) + "\n[1]\n", encoding="utf-8"
    )
    _, env = _run(["assess", str(folder), "--out", str(tmp_path / "out")], capsys)

    assert env["records"]["lines_unrecognised"] == 2
    assert [(b["path"], b["line"]) for b in env["records"]["unrecognised_lines"]] == [
        ("mixed.jsonl", 1),
        ("mixed.jsonl", 3),
    ]


def test_a_file_name_cannot_drive_the_terminal(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = _records(tmp_path / "records")
    (folder / "bad\x1b[31m.json").write_text("{not json", encoding="utf-8")
    code = cli.main(["assess", str(folder), "--out", str(tmp_path / "out")])
    captured = capsys.readouterr()
    assert code == 0
    assert "\x1b" not in captured.out + captured.err
    assert "not read: bad\\x1b[31m.json" in captured.out + captured.err


def test_events_that_outgrow_the_file_limit_are_refused_before_anything_is_written(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = tmp_path / "records"
    folder.mkdir()
    shutil.copy(
        _FIXTURES / "otel-genai-agent-session" / "input.json", folder / "a.json"
    )
    shutil.copy(_FIXTURES / "otel-genai-chat" / "input.json", folder / "b.json")
    # Each file fits; the one source's combined event stream does not.
    monkeypatch.setattr(bundle, "DEFAULT_MAX_MANIFEST_FILE_BYTES", 5900)
    out = tmp_path / "out"

    code, env = _run(["assess", str(folder), "--out", str(out)], capsys)

    assert code == 3 and env["error"]["key"] == "input.bundle_manifest_file_too_large"
    assert not out.exists()


def test_renamed_and_reordered_files_give_the_same_result(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    one = _records(tmp_path / "one")
    two = tmp_path / "two"
    two.mkdir()
    for index, name in enumerate(["session.json", "rag.json", "chat.jsonl"]):
        shutil.copy(one / name, two / f"{9 - index}-{name}")
    _, first = _run(["assess", str(one), "--out", str(tmp_path / "o1")], capsys)
    _, second = _run(["assess", str(two), "--out", str(tmp_path / "o2")], capsys)
    assert first["bundle_digest"] == second["bundle_digest"]
