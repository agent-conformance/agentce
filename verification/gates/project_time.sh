#!/usr/bin/env bash
# Build gate helper for VG-PROJECT-TIME: the risk-lead/CIO project view (Hill 7) over a records
# folder naming two distinct agents, with no --profile so every discovered agent is genuinely
# undeclared -- first the untimed correctness proof from the checkout, then the installed wheel
# timed from the start of the install.
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
(cd "$root/engines/python" && env -u VIRTUAL_ENV uv run --frozen pytest -q -o addopts= -p no:cacheprovider tests/test_records_assess.py::test_records_folder_multi_agent_for_risk_lead_writes_project_view)
(cd "$root" && env -u VIRTUAL_ENV uv run --project tools --frozen python tools/installed_artifacts_check.py project)
