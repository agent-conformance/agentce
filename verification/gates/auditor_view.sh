#!/usr/bin/env bash
# Build gate helper for VG-AUDITOR-VIEW (18.17, Hill 8's auditor lens): a real `assess --deviations
# <fixture>/deviations.yaml --for auditor` run's auditor view -- clause-by-clause results, the "by
# clause" navigation index, accepted and expired deviations, a manual-mode control's disclosed
# not-yet-evaluated note, and the OSCAL risk entry a deviation now adds -- matches a committed golden
# and is never fabricated (contracts/P18-18.17.md C4).
#
# The fixture (verification/gates/fixtures/auditor_view/) is a dedicated four-control catalog loaded
# via --catalog-dir --allow-unverified-catalog, evaluated against one shared Decision event that
# carries no `agent` (so any control whose shape requires `prov:wasAssociatedWith` fails) but does
# carry a `time` (so a control whose shape only requires `prov:atTime` passes):
#   AUV-01: non-conformant, and the register's own AUV-01 entry (unexpired at the fixture's window
#           end) applies, flipping it to `partial` with the full accepted-deviation detail.
#   AUV-02: non-conformant, the same shape as AUV-01, but the register's AUV-02 entry expired before
#           the window end -- ignored, not applied, and reported as a limitation.
#   AUV-03: `manual` mode, rung 0, no shape -- always `not_assessed`, carrying the disclosed
#           not-yet-evaluated note rather than a fabricated result.
#   AUV-04: conformant, with its own crosswalk entry (eu-ai-act / Art. 12), exercising `by_clause`.
# The register's AUV-01 entry's `rationale`, and the fixture event's own id (which becomes the
# evidence `ref`), both carry a `<script>alert(1)</script>` fragment (round 2 N9): every rendering
# must escape it, never emit it as raw markup.
#
# `agentce assess` returns ExitCode.FINDINGS (1) for this fixture's own real, non-conformant verdict
# (AUV-02) -- a genuine finding, not a script failure, so the exit code is captured explicitly instead
# of letting `set -e` treat it as one.
#
# The golden (auditor_view_golden.json) is always a capture of the Python engine's own canonicalized
# `auditor.json`. Regenerate with: verification/gates/auditor_view.sh --write
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
fixture="$root/verification/gates/fixtures/auditor_view"
golden="$root/verification/gates/auditor_view_golden.json"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

run_assess() {
  local out="$1"
  set +e
  (cd "$root/engines/python" && env -u VIRTUAL_ENV uv run --frozen agentce assess \
    --bundle "$fixture/evidence" --profile "$fixture/applicability.yaml" \
    --domain "$fixture/domain.linkml.yaml" --catalog-dir "$fixture/catalog" \
    --allow-unverified-catalog --deviations "$fixture/deviations.yaml" --for auditor \
    --out "$out" >/dev/null 2>&1)
  local code=$?
  set -e
  if [ "$code" -ne 1 ]; then
    echo "auditor-view: assess exited $code, expected 1 (ExitCode.FINDINGS -- AUV-02's own real non-conformant verdict)" >&2
    return 1
  fi
  return 0
}

#: check (e): auditor.json's counts equal a fresh aggregate(assertions) call over the same run's own
#: assertions.json, never a stand-in report.json (round 2 B2: report.json does not exist anywhere).
check_counts_match_assertions() {
  (cd "$root/engines/python" && env -u VIRTUAL_ENV uv run --frozen python3 - "$1" "$2" <<'PY'
import json, sys
from agentce.assertions import Assertion, aggregate
auditor = json.load(open(sys.argv[1], encoding="utf-8"))
assertions = [Assertion.from_json(a) for a in json.load(open(sys.argv[2], encoding="utf-8"))]
sys.exit(0 if auditor.get("counts") == aggregate(assertions) else 1)
PY
  )
}

#: checks (b)/(c)/(d)/(f)/(j): the shape and specific facts of a real run's auditor.json.
check_facts() {
  (cd "$root/engines/python" && env -u VIRTUAL_ENV uv run --frozen python3 - "$1" "$2" "$3" <<'PY'
import json, sys
import yaml
auditor = json.load(open(sys.argv[1], encoding="utf-8"))
manifest = json.load(open(sys.argv[2], encoding="utf-8"))
register = yaml.safe_load(open(sys.argv[3], encoding="utf-8"))["deviations"]
by_control = {c["control"]: c for c in auditor["clauses"]}
register_entry = next(d for d in register if d["control"] == "AUV-01")

problems = []
dev = by_control.get("AUV-01", {})
if dev.get("outcome") != "partial":
    problems.append("AUV-01: expected outcome partial")
d = dev.get("deviation") or {}
if d.get("rationale") != register_entry["rationale"]:
    problems.append("AUV-01: deviation rationale not verbatim from the register")
if d.get("owner") != register_entry["owner"] or d.get("approver") != register_entry["approver"]:
    problems.append("AUV-01: deviation owner/approver not verbatim from the register")

exp = by_control.get("AUV-02", {})
if exp.get("outcome") != "non-conformant" or exp.get("deviation") is not None:
    problems.append("AUV-02: expected outcome non-conformant with no deviation field (expired, ignored)")

man = by_control.get("AUV-03", {})
if man.get("outcome") != "not_assessed" or not man.get("manual_checklist_note"):
    problems.append("AUV-03: expected not_assessed with a manual_checklist_note")

if auditor.get("by_clause", {}).get("eu-ai-act", {}).get("Art. 12") != ["AUV-04"]:
    problems.append("by_clause['eu-ai-act']['Art. 12'] expected exactly ['AUV-04']")

if manifest.get("inputs", {}).get("deviation_register_digest") is None:
    problems.append("manifest.json: deviation_register_digest missing")

print("\n".join(problems))
sys.exit(1 if problems else 0)
PY
  )
}

