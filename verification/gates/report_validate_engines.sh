#!/usr/bin/env bash
# Build gate helper for VG-REPORT-VALIDATE: `agentce report --validate` performs real
# schema-validation of report artifacts, including the two-stage real-third-party-standard check
# for oscal-ar.json, in all three engines.
#
# Reuses tools/report_validate_parity_check.py's own build_base_report/seed_oscal_nist_fault (the
# same builders 18.27's own C3/C4 checks use) rather than authoring a second, independently-drifting
# fixture pair -- the same departure from golden_loop.sh's own-fixtures precedent VG-SIGN already
# takes, for the same reason: this gate and C3 exercise the exact same three-engine validate
# behaviour.
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

fixture_json="$work/fixture.json"
(cd "$root/tools" && env -u VIRTUAL_ENV uv run --frozen python - "$work" > "$fixture_json" <<'PY'
import json
import sys
from pathlib import Path

import report_validate_parity_check as rvpc

work = Path(sys.argv[1])
base = rvpc.build_base_report(work / "base")
corrupted = work / "oscal-nist"
import shutil
shutil.copytree(base, corrupted)
rvpc.seed_oscal_nist_fault(corrupted)
print(json.dumps({"base": str(base), "corrupted": str(corrupted)}))
PY
)
base="$(jq -r .base "$fixture_json")"
corrupted="$(jq -r .corrupted "$fixture_json")"
missing="$work/does-not-exist"

(cd "$root/engines/java" && ./gradlew --no-daemon --quiet installDist)

run_validate() {
  local engine="$1"
  local dir="$2"
  case "$engine" in
    python)
      (cd "$root/engines/python" && env -u VIRTUAL_ENV uv run --frozen agentce report --validate "$dir" --json)
      ;;
    typescript)
      (cd "$root/engines/typescript" && pnpm --silent agentce report --validate "$dir" --json)
      ;;
    java)
      (cd "$root/engines/java" && ./build/install/agentce/bin/agentce report --validate "$dir" --json)
      ;;
  esac
}

status=0

for engine in python typescript java; do
  out="$(run_validate "$engine" "$base")" && code=0 || code=$?
  valid="$(printf '%s' "$out" | jq -r 'if has("valid") then (.valid | tostring) else "none" end')"
  if [ "$code" -ne 0 ] || [ "$valid" != "true" ]; then
    echo "report-validate: $engine's clean base report did not validate (exit $code, valid $valid, expected exit 0 and valid=true)" >&2
    status=1
  fi
done

for engine in python typescript java; do
  out="$(run_validate "$engine" "$corrupted")" && code=0 || code=$?
  valid="$(printf '%s' "$out" | jq -r 'if has("valid") then (.valid | tostring) else "none" end')"
  problems="$(printf '%s' "$out" | jq -c '.problems // []')"
  marker="oscal-ar.json (NIST OSCAL 1.1.2)"
  named="$(printf '%s' "$problems" | jq --arg m "$marker" 'any(.[]; startswith($m))')"
  if [ "$code" -ne 3 ] || [ "$valid" != "false" ] || [ "$named" != "true" ]; then
    echo "report-validate: $engine did not catch the real-schema-only OSCAL violation (exit $code, valid $valid, problems $problems, expected exit 3, valid=false, naming '$marker')" >&2
    status=1
  fi
done

for engine in python typescript java; do
  out="$(run_validate "$engine" "$missing")" && code=0 || code=$?
  key_got="$(printf '%s' "$out" | jq -r '.error.key // .error.message_key // empty')"
  if [ "$code" -ne 3 ] || [ "$key_got" != "input.validate_not_a_directory" ]; then
    echo "report-validate: $engine did not refuse a missing report directory (exit $code, key ${key_got:-none}, expected exit 3 and input.validate_not_a_directory)" >&2
    status=1
  fi
done

[ "$status" -eq 0 ] && echo "report-validate: all three engines validate the clean report, catch the real-schema-only OSCAL violation, and refuse a missing report directory"
exit "$status"
