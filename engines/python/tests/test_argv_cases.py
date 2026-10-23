"""Every case in ``spec/cli/argv-cases.json`` (SPEC 8.5, "Command-line grammar") through the real
scanner and the real CLI entry: exit code and key as the table says, the parsed values of an accept
case, and a refused run that leaves its report directory byte-identical (18.184w)."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path
from typing import Any

import pytest

from agentce import argv, cli

REPO = Path(__file__).resolve().parents[3]
CASES: list[dict[str, Any]] = json.loads(
    (REPO / "spec" / "cli" / "argv-cases.json").read_text(encoding="utf-8")
)["cases"]
PYTHON_CASES = [c for c in CASES if "python" in c.get("engines", ["python"])]
KEY = re.compile(r"^error \[([^\]]+)\]", re.MULTILINE)


@pytest.fixture(scope="module")
def places(tmp_path_factory: pytest.TempPathFactory) -> dict[str, str]:
    root = tmp_path_factory.mktemp("argv-cases")
    report = root / "report"
    assert cli.main(["quickstart", "--out", str(report)]) == 0
    space = root / "sp ace"
    space.mkdir()
    shutil.copytree(REPO / "spec" / "catalogs" / "base" / "eu-ai-act", space / "cat")
    (space / "gaps.md").write_text("", encoding="utf-8")
    return {"{report}": str(report), "{space}": str(space)}


def _fill(value: Any, places: dict[str, str]) -> Any:
    if isinstance(value, str):
        for name, path in places.items():
            value = value.replace(name, path)
        return value
    if isinstance(value, list):
        return [_fill(v, places) for v in value]
    if isinstance(value, dict):
        return {k: _fill(v, places) for k, v in value.items()}
    return value


def _digest(directory: Path) -> dict[str, str]:
    return {
        str(p.relative_to(directory)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(directory.rglob("*"))
        if p.is_file()
    }


@pytest.mark.parametrize("case", PYTHON_CASES, ids=[c["id"] for c in PYTHON_CASES])
def test_case_through_the_cli(
    case: dict[str, Any],
    places: dict[str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    line = _fill(case["argv"], places)
    unchanged = Path(_fill(case["unchanged"], places)) if "unchanged" in case else None
    before = _digest(unchanged) if unchanged else None
    code = cli.main(line)
    out, err = capsys.readouterr()
    head = line[: line.index("--")] if "--" in line else line
    if "--json" in head and code != 0:
        key = json.loads(out)["error"]["key"]
    else:
        found = KEY.search(err)
        key = found.group(1) if found else None
    assert (code, key) == (case["exit"], case["key"]), (out, err)
    if case["rule"] == "help_after_command":
        assert out.startswith("usage: agentce"), out
    if unchanged:
        assert _digest(unchanged) == before


SCAN_CASES = [c for c in PYTHON_CASES if "scan" in c]


@pytest.mark.parametrize("case", SCAN_CASES, ids=[c["id"] for c in SCAN_CASES])
def test_accept_case_reads_its_values(
    case: dict[str, Any], places: dict[str, str]
) -> None:
    line = _fill(case["argv"], places)
    at = next(i for i, t in enumerate(line) if not t.startswith("-"))
    read = argv.scan(argv.command(line[at]), line[at + 1 :], seen=line[:at])
    for field, want in _fill(case["scan"], places).items():
        assert getattr(read, field) == want, field


def test_every_rule_has_a_case() -> None:
    rules = set(argv.grammar()["rules"])
    assert rules == {c["rule"] for c in CASES}
    assert set(argv.grammar()["commands"]) <= {
        t for c in CASES for t in c["argv"] if not t.startswith("-")
    }
