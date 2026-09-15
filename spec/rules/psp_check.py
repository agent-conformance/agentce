#!/usr/bin/env python3
"""psp_check - refuse SHACL shapes that step outside the Portable Shape Profile (SPEC 7.2).

The Portable Shape Profile (PSP) is the strict subset of SHACL Core that every conforming AgentCE
engine executes identically. A rung-2 control MUST NOT depend on a construct that one engine
evaluates and another does not, so this linter refuses any shape that uses a feature the profile
excludes: SPARQL/JS constraints, unbounded property paths, non-anchored regular expressions, logical
combinators, and range constraints on types the profile does not cover, among others (see psp.md).

The check is purely structural over the shapes graph. It never validates data, executes a query, or
consults a learned component; it inspects which SHACL terms a shape uses and how its property paths
are built. Membership questions the profile defers to the engine (class hierarchies, materialised
edges) are out of scope here by design.

Usage:
    psp_check.py <shape.ttl> [<shape.ttl> ...]

Output and exit codes (stable, relied on by the conformance harness):
    every file within the profile   -> prints "PSP OK"                exit 0
    a shape outside the profile     -> prints "REFUSED: <feature>"    exit 1
    a file that will not parse      -> prints "ERROR: <detail>"       exit 2

The refusal names the first excluded feature found, in a fixed priority order, so the message is
deterministic regardless of triple ordering. `REFUSED: sh:sparql` is guaranteed for any shape that
carries a SPARQL constraint.
"""

from __future__ import annotations

import sys
from pathlib import Path

from rdflib import BNode, Graph, Literal, URIRef
from rdflib.collection import Collection
from rdflib.namespace import RDF, XSD, Namespace

SH = Namespace("http://www.w3.org/ns/shacl#")
AGENTCE = Namespace("https://agent-conformance.org/vocab/evidence/v1#")

# SHACL terms the profile permits, by local name (SPEC 7.2).
#   targets:        targetClass, targetNode (agentce:targetWhere is handled separately)
#   shape wiring:   property, path, node, qualifiedValueShape, qualifiedMinCount
#   constraints:    the exact list SPEC 7.2 enumerates for rung 2
#   path operators: alternativePath, inversePath (their shape is checked in _path_reason)
#   annotations:    name, message, description, order, group, severity (never affect an outcome)
ALLOWED = frozenset(
    {
        "targetClass",
        "targetNode",
        "property",
        "path",
        "node",
        "qualifiedValueShape",
        "qualifiedMinCount",
        "minCount",
        "maxCount",
        "class",
        "datatype",
        "nodeKind",
        "in",
        "hasValue",
        "equals",
        "disjoint",
        "lessThan",
        "lessThanOrEquals",
        "minInclusive",
        "maxInclusive",
        "pattern",
        "alternativePath",
        "inversePath",
        "name",
        "message",
        "description",
        "order",
        "group",
        "severity",
    }
)

# Excluded SHACL terms checked first, in this order, so the refusal is deterministic and names the
# most meaningful feature (SPARQL before its helper predicates). Anything in the SHACL namespace that
# is neither ALLOWED nor listed here is still refused, by local name, after this pass.
PRIORITY_DENY = (
    "sparql",
    "js",
    "jsLibrary",
    "jsFunctionName",
    "select",
    "ask",
    "construct",
    "prefixes",
    "closed",
    "ignoredProperties",
    "and",
    "or",
    "not",
    "xone",
    "zeroOrMorePath",
    "oneOrMorePath",
    "zeroOrOnePath",
    "languageIn",
    "uniqueLang",
    "flags",
    "targetSubjectsOf",
    "targetObjectsOf",
    "qualifiedMaxCount",
    "qualifiedValueShapesDisjoint",
)

MAX_PATH_LENGTH = 3  # SPEC 7.2: sequence and alternative of at most three
# Regex metacharacters forbidden after the mandatory leading anchor (SPEC 7.2: "^<literal>").
REGEX_META = frozenset(".^$*+?()[]{}|\\")
RANGE_DATATYPES = frozenset({XSD.integer, XSD.dateTime})


class Refused(Exception):
    """A shape uses a construct the profile excludes; carries the feature to name."""

    def __init__(self, feature: str) -> None:
        super().__init__(feature)
        self.feature = feature


def _local(term: URIRef) -> str:
    s = str(term)
    for sep in ("#", "/"):
        if sep in s:
            return s.rsplit(sep, 1)[1]
    return s


def _sh_predicates(graph: Graph) -> set[str]:
    return {_local(p) for p in set(graph.predicates()) if str(p).startswith(str(SH))}


def _is_list(graph: Graph, node: object) -> bool:
    return (node, RDF.first, None) in graph


