#!/usr/bin/env bash
# Build gate helper for VG-YAML-PROFILE (18.184u): every engine reads YAML under the SPEC 8.7 profile. The shared
# case table spec/rules/yaml-profile-cases.json keeps a refuse case for each rule, and each engine's case-table test
# runs every case through that engine's real loading path: a refuse case gives the file role's key with a message
# naming the rule and line, an accept case loads.
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$root"

env -u VIRTUAL_ENV uv run --project tools --frozen python - spec/rules/yaml-profile-cases.json <<'PY'
import json
import sys

table = json.load(open(sys.argv[1], encoding="utf-8"))
refused = {c["rule"] for c in table["cases"] if c["expect"] == "refuse"}
missing = sorted(set(table["hints"]) - refused)
if missing:
    print(f"FAIL the case table has no refuse case for: {', '.join(missing)}")
    sys.exit(1)
print(f"PASS the case table keeps a refuse case for each of {len(table['hints'])} rules")
PY

(cd engines/python && env -u VIRTUAL_ENV uv run --frozen pytest -q -p no:cacheprovider tests/test_yaml_profile.py) |
  tail -n 3
echo "PASS python case-table test"

# Install here: a gate's place in a CI shard decides which gates ran before it.
(cd engines/typescript && pnpm install --frozen-lockfile >/dev/null && node --import tsx --test src/yamlProfile.test.ts) |
  tail -n 8
echo "PASS typescript case-table test"

(cd engines/java && ./gradlew --no-daemon --quiet test --rerun --tests org.agentce.YamlProfileTest)
echo "PASS java case-table test"
