#!/usr/bin/env bash
# Build gate helper for VG-CLAIM-PARITY: Python, TypeScript and Java assess write the same
# claim.json, each engine signs its own report end to end, and a malformed claim.json is refused
# alike (18.53). The TypeScript dist and the Java jar are rebuilt on every run, so a seeded fault in
# engine source is what the comparison sees.
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
(cd "$root/engines/typescript" && pnpm --silent build >/dev/null)
(cd "$root/engines/java" && ./gradlew --no-daemon --quiet :assemble)
cd "$root/tools"
check() { env -u VIRTUAL_ENV uv run --frozen python claim_parity_check.py "$@"; }
check --sign-refusals
check --end-to-end
check --fail-fast
