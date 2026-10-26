#!/usr/bin/env bash
# Build gate helper for VG-REPORT-FORMAT-PARITY: TypeScript and Java render report.junit.xml,
# report.csv, oscal-ar.xml, public-statement.md and report.pdf byte-identical to Python over the corpus and the
# hostile case table (18.16c, 18.16d), the remediation package and remediation.md (18.16e) and the fix-list skill folder
# (18.16f) as Python writes them. The TypeScript dist and the Java jar are rebuilt on every run, so a
# seeded fault in engine source is what the comparison sees.
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
(cd "$root/engines/typescript" && pnpm --silent build >/dev/null)
(cd "$root/engines/java" && ./gradlew --no-daemon --quiet :assemble)
cd "$root/tools"
check() { env -u VIRTUAL_ENV uv run --frozen python report_format_parity_check.py "$@"; }
check --self-test
check --fail-fast
