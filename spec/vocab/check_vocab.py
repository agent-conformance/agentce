#!/usr/bin/env python3
"""Validate the AgentCE evidence vocabulary (SPEC 6.3, eval P0.3 support).

Checks: agentce.ttl parses (syntax validation, the riot --validate equivalent); the namespace IRI
matches the identifier table; every glue relation of SPEC 6.3 is declared with a domain and a range;
and agentce.jsonld is present and isomorphic to agentce.ttl. Prints "VOCAB OK" on success.

--write-jsonld regenerates agentce.jsonld from agentce.ttl (run when the Turtle changes).
"""
from __future__ import annotations

import sys
from pathlib import Path

from rdflib import Graph
from rdflib.compare import isomorphic
from rdflib.namespace import RDFS

HERE = Path(__file__).resolve().parent
TTL = HERE / "agentce.ttl"
JSONLD = HERE / "agentce.jsonld"
NS = "https://agent-conformance.org/vocab/evidence/v1#"

# Glue relations of SPEC 6.3 that this vocabulary defines (the reused prov:* relations are not ours).
GLUE_RELATIONS = [
    "authorizedBy", "delegatedVia", "chainVerified", "executes", "oversightModality",
    "reviewedBy", "overriddenBy", "interruptedBy", "loadedBundle", "attestedBy", "resultedIn",
    "notifiedBy", "relatedIncident", "sourceClass", "precededBy", "actsOn", "derivedFrom",
    "withinScope", "refusedBy",
]


def fail(msg: str) -> None:
    print(f"FAIL: {msg}")
    raise SystemExit(1)


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    g = Graph()
    try:
        g.parse(TTL, format="turtle")
    except Exception as e:  # noqa: BLE001 - report any parse error as a validation failure
        fail(f"agentce.ttl does not parse: {e}")

    if "--write-jsonld" in argv:
        JSONLD.write_text(g.serialize(format="json-ld", auto_compact=True), encoding="utf-8", newline="\n")
        print(f"WROTE {JSONLD}")
        return 0

    for rel in GLUE_RELATIONS:
        uri = f"{NS}{rel}"
        node = None
        for s in g.subjects():
            if str(s) == uri:
                node = s
                break
        if node is None:
            fail(f"glue relation {rel} not defined at {uri}")
        if not any(True for _ in g.objects(node, RDFS.domain)):
            fail(f"glue relation {rel} has no rdfs:domain")
        if not any(True for _ in g.objects(node, RDFS.range)):
            fail(f"glue relation {rel} has no rdfs:range")

    # Every agentce: subject must use the canonical namespace exactly.
    for s in set(g.subjects()):
        st = str(s)
        if "agent-conformance.org/vocab/evidence" in st and not st.startswith(NS.rstrip("#")):
            fail(f"subject IRI not in the canonical namespace: {st}")

    if not JSONLD.exists():
        fail("agentce.jsonld missing; run check_vocab.py --write-jsonld")
    gj = Graph()
    try:
        gj.parse(JSONLD, format="json-ld")
    except Exception as e:  # noqa: BLE001
        fail(f"agentce.jsonld does not parse: {e}")
    if not isomorphic(g, gj):
        fail("agentce.jsonld is not isomorphic to agentce.ttl")

    print("VOCAB OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
