#!/usr/bin/env bash
# Build gate helper for VG-REPORT-VALIDATE: `agentce report --validate` performs real
# schema-validation of report artifacts, including the two-stage real-third-party-standard check
# for oscal-ar.json, in all three engines, and every engine reads report files by the SPEC 8.7 report-file reader
# contract: tools/report_validate_parity_check.py --reader-cases runs every case of spec/report/reader-cases.json
# (report --validate, readiness, sign, diff) in all three engines, and tools/report_validate_reader_census.py checks
# the table keeps a case for every reader class and every engine's reader names only census classes (18.60).
#
# Reuses tools/report_validate_parity_check.py's own build_base_report/seed_oscal_nist_fault (the
# same builders 18.27's own C3/C4 checks use) rather than authoring a second, independently-drifting
# fixture pair -- the same departure from golden_loop.sh's own-fixtures precedent VG-SIGN already
# takes, for the same reason: this gate and C3 exercise the exact same three-engine validate
# behaviour.
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
work="$(mktemp -d)"
trap 'chmod -R u+rwx "$work" 2>/dev/null; rm -rf "$work"' EXIT

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
unreadable = work / "unreadable"
shutil.copytree(base, unreadable)
rvpc._mutate_optional_mode000(unreadable)
print(json.dumps({"base": str(base), "corrupted": str(corrupted), "unreadable": str(unreadable)}))
PY
)
base="$(jq -r .base "$fixture_json")"
corrupted="$(jq -r .corrupted "$fixture_json")"
unreadable="$(jq -r .unreadable "$fixture_json")"
missing="$work/does-not-exist"

# Build here: a gate's place in a CI shard decides which gates ran before it, and a seeded fault in an engine's
# source only reaches the parity tool through a fresh build.
(cd "$root/engines/typescript" && pnpm install --frozen-lockfile >/dev/null && pnpm --silent build >/dev/null)
(cd "$root/engines/java" && ./gradlew --no-daemon --quiet :assemble installDist)

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

# An optional artifact that is there but cannot be read is a problem, never an absence (18.167).
for engine in python typescript java; do
  out="$(run_validate "$engine" "$unreadable")" && code=0 || code=$?
  got="$(printf '%s' "$out" | jq -c '[.valid, .message_key, .problems]' 2>/dev/null || echo none)"
  want='[false,"report.validation_failed",["auditor.json: cannot read (permission denied)"]]'
  if [ "$code" -ne 3 ] || [ "$got" != "$want" ]; then
    echo "report-validate: $engine did not refuse an unreadable auditor.json (exit $code, got $got, expected exit 3 and $want)" >&2
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

# SPEC 8.7 report-file reader contract (18.60): the case table covers every reader class, and every case gives the
# table's exit, key and named rule in all three engines with the folder left unchanged.
if ! (cd "$root" && env -u VIRTUAL_ENV uv run --project tools --frozen python tools/report_validate_reader_census.py); then
  echo "report-validate: spec/report/reader-cases.json or an engine's reader is outside the reader census" >&2
  status=1
fi
if ! (cd "$root" && env -u VIRTUAL_ENV uv run --project tools --frozen python tools/report_validate_parity_check.py --reader-cases); then
  echo "report-validate: an engine does not read report files by the SPEC 8.7 reader contract (spec/report/reader-cases.json)" >&2
  status=1
fi

[ "$status" -eq 0 ] && echo "report-validate: all three engines validate the clean report, catch the real-schema-only OSCAL violation, refuse an unreadable auditor.json, refuse a missing report directory, and read every reader-cases.json case by the SPEC 8.7 reader contract"
exit "$status"
