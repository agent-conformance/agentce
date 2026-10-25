#!/usr/bin/env bash
# Build gate helper for VG-UX-COPY (18.40b, USER_EXPERIENCE.md R6): the reader's own self-test, then
# the copy rules over the READMEs, the skills, the English message catalogue and the three engines'
# CLI output, and the locked-copy sweep (ux_copy.py), on a fresh TypeScript dist and Java jar.
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
# shellcheck source=build_engines.sh
. "$root/verification/gates/build_engines.sh"
build_engines
cd "$root"
env -u VIRTUAL_ENV uv run --project tools --frozen python3 verification/gates/ux_copy.py --self-test
env -u VIRTUAL_ENV uv run --project tools --frozen python3 verification/gates/ux_copy.py
