#!/usr/bin/env python3
"""Validate the SPEC Appendix G worked example events against the generated artifacts.

--schema (eval P0.2): every example event validates against the generated JSON Schema — the envelope
  against EvidenceEvent and the data against the event type's payload definition.
--expand (eval P0.3): the JSON-LD expansion of event 1 (with the envelope's time/subject/source_class
  copied into the payload, @id set to agentce:event/<id>, and the refs edges materialised onto the
  event node as the graph builder does) contains the minimum triples listed in Appendix G and equals
  the committed golden triple set (examples/appendix-g/event-1.expected.nt).
--write-golden: (re)generate that golden file; run when the model or context changes.
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

from jsonschema import Draft202012Validator
from rdflib import Graph, Literal, URIRef
from rdflib.compare import to_canonical_graph
from rdflib.namespace import XSD

HERE = Path(__file__).resolve().parent
GEN = HERE / "generated"
EXAMPLES = HERE / "examples" / "appendix-g"
VOCAB = "https://agent-conformance.org/vocab/evidence/v1#"
PROV = "http://www.w3.org/ns/prov#"
GOLDEN = EXAMPLES / "event-1.expected.nt"
EVENT_FILES = ["event-1.json", "event-2.json"]


def _schema() -> dict:
    return json.loads((GEN / "json-schema" / "agentce-evidence.schema.json").read_text(encoding="utf-8"))


def check_schema() -> None:
    schema = _schema()
    defs = schema["$defs"]
    for name in EVENT_FILES:
        ev = json.loads((EXAMPLES / name).read_text(encoding="utf-8"))
        envelope = copy.deepcopy(ev)
        envelope["data"] = {}  # data range is Payload; an empty payload is valid, so this checks the envelope
        Draft202012Validator({"$ref": "#/$defs/EvidenceEvent", "$defs": defs}).validate(envelope)
        payload = copy.deepcopy(ev["data"])
        payload.pop("@context", None)
        ptype = payload.pop("@type")
        pdef = f"{ptype}Payload"
        if pdef not in defs:
            raise SystemExit(f"FAIL: no payload definition {pdef} for event {name}")
        Draft202012Validator({"$ref": f"#/$defs/{pdef}", "$defs": defs}).validate(payload)
    print("EXAMPLES OK")


def expand_event(ev: dict) -> Graph:
    ctx = json.loads((GEN / "jsonld-context.json").read_text(encoding="utf-8"))["@context"]
    data = copy.deepcopy(ev["data"])
    data["@context"] = ctx
    data["@id"] = "agentce:event/" + ev["id"]
    data["time"] = ev["time"]
    data["subject"] = ev["subject"]
    data["source_class"] = ev["agentcesourceclass"]
    g = Graph().parse(data=json.dumps(data), format="json-ld")
    # Materialise: hoist the refs node's edges onto the event node (graph builder behaviour, SPEC 6.3).
    event = URIRef(VOCAB + "event/" + ev["id"])
    refs_pred = URIRef(VOCAB + "refs")
    for refs_node in list(g.objects(event, refs_pred)):
        for p, o in list(g.predicate_objects(refs_node)):
            g.add((event, p, o))
            g.remove((refs_node, p, o))
        g.remove((event, refs_pred, refs_node))
    return g


def _canonical_nt(g: Graph) -> str:
    cg = to_canonical_graph(g)
    lines = sorted(line for line in cg.serialize(format="nt").splitlines() if line.strip())
    return "\n".join(lines) + "\n"


def _minimum_triples(ev: dict) -> list[tuple]:
    event = URIRef(VOCAB + "event/" + ev["id"])
    d = ev["data"]
    return [
        (event, URIRef("http://www.w3.org/1999/02/22-rdf-syntax-ns#type"), URIRef(VOCAB + "ToolCall")),
        (event, URIRef(PROV + "wasAssociatedWith"), URIRef(d["agent"]["id"])),
        (event, URIRef(PROV + "atTime"), Literal(ev["time"], datatype=XSD.dateTime)),
        (event, URIRef(VOCAB + "sourceClass"), URIRef(VOCAB + ev["agentcesourceclass"])),
        (event, URIRef(VOCAB + "actsOn"), URIRef(_iri(d["refs"]["instruction"]))),
        (event, URIRef(VOCAB + "authorizedBy"), URIRef(_iri(d["refs"]["authorization"]))),
        (event, URIRef(VOCAB + "delegatedVia"), URIRef(_iri(d["refs"]["delegation"]))),
        (event, URIRef(VOCAB + "executes"), URIRef(_iri(d["refs"]["decision"]))),
        (event, URIRef(PROV + "used"), URIRef(_iri(d["used"][0]))),
    ]


def _iri(curie: str) -> str:
    return VOCAB + curie[len("agentce:"):] if curie.startswith("agentce:") else curie


def check_expand(write_golden: bool) -> None:
    ev = json.loads((EXAMPLES / "event-1.json").read_text(encoding="utf-8"))
    g = expand_event(ev)
    missing = [t for t in _minimum_triples(ev) if t not in g]
    if missing:
        for t in missing:
            print(f"FAIL missing minimum triple: {t}")
        raise SystemExit(1)
    nt = _canonical_nt(g)
    if write_golden:
        GOLDEN.write_text(nt, encoding="utf-8", newline="\n")
        print(f"WROTE {GOLDEN}")
        return
    if not GOLDEN.exists():
        raise SystemExit(f"FAIL: golden {GOLDEN} missing; run --write-golden")
    golden = GOLDEN.read_text(encoding="utf-8")
    if nt != golden:
        print("FAIL: expansion does not equal the committed golden triple set")
        raise SystemExit(1)
    print("EXPANSION OK")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Validate the Appendix G example events.")
    ap.add_argument("--schema", action="store_true")
    ap.add_argument("--expand", action="store_true")
    ap.add_argument("--write-golden", action="store_true")
    args = ap.parse_args(argv)
    if args.schema:
        check_schema()
    if args.expand or args.write_golden:
        check_expand(write_golden=args.write_golden)
    if not (args.schema or args.expand or args.write_golden):
        ap.error("choose --schema, --expand, or --write-golden")
    return 0


if __name__ == "__main__":
    sys.exit(main())
