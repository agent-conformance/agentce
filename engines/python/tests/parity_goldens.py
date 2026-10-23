"""Shared producers for the TypeScript and Java engines' parity goldens (SPEC §6.3, §6.5, §6.6, §9.6).

Each engine's ``generate_goldens.py`` builds one map, golden file name -> the exact text to commit, from the
live Python reference engine, and hands it to :func:`write_or_check`. The producers here cover the golden sets
both engines share (the graph triple dump, applicability, coverage, integrity and the state directory); each
engine serialises JSON in its own committed style through the ``dump`` it passes in. ``--check`` compares the
map with the committed files byte for byte and writes nothing; VG-ENGINE-GOLDENS-FRESH runs it for both engines.
"""

from __future__ import annotations

import datetime
import json
import math
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from agentce.applicability import ControlMeta, resolve
from agentce.coverage import compute_coverage
from agentce.domain import DomainBinding
from agentce.errors import InputError
from agentce.graph import build_graph
from agentce.integrity import verify_bundle
from agentce.profile import Profile
from agentce.safe_yaml import load_untrusted_yaml
from agentce.state import StateDir

Dump = Callable[[object], str]

#: The state golden's inputs (``state-fixture.json``), copied into the golden so the engines' tests read one file.
STATE_INPUTS = (
    "manifest_bytes",
    "bundle_a",
    "bundle_b",
    "window_a",
    "window_b",
    "events_b",
)


def _load(testdata: Path, name: str) -> Any:
    return json.loads((testdata / name).read_text(encoding="utf-8"))


def graph_golden(testdata: Path) -> str:
    """Every edge, literal and class-closure row of the graph built from ``graph-fixture.json``, one per
    line, sorted by UTF-8 bytes: the format of the ports' ``dumpTriples()``."""
    fixture = _load(testdata, "graph-fixture.json")
    conn = build_graph(
        fixture["events"], domain=DomainBinding.from_dict(fixture["domain"])
    ).conn
    lines = [
        f"E\t{s}\t{p}\t{o}" for s, p, o in conn.execute("SELECT s, p, o FROM edges")
    ]
    lines += [
        f"L\t{s}\t{p}\t{val}\t{datatype}"
        for s, p, val, datatype in conn.execute(
            "SELECT s, p, val, datatype FROM literals"
        )
    ]
    lines += [
        f"C\t{descendant}\t{ancestor}"
        for descendant, ancestor in conn.execute(
            "SELECT descendant, ancestor FROM class_closure"
        )
    ]
    return "\n".join(sorted(lines, key=lambda line: line.encode("utf-8"))) + "\n"


def applicability_golden(testdata: Path) -> list[dict[str, Any]]:
    fixture = _load(testdata, "applicability-fixture.json")
    controls = [
        ControlMeta(
            id=c["id"],
            applies_to_roles=c.get("appliesToRoles", []),
            family=c.get("family", ""),
        )
        for c in fixture["controls"]
    ]
    return resolve(Profile.from_dict(fixture["profile"]), fixture["events"], controls)


def coverage_golden(testdata: Path) -> dict[str, Any]:
    fixture = _load(testdata, "coverage-fixture.json")
    return compute_coverage(
        fixture["events"],
        Profile.from_dict(fixture["profile"]),
        members={
            "reference/prov-counts.json": (
                testdata / "coverage-bundle" / "reference" / "prov-counts.json"
            ).read_bytes()
        },
    )


def integrity_golden(testdata: Path) -> list[dict[str, Any]]:
    fixture = _load(testdata, "integrity-fixture.json")
    return [
        result.to_json()
        for result in verify_bundle(fixture["events"], fixture["manifest"])
    ]


