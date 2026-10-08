#!/usr/bin/env bash
# Build gate helper for VG-CLI-OPTIONS (item 18.105, MAINTAINER-INBOX row 153): every command line in
# fixtures/cli_options/cases.json gives its expected exit code and refusal key in the real Python,
# TypeScript and Java CLIs, and a refused run writes nothing under --out. Builds the TypeScript dist and
# the Java jar first so a stale build cannot pass.
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
(cd "$root/engines/typescript" && pnpm install --frozen-lockfile >/dev/null && pnpm build >/dev/null)
(cd "$root/engines/java" && ./gradlew --no-daemon :assemble -q)
env -u VIRTUAL_ENV uv run --project "$root/engines/python" --frozen python "$root/verification/gates/cli_options_check.py"
