#!/usr/bin/env bash
# Build gate helper for VG-DIFF-READINESS-PARITY: Python, TypeScript and Java give byte-identical
# `agentce diff` and `agentce readiness` output and exit codes (18.59). The self-tests need no build,
# so they run first; the TypeScript dist and the Java jar are then rebuilt so the real checks see a
# seeded fault in engine source.
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
check() { (cd "$root/tools" && env -u VIRTUAL_ENV uv run --frozen python "$@"); }
check diff_parity_check.py --self-test
check readiness_parity_check.py --self-test
(cd "$root/engines/typescript" && pnpm --silent build >/dev/null)
(cd "$root/engines/java" && ./gradlew --no-daemon --quiet :runnableJar)
check diff_parity_check.py
check readiness_parity_check.py
