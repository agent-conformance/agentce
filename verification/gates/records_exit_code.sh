#!/usr/bin/env bash
# Build gate helper for VG-RECORDS-EXIT-CODE (item 18.72, SPEC §8.5): a records-folder scan
# (`agentce assess <folder>`) never returns exit 2 on its own, so a first run over the records a team
# already keeps is not a failure. A team that wants CI to fail on evidence gaps opts in with
# `--fail-on 'outcome=="insufficient_evidence" and severity=="high"'`, which returns 1. Exit 2 stays
# with the formal --bundle/--profile assessment, and G2 shows it: the same evidence and the same
# assertions exit 2 there and 1 as a records scan, so the mode decides, not the assertions.
#
# The fixture (verification/gates/fixtures/records_exit_code/) is VG-QUICK-PATH's pinned copy of the
# otel-genai agent-session export plus a gate-only catalog: RX-GAP (severity high, needs a ModelCall
# the records lack: insufficient_evidence) and RX-FAIL (medium, every recorded ToolCall fails it:
# non-conformant). Over that catalog the gate pins every assertion exactly; over the default catalog
# (G5-G7) it checks properties, so a baseline catalog release does not break it.
#
# Python only for now: TypeScript and Java have no records-folder mode until item 18.69 ports it,
# and item 18.72a then runs these scenarios through all three engines.
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
fixture="$root/verification/gates/fixtures/records_exit_code"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
gap='outcome=="insufficient_evidence" and severity=="high"'
catalog=(--catalog-dir "$fixture/catalog" --allow-unverified-catalog)
rx='[["RX-FAIL","non-conformant","medium"],["RX-GAP","insufficient_evidence","high"]]'

fail() {
  echo "records-exit-code: $1" >&2
  exit 1
}

# check <id> <exit> <exit_status> <fail_on> <assertions> <assess args...>
# Runs `assess <args...> --out <work>/<id> --json` and fails unless the exit code is <exit> and the
# --json exit_status and fail_on equal the given JSON values. <assertions> is either the exact JSON
# list of sorted [control, outcome, severity] for every assertion that is not conformant, or
# "baseline": every assertion insufficient_evidence and at least one severity high (G6 passes the
# severity-high count from G5 as its expected fail_on.matched). A mismatch prints its reason and exits.
check() {
  local id="$1" want_code="$2" want_status="$3" want_fail_on="$4" want_assertions="$5"
  shift 5
  set +e
  (cd "$root/engines/python" && env -u VIRTUAL_ENV uv run --frozen agentce assess "$@" --out "$work/$id" --json) \
    >"$work/$id.json" 2>"$work/$id.err"
  local code=$?
  set -e
  [ "$code" -eq "$want_code" ] || fail "$id exited $code, expected $want_code: $(tail -n 5 "$work/$id.err")"
  python3 - "$id" "$work/$id.json" "$work/$id/assertions.json" "$want_status" "$want_fail_on" "$want_assertions" <<'PY' ||
import json
import sys

sid, result_path, assertions_path, status, fail_on, expected = sys.argv[1:]
result = json.load(open(result_path, encoding="utf-8"))
assertions = json.load(open(assertions_path, encoding="utf-8"))
problems = []
if result.get("exit_status") != json.loads(status):
    problems.append(f"exit_status {result.get('exit_status')}, expected {status}")
if result.get("fail_on") != json.loads(fail_on):
    problems.append(f"fail_on {result.get('fail_on')}, expected {fail_on}")
if expected == "baseline":
    outcomes = sorted({a["outcome"] for a in assertions})
    high = sum(1 for a in assertions if a["severity"] == "high")
    if outcomes != ["insufficient_evidence"] or high == 0:
        problems.append(f"expected only insufficient_evidence with a severity-high one, got {outcomes}, {high} high")
else:
    got = sorted([a["control"], a["outcome"], a["severity"]] for a in assertions if a["outcome"] != "conformant")
    if got != json.loads(expected):
        problems.append(f"assertions {got}, expected {expected}")
if problems:
    print(f"{sid}: " + "; ".join(problems), file=sys.stderr)
    sys.exit(1)
print(f"{sid}: exit {result['exit_code']} {result['exit_status']} fail_on={result.get('fail_on')}")
PY
    exit 1
}

# fail_on_json <expression> <matched>: the --json fail_on object for an opt-in run.
fail_on_json() {
  python3 -c 'import json, sys; print(json.dumps({"expression": sys.argv[1], "matched": int(sys.argv[2])}))' "$1" "$2"
}

# G1: records + gate catalog: a severity-high gap and a finding; exit 1, never 2.
check G1 1 '["findings"]' null "$rx" "$fixture/records" "${catalog[@]}"
# G2: the same derived evidence as a formal --bundle/--profile assessment: exit 2 (the control case).
check G2 2 '["findings","insufficient_evidence"]' null "$rx" \
  --bundle "$work/G1/records-bundle" --profile "$work/G1/applicability.yaml" "${catalog[@]}"
# G3: a records folder with a declared --profile (the derived one passed back) is still a records scan.
check G3 1 '["findings"]' null "$rx" "$fixture/records" --profile "$work/G1/applicability.yaml" "${catalog[@]}"
# G4: G1 plus the opt-in: it decides the exit and counts the one gap.
check G4 1 '["findings"]' "$(fail_on_json "$gap" 1)" "$rx" "$fixture/records" "${catalog[@]}" --fail-on "$gap"
# G5: a newcomer's first run against the default catalog: severity-high gaps, exit 0.
check G5 0 '["ok"]' null baseline "$fixture/records"
# G6: G5 plus the opt-in: exit 1, matched = the severity-high gaps.
high=$(python3 -c 'import json, sys; print(sum(a["severity"] == "high" for a in json.load(open(sys.argv[1]))))' "$work/G5/assertions.json")
check G6 1 '["findings"]' "$(fail_on_json "$gap" "$high")" baseline "$fixture/records" --fail-on "$gap"
# G7: an opt-in that matches nothing keeps exit 0.
check G7 0 '["ok"]' "$(fail_on_json 'severity=="critical"' 0)" baseline "$fixture/records" --fail-on 'severity=="critical"'
# G8: the docs recipe's combined form fails on the gap and on the finding.
both="$gap or outcome==\"non-conformant\""
check G8 1 '["findings"]' "$(fail_on_json "$both" 2)" "$rx" "$fixture/records" "${catalog[@]}" --fail-on "$both"

# G9: an unquoted literal is refused before anything is written: exit 3, input.fail_on_invalid_expression.
set +e
(cd "$root/engines/python" && env -u VIRTUAL_ENV uv run --frozen agentce assess "$fixture/records" "${catalog[@]}" \
  --fail-on 'outcome==insufficient_evidence' --out "$work/G9" --json) >"$work/G9.json" 2>"$work/G9.err"
code=$?
set -e
[ "$code" -eq 3 ] || fail "G9 exited $code, expected 3: $(tail -n 5 "$work/G9.err")"
key=$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["error"]["key"])' "$work/G9.json")
[ "$key" = "input.fail_on_invalid_expression" ] || fail "G9: error key $key, expected input.fail_on_invalid_expression"
[ ! -e "$work/G9/assertions.json" ] || fail "G9: a refused run wrote assertions.json"
echo "G9: exit 3 $key, nothing written"
echo "records-exit-code: G1-G9 hold (Python)"
