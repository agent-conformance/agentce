#!/usr/bin/env bash
# Build gate helper for VG-OWN-RULES: an operator installs fresh, authors a from-scratch catalog
# (catalog init), previews its own support matrix (catalog lint --support-matrix), signs it with a
# freshly generated key (catalog sign --new-key --write-trust-root), and assesses their own evidence
# bundle against it, from an empty directory to a real conformant verdict within the timed budget
# (Hill 6); a catalog tampered after signing is refused.
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
(cd "$root" && env -u VIRTUAL_ENV uv run --project tools --frozen python tools/installed_artifacts_check.py own_rules)
