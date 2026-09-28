#!/usr/bin/env bash
# Build gate helper for VG-SELF-APPROVAL-LABEL: an approval an agent records about itself stays
# labelled self_report end to end through the real CLI, and a class-mismatched event claiming
# independent_system is quarantined and gains nothing (SPEC §6.4).
#
# Two fully independent fixture bundles (round 2 of contracts/P18-18.6.md, finding 1: a single
# shared bundle gives the "spoofed" scenario SELF-01 == conformant too, since the honest event alone
# satisfies the control, plus a duplicate_id quarantine from a reused Decision event id) --
# honest/ and spoofed/, each its own manifest declaring sources[].class == self_report, each its own
# `agentce assess` invocation. The only variable between them is the ApprovalDecided event's own
# claimed agentcesourceclass.
#
# This mechanism (ingest.py's class_mismatch check) already holds correctly at base, unit-tested at
# the ingest-result level only (test_ingest.py::test_class_mismatch) -- this gate is the first
# end-to-end, real-CLI proof that the mismatch actually changes what a user's report shows
# (discrimination: bad-fixture, not red-at-base; its teeth come from the seeded fault below).
#
# Regenerate nothing here: every assertion is a live structural check against real assess output.
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
fixture="$root/verification/gates/fixtures/self_approval"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

run_assess() {
  local bundle="$1" out="$2"
  (cd "$root/engines/python" && env -u VIRTUAL_ENV PYTHONDONTWRITEBYTECODE=1 uv run --frozen agentce assess \
    --bundle "$bundle" --profile "$fixture/applicability.yaml" \
    --domain "$fixture/domain.linkml.yaml" --catalog-dir "$fixture/catalog" \
    --allow-unverified-catalog --report-language en --out "$out" >/dev/null)
}

run_assess "$fixture/honest/evidence" "$work/honest"
run_assess "$fixture/spoofed/evidence" "$work/spoofed"

status=0

(cd "$root/engines/python" && env -u VIRTUAL_ENV PYTHONDONTWRITEBYTECODE=1 uv run --frozen python - "$work/honest" <<'PY'
import json
import sys

work = sys.argv[1]
honest = json.load(open(f"{work}/assertions.json"))
self01 = next(a for a in honest if a["control"] == "SELF-01")
if self01["outcome"] != "conformant":
    print(f"self-approval: honest/ SELF-01 outcome is {self01['outcome']!r}, want conformant", file=sys.stderr)
    sys.exit(1)

activity = json.load(open(f"{work}/activity.json"))
recorder = activity["approvals_by_recorder"]
if recorder.get("self_report") != 1 or any(v for k, v in recorder.items() if k != "self_report"):
    print(f"self-approval: honest/ approvals_by_recorder is {recorder}, want only self_report=1", file=sys.stderr)
    sys.exit(1)
print("self-approval: honest step (a)+(b) ok -- SELF-01 conformant, self_report=1 only")
PY
) || status=1

# One subprocess for both the raw i18n label and its HTML-escaped form, read as two lines --
# avoids a second uv/Python startup purely to call html.escape() on the first call's own output.
labels=$(cd "$root/engines/python" && env -u VIRTUAL_ENV PYTHONDONTWRITEBYTECODE=1 uv run --frozen python <<'PY'
import html
import json

label = json.load(open("agentce/data/i18n/messages.en.json"))["report.activity_recorder_self_report"]
print(label)
print(html.escape(label))
PY
)
label="$(sed -n '1p' <<<"$labels")"
html_label="$(sed -n '2p' <<<"$labels")"

if ! grep -qF "Approvals recorded by: ${label} 1" "$work/honest/report.md"; then
  echo "self-approval: honest/ report.md is missing the exact line 'Approvals recorded by: ${label} 1'" >&2
  status=1
else
  echo "self-approval: honest step (c) ok -- report.md has the exact activity line"
fi
if ! grep -qF "Approvals recorded by: ${html_label} 1" "$work/honest/report.html"; then
  echo "self-approval: honest/ report.html is missing the html-escaped activity line" >&2
  status=1
else
  echo "self-approval: honest step (c) ok -- report.html has the html-escaped activity line"
fi

(cd "$root/engines/python" && env -u VIRTUAL_ENV PYTHONDONTWRITEBYTECODE=1 uv run --frozen python - "$work/spoofed" <<'PY'
import json
import sys

work = sys.argv[1]
lines = [json.loads(l) for l in open(f"{work}/quarantine.jsonl") if l.strip()]
mismatches = [l for l in lines if l.get("reason") == "class_mismatch"]
if len(mismatches) != 1:
    print(f"self-approval: spoofed/ quarantine.jsonl has {len(mismatches)} class_mismatch entries, want exactly 1", file=sys.stderr)
    sys.exit(1)

activity = json.load(open(f"{work}/activity.json"))
if activity["approvals_by_recorder"].get("independent_system"):
    print(f"self-approval: spoofed/ approvals_by_recorder.independent_system is nonzero: {activity['approvals_by_recorder']}", file=sys.stderr)
    sys.exit(1)

spoofed = json.load(open(f"{work}/assertions.json"))
self01 = next(a for a in spoofed if a["control"] == "SELF-01")
if self01["outcome"] != "insufficient_evidence":
    print(f"self-approval: spoofed/ SELF-01 outcome is {self01['outcome']!r}, want insufficient_evidence", file=sys.stderr)
    sys.exit(1)
print("self-approval: spoofed step (a)+(b)+(c) ok -- one class_mismatch entry, independent_system=0, SELF-01 insufficient_evidence")
PY
) || status=1

[ "$status" -eq 0 ] && echo "self-approval: honest approval stayed labelled self_report, spoofed approval was quarantined and gained nothing"
exit "$status"
