#!/usr/bin/env bash
# Build gate helper for VG-PYTHON-VERSIONS (18.114): the Python engine's whole test suite passes on
# every Python its requires-python admits, not only the one CI's other jobs pin. Pass the versions to
# run; with none, all four run. 3.12.10 is named on its own: its argparse already drops a leading `--`
# the way 3.13 does, where 3.12.3's does not.
#
# Each version runs in its own copy of the working tree (tracked and untracked files, so an uncommitted
# change such as a seeded fault is included) and its own isolated environment, never the shared
# engines/python/.venv: some tests edit engine files in place, so two versions must not share a tree.
# The versions run at the same time; the test files whose answers changed between versions run first,
# then the rest of the suite, and the first failing version stops the rest.
set -uo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$root"
[ "$#" -gt 0 ] || set -- 3.14 3.13 3.12.10 3.12.3
first=(
  engines/python/tests/test_input_hardening.py
  engines/python/tests/test_cli.py
  engines/python/tests/test_verify_targets.py
)
work="$(mktemp -d)"
pids=()
# Each version runs in its own process group (job control on), so stopping it stops uv and pytest too.
set -m
stop() {
  for pid in "${pids[@]}"; do
    [ -n "$pid" ] && kill -TERM -- "-$pid" 2>/dev/null
  done
  wait 2>/dev/null
  rm -rf "$work"
}
trap stop EXIT
versions=()
for version in "$@"; do
  tree="$work/$version"
  mkdir -p "$tree"
  git ls-files -z -co --exclude-standard | rsync -a --from0 --files-from=- ./ "$tree/"
  (
    cd "$tree" &&
      pytest() {
        env -u VIRTUAL_ENV uv run --isolated --python "$version" --project engines/python --frozen \
          python -m pytest -q -x -p no:cacheprovider "$@"
      } &&
      pytest "${first[@]}" &&
      pytest "${first[@]/#/--ignore=}" engines/python/tests
  ) >"$work/$version.log" 2>&1 &
  pids+=("$!")
  versions+=("$version")
done
# bash 3.2 (macOS) has no `wait -n`: poll, and stop at the first version that fails.
left=${#pids[@]}
while [ "$left" -gt 0 ]; do
  for i in "${!pids[@]}"; do
    pid="${pids[$i]}"
    [ -n "$pid" ] && ! kill -0 "$pid" 2>/dev/null || continue
    wait "$pid"
    code=$?
    pids[$i]=""
    left=$((left - 1))
    version="${versions[$i]}"
    echo "== Python $version (exit $code)"
    tail -n 1 "$work/$version.log"
    if [ "$code" -ne 0 ]; then
      tail -n 40 "$work/$version.log" >&2
      echo "VG-PYTHON-VERSIONS: the Python engine's tests fail on Python $version" >&2
      exit 1
    fi
  done
  sleep 2
done
