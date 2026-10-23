#!/usr/bin/env bash
# Build gate helper for VG-JSON-PROFILE (18.184x): every engine reads JSON under the SPEC 8.7 profile. The shared case
# table spec/report/json-cases.json goes through each engine's json-cases seam and every result must equal the table's
# (tools/json_parity_check.py); seven real-CLI rows give the profile's exit code, key and text in all three engines;
# and a records line outside the profile on the write that makes ROB-02 non-conformant is quarantined naming the rule
# and never reads better (the json-profile- cases of VG-QUARANTINE-NO-PASS).
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$root"

# Install here: a gate's place in a CI shard decides which gates ran before it.
(cd engines/typescript && pnpm install --frozen-lockfile >/dev/null && pnpm --silent build >/dev/null)
(cd engines/java && ./gradlew --no-daemon --quiet :assemble installDist)

env -u VIRTUAL_ENV uv run --project tools --frozen python tools/json_parity_check.py --self-test
env -u VIRTUAL_ENV uv run --project tools --frozen python tools/json_parity_check.py
env -u VIRTUAL_ENV uv run --project tools --frozen python tools/json_parity_check.py --cli
QUARANTINE_NO_PASS_ONLY=json-profile- bash verification/gates/quarantine_no_pass.sh