def _path_reason(
    graph: Graph, node: object, seen: set[object] | None = None
) -> str | None:
    """Return a refusal feature if the property path is outside the profile, else None.

    Permitted: a predicate (IRI), an inverse of a permitted path, a sequence of <=3 permitted paths,
    and an alternative of <=3 permitted paths. Zero-or-more, one-or-more, and zero-or-one paths are
    refused: the profile replaces unbounded traversal with engine-materialised edges (SPEC 7.2).
    """
    seen = seen or set()
    if isinstance(node, URIRef):
        return None  # predicate path
    if node in seen:  # a cyclic blank-node path is not a valid finite path
        return "path (cyclic)"
    seen = seen | {node}

    for banned in ("zeroOrMorePath", "oneOrMorePath", "zeroOrOnePath"):
        if (node, SH[banned], None) in graph:
            return f"sh:{banned}"

    inverse = graph.value(node, SH.inversePath)
    if inverse is not None:
        return _path_reason(graph, inverse, seen)

    alternative = graph.value(node, SH.alternativePath)
    if alternative is not None:
        if not _is_list(graph, alternative):
            return "sh:alternativePath (not a list)"
        members = list(Collection(graph, alternative))
        if len(members) > MAX_PATH_LENGTH:
            return f"path alternative of {len(members)} (max {MAX_PATH_LENGTH})"
        return _first_reason(_path_reason(graph, m, seen) for m in members)

    if _is_list(graph, node):
        members = list(Collection(graph, node))
        if len(members) > MAX_PATH_LENGTH:
            return f"path sequence of {len(members)} (max {MAX_PATH_LENGTH})"
        return _first_reason(_path_reason(graph, m, seen) for m in members)

    # A blank node that is neither an inverse, an alternative, nor a sequence is not a portable path.
    return "path (unsupported blank node)"


def _first_reason(reasons) -> str | None:
    for r in reasons:
        if r is not None:
            return r
    return None


def _check_paths(graph: Graph) -> None:
    for path_node in graph.objects(None, SH.path):
        reason = _path_reason(graph, path_node)
        if reason is not None:
            raise Refused(reason)


def _check_patterns(graph: Graph) -> None:
    # sh:pattern is permitted only as an anchored literal prefix: "^" then literal characters, no
    # metacharacters and no flags (SPEC 7.2). Anything else is a portable-regex refusal.
    for value in graph.objects(None, SH.pattern):
        text = str(value)
        if not text.startswith("^") or any(c in REGEX_META for c in text[1:]):
            raise Refused("sh:pattern (non-literal regex)")


def _check_ranges(graph: Graph) -> None:
    # sh:minInclusive / sh:maxInclusive are permitted on xsd:integer and xsd:dateTime only.
    for pred in (SH.minInclusive, SH.maxInclusive):
        for value in graph.objects(None, pred):
            if not isinstance(value, Literal) or value.datatype not in RANGE_DATATYPES:
                raise Refused(f"{_local(pred)} on a non-integer/dateTime bound")


def _check_target_where(graph: Graph) -> None:
    # agentce:targetWhere is a conjunction of property-value equalities the engine resolves before
    # validation (SPEC 7.2). Its object must be a node of simple value equalities: no nested node,
    # no list, no further shape structure.
    for where in graph.objects(None, AGENTCE.targetWhere):
        for _, value in graph.predicate_objects(where):
            if isinstance(value, BNode):
                raise Refused("agentce:targetWhere (nested node, not a value equality)")


def check_graph(graph: Graph) -> None:
    """Raise Refused(feature) if the shapes graph steps outside the profile."""
    used = _sh_predicates(graph)
    for feature in PRIORITY_DENY:
        if feature in used:
            raise Refused(f"sh:{feature}")
    for name in sorted(used):
        if name not in ALLOWED:
            raise Refused(f"sh:{name}")
    _check_paths(graph)
    _check_patterns(graph)
    _check_ranges(graph)
    _check_target_where(graph)


def check_file(path: Path) -> None:
    """Parse and check one shapes file. Raises Refused on a non-portable shape; ValueError on parse."""
    graph = Graph()
    try:
        graph.parse(path, format="turtle")
    except Exception as e:
        raise ValueError(str(e)) from e
    check_graph(graph)


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        print("ERROR: no shape file given", file=sys.stderr)
        return 2
    for arg in argv:
        path = Path(arg)
        if not path.exists():
            print(f"ERROR: no such file: {arg}", file=sys.stderr)
            return 2
        try:
            check_file(path)
        except ValueError as e:
            print(f"ERROR: {arg}: {e}", file=sys.stderr)
            return 2
        except Refused as e:
            print(f"REFUSED: {e.feature}")
            return 1
    print("PSP OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
