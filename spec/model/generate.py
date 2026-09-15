#!/usr/bin/env python3
"""Deterministic generation of downstream artifacts from the LinkML evidence model.

Produces, under --out (default: ./generated):
  json-schema/agentce-evidence.schema.json   validation schema
  jsonld-context.json                         JSON-LD context for the graph
  shacl/agentce-evidence.shacl.ttl            SHACL shapes for the events themselves
  types/{py,ts,go,java}/...                    typed bindings

Determinism (SPEC 6.7, eval P0.1): the only non-deterministic content any LinkML generator emits is
the Python generator's "Generation date" comment; it is normalized to a fixed epoch so two runs are
byte-identical. Output never depends on the --out path.
"""
from __future__ import annotations

import os
import sys

# Fix the hash seed before anything imports rdflib/linkml, so set/dict iteration (which affects RDF
# serialisation order) is stable across processes. Re-exec once with the seed pinned.
if os.environ.get("PYTHONHASHSEED") != "0":
    os.environ["PYTHONHASHSEED"] = "0"
    os.execv(sys.executable, [sys.executable, *sys.argv])

import argparse
import json
import re
from pathlib import Path

from linkml.generators.golanggen import GolangGenerator
from linkml.generators.javagen import JavaGenerator
from linkml.generators.jsonschemagen import JsonSchemaGenerator
from linkml.generators.pythongen import PythonGenerator
from linkml.generators.shaclgen import ShaclGenerator
from linkml.generators.typescriptgen import TypescriptGenerator
from rdflib import Graph
from rdflib.compare import to_canonical_graph

HERE = Path(__file__).resolve().parent
MODEL = HERE / "agentce-evidence.linkml.yaml"

# Event type terms map the payload @type (e.g. "ToolCall") to the vocabulary class IRI.
EVENT_TYPES = [
    "SessionStart", "SessionEnd", "BundleLoaded", "Attestation", "ModelCall", "ToolCall",
    "ResourceAccess", "MemoryWrite", "MemoryRead", "Instruction", "Refusal", "PolicyDecision",
    "AuthzCheck", "DelegationIssued", "Decision", "ApprovalRequested", "ApprovalDecided",
    "Override", "Interrupt", "Outcome", "Incident", "Notice", "Disclosure", "IntegrityResult",
]


def agentce_context() -> dict:
    """The vendored AgentCE JSON-LD context (SPEC 6.2.2, Appendix A). It encodes the glue relations
    of SPEC 6.3 so that expanding an event (with the envelope's time/subject/source_class copied into
    the payload and @id set to agentce:event/<id>) yields the provenance graph. LinkML's context
    generator cannot express the nested refs mapping, so this is authored from the specification."""
    obj: dict = {
        "@version": 1.1,
        "agentce": "https://agent-conformance.org/vocab/evidence/v1#",
        "prov": "http://www.w3.org/ns/prov#",
        "xsd": "http://www.w3.org/2001/XMLSchema#",
        "id": "@id",
    }
    for t in EVENT_TYPES:
        obj[t] = f"agentce:{t}"
    obj.update({
        "agent": {"@id": "prov:wasAssociatedWith", "@type": "@id"},
        "acted_for": {"@id": "prov:actedOnBehalfOf", "@type": "@id", "@container": "@list"},
        "used": {"@id": "prov:used", "@type": "@id", "@container": "@set"},
        "time": {"@id": "prov:atTime", "@type": "xsd:dateTime"},
        "subject": {"@id": "agentce:subject", "@type": "@id"},
        "source_class": {"@id": "agentce:sourceClass", "@type": "@vocab"},
        "enforcement_point": "agentce:enforcement_point",
        "independent_system": "agentce:independent_system",
        "self_report": "agentce:self_report",
        "oversight_modality": {"@id": "agentce:oversightModality", "@type": "@vocab"},
        "refs": {"@id": "agentce:refs", "@context": {
            "authorization": {"@id": "agentce:authorizedBy", "@type": "@id"},
            "delegation": {"@id": "agentce:delegatedVia", "@type": "@id"},
            "decision": {"@id": "agentce:executes", "@type": "@id"},
            "request": {"@id": "agentce:decides", "@type": "@id"},
            "instruction": {"@id": "agentce:actsOn", "@type": "@id"},
            "parent": {"@id": "agentce:derivedFrom", "@type": "@id"},
            "origin": {"@id": "agentce:derivedFrom", "@type": "@id"},
        }},
    })
    return {"@context": obj}


def canonical_turtle(text: str) -> str:
    """Parse Turtle and re-serialise with canonical blank-node labels, so output is byte-stable
    across runs (rdflib otherwise assigns random blank-node ids and varies list order)."""
    g = Graph().parse(data=text, format="turtle")
    return to_canonical_graph(g).serialize(format="turtle")

_DATE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?")


def normalize(text: str) -> str:
    """Replace generation timestamps with a fixed epoch so output is byte-stable across runs."""
    out_lines = []
    for line in text.splitlines(keepends=True):
        if "Generation date" in line or "source file date" in line:
            line = _DATE.sub("1970-01-01T00:00:00", line)
        out_lines.append(line)
    return "".join(out_lines)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not text.endswith("\n"):
        text += "\n"
    path.write_text(normalize(text), encoding="utf-8", newline="\n")


def generate(out: Path) -> None:
    model = str(MODEL)
    _write(out / "json-schema" / "agentce-evidence.schema.json", JsonSchemaGenerator(model).serialize())
    _write(out / "jsonld-context.json", json.dumps(agentce_context(), indent=2, ensure_ascii=False))
    _write(out / "shacl" / "agentce-evidence.shacl.ttl", canonical_turtle(ShaclGenerator(model).serialize()))
    _write(out / "types" / "py" / "agentce_evidence.py", PythonGenerator(model).serialize())
    _write(out / "types" / "ts" / "agentce-evidence.ts", TypescriptGenerator(model).serialize())
    _write(out / "types" / "go" / "agentce_evidence.go", GolangGenerator(model).serialize())
    # Java is multi-file (one class per file); the generator writes into a directory.
    java_dir = out / "types" / "java"
    java_dir.mkdir(parents=True, exist_ok=True)
    JavaGenerator(model).serialize(directory=str(java_dir))
    for jf in sorted(java_dir.rglob("*.java")):
        jf.write_text(normalize(jf.read_text(encoding="utf-8")), encoding="utf-8", newline="\n")


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate downstream artifacts from the LinkML model.")
    ap.add_argument("--out", default=str(HERE / "generated"), help="output directory")
    args = ap.parse_args()
    generate(Path(args.out).resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
