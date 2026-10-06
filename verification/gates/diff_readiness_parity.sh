#!/usr/bin/env bash
# Build gate helper for VG-DIFF-READINESS-PARITY: Python, TypeScript and Java give byte-identical
# `agentce diff` and `agentce readiness` output and exit codes (18.59). Both checkers use Python as the
# reference, so a fault in either port is what they catch; VG-DIFF covers Python's diff alone. The
# TypeScript dist and the Java jar are rebuilt on every run, so a seeded fault in engine source is what
# the comparison sees.
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
(cd "$root/engines/typescript" && pnpm --silent build >/dev/null)
(cd "$root/engines/java" && ./gradlew --no-daemon --quiet :assemble)
cd "$root/tools"
check() { env -u VIRTUAL_ENV uv run --frozen python "$@"; }
check diff_parity_check.py --self-test
check diff_parity_check.py
check readiness_parity_check.py --self-test
check readiness_parity_check.py
