"""Census-driven mutation finder for `report_validate_parity_check.py` (item 18.27).

`report_validate_keyword_census.assertion_pairs()` lists every (schema, keyword) pair a
`report --validate` problem can be reported under. This script finds, for each pair, one small
mutation of a real report artifact that fails exactly that keyword at exactly one location, using the
Python engine's own validators (Python is the reference). The parity check then applies each mutation
and compares all three engines; a pair with no mutation is reported, never skipped.

The search is mechanical, not hand-picked: every distinct node shape of each artifact (array members
collapsed to their first and last element) is tried with a fixed list of edits (replace with a value
of another type or an out-of-range value, delete a key, add an unexpected key, empty or duplicate an
array). Before that, each artifact is grown: every empty array gets one member and every absent
optional property one value, synthesised from the schema itself and kept only when the document stays
valid, so keywords on parts a quickstart run happens not to write are reached too.

Runs in the Python engine's environment (it imports `agentce.report`):

    uv run --frozen --project engines/python python tools/report_validate_census_cases.py BASE_DIR...

Prints a JSON list of mutations. Each `BASE_DIR` is a report directory; its name is the base name.
"""

from __future__ import annotations

import copy
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterator

import jsonschema
import regex

from agentce import report

sys.path.insert(0, str(Path(__file__).resolve().parent))
import report_validate_keyword_census as census  # noqa: E402

#: Artifact -> (local profile, real third-party standard or None), as `validate_report` checks them.
ARTIFACTS: dict[str, tuple[str, str | None]] = {
    filename: (schema, None) for filename, schema in report._ARTIFACT_SCHEMAS.items()
}
ARTIFACTS["oscal-ar.json"] = (
    "oscal-assessment-results",
    "oscal-assessment-results-nist-1.1.2",
)
ARTIFACTS["results.sarif"] = ("results-sarif", "sarif-2.1.0")

UNEXPECTED_KEY = "zz_unexpected"

#: `format` is never asserted by `report --validate`; to find a node whose schema declares one, the
#: search validates with these checkers on (the stdlib checker set has neither `date-time` nor `uri`).
FORMAT_CHECKER = jsonschema.FormatChecker()
FORMAT_CHECKER.checks("date-time")(
    lambda s: not isinstance(s, str) or bool(re.match(r"^\d{4}-\d\d-\d\dT", s))
)
for _name in ("uri", "uri-reference"):
    FORMAT_CHECKER.checks(_name)(
        lambda s: not isinstance(s, str) or bool(re.match(r"^[A-Za-z][\w+.-]*:\S*$", s))
    )
FORMAT_CHECKER.checks("email")(lambda s: not isinstance(s, str) or "@" in s)


def _validator(schema_name: str, *, formats: bool = False) -> Any:
    schema = report._load_schema(schema_name)
    checker = FORMAT_CHECKER if formats else None
    if schema_name == "oscal-assessment-results-nist-1.1.2":
        return report._OscalArValidator(schema, format_checker=checker)
    if schema_name == "sarif-2.1.0":
        return jsonschema.Draft4Validator(schema, format_checker=checker)
    return jsonschema.Draft202012Validator(schema, format_checker=checker)


class Stages:
    """The two validation stages of one artifact, mirroring `validate_report`: the real standard
    runs only when the local profile passes."""

    def __init__(self, filename: str) -> None:
        local, real = ARTIFACTS[filename]
        self.names = [local] + ([real] if real else [])
        self.plain = [_validator(n) for n in self.names]
        self.formats = [_validator(n, formats=True) for n in self.names]

    def errors(self, doc: Any) -> tuple[str | None, list[Any]]:
        """(failing stage, its errors), or (None, []) when the document is valid."""
        for name, validator in zip(self.names, self.plain):
            errors = list(validator.iter_errors(doc))
            if errors:
                return name, errors
        return None, []

    def format_errors(self, doc: Any) -> tuple[str | None, list[Any]]:
        for name, validator in zip(self.names, self.formats):
            errors = list(validator.iter_errors(doc))
            if errors:
                return name, errors
        return None, []


# --- Walking and editing a document --------------------------------------------------------------


def _shape(path: tuple[Any, ...]) -> tuple[Any, ...]:
    return tuple("*" if isinstance(p, int) else p for p in path)


def nodes(doc: Any) -> Iterator[tuple[tuple[Any, ...], Any]]:
    """Every node of `doc`, depth first; of an array, only its first and last member."""
    seen: set[tuple[Any, ...]] = set()

    def visit(
        node: Any, path: tuple[Any, ...]
    ) -> Iterator[tuple[tuple[Any, ...], Any]]:
        yield path, node
        if isinstance(node, dict):
            for key in sorted(node):
                yield from visit(node[key], path + (key,))
        elif isinstance(node, list) and node:
            for i in sorted({0, len(node) - 1}):
                child = path + (i,)
                if (_shape(child), i == 0) in seen:
                    continue
                seen.add((_shape(child), i == 0))
                yield from visit(node[i], child)

    yield from visit(doc, ())


