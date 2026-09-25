---
title: GRC Connectors
description: A real path from an AgentCE assessment into RegScale, ServiceNow GRC, Vanta, and Drata — beyond handing a compliance team a raw OSCAL file.
---

An AgentCE assessment produces real, schema-validated report artifacts (SPEC §9); this page is about
what a compliance team does with them once a GRC platform is in the loop. The four platforms split into
two real, different intake shapes, so this page gives each shape its own recipe rather than one that
pretends they are the same.

- **RegScale** and **ServiceNow GRC** (Continuous Authorization and Monitoring, "CAM") both natively
  import NIST OSCAL. AgentCE's own `oscal-ar.json` (`agentce.report.render_oscal`, already
  schema-validated by `agentce.report.validate_oscal_ar_nist` against the vendored NIST OSCAL 1.1.2
  Assessment Results schema, SPEC §9) is already the artifact they consume — no transform needed.
- **Vanta** and **Drata** have no native OSCAL import. Both instead expose a generic evidence/document/
  test-record API keyed on a handful of plain fields. `tools/grc/evidence_export.py` produces exactly
  that shape from a real `assertions.json`.

Every claim below cites the vendor's own documentation, retrieved 2026-09-24. Two are hedged
explicitly, below, because that day's fetch did not fully corroborate them — this page says so rather
than rounding a partial answer up to a confident one.

## RegScale and ServiceNow GRC: import the real `oscal-ar.json`

There is no static, committed `oscal-ar.json` in this repository — the artifact is generated for real,
from a real `assertions.json`, the same way the engine's own tests do. Generate one in-process from the
committed example fixture (`spec/report/examples/assertions.example.json`) and validate it against the
vendored NIST OSCAL 1.1.2 schema before trusting it:

```bash
uv run --project engines/python python - <<'PY'
import json
from agentce.assertions import Assertion
from agentce.report import render_oscal, validate_oscal_ar_nist

data = json.loads(open("spec/report/examples/assertions.example.json").read())
assertions = [Assertion.from_json(a) for a in data]
document = render_oscal(assertions)
problems = validate_oscal_ar_nist(document)
assert not problems, problems
findings = len(document["assessment-results"]["results"][0].get("findings", []))
print(f"oscal-ar.json is schema-valid NIST OSCAL 1.1.2 ({findings} finding(s))")
PY
```

The same document, written to a real file instead of held in memory, is what you hand to either
platform's own import path — the CLI's `report` command drives the identical `render_oscal` function
(`engines/python/agentce/commands/__init__.py::cmd_report`), so this is the more robust way to produce
the file for real, on your own `assertions.json`:

```bash
OUT=$(mktemp -d)
uv run --project engines/python agentce report \
  --from spec/report/examples/assertions.example.json \
  --format oscal \
  --out "$OUT/oscal-ar.json" \
  --json
ls -l "$OUT/oscal-ar.json"
```

### RegScale

