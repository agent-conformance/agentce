#!/usr/bin/env bash
# Build gate helper for VG-JSON-INTEGER-LIMIT: an integer literal over 4300 digits is invalid JSON in
# all three engines and in Python under every int_max_str_digits setting, so ingest quarantines the
# line and report --validate names the file (item 18.71). The checks live in
# tools/json_integer_limit_parity_check.py; this script builds the TypeScript and Java engines first
# so a seeded fault in their sources is what the check runs.
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"

(cd "$root/engines/typescript" && pnpm install --frozen-lockfile >/dev/null && pnpm build >/dev/null)
(cd "$root/engines/java" && ./gradlew --no-daemon --quiet installDist)

cd "$root/tools"
env -u VIRTUAL_ENV uv run --frozen python json_integer_limit_parity_check.py --self-test
env -u VIRTUAL_ENV uv run --frozen python json_integer_limit_parity_check.py
