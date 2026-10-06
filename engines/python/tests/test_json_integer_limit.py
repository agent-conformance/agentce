"""An integer literal over 4300 digits is invalid JSON at every call site, in every interpreter setting.

CPython's ``json.loads`` refuses such a literal with a bare ``ValueError``, not a ``JSONDecodeError``,
and only under the default ``sys.int_max_str_digits``. Java's ``Json.parse`` and TypeScript's
``parseJson`` refuse it as invalid JSON, so ingest quarantines the line and ``report --validate`` names
the file. These tests feed a 5000-digit literal to each call site that used to catch only
``JSONDecodeError`` (item 18.71).
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from agentce import conformance
from agentce.assertions import Assertion, EvidencePointer
from agentce.bundle import load_bundle
from agentce.catalog import Catalog
from agentce.commands import _adapt_export
from agentce.errors import InputError
from agentce.ingest import ingest
from agentce.quarantine import QuarantineReason
from agentce.report import _recorded_outputs, validate_report, write_report
from agentce.safe_json import JSONError, load_json

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PACKAGE = Path(__file__).resolve().parents[1] / "agentce"
BIG = "9" * 5000
TOO_LONG = "integer literal exceeds 4300 digits"


@pytest.fixture(params=[4300, 0, 640], ids=["default", "unlimited", "lowest"])
def int_digits(request: pytest.FixtureRequest) -> Iterator[int]:
    """Run the test under the default limit, no limit, and the lowest limit the interpreter allows."""
    before = sys.get_int_max_str_digits()
    sys.set_int_max_str_digits(request.param)
    try:
        yield request.param
    finally:
        sys.set_int_max_str_digits(before)


def test_load_json_reads_up_to_4300_digits(int_digits: int) -> None:
    assert load_json('{"n": %s}' % ("7" * 4300))["n"] == _big("7" * 4300)
    assert load_json("-" + "8" * 4300) == -_big("8" * 4300)
    assert load_json("[-0, 0, 12, -34]") == [0, 0, 12, -34]
    assert load_json(b'{"n": 5}') == {"n": 5}


def test_load_json_refuses_4301_digits(int_digits: int) -> None:
    for text in ("9" * 4301, "-" + "9" * 4301, '{"a": [1, %s]}' % BIG):
        with pytest.raises(JSONError) as caught:
            load_json(text)
        assert caught.value.msg == TOO_LONG
        assert str(caught.value) == TOO_LONG


def test_load_json_leaves_strings_and_floats_alone(int_digits: int) -> None:
    assert load_json('"%s"' % BIG) == BIG
    assert load_json("1.5e" + "0" * 5000) == 1.5
    assert load_json("0.%s" % BIG) == pytest.approx(1.0)


def test_load_json_keeps_the_decoder_message_for_syntax_errors(int_digits: int) -> None:
    with pytest.raises(JSONError) as caught:
        load_json('{"n": }')
    assert caught.value.msg == "Expecting value"
    assert str(caught.value) == "Expecting value: line 1 column 7 (char 6)"
    # The first error in document order wins, whichever pass finds it.
    with pytest.raises(JSONError) as first:
        load_json("[%s, }" % BIG)
    assert first.value.msg == TOO_LONG
    with pytest.raises(JSONError) as syntax_first:
        load_json("[}, %s]" % BIG)
    assert syntax_first.value.msg == "Expecting value"


def test_load_json_lets_invalid_utf8_through() -> None:
    with pytest.raises(UnicodeDecodeError):
        load_json(b'{"n": "\xff"}')


def test_ingest_quarantines_an_over_limit_line(
    make_bundle: Callable[..., Any], example_event: dict[str, Any], int_digits: int
) -> None:
    lines = [json.dumps(example_event), '{"n": %s}' % BIG, '{"n": %s}' % ("9" * 4300)]
    result = ingest(load_bundle(make_bundle(lines)))
    assert len(result.accepted) == 1
    assert [q.reason for q in result.quarantined] == [
        QuarantineReason.SCHEMA_INVALID,
        QuarantineReason.SCHEMA_INVALID,
    ]
    assert result.quarantined[0].detail == f"invalid JSON: {TOO_LONG}"
    # 4300 digits parse: that line is refused for its shape, not as invalid JSON.
    assert not (result.quarantined[1].detail or "").startswith("invalid JSON")


def _report(directory: Path) -> Path:
    assertion = Assertion(
        control="OVS-03",
        control_version="2026.09",
        subject="spiffe://corp/agents/a",
        outcome="conformant",
        rung=2,
        mode="automated",
        window=("2026-01-01T00:00:00Z", "2026-02-01T00:00:00Z"),
        population=(3, 0),
        severity="high",
        family="OVS",
        evidence=[
            EvidencePointer(
                "agentce:event/x", "sha256:" + "0" * 64, "enforcement_point"
            )
        ],
    )
    catalog = Catalog(
        id="eu-ai-act",
        version="2026.09",
        directory=_REPO_ROOT / "spec/catalogs/base/eu-ai-act",
        controls=[],
        shapes={},
    )
    write_report(
        directory, [assertion], bundle_digest="sha256:" + "a" * 64, catalogs=[catalog]
    )
    assert validate_report(directory) == []
    return directory


@pytest.mark.parametrize(
    ("artifact", "problem"),
    [
        ("assertions.json", f"assertions.json: invalid JSON ({TOO_LONG})"),
        ("manifest.json", f"manifest.json: invalid JSON ({TOO_LONG})"),
        (
            "runtime_drift.jsonl",
            f"runtime_drift.jsonl: line 1 is not valid JSON ({TOO_LONG})",
        ),
    ],
)
def test_validate_report_names_an_over_limit_artifact(
    tmp_path: Path, artifact: str, problem: str, int_digits: int
) -> None:
    out = _report(tmp_path)
    (out / artifact).write_text('{"n": %s}\n' % BIG, encoding="utf-8")
    assert validate_report(out) == [problem]


def test_validate_report_accepts_a_4300_digit_jsonl_line(
    tmp_path: Path, int_digits: int
) -> None:
    out = _report(tmp_path)
    (out / "runtime_drift.jsonl").write_text(
        '{"n": %s}\n' % ("9" * 4300), encoding="utf-8"
    )
    assert validate_report(out) == []


def test_recorded_outputs_ignores_an_over_limit_manifest(
    tmp_path: Path, int_digits: int
) -> None:
    (tmp_path / "manifest.json").write_text(
        '{"outputs": {"n": %s}}' % BIG, encoding="utf-8"
    )
    assert _recorded_outputs(tmp_path) == {}


def test_an_over_limit_manifest_records_no_outputs(
    tmp_path: Path, int_digits: int
) -> None:
    out = _report(tmp_path)
    manifest = '{"outputs": {"runtime_drift.jsonl": "x"}, "n": %s}' % BIG
    (out / "manifest.json").write_text(manifest, encoding="utf-8")
    # Not "runtime_drift.jsonl: missing (recorded ...)": an unreadable manifest records nothing.
    assert validate_report(out) == [f"manifest.json: invalid JSON ({TOO_LONG})"]


def _fake_run(stdout: str) -> Callable[..., subprocess.CompletedProcess[str]]:
    def run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=[], returncode=0, stdout=stdout, stderr=""
        )

    return run


def test_adapter_conformance_reports_an_over_limit_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(conformance.subprocess, "run", _fake_run('{"total": %s}' % BIG))
    result = conformance._adapter_conformance(Path("adapters"), None)
    assert result["round_trip"] is False
    assert result["total"] == 0


def test_adapter_ingest_refuses_an_over_limit_result(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import agentce.commands as commands

    (tmp_path / "otel-genai").mkdir()
    monkeypatch.setattr(commands.subprocess, "run", _fake_run('{"events": [%s]}' % BIG))
    with pytest.raises(InputError) as caught:
        _adapt_export(
            tmp_path,
            "otel-genai",
            tmp_path / "export.json",
            subject="spiffe://corp/agents/a",
            source_class="self_report",
        )
    assert caught.value.key == "input.ingest_failed"
    assert TOO_LONG in str(caught.value)


def _big(digits: str) -> int:
    """The exact integer, built under any interpreter limit (chunks under the lowest limit)."""
    value = 0
    for start in range(0, len(digits), 500):
        chunk = digits[start : start + 500]
        value = value * 10 ** len(chunk) + int(chunk)
    return value


def _caught(handler: ast.ExceptHandler) -> set[str]:
    if handler.type is None:
        return {"BaseException"}
    nodes = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    return {
        n.attr if isinstance(n, ast.Attribute) else getattr(n, "id", "") for n in nodes
    }


def test_no_json_call_sits_under_a_decode_only_handler() -> None:
    """A ``try`` that catches ``JSONDecodeError`` or ``JSONError`` but not ``ValueError`` must not call
    ``json.loads``/``json.load`` itself: an over-limit integer would escape it. Use :func:`load_json`
    (``safe_json.py`` is exempt: it is that helper)."""
    bad = []
    for path in sorted(_PACKAGE.rglob("*.py")):
        if path.name == "safe_json.py":
            continue  # the helper itself: its second pass parses integers with its own strict rule
        for node in ast.walk(ast.parse(path.read_text("utf-8"))):
            if not isinstance(node, ast.Try):
                continue
            caught = set().union(*(_caught(h) for h in node.handlers))
            if not caught & {"JSONDecodeError", "JSONError"} or caught & {
                "ValueError",
                "Exception",
                "BaseException",
            }:
                continue
            bad += [
                f"{path.relative_to(_PACKAGE)}:{call.lineno}"
                for stmt in node.body
                for call in ast.walk(stmt)
                if isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and call.func.attr in ("loads", "load")
                and isinstance(call.func.value, ast.Name)
                and call.func.value.id == "json"
            ]
    assert not bad, bad
