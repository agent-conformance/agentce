#!/usr/bin/env bash
# Build gate helper for VG-CLI-OPTIONS (item 18.105, MAINTAINER-INBOX row 153): every command line in
# fixtures/cli_options/cases.json (or the case file named as the first argument: top_level.json for
# VG-CLI-TOP-LEVEL, 18.108) gives its expected exit code and refusal key in the real Python,
# TypeScript and Java CLIs, and a refused run writes nothing under --out. Builds the TypeScript dist and
# the Java jar first so a stale build cannot pass, skipping a build whose inputs hash the same as at its
# last build here (18.107: the seeded-fault demo runs this once per fault, and a fault in one engine
# leaves the other's build untouched).
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"

# shellcheck source=build_engines.sh
. "$root/verification/gates/build_engines.sh"
build_engines
env -u VIRTUAL_ENV uv run --project "$root/engines/python" --frozen python "$root/verification/gates/cli_options_check.py" "$@"
