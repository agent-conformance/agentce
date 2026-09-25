#!/usr/bin/env python3
"""grc_connectors_check - prove the GRC-connectors recipes are real, not just documented prose.

Two independent facts, neither standing in for the other, for item 16.3's GRC-connectors stage:

**OSCAL path (RegScale / ServiceNow GRC).** Both vendors natively import NIST OSCAL Assessment
Results (RegScale's documented OSCAL-JSON bulk import; ServiceNow GRC's Continuous Authorization and
Monitoring module's documented OSCAL SSP/catalog/Assessment-Plan import) -- so the engine's existing
``render_oscal``/``validate_oscal_ar_nist`` (``engines/python/agentce/report.py``) are already what
they consume; no transform is needed for these two. This check generates a real OSCAL AR document
in-process from the real, committed ``spec/report/examples/assertions.example.json`` fixture -- the
same fixture ``engines/python``'s own tests already use -- and asserts ``validate_oscal_ar_nist``
returns zero problems: a mechanical proof that the document a compliance team would actually import is
schema-valid, not a claim resting on prose.

**Evidence-export path (Vanta / Drata).** Neither vendor has a native OSCAL import; both instead expose
a generic evidence/document/test-record API, so ``tools/grc/evidence_export.py`` transforms a real
``assertions.json`` into a vendor-neutral ``evidence.json``. This check imports that module for real,
runs its real transform against the real example fixture, and confirms the real output field names
(``control_id``, ``subject``, ``outcome``, ``digest``, ``evidence_refs``, ``checked_at``) -- derived by
actually running the transform, never hand-typed here -- are exactly what ``website/src/content/docs/
docs/grc-connectors.md`` documents: a text check against the doc page's own committed content, not
trusting prose that the two ever agree.

    grc_connectors_check.py              run both checks (the invocation CI uses)
    grc_connectors_check.py --self-test  prove each check discriminates: a broken/invalid OSCAL
                                          document is caught, and a docs page naming the wrong field
                                          name or the wrong module path is caught, against synthetic
                                          input, never a committed file

Run in the engine's environment (needs ``agentce.report``/``agentce.assertions`` and their
``engines/python`` dependencies, notably ``jsonschema``):
``uv run --project engines/python --frozen python tools/grc_connectors_check.py``. No network, no
learned component.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSERTIONS_FIXTURE = ROOT / "spec" / "report" / "examples" / "assertions.example.json"
DOC_PAGE = ROOT / "website" / "src" / "content" / "docs" / "docs" / "grc-connectors.md"
EVIDENCE_EXPORT_MODULE_PATH = "tools/grc/evidence_export.py"

sys.path.insert(0, str(ROOT / "tools" / "grc"))

from agentce.assertions import Assertion  # noqa: E402
from agentce.report import render_oscal, validate_oscal_ar_nist  # noqa: E402

import evidence_export  # type: ignore[import-not-found]  # noqa: E402  (needs the sys.path insert above; mypy's script-mode search path does not follow it)


def _load_real_assertions(fixture: Path = ASSERTIONS_FIXTURE) -> list[Assertion]:
    data = json.loads(fixture.read_text(encoding="utf-8"))
    return [Assertion.from_json(a) for a in data]


# --- OSCAL path (RegScale / ServiceNow GRC). --------------------------------------------------------


def check_oscal(fixture: Path = ASSERTIONS_FIXTURE) -> list[str]:
    """Generate a real OSCAL AR document from the real example fixture; it must validate clean."""
    if not fixture.is_file():
        return [f"{fixture}: missing"]
    assertions = _load_real_assertions(fixture)
    if not assertions:
        return [f"{fixture}: parsed to zero assertions"]
    document = render_oscal(assertions)
    problems = validate_oscal_ar_nist(document)
    return [f"oscal-ar.json (NIST OSCAL 1.1.2): {p}" for p in problems]


# --- Evidence-export path (Vanta / Drata). ------------------------------------------------------------


def _real_evidence_field_names(fixture: Path = ASSERTIONS_FIXTURE) -> list[str]:
    """Run the real transform against the real fixture; return the real output field names, derived
    by actually running the code, never hand-typed."""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "evidence.json"
        records = evidence_export.export_evidence(fixture, out)
    if not records:
        raise ValueError(f"{fixture} produced zero evidence.json records")
    return sorted(records[0])


def check_docs_consistency(doc_text: str | None = None) -> list[str]:
    """``grc-connectors.md`` names the real module path and every real output field name."""
    if not (ROOT / "tools" / "grc" / "evidence_export.py").is_file():
        return ["tools/grc/evidence_export.py is missing"]
    try:
        field_names = _real_evidence_field_names()
    except Exception as exc:
        return [
            f"evidence_export.export_evidence failed against the real fixture: {exc}"
        ]

    if doc_text is None:
        if not DOC_PAGE.is_file():
            return [f"{DOC_PAGE}: missing"]
        doc_text = DOC_PAGE.read_text(encoding="utf-8")

    problems: list[str] = []
    if EVIDENCE_EXPORT_MODULE_PATH not in doc_text:
        problems.append(
            f"the doc page does not name the real module path `{EVIDENCE_EXPORT_MODULE_PATH}`"
        )
    for name in field_names:
        if f"`{name}`" not in doc_text:
            problems.append(
                f"the doc page does not name the real output field `{name}`"
            )
    return problems


def check(root: Path = ROOT) -> list[str]:
    fixture = root / "spec" / "report" / "examples" / "assertions.example.json"
    problems = list(check_oscal(fixture))
    problems += [f"docs consistency: {p}" for p in check_docs_consistency()]
    return problems


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if "--self-test" in args:
        return self_test()
    problems = check(ROOT)
    for problem in problems:
        print(f"FAIL {problem}", file=sys.stderr)
    if problems:
        return 1
    print(
        "grc_connectors_check: ok — OSCAL path and evidence-export docs consistency both verified"
    )
    return 0


# --- self-test ---------------------------------------------------------------------------------------


def _selftest_oscal() -> bool:
    ok = True
    problems = check_oscal(ASSERTIONS_FIXTURE)
    if problems:
        print(f"self-test oscal-good-tree: FAIL {problems}")
        ok = False
    else:
        print("self-test oscal-good-tree: ok")

    # A deliberately broken/invalid OSCAL document (not shaped like one at all) is caught.
    broken = {"not-oscal": True}
    problems = validate_oscal_ar_nist(broken)
    if problems:
        print("self-test oscal-broken-document-is-caught: ok")
    else:
        print("self-test oscal-broken-document-is-caught: FAIL (no problems reported)")
        ok = False

    # A real document with a required field deliberately deleted is also caught.
    real_document = render_oscal(_load_real_assertions(ASSERTIONS_FIXTURE))
    mutilated = json.loads(json.dumps(real_document))
    del mutilated["assessment-results"]["results"]
    problems = validate_oscal_ar_nist(mutilated)
    if problems:
        print("self-test oscal-mutilated-document-is-caught: ok")
    else:
        print(
            "self-test oscal-mutilated-document-is-caught: FAIL (no problems reported)"
        )
        ok = False
    return ok


def _selftest_docs_consistency() -> bool:
    ok = True
    problems = check_docs_consistency()
    if problems:
        print(f"self-test docs-consistency-good-tree: FAIL {problems}")
        ok = False
    else:
        print("self-test docs-consistency-good-tree: ok")

    field_names = _real_evidence_field_names()
    good_text = f"See `{EVIDENCE_EXPORT_MODULE_PATH}`. Fields: " + ", ".join(
        f"`{name}`" for name in field_names
    )
    found = check_docs_consistency(good_text)
    if found:
        print(f"self-test docs-consistency-synthetic-good-doc: FAIL {found}")
        ok = False
    else:
        print("self-test docs-consistency-synthetic-good-doc: ok")

    # A doc page naming the wrong field name is caught.
    wrong_field = good_text.replace("`checked_at`", "`checked_on`")
    found = check_docs_consistency(wrong_field)
    if any("checked_at" in p for p in found):
        print("self-test docs-consistency-wrong-field-name-is-caught: ok")
    else:
        print(f"self-test docs-consistency-wrong-field-name-is-caught: FAIL {found}")
        ok = False

    # A doc page naming the wrong module path is caught.
    wrong_path = good_text.replace(
        EVIDENCE_EXPORT_MODULE_PATH, "tools/grc/export_evidence.py"
    )
    found = check_docs_consistency(wrong_path)
    if any("module path" in p for p in found):
        print("self-test docs-consistency-wrong-module-path-is-caught: ok")
    else:
        print(f"self-test docs-consistency-wrong-module-path-is-caught: FAIL {found}")
        ok = False

    return ok


def self_test() -> int:
    ok = _selftest_oscal() and _selftest_docs_consistency()
    print(f"grc_connectors_check self-test: {'ok' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
