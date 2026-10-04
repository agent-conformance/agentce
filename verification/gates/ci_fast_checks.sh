#!/usr/bin/env bash
# Build gate helper for VG-CI-FAST-CHECKS (18.37i): see ci_fast_checks_check.py's module docstring.
# Covers, for real, in the quick tier: ruff check, ruff format --check, mypy, pnpm lint,
# pnpm typecheck, catalog_registry_check.py --self-test, registry_check.py --self-test -- the exact
# commands and directories ci_fast_checks_check.py's MANIFEST pins, checked against CI itself.
# Same two-call shape as ci_demo_coverage.sh -- the self-test first (the drift detector has teeth),
# then the real script, which checks the CI workflows against its own MANIFEST and, if clean, runs
# every MANIFEST entry for real itself (the single source of truth is also what executes).
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$root"
env -u VIRTUAL_ENV uv run --project tools --frozen python3 verification/gates/ci_fast_checks_check.py --self-test
env -u VIRTUAL_ENV uv run --project tools --frozen python3 verification/gates/ci_fast_checks_check.py
