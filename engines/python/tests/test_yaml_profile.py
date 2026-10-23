"""Every case of the shared YAML profile table (spec/rules/yaml-profile-cases.json, SPEC 8.7; 18.184u) through the
real loaders: a refuse case is refused under each file role's key with a message naming the rule and line, an accept
case reads."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentce.errors import InputError
from agentce.safe_yaml import YamlProfileError, load_catalog_yaml, load_untrusted_yaml
from agentce.yaml_profile import HINTS

_REPO = Path(__file__).resolve().parents[3]
_TABLE = json.loads(
    (_REPO / "spec" / "rules" / "yaml-profile-cases.json").read_text(encoding="utf-8")
)
_ROLES = [
    ("input.profile_invalid", "the applicability profile"),
    ("input.domain_binding_invalid", "the domain binding"),
    ("input.deviation_invalid", "the deviation register"),
    ("input.collect_config", "the collect config"),
]


def case_text(case: dict) -> str:
    """The case's YAML, built from its ``generate`` description when it has one (the table's ``generators``)."""
    if "yaml" in case:
        return case["yaml"]
    return _generate(case["generate"]["kind"], case["generate"]["depth"])


def _generate(kind: str, depth: int) -> str:
    if kind == "block_map":
        return (
            "".join("  " * i + "k:\n" for i in range(depth - 1))
            + "  " * (depth - 1)
            + "k: v\n"
        )
    if kind == "block_seq":
        return (
            "".join("  " * i + "-\n" for i in range(depth - 1))
            + "  " * (depth - 1)
            + "- v\n"
        )
    if kind == "flow_seq":
        return "[" * depth + "]" * depth + "\n"
    if kind == "flow_map":
        return "{a: " * (depth - 1) + "{}" + "}" * (depth - 1) + "\n"
    if kind == "mixed":
        half = depth // 2
        rest = depth - half
        return _generate("block_map", half)[:-2] + "[" * rest + "]" * rest + "\n"
    if kind == "indentless":
        return (
            _generate("block_map", depth - 1)[:-6]
            + "k:\n"
            + "  " * (depth - 2)
            + "- v\n"
        )
    raise ValueError(kind)


def test_hints_match_the_table() -> None:
    assert _TABLE["hints"] == HINTS


def test_every_rule_keeps_a_refuse_case() -> None:
    refused = {c["rule"] for c in _TABLE["cases"] if c["expect"] == "refuse"}
    assert refused == set(HINTS)


@pytest.mark.parametrize(
    "case", _TABLE["cases"], ids=[c["id"] for c in _TABLE["cases"]]
)
def test_case(case: dict, tmp_path: Path) -> None:
    text = case_text(case)
    path = tmp_path / "input.yaml"
    path.write_bytes(text.encode("utf-8"))
    if case["expect"] == "accept":
        assert load_catalog_yaml(text) is not None
        for key, what in _ROLES:
            load_untrusted_yaml(path, key=key, what=what)
        return
    tail = (
        f"is outside the YAML profile: {case['rule']} at line {case['line']} (SPEC 8.7)"
    )
    with pytest.raises(YamlProfileError) as caught:
        load_catalog_yaml(text)
    assert caught.value.violation.message() == tail
    for key, what in _ROLES:
        with pytest.raises(InputError) as refused:
            load_untrusted_yaml(path, key=key, what=what)
        assert refused.value.key == key
        assert refused.value.cause == f"{what} at {str(path)!r} {tail}."
        assert refused.value.fix == HINTS[case["rule"]] + "."