def get(doc: Any, path: tuple[Any, ...]) -> Any:
    for p in path:
        doc = doc[p]
    return doc


def apply(doc: Any, mutation: dict[str, Any]) -> Any:
    """`doc` with `mutation` applied (a copy; `doc` is untouched)."""
    doc = copy.deepcopy(doc)
    path = tuple(mutation["path"])
    op, value = mutation["op"], mutation.get("value")
    if op == "replace":
        if not path:
            return value
        get(doc, path[:-1])[path[-1]] = value
    elif op == "delete":
        del get(doc, path[:-1])[path[-1]]
    elif op == "add_key":
        get(doc, path)[value] = 1
    elif op == "duplicate":
        target = get(doc, path)
        target.append(copy.deepcopy(target[0]))
    return doc


def edits(node: Any) -> Iterator[dict[str, Any]]:
    """The fixed edit list tried at one node."""
    replacements: tuple[Any, ...] = ("x x", -1, 1.5, True, None, [], {}, 1000000000)
    for value in replacements:
        if type(value) is not type(node) or value != node:
            yield {"op": "replace", "value": value}
    if isinstance(node, str):
        yield {"op": "replace", "value": ""}
        yield {"op": "replace", "value": "not-a-valid-value"}
    if isinstance(node, (int, float)) and not isinstance(node, bool):
        yield {"op": "replace", "value": node + 1000}
    if isinstance(node, dict):
        for key in sorted(node):
            yield {"op": "delete", "key": key}
        yield {"op": "add_key", "value": UNEXPECTED_KEY}
    if isinstance(node, list) and node:
        yield {"op": "duplicate"}


# --- Growing a document from its schema ----------------------------------------------------------


_FORMAT_EXAMPLES = {
    "date-time": "2026-01-01T00:00:00Z",
    "uri": "https://example.org/x",
    "uri-reference": "https://example.org/x",
    "email": "a@example.org",
}
#: Strings tried, in order, against a `pattern` when synthesising a value.
_PATTERN_EXAMPLES = (
    "x",
    "11111111-1111-4111-8111-111111111111",
    "2026-01-01T00:00:00Z",
    "https://example.org/x",
    "a@example.org",
    "en-US",
    "text/plain",
    "1.2.3.4",
    "eA==",
    "sha256:" + "0" * 64,
)


