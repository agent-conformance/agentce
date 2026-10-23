"""The ``json-cases`` seam (SPEC 8.7, 18.184x): run every row of the JSON profile case table
(``spec/report/json-cases.json``) through this engine's reader and print [id, result] per row in table order, the
shape the TypeScript and Java ``json-cases`` seams print, for ``tools/json_parity_check.py``.

Usage: ``python -m agentce.tools.json_cases <json-cases.json>``
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from agentce.canonical import CanonicalizationError, canonicalize
from agentce.safe_json import (
    JsonProfileError,
    is_json_integer,
    json_equal,
    read_bundled,
    read_json,
    read_jsonl,
    render_number,
    render_value,
)


def _reader(case: dict[str, Any]) -> dict[str, Any]:
    raw = (
        bytes.fromhex(case["hex"])
        if "hex" in case
        else case["text"].encode("utf-8", "surrogatepass")
    )
    try:
        if case["mode"] == "json":
            return {"canonical": render_value(read_json(raw, bom=case["bom"]))}
        lines = read_jsonl(raw, bom=case["bom"])
    except JsonProfileError as exc:
        return {"refuse": [exc.rule, exc.line]}
    for _, value in lines:
        if isinstance(value, JsonProfileError):
            return {"refuse": [value.rule, value.line]}
    return {"records": [number for number, _ in lines]}


def result(case: dict[str, Any]) -> dict[str, Any]:
    """This engine's result for one case-table row."""
    if "mode" in case:
        return _reader(case)
    rule = case["rule"]
    if rule == "number_rendering":
        return {"rendered": render_number(read_bundled(case["input"].encode()))}
    if rule in ("compare_by_value", "compare_across_types"):
        left = read_bundled(case["left"].encode())
        right = read_bundled(case["right"].encode())
        return {"equal": [json_equal(left, right), json_equal(right, left)]}
    if rule == "integer_field_float_form":
        return {"integer": is_json_integer(read_bundled(case["text"].encode()))}
    if rule == "non_integer_number":
        try:
            canonicalize(read_bundled(case["text"].encode()))
        except CanonicalizationError as exc:
            return {"refusal": str(exc)}
        return {"refusal": None}
    return {"unknown_rule": rule}


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("json-cases: a case file path is required", file=sys.stderr)
        return 3
    sys.setrecursionlimit(max(sys.getrecursionlimit(), 10_000))
    cases = read_bundled(Path(args[0]).read_bytes())["cases"]
    print(json.dumps([[c["id"], result(c)] for c in cases], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
