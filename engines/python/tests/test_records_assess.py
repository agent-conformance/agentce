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
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from agentce import bundle, cli
from agentce.tools.validate_profile import validate_profile

_REPO_ROOT = Path(__file__).resolve().parents[3]
_LINT_PROFILE = (
    _REPO_ROOT / "skills" / "agentce-get-evidence" / "scripts" / "lint_profile.py"
)
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


def _multi_agent_records(folder: Path) -> Path:
    """A records folder naming two distinct agents (datadog: fraud-detection-agent, langfuse:
    checkout-copilot) plus one file with no agent id at all (openinference-rag)."""
    folder.mkdir(parents=True, exist_ok=True)
    shutil.copy(_FIXTURES / "datadog" / "input.json", folder / "fraud.json")
    shutil.copy(_FIXTURES / "langfuse" / "input.json", folder / "checkout.json")
    shutil.copy(_FIXTURES / "openinference-rag" / "input.json", folder / "rag.json")
    return folder


def _assertions(out: Path) -> list[dict[str, Any]]:
    loaded: list[dict[str, Any]] = json.loads(
        (out / "assertions.json").read_text(encoding="utf-8")
    )
    return loaded


def _manifest(out: Path) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads((out / "manifest.json").read_text("utf-8"))
    return loaded


def _activity(out: Path) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads((out / "activity.json").read_text("utf-8"))
    return loaded


def _events(out: Path) -> list[dict[str, Any]]:
    """Every event in the run's rebuilt records bundle."""
    return [
        json.loads(line)
        for path in (out / "records-bundle" / "events").glob("*.jsonl")
        for line in path.read_text(encoding="utf-8").splitlines()
    ]


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