class Schema:
    """Local `$ref` resolution (JSON pointers and draft-07 `#anchor` `$id`s) over one schema."""

    def __init__(self, name: str) -> None:
        self.root = report._load_schema(name)
        self.anchors: dict[str, Any] = {}
        self._index(self.root)

    def _index(self, node: Any) -> None:
        if isinstance(node, dict):
            anchor = node.get("$id")
            if isinstance(anchor, str) and anchor.startswith("#"):
                self.anchors[anchor] = node
            for value in node.values():
                self._index(value)
        elif isinstance(node, list):
            for value in node:
                self._index(value)

    def resolve(self, schema: Any) -> Any:
        for _ in range(50):
            if not isinstance(schema, dict) or "$ref" not in schema:
                return schema
            ref = schema["$ref"]
            if ref in self.anchors:
                schema = self.anchors[ref]
                continue
            target: Any = self.root
            for token in ref.lstrip("#").strip("/").split("/"):
                if token:
                    target = target[token.replace("~1", "/").replace("~0", "~")]
            schema = target
        return schema

    def reaches(self, schema: Any) -> set[str]:
        """Every assertion keyword `schema` or any schema under it uses."""
        found: set[str] = set()
        seen: set[int] = set()
        stack = [schema]
        while stack:
            node = self.resolve(stack.pop())
            if not isinstance(node, dict) or id(node) in seen:
                continue
            seen.add(id(node))
            found |= census.ASSERTION_KEYWORDS & node.keys()
            if node.get("additionalProperties") is False:
                found.add("additionalProperties")
            stack += node.get("properties", {}).values()
            for key in ("items", "additionalProperties"):
                if isinstance(node.get(key), dict):
                    stack.append(node[key])
            for key in ("allOf", "anyOf", "oneOf"):
                stack += node.get(key, [])
        return found

    def alternatives(self, schema: Any) -> list[Any]:
        """One synthesised value per `anyOf`/`oneOf` branch of `schema` (just one value otherwise)."""
        resolved = self.resolve(schema)
        if isinstance(resolved, dict):
            for combinator in ("anyOf", "oneOf"):
                if combinator in resolved:
                    rest = {k: v for k, v in resolved.items() if k != combinator}
                    return [
                        self.synth({**rest, "allOf": [branch]}, fill=fill)
                        for branch in resolved[combinator]
                        for fill in (True, False)
                    ]
        return [self.synth(schema, fill=True), self.synth(schema)]

    def synth(self, schema: Any, depth: int = 0, *, fill: bool = False) -> Any:
        """A small value meant to satisfy `schema` (the caller checks it does); with `fill`, every
        array gets at least one member."""
        schema = self.resolve(schema)
        if not isinstance(schema, dict) or depth > 12:
            return None
        if "const" in schema:
            return schema["const"]
        if "enum" in schema:
            return schema["enum"][0]
        for combinator in ("allOf", "anyOf", "oneOf"):
            if combinator in schema:
                merged = {k: v for k, v in schema.items() if k != combinator}
                for branch in schema[combinator]:
                    branch = self.resolve(branch)
                    if isinstance(branch, dict):
                        merged = {**branch, **merged}
                        if combinator != "allOf":
                            break
                return self.synth(merged, depth + 1, fill=fill)
        kind = schema.get("type")
        if isinstance(kind, list):
            kind = next((k for k in kind if k != "null"), kind[0])
        if kind == "object" or (kind is None and "properties" in schema):
            props = schema.get("properties", {})
            return {
                key: self.synth(props.get(key, {}), depth + 1, fill=fill)
                for key in schema.get("required", [])
            }
        if kind == "array":
            count = max(schema.get("minItems", 0), 1 if fill else 0)
            item = self.synth(schema.get("items", {}), depth + 1, fill=fill)
            return [copy.deepcopy(item) for _ in range(count)]
        if kind in ("integer", "number"):
            return max(schema.get("minimum", 1), 1)
        if kind == "boolean":
            return True
        if kind == "null":
            return None
        pattern = schema.get("pattern")
        candidates = [
            _FORMAT_EXAMPLES.get(schema.get("format", ""), "x"),
            *_PATTERN_EXAMPLES,
        ]
        return next(
            (c for c in candidates if pattern is None or regex.search(pattern, c)), "x"
        )


def shrink(doc: Any, stages: Stages) -> Any:
    """`doc` with every array cut to its first member wherever the document stays valid, so each
    trial validates a small document."""
    for path, _ in list(nodes(doc)):
        try:
            node = get(doc, path)
        except (IndexError, KeyError):
            continue  # under an array an earlier step already cut
        if isinstance(node, list) and len(node) > 1:
            trial = apply(doc, {"path": list(path), "op": "replace", "value": node[:1]})
            if stages.errors(trial)[0] is None:
                doc = trial
    return doc


def grow(doc: Any, stages: Stages, schemas: list[Schema], keywords: set[str]) -> Any:
    """`doc` with one synthesised member in each empty array and one value for each absent optional
    property whose schema can reach one of `keywords`, kept only where the document stays valid."""
    for path, node in list(nodes(doc)):
        if isinstance(node, list) and not node:
            for schema, sub in zip(schemas, _array_item_schemas(stages, doc, path)):
                if schema.reaches(sub) & keywords:
                    doc = _first_valid(
                        doc, path, [[v] for v in schema.alternatives(sub)], stages
                    )
        elif node is None and path:
            for schema, sub in zip(schemas, _object_schemas(stages, doc, path)):
                if schema.reaches(sub) & keywords:
                    values = [v for v in schema.alternatives(sub) if v is not None]
                    doc = _first_valid(doc, path, values, stages)
        elif isinstance(node, dict):
            for schema, sub in zip(schemas, _object_schemas(stages, doc, path)):
                for key, prop in sorted(
                    _properties(schema, schema.resolve(sub)).items()
                ):
                    if key in get(doc, path) or not schema.reaches(prop) & keywords:
                        continue
                    doc = _first_valid(
                        doc, path + (key,), schema.alternatives(prop), stages
                    )
    return doc


def _first_valid(
    doc: Any, path: tuple[Any, ...], values: list[Any], stages: Stages
) -> Any:
    """`doc` with the value at `path` set to the first of `values` that keeps it valid (unchanged
    when none does)."""
    for value in values:
        trial = copy.deepcopy(doc)
        get(trial, path[:-1])[path[-1]] = value
        if stages.errors(trial)[0] is None:
            return trial
    return doc


def _properties(schema: Schema, sub: Any) -> dict[str, Any]:
    if not isinstance(sub, dict):
        return {}
    props = dict(sub.get("properties", {}))
    for combinator in ("allOf",):
        for branch in sub.get(combinator, []):
            props.update(_properties(schema, schema.resolve(branch)))
    return props