def state_golden(testdata: Path) -> dict[str, Any]:
    """Record a report, then plan a re-assessment against a changed bundle with a late event; and the
    ``state.json`` Python writes once ``last_outcomes`` holds prior outcomes (a port must load and save it
    unchanged, SPEC §9.6)."""
    inputs = _load(testdata, "state-fixture.json")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        manifest = root / "manifest.json"
        manifest.write_text(inputs["manifest_bytes"], encoding="utf-8")
        state = StateDir.load(root / "state")
        digest = state.record(inputs["bundle_a"], manifest, inputs["window_a"])
        state_json = (root / "state" / "state.json").read_text(encoding="utf-8")
        supersedes, late = StateDir.load(root / "state").plan(
            inputs["bundle_b"], inputs["events_b"], inputs["window_b"]
        )
        with_outcomes = StateDir.load(root / "state")
        with_outcomes.last_outcomes = inputs["last_outcomes"]
        with_outcomes.save()
        state_json_with_outcomes = (root / "state" / "state.json").read_text(
            encoding="utf-8"
        )
    return {
        **{key: inputs[key] for key in STATE_INPUTS},
        "manifest_digest": digest,
        "state_json_after_record": state_json,
        "supersedes": supersedes,
        "late_events": late,
        "state_json_with_outcomes": state_json_with_outcomes,
    }


#: The YAML node forms table (18.184k): tags x scalar styles x values, each read as a mapping value and a block
#: sequence entry, then key kinds, merges and nesting inside task_scopes and budgets. Digits are Unicode 15.0 or
#: older (D3); a reading with a non-string key is out (D2) and the producer refuses to write one.
NODE_TAGS: tuple[str, ...] = (
    *("", "!", "!<!>", "!!str", "!!int", "!!float", "!!bool", "!!null", "!!merge"),
    "!!timestamp",
    "!<tag:yaml.org,2002:merge>",
)
NODE_VALUES: tuple[str, ...] = (
    *(
        "yes",
        "on",
        "10",
        "010",
        "0x10",
        "1e1",
        "1.5",
        ".inf",
        "null",
        "~",
        "",
        "2026-01-02",
    ),
    *(
        "lookup",
        "=",
        "<<",
        "\u0661\u0662",
        "\uff11",
        "1\u0661",
        "\u00b2",
        "\U0001d7cf\U0001d7d0",
    ),
)
KEY_FORMS = {
    "flow_seq": "{[K]: V}",
    "flow_map": "{{K: 1}: V}",
    "explicit_flow_seq": "? [K]\n: V",
    "explicit_block_seq": "? - K\n: V",
    "explicit_flow_map": "? {K: 1}\n: V",
    "explicit_block_map": "? K: 1\n: V",
    "explicit_in_flow_map": "{? [K] : V}",
    "explicit_in_flow_seq": "[? [K] : V]",
    "flow_pair_in_seq": "[[K]: V]",
    "multiline_flow_key": "? [K,\n   K2]\n: V",
    "spaces_before_colon": "[K]   : V",
    "comment_before_colon": "? [K] # c\n: V",
    "blank_line_before_colon": "? [K]\n\n: V",
    "comment_line_before_colon": "? [K]\n# c\n: V",
    "anchored_key": "? &k [K]\n: V",
    "alias_seq_key": "x: &s [K]\n*s : V",
    "alias_map_key": "x: &m {K: 1}\n*m : V",
    "tagged_seq_key": "!!seq [K]: V",
    "explicit_no_value": "? [K]",
    "explicit_comment_no_value": "? # c\n  [K]",
    "explicit_on_next_line": "?\n  [K]\n: V",
    "explicit_scalar": "? K\n: V",
    "explicit_scalar_no_value": "? K\nb: V",
    "colon_plain_key": "a:\n  - x\n:b: V",
    "flow_seq_colon_text": "a: [x, ':y']\nb: V",
    "json_like": '{"K":[1],"b":V}',
    "empty_value": "K:\nb: V",
    "eq_key": "=: V",
    "flow_entry_seq": "{[K]}",
    "flow_entry_seq_after": "{x: V, [K]}",
    "flow_entry_map": "{{K: 1}}",
    "flow_entry_tagged": "{!!seq [K]}",
    "flow_entry_alias": "x: &s [K]\ny: {*s}",
    "flow_entry_comment": "{x: V, # c\n [K]}",
    "flow_entry_scalar": "{K, x: V}",
    "hash_in_quoted_key": '{"K #": [V]}',
    "hash_in_quoted_value": "{x: 'K #', y: [V]}",
    "comment_glued_after_comma": "{x: V,#:\n [K]}",
    "comment_glued_after_brace": "{#:\n [K]}",
    "comment_glued_question": "{x: V,#?\n [K]}",
    "comment_glued_in_seq": "[x,#?\n [K]]",
    "comment_glued_in_seq_colon": "[x,#:\n [K]]",
    "comment_glued_after_key": "? [K]#:\n: V",
    "comment_glued_after_value": "{x: [K]#:\n, y: V}",
    "comment_glued_after_question": "?#:\n  [K]\n: V",
}
MERGE_FORMS = {
    "one": "b: {<<: {t: 1}}",
    "two_keys": "b: {<<: {t: 1000}, <<: {t: 10}}",
    "two_keys_block": "b:\n  <<: {t: 1000}\n  <<: {t: 10}",
    "beside_tagged": "b: {<<: {t: 1}, !!merge x: {t: 2}}",
    "seq_first_wins": "b: {<<: [{t: 1}, {t: 2}]}",
    "explicit_after": "b: {<<: {t: 1}, t: 2}",
    "explicit_before": "b: {t: 2, <<: {t: 1}}",
    "anchor": "a: &a {t: 1, u: 2}\nb: {<<: *a, u: 3}",
    "quoted_key": 'b: {"<<": 1}',
    "value": "b: <<",
    "seq_value": "b: [<<]",
    "nonspecific_key": "b: {! <<: {t: 1}}",
    "tagged_foo": "b: {!!merge foo: {t: 1}}",
    "tagged_quoted": 'b: {!!merge "<<": {t: 1}}',
    "scalar_source": "b: {<<: 1}",
    "repeat_after_merge": "b: {<<: {t: 1}, t: 2, t: 3}",
    "seq_with_seq": "b: {<<: [{t: 1}, [x]]}",
    "seq_of_seq": "b: {<<: [[x]]}",
    "seq_source": "b: {<<: [x]}",
    "alias_seq_source": "a: &a [x]\nb: {<<: *a}",
    "no_value": "b: {<<}",
    "block_seq_source": "b:\n  <<:\n    - {t: 1}\n    - [x]",
    "explicit_no_value": "b:\n  ? <<",
    "explicit_with_value": "b:\n  ? <<\n  : {t: 1}",
    "explicit_flow_no_value": "b: {? <<}",
    "explicit_flow_with_value": "b: {? << : {t: 1}, u: 2}",
    "explicit_seq_sources": "b:\n  t: 3\n  ? <<\n  : [{t: 1, u: 1}, {u: 2, w: 2}]",
}


