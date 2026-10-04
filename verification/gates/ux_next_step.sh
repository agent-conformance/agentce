#!/usr/bin/env bash
# Build gate helper for VG-UX-NEXT-STEP (18.40, USER_EXPERIENCE.md R4): the AST scan
# (ux_next_step.py) over every literal-key, literal-fix InputError/AgentceError call site in the
# Python engine passes against the real tree, and the committed docs/errors.md -- the real,
# published error reference -- is exactly what `agentce doctor --write-errors` writes today and
# names every key the scan found raised. Catching either the consistency/registration/actor-naming
# defect (the scan) or a merely-stale generated reference (the doctor-write diff) independently --
# a gate that only ran one leg could pass while the other drifted.
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

status=0

echo "ux-next-step: AST scan (literal-key, literal-fix call sites; actor-naming table)"
if ! (cd "$root" && uv run --project tools --frozen python3 verification/gates/ux_next_step.py); then
  echo "ux-next-step: the scan found a consistency, registration, or actor-naming problem" >&2
  status=1
fi

echo "ux-next-step: docs/errors.md is exactly what agentce doctor --write-errors writes today"
(cd "$root/engines/python" && env -u VIRTUAL_ENV uv run --frozen agentce doctor \
  --write-errors "$work/errors.md" >/dev/null)
if ! diff -q "$work/errors.md" "$root/docs/errors.md" >/dev/null; then
  echo "ux-next-step: docs/errors.md is stale relative to a real doctor --write-errors run" >&2
  diff -u "$work/errors.md" "$root/docs/errors.md" >&2 || true
  status=1
fi

echo "ux-next-step: every key the scan found raised appears as a row in that reference"
(cd "$root" && uv run --project tools --frozen python3 verification/gates/ux_next_step.py \
  --list-raised-keys) > "$work/raised_keys.txt"
while IFS= read -r key; do
  if ! grep -qF "\`$key\`" "$work/errors.md"; then
    echo "ux-next-step: '$key' is raised with a literal key and fix but has no row in the doctor-written reference" >&2
    status=1
  fi
done < "$work/raised_keys.txt"

[ "$status" -eq 0 ] && echo "ux-next-step: the scan is clean and docs/errors.md matches a real doctor --write-errors run, naming every key the scan found raised"
exit "$status"