#: check (f)/OSCAL: the deviated finding's target.status.reason is "partial" (never .state, which
#: cannot distinguish partial from non-conformant), and its risk entry cites the matching
#: mitigating-factors (round 2 N3/B2), verbatim from the register -- never a retyped literal.
check_oscal_risk() {
  (cd "$root/engines/python" && env -u VIRTUAL_ENV uv run --frozen python3 - "$1" "$2" <<'PY'
import json, sys
import yaml
doc = json.load(open(sys.argv[1], encoding="utf-8"))["assessment-results"]["results"][0]
register_entry = next(d for d in yaml.safe_load(open(sys.argv[2], encoding="utf-8"))["deviations"] if d["control"] == "AUV-01")
finding = next(f for f in doc["findings"] if f["target"]["target-id"] == "AUV-01")
if finding["target"]["status"]["reason"] != "partial":
    sys.exit(1)
risk_uuid = finding.get("related-risks", [{}])[0].get("risk-uuid")
risk = next((r for r in doc.get("risks", []) if r["uuid"] == risk_uuid), None)
if risk is None:
    sys.exit(1)
factors = risk.get("mitigating-factors", [])
if not factors or factors[0].get("description") != register_entry["compensating_control"]:
    sys.exit(1)
sys.exit(0)
PY
  )
}

if [ "${1:-}" = "--write" ]; then
  run_assess "$work/python"
  if ! check_counts_match_assertions "$work/python/auditor.json" "$work/python/assertions.json"; then
    echo "auditor-view: the run's own auditor.json counts diverged from a fresh aggregate() over its assertions.json; refusing to write a bad golden" >&2
    exit 1
  fi
  cp "$work/python/auditor.json" "$golden"
  echo "auditor-view: wrote $golden from a live Python run"
  exit 0
fi

status=0
py_out="$work/python"
run_assess "$py_out" || status=1

for name in auditor.json auditor.md auditor.html oscal-ar.json oscal-ar.xml; do
  if [ ! -f "$py_out/$name" ]; then
    echo "auditor-view: python did not write $name for --for auditor" >&2
    status=1
  fi
done
for stray in report.md report.html; do
  if [ -f "$py_out/$stray" ]; then
    echo "auditor-view: python wrote $stray for --for auditor, which PRESET_EMIT['auditor'] must not select" >&2
    status=1
  fi
done

if [ -f "$py_out/auditor.json" ]; then
  if ! cmp -s "$golden" "$py_out/auditor.json"; then
    echo "auditor-view: python auditor.json differs from the committed golden $golden" >&2
    status=1
  fi
  if [ -f "$py_out/assertions.json" ] && ! check_counts_match_assertions "$py_out/auditor.json" "$py_out/assertions.json"; then
    echo "auditor-view: auditor.json's counts diverged from a fresh aggregate() over the run's own assertions.json" >&2
    status=1
  fi
  if [ -f "$py_out/manifest.json" ]; then
    facts_out="$(check_facts "$py_out/auditor.json" "$py_out/manifest.json" "$fixture/deviations.yaml")" || { echo "auditor-view: $facts_out" >&2; status=1; }
  fi
fi

if [ -f "$py_out/oscal-ar.json" ] && ! check_oscal_risk "$py_out/oscal-ar.json" "$fixture/deviations.yaml"; then
  echo "auditor-view: AUV-01's oscal-ar.json finding/risk entry did not match the expected shape (target.status.reason, mitigating-factors)" >&2
  status=1
fi

# check (i): the hostile evidence ref and the hostile deviation rationale render escaped, never raw.
for f in "$py_out/auditor.md" "$py_out/auditor.html"; do
  if [ -f "$f" ] && grep -q '<script>alert(1)</script>' "$f"; then
    echo "auditor-view: $f rendered the hostile fixture text unescaped" >&2
    status=1
  fi
done
if [ -f "$py_out/auditor.html" ] && ! grep -q '&lt;script&gt;' "$py_out/auditor.html"; then
  echo "auditor-view: auditor.html never shows the hostile text HTML-escaped -- the escaping check itself may be vacuous" >&2
  status=1
fi

# check (j): the re-run command printed in auditor.md/.html itself includes --deviations (round 2 B1:
# not just reverify_argv in isolation).
if [ -f "$py_out/auditor.md" ] && ! grep -q -- '--deviations' "$py_out/auditor.md"; then
  echo "auditor-view: auditor.md's own re-run command omits --deviations" >&2
  status=1
fi

# check (h): agentce report --validate exits 0.
if ! (cd "$root/engines/python" && env -u VIRTUAL_ENV uv run --frozen agentce report --validate "$py_out" >/dev/null 2>&1); then
  echo "auditor-view: agentce report --validate refused the python engine's own output" >&2
  status=1
fi

[ "$status" -eq 0 ] && echo "auditor-view: python's real assess --for auditor run matches the golden, every section's facts (deviations, expiry, manual note, by_clause, OSCAL risk) hold, hostile text renders escaped, and the re-run command includes --deviations"
exit "$status"
