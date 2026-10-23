"""The JSON profile (SPEC 8.7) against the shared case table ``spec/report/json-cases.json``."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from agentce.safe_json import (
    JsonProfileError,
    json_equal,
    read_json,
    read_jsonl,
    render_number,
)

CASES = json.loads(
    (
        Path(__file__).resolve().parents[3] / "spec" / "report" / "json-cases.json"
    ).read_text(encoding="utf-8")
)["cases"]


def _canonical(value: Any) -> str:
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, (int, float)):
        return render_number(value)
    if isinstance(value, list):
        return "[" + ",".join(_canonical(v) for v in value) + "]"
    return (
        "{"
        + ",".join(
            f"{json.dumps(k, ensure_ascii=False)}:{_canonical(value[k])}"
            for k in sorted(value)
        )
        + "}"
    )


def _raw(case: dict[str, Any]) -> bytes:
    return (
        bytes.fromhex(case["hex"])
        if "hex" in case
        else case["text"].encode("utf-8", "surrogatepass")
    )


READER = [c for c in CASES if "mode" in c]


@pytest.mark.parametrize("case", READER, ids=[c["id"] for c in READER])
def test_reader_case(case: dict[str, Any]) -> None:
    sys.setrecursionlimit(
        max(sys.getrecursionlimit(), 10_000)
    )  # _canonical recurses once per level
    raw = _raw(case)
    if case["mode"] == "json":
        if case["expect"] == "accept":
            assert _canonical(read_json(raw, bom=case["bom"])) == case["canonical"]
        else:
            with pytest.raises(JsonProfileError) as exc:
                read_json(raw, bom=case["bom"])
            assert (exc.value.rule, exc.value.line) == (case["rule"], case["line"])
        return
    try:
        lines = read_jsonl(raw, bom=case["bom"])
    except JsonProfileError as exc:
        assert case["expect"] == "refuse" and (exc.rule, exc.line) == (
            case["rule"],
            case["line"],
        )
        return
    bad = [(n, v) for n, v in lines if isinstance(v, JsonProfileError)]
    if case["expect"] == "accept":
        assert not bad and [n for n, _ in lines] == case["records"]
    else:
        assert [(n, v.rule) for n, v in bad] == [(case["line"], case["rule"])]


@pytest.mark.parametrize(
    "case", [c for c in CASES if c["rule"] == "number_rendering"], ids=lambda c: c["id"]
)
def test_number_rendering(case: dict[str, Any]) -> None:
    assert (
        render_number(read_json(case["input"].encode(), bom="refuse")) == case["expect"]
    )


@pytest.mark.parametrize(
    "case",
    [c for c in CASES if c["rule"] in ("compare_by_value", "compare_across_types")],
    ids=lambda c: c["id"],
)
def test_comparison(case: dict[str, Any]) -> None:
    left = read_json(case["left"].encode(), bom="refuse")
    right = read_json(case["right"].encode(), bom="refuse")
    assert json_equal(left, right) is case["equal"]
    assert json_equal(right, left) is case["equal"]


def test_every_rule_is_run() -> None:
    run = {c["rule"] for c in READER} | {
        "number_rendering",
        "compare_by_value",
        "compare_across_types",
    }
    later = {
        "integer_field_float_form",
        "non_integer_number",
    }  # their callers' tests read these rows
    assert {c["rule"] for c in CASES} <= run | later