RegScale documents a CLI (`RegScale-CLI`'s `oscal` command) that bulk-processes and loads OSCAL JSON
files, built specifically so a team never has to hand-code OSCAL loading against RegScale's API
(RegScale, "NIST OSCAL", <https://regscale.readme.io/docs/nist-oscal>, retrieved 2026-09-24). Point it
at `oscal-ar.json`:

```bash no-run needs the operator's own installed RegScale CLI and an authenticated RegScale tenant
regscale oscal load --file "$OUT/oscal-ar.json"
```

**Hedge.** As documented on that page today, the CLI's *currently stated* bulk-import scope is
catalogs and profiles, with System Security Plan and component import marked "future" — assessment
results are not named as a supported import target on that specific page, even though RegScale's own
product marketing and NIST OSCAL-workshop materials describe broader OSCAL support (including
one-click AR *generation*, the opposite direction) elsewhere. Confirm current assessment-results import
availability in your own RegScale tenant/CLI version before relying on this path; the command above is
the real, documented invocation shape, not a guarantee that every RegScale deployment accepts an AR
document through it today.

### ServiceNow GRC (Continuous Authorization and Monitoring)

ServiceNow's CAM workspace documents importing all four OSCAL model types relevant to an authorization
package — Catalog, System Security Plan, Assessment Plan, and **Assessment Results** — through a guided
"New Import" playbook reached from the OSCAL Imports landing page: pick the model type, attach the JSON
file, map OSCAL-file users to ServiceNow users, then preview and execute (ServiceNow, "Import in OSCAL
format", <https://www.servicenow.com/docs/r/governance-risk-compliance/grc-continuous-authorization-and-monitoring-workspace/import-oscal.html>,
retrieved 2026-09-24). Unlike RegScale's CLI, Assessment Results is explicitly one of the four supported
model types — attach `"$OUT/oscal-ar.json"` as the Assessment Results import in that playbook. The
import is UI-driven; ServiceNow's own documentation does not describe a public API for it, so this is a
manual (if guided) step, not something this repository can script against a live instance.

## Vanta and Drata: the vendor-neutral `evidence.json`

Neither vendor imports OSCAL. `tools/grc/evidence_export.py` reads a real `assertions.json` and writes
one `evidence.json` record per assertion, stdlib-only, deterministic, offline (`tools/pyproject.toml`
gains no new dependency for it). Prove it against itself first:

```bash
REPO=$(python3 -c "import os; print(os.path.dirname(os.path.realpath('engines')))")
cd "$REPO"
uv run --project tools python tools/grc/evidence_export.py --self-test
```

Then run the real transform against the real example fixture:

```bash
REPO=$(python3 -c "import os; print(os.path.dirname(os.path.realpath('engines')))")
cd "$REPO"
OUT=$(mktemp -d)
uv run --project tools python tools/grc/evidence_export.py spec/report/examples/assertions.example.json "$OUT/evidence.json"
cat "$OUT/evidence.json"
```

Each record carries `control_id` (the assertion's own `control` field, renamed for this transform's
output vocabulary), `subject`, `outcome`, `digest` (a SHA-256 of the assertion record's own canonical
JSON), `evidence_refs` (the assertion's own evidence-pointer `ref`s), and `checked_at` (pinned to the
assertion's own `window.end` — never a wall-clock read; see the module's own docstring for why that
matters for reproducibility). `tools/grc_connectors_check.py` mechanically confirms this page names
those six field names and `tools/grc/evidence_export.py`'s real module path correctly — a text check
against this page's own content, not a claim resting on prose:

```bash
REPO=$(python3 -c "import os; print(os.path.dirname(os.path.realpath('engines')))")
cd "$REPO"
uv run --project engines/python python tools/grc_connectors_check.py
```

### Vanta

Vanta documents two real, separate generic-evidence mechanisms, either of which this `evidence.json`
fits:

- **Document upload tied to an Evidence Request.** Every document in Vanta is associated with an
  Evidence Request, which names the controls it is evidence for; uploading through the API removes the
  need for a person to log into the Vanta UI to attach it (Vanta, "Upload a document",
  <https://developer.vanta.com/docs/upload-a-document>, retrieved 2026-09-24; scope
  `vanta-api.documents:upload`). Upload `evidence.json` itself as the attached document for the Evidence
  Request that names the controls in `control_id`.
- **Custom-integration resource push plus a Custom Test.** Vanta's private-integration path lets an
  external system `PUT` records to a resource endpoint
  (`https://api.vanta.com/v1/resources/{resource_type}`) and then define a Custom Test in the Vanta
  dashboard that evaluates each record's fields — here, `outcome` — as pass/fail (Vanta, "Build a
  private integration", <https://developer.vanta.com/docs/quickstart/build-private-integration>,
  retrieved 2026-09-24). Push each `evidence.json` record as one resource record, and author a Custom
  Test that reads `outcome == "conformant"` as passing.

### Drata

Drata's Custom Connections framework documents a records endpoint for exactly this shape: define a
custom connection with a resource schema (either an explicit JSON Schema or one Drata infers from sample
data), then upsert records into it —

```
POST https://public-api.drata.com/public/v2/custom-connections/{connectionId}/resources/{resourceId}/records
Authorization: Bearer <DRATA_API_KEY>
Content-Type: application/json

{"data": {"id": "OVS-03:spiffe://corp/agents/claims-triage", "control_id": "OVS-03", "subject": "spiffe://corp/agents/claims-triage", "outcome": "non-conformant", "digest": "sha256:...", "evidence_refs": ["agentce:event/01J9XABCDE"], "checked_at": "2026-09-01T00:00:00.000Z"}}
```

— each record matched and upserted by its own `id`, so a stable synthetic key such as
`f"{control_id}:{subject}"` (both real `evidence.json` fields) works well (Drata, "Work with Custom
Connections", <https://developers.drata.com/developer-portal/v2/recipes/custom-connections/>, and
Drata Help Center, "Part 2: Automate Evidence Submission",
<https://help.drata.com/en/articles/11825486-part-2-automate-evidence-submission>, both retrieved
2026-09-24).

**Hedge.** Drata's documentation is the least independently corroborated of the four vendor claims on
this page: it is served as a dynamic, JavaScript-rendered app, so the endpoint path, the required
`data`-envelope shape, and the upsert-by-`id` behavior above are drawn from that day's fetched page
content and cross-checked against two independent Drata pages that agreed with each other — not from an
actual authenticated call this repository made against a live Drata tenant (which would need a
credential this repository does not hold, and which this item's constraints forbid regardless). Confirm
the exact request/response shape against your own Drata tenant's API reference before wiring automation
to it.

## What this is not

Nothing on this page or in `tools/grc/` calls a vendor's live endpoint. `tools/grc/evidence_export.py`
is a pure, offline, stdlib-only transform; `tools/grc_connectors_check.py` generates and validates an
OSCAL document in-process and text-checks this page's own content — neither one holds a credential or
makes a network call. The `curl`-shaped commands for RegScale's CLI, Vanta's document-upload API, and
Drata's records API above are illustrative of the real, documented shape a compliance team would use
against their own tenant; this repository never runs them.

## Read next

- [Running Assessments](/docs/running-assessments/) — how `assertions.json` and the report artifacts
  are produced in the first place.
- [Trace-Store Connectors](/docs/trace-store-connectors/) — the evidence-ingestion side of the same
  "no new adapter code" pattern.
- [CI Integration](/docs/ci-integration/) — wiring a report and its artifacts into a pipeline.
