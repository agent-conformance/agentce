#!/usr/bin/env bash
# Build gate helper for VG-PSP-SHAPE-PROFILE-ENFORCED (18.78): Python, TypeScript and Java refuse every
# catalog shape spec/rules/psp_check.py refuses, with the same key and feature, and Python's catalog lint
# reports it; a shape file that will not parse is catalog.shape.parse_error everywhere. The scenarios and
# their pinned answers are in tools/psp_profile_expected.json; tools/psp_profile_check.py runs them.
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$root"
env -u VIRTUAL_ENV uv run --project tools --frozen python tools/psp_profile_check.py
