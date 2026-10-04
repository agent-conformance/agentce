#!/usr/bin/env bash
# Build gate helper for VG-INGEST-SUPPORT-MATRIX (18.39): see ingest_support_matrix_check.py's module
# docstring. Same two-call shape as ci_fast_checks.sh -- the self-test first (proves the gate
# discriminates: unknown source, wrong tier, count drift, docs-table drift, each its own message key),
# then the real check against the committed README.md, trace-store-connectors.md and
# spec/ingest/support-matrix.yaml.
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$root"
env -u VIRTUAL_ENV uv run --project tools --frozen python3 verification/gates/ingest_support_matrix_check.py --self-test
env -u VIRTUAL_ENV uv run --project tools --frozen python3 verification/gates/ingest_support_matrix_check.py
