#!/usr/bin/env bash
# Build gate helper for VG-REACH-TABLE (18.41): see reach_table_check.py's module docstring. Same
# two-call shape as ingest_support_matrix.sh -- the self-test first (proves the gate discriminates:
# a hand-edited primary or secondary count, a missing headline, a missing or duplicated section, a
# duplicated row, a missing/wrong source, each its own message key), then the real check against the
# committed README.md, the docs and website Supported-sources pages, and the landing page.
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$root"
env -u VIRTUAL_ENV uv run --project tools --frozen python3 verification/gates/reach_table_check.py --self-test
env -u VIRTUAL_ENV uv run --project tools --frozen python3 verification/gates/reach_table_check.py
