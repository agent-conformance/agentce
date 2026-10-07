#!/usr/bin/env bash
# Build gate helper for VG-QUICK-PATH: `agentce assess <folder>` (SPEC §12, §13.4 AX-1) reads a
# folder of trace exports with no --bundle/--profile and proves three things a real scan must keep
# true (18.32): the records-vs-bundle divergence (the same derived evidence judges
# insufficient_evidence as a records run but not_applicable once reduced to a formal bundle+profile,
# SPEC §8.7 -- a records run never claims a control out of scope for lack of evidence), the
# checks_unlocked signal on a real scan through a tight gate-only catalog, and that the derived
# profile's pilot_window keeps passing the skill's own lint (closing the loop with 18.32 C1).
#
# The fixture (verification/gates/fixtures/quick_path/) is one pinned copy of the otel-genai
# adapter's own agent-session trace export (adapters/otel-genai/fixtures/otel-genai-agent-session/
# input.json): it carries a self-reported ToolCall but no ModelCall at all, so the gate-only
# QP-UNLOCK control (minimum_evidence: a self-reported ToolCall and a self-reported ModelCall) is
# missing exactly one requirement -- insufficient_evidence with one blind spot, not the needed_by
# case (VG-BLIND-SPOTS already covers two requirements missing at once).
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
fixture="$root/verification/gates/fixtures/quick_path"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

run_py() {
  (cd "$root/engines/python" && env -u VIRTUAL_ENV uv run --frozen agentce "$@")
}

fail() {
  echo "quick-path: $1" >&2
  exit 1
}

# Runs `agentce <args...>`, writing stdout to $out_file and stderr (the structured logs,
# agentce.logsetup) to $err_file separately -- a step whose $out_file must parse as clean JSON
# (step 2) would otherwise see a log line interleaved into it -- and failing with $label and the
# command's own tail of stderr unless it exits with $expected_code. Every step below is one call.
run_step() {
  local label="$1" expected_code="$2" out_file="$3" err_file="$4"
  shift 4
  set +e
  run_py "$@" >"$out_file" 2>"$err_file"
  local code=$?
  set -e
  if [ "$code" -ne "$expected_code" ]; then
    fail "$label exited $code, expected $expected_code: $(tail -n 5 "$err_file")"
  fi
}

# Every assertion in $1/assertions.json has outcome $2 (python3 -c does the one-outcomes check used
# by step 1).
assert_only_outcome() {
  local out="$1" expected="$2" label="$3"
  local outcomes
  outcomes="$(python3 -c "
import json
data = json.load(open('$out/assertions.json'))
print(sorted({a['outcome'] for a in data}))
")"
  if [ "$outcomes" != "['$expected']" ]; then
    fail "$label: expected every assertion $expected, got $outcomes"
  fi
}

# Step 1: a records-folder run against the default baseline catalog -- every assertion
# insufficient_evidence, none not_applicable (the records side of the divergence; a records run has
# no formal applicability declaration, so an empty population says the records show none, never
# that the control is out of scope).
run_step "step 1 (records/, default catalog)" 0 "$work/step1.out" "$work/step1.err" \
  assess "$fixture/records" --out "$work/step1"
assert_only_outcome "$work/step1" insufficient_evidence "step 1"

# Step 2: the very same derived evidence, reduced to a formal --bundle/--profile assessment -- every
# control the records give no population for flips to not_applicable (the bundle side of the
# divergence this gate exists to prove). DOC-01 is the one exception: the records hold a ToolCall,
# which is DOC-01's population, and no BundleLoaded manifest declaring it, so DOC-01 reads
# insufficient_evidence and, being severity high, the run exits 2.
run_step "step 2 (--bundle/--profile on the same evidence)" 2 "$work/step2.json" "$work/step2.err" \
  assess --bundle "$work/step1/records-bundle" --profile "$work/step1/applicability.yaml" \
  --out "$work/step2" --json
outcomes="$(python3 -c "
import json
data = json.load(open('$work/step2/assertions.json'))
print(sorted({(a['outcome'] if a['control'] == 'DOC-01' else 'other:' + a['outcome']) for a in data}))
")"
if [ "$outcomes" != "['insufficient_evidence', 'other:not_applicable']" ]; then
  fail "step 2: expected DOC-01 insufficient_evidence and every other control not_applicable, got $outcomes"
fi

# Step 3: the same records folder against the gate-only QP-UNLOCK catalog -- a self-reported
# ToolCall but no ModelCall leaves exactly one requirement missing: one assertion
# (QP-UNLOCK, insufficient_evidence) and one named blind spot naming QP-UNLOCK as unlocked,
# checks_unlocked: 1.
run_step "step 3 (QP-UNLOCK catalog)" 0 "$work/step3.out" "$work/step3.err" \
  assess "$fixture/records" --catalog-dir "$fixture/catalog" --allow-unverified-catalog \
  --out "$work/step3"
if ! python3 - "$work/step3" <<'PY'
import json
import sys

out = sys.argv[1]
assertions = json.load(open(f"{out}/assertions.json", encoding="utf-8"))
if [(a["control"], a["outcome"]) for a in assertions] != [("QP-UNLOCK", "insufficient_evidence")]:
    print(f"assertions.json: {assertions}", file=sys.stderr)
    sys.exit(1)

spots = json.load(open(f"{out}/blind-spots.json", encoding="utf-8"))["blind_spots"]
if len(spots) != 1:
    print(f"blind-spots.json: expected exactly one blind spot, got {spots}", file=sys.stderr)
    sys.exit(1)
spot = spots[0]
unlocked_controls = {c["control"] for c in spot["unlocked_checks"]}
if (
    spot["event"] != "ModelCall"
    or spot["class"] != "self_report"
    or spot["checks_unlocked"] != 1
    or "QP-UNLOCK" not in unlocked_controls
):
    print(f"blind-spots.json: unexpected blind spot {spot}", file=sys.stderr)
    sys.exit(1)
PY
then
  fail "step 3: assertions.json/blind-spots.json did not match the gate's rubric"
fi

# Step 4: the step-1 derived profile still passes the skill's own lint despite its short,
# exploratory window -- closing the loop with 18.32 C1 (pilot_window: true); the gate fails if C1's
# fix regresses. lint_profile.py exits 1 on a non-clean profile, so the call runs outside `set -e`
# (like run_step) and its own output drives the fail message, instead of the script exiting silently
# on the command substitution's exit code.
set +e
lint_out="$(cd "$root/engines/python" && env -u VIRTUAL_ENV uv run --frozen --quiet python \
  "$root/skills/agentce-get-evidence/scripts/lint_profile.py" \
  --profile "$work/step1/applicability.yaml" --json)"
set -e
if ! echo "$lint_out" | python3 -c "import json, sys; sys.exit(0 if json.load(sys.stdin)['clean'] is True else 1)"; then
  fail "step 4: the derived profile no longer passes the skill's lint: $lint_out"
fi

echo "quick-path: records-vs-bundle divergence, checks_unlocked, and the derived profile's lint are all OK"
