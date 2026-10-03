#!/usr/bin/env bash
# Build gate helper for VG-CATALOG-RULE-UNIQUE (18.37, SPEC §7.3): no two rung-2 controls in a
# catalog share an identical shape and fixture set, so a control's rule always tests what its own
# title says -- found live in 18.37, where DOC-01 shipped with REC-01's shape and fixtures verbatim
# (baseline and eu-ai-act) and ROB-02 shipped with DAT-01's shape and fixtures verbatim (eu-ai-act).
# The check scans every catalog under spec/catalogs/{base,overlays} (not just
# the three named in the baseline file), and is baseline-aware: `verification/gates/fixtures/
# rule_uniqueness/baseline.json` discloses today's known pre-existing duplicate pairs (owned by
# 18.37a, 18.37b, 18.37c). The gate fails on a pair NOT already in that file -- a genuinely new
# duplicate -- and on a baseline entry that is no longer a real duplicate (the file shrinks, never
# grows by staying stale).
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
status=0

echo "catalog-rule-uniqueness: python unit tests"
if ! (cd "$root/engines/python" && env -u VIRTUAL_ENV uv run --frozen pytest -q -o addopts= -p no:cacheprovider \
  tests/test_rule_uniqueness.py); then
  echo "catalog-rule-uniqueness: rule_uniqueness_problems/new_rule_uniqueness_problems did not behave as the baseline expects" >&2
  status=1
fi

echo "catalog-rule-uniqueness: every shipped base and overlay catalog, against the committed baseline"
if ! (cd "$root/engines/python" && env -u VIRTUAL_ENV uv run --frozen python3 \
  "$root/verification/gates/catalog_rule_uniqueness_check.py" \
  "$root/spec/catalogs" "$root/verification/gates/fixtures/rule_uniqueness/baseline.json"); then
  status=1
fi

[ "$status" -eq 0 ] && echo "catalog-rule-uniqueness: no shipped catalog has a duplicate rung-2 shape/fixture pair beyond the committed, disclosed baseline"
exit "$status"