def _node_scalar(tag: str, style: str, value: str, indent: str) -> str | None:
    if style == "plain":
        text = value
    elif style == "single":
        text = f"'{value}'"
    elif style == "double":
        text = f'"{value}"'
    else:
        text = f"|\n{indent}  {value}" if value else "|"
    return f"{tag} {text}".strip() if tag else text


def _node_form_cases() -> dict[str, str]:
    cases: dict[str, str] = {}
    for tag in NODE_TAGS:
        for style in ("plain", "single", "double", "literal"):
            for index, value in enumerate(NODE_VALUES):
                name = f"scalar.{tag or 'none'}.{style}.{index}"
                cases[f"{name}.value"] = f"v: {_node_scalar(tag, style, value, '')}\n"
                cases[f"{name}.entry"] = (
                    f"v:\n  - {_node_scalar(tag, style, value, '  ')}\n"
                )
    for tag in NODE_TAGS:
        for value in ("yes", "10", "5", "lookup", "1.5"):
            tagged = f"{tag} {value}" if tag else value
            cases[f"nested.{tag or 'none'}.{value}"] = (
                "task_scopes:\n"
                f"  - {{task_type: t, allowed_tools: [{tagged}], budgets: {{tokens: {tagged}, spend: {tagged}}}}}\n"
                f"budgets:\n  tokens: {tagged}\n  spend: {tagged}\n"
            )
    for name, form in KEY_FORMS.items():
        cases[f"key.{name}"] = (
            form.replace("K2", "b").replace("K", "a").replace("V", "1") + "\n"
        )
        scope = (
            form.replace("K2", "lookup")
            .replace("K", "allowed_tools")
            .replace("V", '["yes"]')
        )
        cases[f"key.{name}.task_scopes"] = (
            "task_scopes:\n  - " + scope.replace("\n", "\n    ") + "\n"
        )
    for name, form in MERGE_FORMS.items():
        cases[f"merge.{name}"] = form + "\n"
        budgets = form.replace("b:", "budgets:", 1).replace("t:", "tokens:")
        cases[f"merge.{name}.budgets"] = (
            "task_scopes:\n  - task_type: x\n    "
            + budgets.replace("\n", "\n    ")
            + "\n"
        )
    return cases


