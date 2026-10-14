#!/usr/bin/env bash
# Build gate VG-TRUST-MATRIX (item 18.176): the trust-model case matrix (fixtures/trust_matrix/matrix.yaml) in the
# real Python, TypeScript and Java CLIs, judged against the open list and the coverage checks (trust_matrix.py). Runs
# the quick list, or every cell with AGENTCE_VERIFY_TIER=full; arguments pass on to trust_matrix.py.
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
# shellcheck source=build_engines.sh
. "$root/verification/gates/build_engines.sh"
build_engines
env -u VIRTUAL_ENV uv run --project "$root/engines/python" --frozen python "$root/verification/gates/trust_matrix.py" "$@"