def _array_item_schemas(stages: Stages, doc: Any, path: tuple[Any, ...]) -> list[Any]:
    """The `items` schema of the array at `path`, per stage."""
    trial = apply(
        doc,
        {
            "path": list(path),
            "op": "replace",
            "value": [{"__probe__": 1}, 1, "x", True],
        },
    )
    out = []
    for validator in stages.plain:
        item = None
        for error in validator.iter_errors(trial):
            where = tuple(error.absolute_path)
            if len(where) == len(path) + 1 and where[:-1] == path:
                item = error.schema
                break
        out.append(item if item is not None else {})
    return out


def _object_schemas(stages: Stages, doc: Any, path: tuple[Any, ...]) -> list[Any]:
    """The schema of the object at `path`, per stage."""
    trial = apply(doc, {"path": list(path), "op": "replace", "value": "__probe__"})
    out = []
    for validator in stages.plain:
        sub = None
        for error in validator.iter_errors(trial):
            if tuple(error.absolute_path) == path:
                sub = error.schema
                break
        out.append(sub if sub is not None else {})
    return out


# --- The search ----------------------------------------------------------------------------------


def location(path: Any) -> str:
    return "/".join(str(p) for p in path) or "<root>"


#: How many times an artifact is grown (see `grow`) while some of its pairs are still unreached.
GROW_ROUNDS = 8


def _try(
    doc: Any,
    node_path: tuple[Any, ...],
    edit: dict[str, Any],
    stages: Stages,
    wanted: set[tuple[str, str]],
    branches: bool = False,
) -> dict[str, Any] | None:
    """The mutation `edit` at `node_path`, if it fails exactly one wanted keyword exactly once (with
    `branches`, also a keyword failing inside the branches of a single failing combinator)."""
    mutation: dict[str, Any] = {"path": list(node_path), **edit}
    if edit["op"] == "delete":
        mutation["path"] = list(node_path) + [mutation.pop("key")]
    trial = apply(doc, mutation)
    stage, errors = stages.errors(trial)
    if stage is None:
        stage, errors = stages.format_errors(trial)
        if stage is None or [e.validator for e in errors] != ["format"]:
            return None
    elif len(errors) != 1:
        return None
    keyword = str(errors[0].validator)
    if branches and keyword in ("anyOf", "oneOf"):
        # A keyword that only occurs inside a combinator branch is reported as the combinator.
        keyword = next(
            (k for k in sorted(_branch_keywords(errors[0])) if (stage, k) in wanted),
            keyword,
        )
    if (stage, keyword) not in wanted:
        return None
    return {
        "stage": stage,
        "keyword": keyword,
        "reported_as": str(errors[0].validator),
        "location": location(errors[0].absolute_path),
        "document": trial,
    }


def _branch_keywords(error: Any) -> set[str]:
    found = set()
    for sub in error.context or ():
        found.add(str(sub.validator))
        found |= _branch_keywords(sub)
    return found


def find(base: Path, wanted: set[tuple[str, str]]) -> list[dict[str, Any]]:
    """One mutation per still-wanted (schema, keyword) pair reachable from `base`'s artifacts."""
    found = []
    for filename in sorted(ARTIFACTS):
        path = base / filename
        if not path.is_file():
            continue
        stages = Stages(filename)
        if not any(pair[0] in stages.names for pair in wanted):
            continue
        doc = shrink(json.loads(path.read_text(encoding="utf-8")), stages)
        schemas = [Schema(n) for n in stages.names]
        searched: set[tuple[Any, ...]] = set()
        for _ in range(GROW_ROUNDS + 1):
            for node_path, node in nodes(doc):
                if not any(pair[0] in stages.names for pair in wanted):
                    break
                if _shape(node_path) in searched:
                    continue
                searched.add(_shape(node_path))
                for edit in edits(node):
                    hit = _try(doc, node_path, edit, stages, wanted)
                    if hit is not None:
                        wanted.discard((hit["stage"], hit["keyword"]))
                        found.append({"base": base.name, "file": filename, **hit})
            keywords = {k for stage, k in wanted if stage in stages.names}
            if not keywords:
                break
            doc = grow(doc, stages, schemas, keywords)
        for node_path, node in nodes(doc):
            for edit in edits(node):
                if not any(pair[0] in stages.names for pair in wanted):
                    break
                hit = _try(doc, node_path, edit, stages, wanted, branches=True)
                if hit is not None:
                    wanted.discard((hit["stage"], hit["keyword"]))
                    found.append({"base": base.name, "file": filename, **hit})
    return found


def main(argv: list[str]) -> int:
    wanted = set(census.assertion_pairs())
    found: list[dict[str, Any]] = []
    for base in argv:
        found += find(Path(base), wanted)
    json.dump({"mutations": found, "uncovered": sorted(wanted)}, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
