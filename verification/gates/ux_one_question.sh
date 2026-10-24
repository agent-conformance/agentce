#!/usr/bin/env bash
# Build gate helper for VG-UX-ONE-QUESTION (18.40a, USER_EXPERIENCE.md R2): the reader's own self-test, then
# the real check over the approved skills' First minute sections and the three engines' first-run
# commands at a pseudo-terminal (ux_one_question.py), on a fresh TypeScript dist and Java jar.
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
# shellcheck source=build_engines.sh
. "$root/verification/gates/build_engines.sh"
build_engines
cd "$root"
env -u VIRTUAL_ENV uv run --project tools --frozen python3 verification/gates/ux_one_question.py --self-test
env -u VIRTUAL_ENV uv run --project tools --frozen python3 verification/gates/ux_one_question.py
