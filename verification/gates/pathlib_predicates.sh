#!/usr/bin/env bash
# Build gate helper for VG-PATHLIB-PREDICATES (18.163): no new bare Path.is_file/is_dir/exists/is_symlink
# call in the Python engine beyond the committed, shrink-only baseline. Those four answer a path under a
# folder that can be listed but not entered differently on 3.12/3.13 (raise) and 3.14 (False); the engine's
# own helpers, signing.is_regular_file and is_directory, answer the same on every Python.
set -uo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
check=(env -u VIRTUAL_ENV uv run --no-project python "$root/verification/gates/pathlib_predicates_check.py")
"${check[@]}" --self-test || exit 1
"${check[@]}" "$root/engines/python/agentce" "$root/verification/gates/fixtures/pathlib_predicates/baseline.json"
