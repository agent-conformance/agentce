#!/usr/bin/env bash
# Build gate helper for VG-PYTHON-VERSIONS (18.114): the Python engine's whole test suite passes on
# every Python its requires-python admits, not only the one CI's other jobs pin. Each version runs in
# its own isolated environment (never the shared engines/python/.venv), one after another, because
# some tests edit engine files in place. Pass the versions to run; with none, all four run, newest
# first, and the gate stops at the first red one. 3.12.10 is named on its own: its argparse already
# drops a leading `--` the way 3.13 does, where 3.12.3's does not.
set -uo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$root"
[ "$#" -gt 0 ] || set -- 3.14 3.13 3.12.10 3.12.3
for version in "$@"; do
  echo "== Python $version"
  if ! env -u VIRTUAL_ENV uv run --isolated --python "$version" --project engines/python --frozen \
    python -m pytest -q -p no:cacheprovider engines/python/tests; then
    echo "VG-PYTHON-VERSIONS: the Python engine's tests fail on Python $version" >&2
    exit 1
  fi
done
