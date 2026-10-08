#!/usr/bin/env bash
# Build gate helper for VG-CLI-OPTIONS (item 18.105, MAINTAINER-INBOX row 153): every command line in
# fixtures/cli_options/cases.json gives its expected exit code and refusal key in the real Python,
# TypeScript and Java CLIs, and a refused run writes nothing under --out. Builds the TypeScript dist and
# the Java jar first so a stale build cannot pass.
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
# The two builds are independent; Gradle is waited on by PID so set -e still sees a failure.
(cd "$root/engines/java" && ./gradlew --no-daemon --quiet :runnableJar) & gradle_pid=$!
(cd "$root/engines/typescript" && pnpm install --frozen-lockfile >/dev/null && pnpm build >/dev/null)
wait "$gradle_pid"
env -u VIRTUAL_ENV uv run --project "$root/engines/python" --frozen python "$root/verification/gates/cli_options_check.py"