def _node_reading(value: Any) -> Any:
    """Python's reading of one node, by type: numbers by value (the TypeScript port has one number type)."""
    if value is None:
        return None
    if isinstance(value, bool):
        return {"b": value}
    if isinstance(value, (int, float)):
        number = float(value)
        if math.isnan(number) or math.isinf(number):
            return {"n": str(number)}
        return {"n": number}
    if isinstance(value, str):
        return {"s": value}
    if isinstance(value, (datetime.date, datetime.datetime)):
        return {"ts": str(value)}
    if isinstance(value, list):
        return {"seq": [_node_reading(item) for item in value]}
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise ValueError(
                f"a non-string key is out of the node-forms table (D2): {value!r}"
            )
        return {"map": [[key, _node_reading(value[key])] for key in sorted(value)]}
    raise ValueError(f"no node reading for {value!r}")


def yaml_node_forms_golden() -> list[dict[str, Any]]:
    """Each node form's YAML and Python's reading of it through the domain binding's loader, or the key it is
    refused under (SPEC §7.4; 18.184k): the TypeScript and Java readers must read every case the same way."""
    rows: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "domain.yaml"
        for name, text in _node_form_cases().items():
            path.write_text(text, encoding="utf-8")
            try:
                reading = _node_reading(
                    load_untrusted_yaml(
                        path,
                        key="input.domain_binding_invalid",
                        what="the domain binding",
                    )
                )
            except InputError as error:
                reading = {"refused": error.key}
            rows.append({"name": name, "yaml": text, "reads": reading})
    return rows


def shared_goldens(testdata: Path, dump: Dump) -> dict[str, str]:
    return {
        "graph-golden.txt": graph_golden(testdata),
        "applicability-golden.json": dump(applicability_golden(testdata)),
        "coverage-golden.json": dump(coverage_golden(testdata)),
        "integrity-golden.json": dump(integrity_golden(testdata)),
        "state-golden.json": dump(state_golden(testdata)),
        "yaml-node-forms-golden.json": dump(yaml_node_forms_golden()),
    }


def stale(testdata: Path, goldens: dict[str, str]) -> list[str]:
    """Each golden whose committed bytes differ from ``goldens`` (or is missing), and each committed
    ``*-golden.*`` file no producer writes."""
    problems: list[str] = []
    for name, text in sorted(goldens.items()):
        path = testdata / name
        if not path.is_file():
            problems.append(f"{name}: missing")
        elif path.read_text(encoding="utf-8") != text:
            problems.append(f"{name}: differs from the live Python engine")
    problems += [
        f"{path.name}: committed but no generator produces it"
        for path in sorted(testdata.glob("*-golden.*"))
        if path.name not in goldens
    ]
    return problems


def write_or_check(testdata: Path, goldens: dict[str, str], argv: list[str]) -> int:
    """No argument: write every golden. ``--check``: compare, write nothing, exit 1 on any problem."""
    if argv not in ([], ["--check"]):
        print(f"usage: generate_goldens.py [--check] (got {argv!r})", file=sys.stderr)
        return 2
    if argv:
        problems = stale(testdata, goldens)
        for problem in problems:
            print(f"STALE {testdata.name}/{problem}", file=sys.stderr)
        return 1 if problems else 0
    for name, text in goldens.items():
        (testdata / name).write_text(text, encoding="utf-8")
        print(f"wrote {testdata / name}")
    return 0
