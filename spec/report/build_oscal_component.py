"""Generate the AgentCE OSCAL component definition (SPEC §2.1, §7.3, item 5.5).

The component definition describes AgentCE -- the assessment engine -- as an OSCAL ``validation``
component whose single control implementation targets the EU AI Act base catalog: one implemented
requirement per catalog control, stating that the engine evaluates that control against recorded
evidence and emits a per-control OSCAL finding. It lets a governance platform ingest *what AgentCE
assesses* in the same OSCAL family it already reads for AgentCE's assessment results.

The document is generated, never hand-edited: identifiers are UUIDv5 over a fixed namespace so the
output is byte-identical on every host, and the control list is read from the base catalog so the two
never drift. ``validate_oscal.py`` regenerates in memory and refuses a committed file that differs.

Run in place to refresh the committed artifact::

    cd spec/report && uv run python build_oscal_component.py
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CATALOG_DIR = REPO_ROOT / "spec" / "catalogs" / "base" / "eu-ai-act"
OUTPUT = Path(__file__).resolve().parent / "oscal-component-definition.json"

#: UUIDv5 namespace for every identifier in the document (deterministic, host-independent).
NAMESPACE = "https://agent-conformance.org/oscal/component-definition"
#: Fixed as-of date tied to the catalog version, so the output carries no runtime timestamp.
LAST_MODIFIED = "2026-09-16T00:00:00Z"
OSCAL_VERSION = "1.1.2"
CATALOG_SOURCE = "https://agent-conformance.org/catalogs/base/eu-ai-act"


def _uid(key: str) -> str:
    """A deterministic UUIDv5 for ``key`` within the document namespace."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{NAMESPACE}#{key}"))


def _catalog() -> tuple[str, dict[str, str]]:
    """Return ``(catalog_version, {control_id: title})`` read from the base catalog."""
    meta = yaml.safe_load((CATALOG_DIR / "catalog.yaml").read_text("utf-8")) or {}
    titles: dict[str, str] = {}
    for rel in meta.get("controls", []):
        data = yaml.safe_load((CATALOG_DIR / rel).read_text("utf-8")) or {}
        titles[str(data["id"])] = str(data.get("title", "")).strip()
    return str(meta.get("version", "")), titles


def build() -> dict[str, Any]:
    """Build the OSCAL component-definition document as a plain dict."""
    version, titles = _catalog()
    implemented = [
        {
            "uuid": _uid(f"implemented-requirement/{control_id}"),
            "control-id": control_id,
            "description": (
                f"AgentCE evaluates control {control_id} ({title}) against recorded evidence and "
                f"reports a per-control OSCAL finding with the outcome and evidence pointers."
            ),
        }
        for control_id, title in titles.items()
    ]
    return {
        "component-definition": {
            "uuid": _uid("root"),
            "metadata": {
                "title": "AgentCE Agent Conformance Engine — OSCAL component definition",
                "last-modified": LAST_MODIFIED,
                "version": version,
                "oscal-version": OSCAL_VERSION,
                "roles": [
                    {"id": "assessor", "title": "Conformance assessor"},
                ],
                "parties": [
                    {
                        "uuid": _uid("party/agentce"),
                        "type": "organization",
                        "name": "AgentCE",
                        "links": [{"href": "https://agent-conformance.org", "rel": "homepage"}],
                    }
                ],
                "responsible-parties": [
                    {"role-id": "assessor", "party-uuids": [_uid("party/agentce")]}
                ],
            },
            "components": [
                {
                    "uuid": _uid("component/engine"),
                    "type": "validation",
                    "title": "AgentCE assessment engine",
                    "description": (
                        "A deterministic, model-free engine that assesses recorded agent evidence "
                        "against an executable control catalog and emits assertions, an OSCAL "
                        "assessment result, SARIF, and evidence packs (SPEC §5, §9)."
                    ),
                    "props": [
                        {"name": "version", "value": version},
                    ],
                    "control-implementations": [
                        {
                            "uuid": _uid("control-implementation/eu-ai-act"),
                            "source": CATALOG_SOURCE,
                            "description": (
                                "The controls of the EU AI Act base catalog that the engine "
                                "evaluates. Each implemented requirement names one catalog control; "
                                "obligation crosswalks to regulatory and standards clauses live "
                                "beside the catalog under crosswalk/."
                            ),
                            "implemented-requirements": implemented,
                        }
                    ],
                }
            ],
        }
    }


def render(document: dict[str, Any]) -> str:
    """Serialise the document canonically (two-space indent, UTF-8, trailing newline)."""
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"


def main() -> int:
    OUTPUT.write_text(render(build()), encoding="utf-8")
    print(f"wrote {OUTPUT.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