def test_the_derived_profile_passes_lint_and_doctor_despite_its_short_window(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A records-folder scan derives a short, exploratory window (the fixtures span minutes, not 90
    days); ``pilot_window: true`` on the derived profile must make it pass every check an adopter
    runs on it (18.32 C1): the skill's own lint, schema validation, and ``agentce doctor``."""
    out = tmp_path / "out"
    _run(["assess", str(_records(tmp_path / "records")), "--out", str(out)], capsys)
    profile_path = out / "applicability.yaml"
    profile = yaml.safe_load(profile_path.read_text(encoding="utf-8"))

    assert profile["pilot_window"] is True
    assert validate_profile(profile) == []

    lint = subprocess.run(
        [sys.executable, str(_LINT_PROFILE), "--profile", str(profile_path), "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert lint.returncode == 0, lint.stdout + lint.stderr
    assert json.loads(lint.stdout)["clean"] is True

    doctor_code, doctor_env = _run(["doctor", "--project", str(out)], capsys)
    problem_keys = {p["key"] for p in doctor_env["problems"]}
    assert "schema_invalid" not in problem_keys, doctor_env["problems"]


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

    events = _events(out)
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


def test_a_records_folder_with_multiple_agents_derives_one_subject_per_agent(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import yaml

    folder = str(_multi_agent_records(tmp_path / "records"))
    out = tmp_path / "out"

    code, env = _run(["assess", folder, "--out", str(out)], capsys)

    assert code == 0
    events = _events(out)
    assert {e["subject"] for e in events} == {
        "spiffe://corp/agents/checkout-copilot",
        "spiffe://corp/agents/fraud-detection-agent",
        "agentce:subject/local",
    }
    profile = yaml.safe_load((out / "applicability.yaml").read_text(encoding="utf-8"))
    assert [s["id"] for s in profile["subjects"]] == [
        "spiffe://corp/agents/checkout-copilot",
        "spiffe://corp/agents/fraud-detection-agent",
        "agentce:subject/local",
    ]


def test_records_folder_multi_agent_for_risk_lead_writes_project_view(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """18.14 C5: the risk-lead/CIO preset over a records folder that discovers more than one agent
    writes the project view -- every discovered agent side by side. A records-folder run cannot know
    which of several discovered agents an adopter meant to declare, so C1 leaves all of them
    undeclared (Hill 7's "undeclared agents listed")."""
    folder = str(_multi_agent_records(tmp_path / "records"))
    out = tmp_path / "out"

    code, env = _run(
        ["assess", folder, "--for", "risk-lead", "--out", str(out)], capsys
    )

    assert code == 0
    for name in ("project.md", "project.html", "project.json"):
        assert (out / name).is_file(), name
    assert (out / "report.md").read_bytes() == (out / "project.md").read_bytes()
    assert (out / "report.html").read_bytes() == (out / "project.html").read_bytes()
    project = json.loads((out / "project.json").read_text(encoding="utf-8"))
    # `agentce:subject/local` (the id-less-events subject) carries no `data.agent.id` of its own, so
    # it is never in any row's `agents_observed` and cannot appear here -- only a subject with a real,
    # observed agent id can (`compute_project_view`'s own definition of "undeclared").
    assert set(project["undeclared_agents"]) == {
        "spiffe://corp/agents/checkout-copilot",
        "spiffe://corp/agents/fraud-detection-agent",
    }
    agent_dirs = sorted(p.name for p in (out / "agents").iterdir())
    assert len(agent_dirs) == 3


def test_a_records_folder_with_one_agent_id_names_that_agent_as_its_subject(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`_records()`'s fixture carries exactly one real `gen_ai.agent.id` (credit-underwriter) alongside
    id-less events: every event goes to one subject, and that subject is the agent the records name,
    so the derived profile declares that agent by its own id."""
    import yaml

    folder = str(_records(tmp_path / "records"))
    out = tmp_path / "out"

    code, env = _run(["assess", folder, "--out", str(out)], capsys)

    assert code == 0
    events = _events(out)
    assert {e["subject"] for e in events} == {"spiffe://corp/agents/credit-underwriter"}
    profile = yaml.safe_load((out / "applicability.yaml").read_text(encoding="utf-8"))
    assert [s["id"] for s in profile["subjects"]] == [
        "spiffe://corp/agents/credit-underwriter"
    ]


def test_a_records_folder_naming_no_agent_keeps_the_default_subject(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """With no `gen_ai.agent.id` anywhere there is no agent to name, so the one subject stays
    `agentce:subject/local` and nothing shows as undeclared."""
    folder = tmp_path / "records"
    folder.mkdir()
    document = (
        (_FIXTURES / "otel-genai-chat" / "input.json")
        .read_text(encoding="utf-8")
        .replace('"gen_ai.agent.id"', '"x.unrelated"')
    )
    (folder / "chat.json").write_text(document, encoding="utf-8")
    out = tmp_path / "out"

    code, env = _run(["assess", str(folder), "--out", str(out)], capsys)

    assert code == 0
    events = _events(out)
    assert {e["subject"] for e in events} == {"agentce:subject/local"}
    activity = _activity(out)
    assert activity["undeclared"]["agents"] == []


def test_a_records_folder_with_one_real_agent_shows_nothing_as_undeclared(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """C1: a records folder naming exactly one real agent id (plus id-less events) is a fresh, unedited
    first run. 18.4's promise ("nothing shows as undeclared") holds for the agent itself, not only for
    tools and models, because the derived profile names that agent."""
    folder = str(_records(tmp_path / "records"))
    out = tmp_path / "out"

    code, env = _run(["assess", folder, "--out", str(out)], capsys)

    assert code == 0
    activity = _activity(out)
    assert activity["undeclared"]["agents"] == []
    report_md = (out / "report.md").read_text(encoding="utf-8")
    assert "Not yet declared in your profile" in report_md
    assert (
        "Agents:"
        not in report_md.split("Not yet declared in your profile", 1)[1].split(
            "Where your records can't show it yet", 1
        )[0]
    )

    # The same promise holds on a re-run with the tool's own, unedited derived profile: it names the
    # agent, so re-feeding it via `--profile` declares exactly that agent.
    out2 = tmp_path / "out2"
    code2, env2 = _run(
        [
            "assess",
            folder,
            "--profile",
            str(out / "applicability.yaml"),
            "--out",
            str(out2),
        ],
        capsys,
    )
    assert code2 == 0
    activity2 = _activity(out2)
    assert activity2["undeclared"]["agents"] == []
    report_md2 = (out2 / "report.md").read_text(encoding="utf-8")
    assert (
        "Not yet declared in your profile" not in report_md2
        or "Agents:"
        not in report_md2.split("Not yet declared in your profile", 1)[1].split(
            "Where your records can't show it yet", 1
        )[0]
    )


@pytest.mark.parametrize("keep_first_agent", [True, False], ids=["grown", "replaced"])
def test_a_new_agent_under_a_re_fed_single_agent_profile_is_undeclared(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], keep_first_agent: bool
) -> None:
    """Verifier round 3's repro and residual: the tool's own derived single-agent profile, re-fed over
    a folder that has since grown a second agent (grown) or whose agent was replaced by a different
    lone one (replaced). The profile names only the first agent, so the new one is undeclared either
    way; counting the agents the folder names now could not tell these two cases apart."""
    records = tmp_path / "records"
    out_a = tmp_path / "out-a"
    _run(["assess", str(_records(records)), "--out", str(out_a)], capsys)

    later = tmp_path / "later"
    if keep_first_agent:
        shutil.copytree(records, later)
    else:
        later.mkdir()
    shutil.copy(_FIXTURES / "datadog" / "input.json", later / "fraud.json")
    out_b = tmp_path / "out-b"
    code, env = _run(
        [
            "assess",
            str(later),
            "--profile",
            str(out_a / "applicability.yaml"),
            "--out",
            str(out_b),
        ],
        capsys,
    )

    assert code == 0
    assert _activity(out_b)["undeclared"]["agents"] == [
        "spiffe://corp/agents/fraud-detection-agent"
    ]


def test_a_real_agent_id_equal_to_the_default_subject_constant_is_not_listed_twice(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """18.14b C2 probe b': a real `gen_ai.agent.id` that happens to equal the synthetic
    `agentce:subject/local` catch-all, alongside a second real agent and id-less events, must not
    make the derived profile declare the same subject id twice (`scan()`'s catch-all is skipped when
    it already matches a real id). The id-less events still merge into that agent's bucket -- an
    accepted limitation of this exact textual collision, not a design error, since there is no other
    named subject to attribute them to."""
    folder = tmp_path / "records"
    folder.mkdir()
    collided = (
        (_FIXTURES / "otel-genai-agent-session" / "input.json")
        .read_text(encoding="utf-8")
        .replace("spiffe://corp/agents/credit-underwriter", "agentce:subject/local")
    )
    (folder / "collided.json").write_text(collided, encoding="utf-8")
    shutil.copy(_FIXTURES / "datadog" / "input.json", folder / "fraud.json")
    shutil.copy(_FIXTURES / "openinference-rag" / "input.json", folder / "rag.json")
    out = tmp_path / "out"

    code, env = _run(["assess", str(folder), "--out", str(out)], capsys)

    assert code == 0
    profile = yaml.safe_load((out / "applicability.yaml").read_text(encoding="utf-8"))
    subject_ids = [s["id"] for s in profile["subjects"]]
    assert subject_ids == sorted(set(subject_ids)), subject_ids
    assert set(subject_ids) == {
        "agentce:subject/local",
        "spiffe://corp/agents/fraud-detection-agent",
    }
    assert _activity(out)["undeclared"]["agents"] == subject_ids


def test_renamed_and_reordered_files_give_the_same_result_with_multiple_agents(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    one = _multi_agent_records(tmp_path / "one")
    two = tmp_path / "two"
    two.mkdir()
    for index, name in enumerate(["fraud.json", "checkout.json", "rag.json"]):
        shutil.copy(one / name, two / f"{9 - index}-{name}")
    _, first = _run(["assess", str(one), "--out", str(tmp_path / "o1")], capsys)
    _, second = _run(["assess", str(two), "--out", str(tmp_path / "o2")], capsys)
    assert first["bundle_digest"] == second["bundle_digest"]


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


def test_an_output_folder_above_the_records_folder_still_finds_the_records(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    traces = _records(project / "traces")
    monkeypatch.chdir(project)
    code, env = _run(["assess", "traces", "--out", "."], capsys)
    assert code == 0 and len(env["records"]["files"]) == 3

    assert traces.is_dir()


def test_an_output_folder_that_would_overwrite_the_records_is_refused_and_nothing_is_deleted(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "out"
    bundle = _records(out / "records-bundle")
    inside = _records(out / "records-bundle" / "traces")
    before = {p: p.read_bytes() for p in bundle.rglob("*") if p.is_file()}
    for folder, out_arg in ((bundle, str(out)), (inside, str(out))):
        code, env = _run(["assess", str(folder), "--out", out_arg], capsys)
        assert code == 3 and env["error"]["key"] == "input.records_out_collides"
    monkeypatch.chdir(bundle)
    code, env = _run(["assess", ".", "--out", "."], capsys)
    assert code == 3 and env["error"]["key"] == "input.records_out_collides"
    assert {p: p.read_bytes() for p in bundle.rglob("*") if p.is_file()} == before


def test_a_different_spelling_of_the_same_folder_is_still_a_collision(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    records = _records(tmp_path / "traces")
    if not (tmp_path / "TRACES").exists():
        pytest.skip("this file system tells folder names apart by case")
    before = {p: p.read_bytes() for p in records.rglob("*") if p.is_file()}
    code, env = _run(
        ["assess", str(records), "--out", str(tmp_path / "TRACES")], capsys
    )
    assert code == 3 and env["error"]["key"] == "input.records_out_collides"

    out = tmp_path / "out"
    stale = _records(out / "records-bundle")
    (stale / "manifest.json").write_text("{}", encoding="utf-8")
    before |= {p: p.read_bytes() for p in stale.rglob("*") if p.is_file()}
    code, env = _run(["assess", str(out / "Records-Bundle"), "--out", str(out)], capsys)
    assert code == 3 and env["error"]["key"] == "input.records_out_collides"
    assert {p: p.read_bytes() for p in before} == before


def test_a_records_run_that_judges_nothing_renders_no_report(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("agentce.commands.evaluated_nothing", lambda _a: True)
    out = tmp_path / "out"
    code, env = _run(
        ["assess", str(_records(tmp_path / "records")), "--out", str(out)], capsys
    )
    assert code == 3 and env["error"]["key"] == "input.nothing_evaluated"
    assert not (out / "report.md").exists() and not (out / "report.html").exists()


def test_a_profile_that_declares_no_subject_is_refused_before_anything_is_written(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    empty = tmp_path / "none.yaml"
    empty.write_text("profile_version: 1\nsubjects: []\n", encoding="utf-8")
    out = tmp_path / "out"
    code, env = _run(
        [
            "assess",
            str(_records(tmp_path / "records")),
            "--profile",
            str(empty),
            "--out",
            str(out),
        ],
        capsys,
    )
    assert code == 3 and env["error"]["key"] == "input.profile_invalid"
    assert not out.exists()


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


def test_a_default_output_folder_holding_records_is_refused_and_no_record_is_overwritten(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = _records(tmp_path / "records")
    user_out = folder / "out"
    user_out.mkdir()
    kept = user_out / "manifest.json"
    kept.write_text(
        (next(folder.glob("*.json"))).read_text(encoding="utf-8"), encoding="utf-8"
    )
    before = kept.read_bytes()
    monkeypatch.chdir(folder)

    code, _ = _run(["assess", "."], capsys)

    assert code == 3
    assert kept.read_bytes() == before
    assert not (user_out / "records-bundle").exists()


def test_an_output_folder_that_is_the_records_folder_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = _records(tmp_path / "records")
    before = {p.name: p.read_bytes() for p in folder.iterdir() if p.is_file()}

    code, _ = _run(["assess", str(folder), "--out", str(folder)], capsys)

    assert code == 3
    assert {p.name: p.read_bytes() for p in folder.iterdir() if p.is_file()} == before


def test_compressed_files_and_symlinked_folders_are_listed_as_not_read(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = _records(tmp_path / "records")
    (folder / "old.json.gz").write_bytes(b"\x1f\x8b")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (folder / "linked").symlink_to(elsewhere, target_is_directory=True)

    code, envelope = _run(
        ["assess", str(folder), "--out", str(tmp_path / "out")], capsys
    )

    reasons = {u["path"]: u["reason"] for u in envelope["records"]["unrecognised"]}
    assert code == 0
    assert "decompress" in reasons["old.json.gz"]
    assert "symlinked folder" in reasons["linked"]
