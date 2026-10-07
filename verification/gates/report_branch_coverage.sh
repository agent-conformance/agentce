#!/usr/bin/env bash
# Build gate helper for VG-REPORT-BRANCH-COVERAGE: `_verify_report` (engines/python/agentce/commands/
# __init__.py) has about 15 raise sites across its 9 reproduction steps, several sharing a message key, so
# an exit-code/key check alone cannot tell two branches apart (three verifier rounds on item 18.8 each found
# a live mutation an exit-code/key check missed). This runs the engine's own `test_verify_report*` tests
# under coverage.py's branch tracking and requires 100% line and branch coverage of `_verify_report` and every
# function nested in it, and refuses any coverage.py pragma or excluded line in its span (18.75).
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$root"
env -u VIRTUAL_ENV uv run --project tools --frozen python tools/verify_report_branch_coverage_check.py --self-test
env -u VIRTUAL_ENV uv run --project tools --frozen python tools/verify_report_branch_coverage_check.py
