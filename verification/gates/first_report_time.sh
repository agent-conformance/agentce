#!/usr/bin/env bash
# Build gate helper for VG-FIRST-REPORT-TIME: the behaviour of `agentce assess <folder>` in the checkout,
# then the installed wheel turning a folder of OpenTelemetry records into a report with no other argument,
# offline, within the five-minute budget.
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
(cd "$root/engines/python" && env -u VIRTUAL_ENV uv run --frozen pytest -q -o addopts= -p no:cacheprovider tests/test_records_assess.py)
(cd "$root" && env -u VIRTUAL_ENV uv run --project tools --frozen python tools/installed_artifacts_check.py records --offline)
