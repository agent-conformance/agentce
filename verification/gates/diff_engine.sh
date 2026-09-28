#!/usr/bin/env bash
# Build gate helper for VG-DIFF: `agentce diff --format md` renders a classified "## What changed"
# section (closed/opened/other, over the full six-outcome vocabulary) against a dedicated,
# hand-authored fixture pair that exercises every C1(a) classification bucket at once: one closed
# (non-conformant -> conformant), one opened (conformant -> insufficient_evidence), one gap-to-gap
# other (partial -> not_assessed), one not_applicable-touching other (conformant -> not_applicable),
# and one added assertion (other regardless of its own outcome). Output is byte-compared against a
# committed golden (diff_golden.md) rather than re-deriving the classification inside this script,
# which would duplicate cmd_diff's own logic instead of exercising the installed command.
#
# `agentce diff`'s own exit code is 1 (ExitCode.FINDINGS) whenever anything differs, which this
# fixture always does, so this script checks for exit 1 explicitly rather than running the diff
# invocation under a bare `set -e`, which would misread it as a failure.
#
# Both fixture files are also validated against spec/report/assertions.schema.json directly with
# jsonschema (already a pinned dependency, engines/python/pyproject.toml) -- `agentce report
# --validate` takes a report *directory*, not a bare assertions.json file, and exits 3 on either
# input, so it cannot be used here (round 2 of contracts/P18-18.6.md, finding 3).
#
# Regenerate the golden with:
#   verification/gates/diff_engine.sh --write
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
fixture="$root/verification/gates/fixtures/diff"
golden="$root/verification/gates/diff_golden.md"

validate_fixtures() {
  (cd "$root/engines/python" && env -u VIRTUAL_ENV uv run --frozen python -c "
import json, pathlib, jsonschema
schema = json.loads(pathlib.Path('$root/spec/report/assertions.schema.json').read_text())
for f in ('$fixture/before/assertions.json', '$fixture/after/assertions.json'):
    jsonschema.validate(json.loads(pathlib.Path(f).read_text()), schema)
")
}

run_diff() {
  (cd "$fixture" && env -u VIRTUAL_ENV uv run --frozen --project "$root/engines/python" agentce diff before/assertions.json after/assertions.json --format md)
}

if ! validate_fixtures; then
  echo "diff: a fixture file is not a schema-valid assertions.json document" >&2
  exit 1
fi

if [ "${1:-}" = "--write" ]; then
  set +e
  out="$(run_diff)"
  code=$?
  set -e
  if [ "$code" -ne 1 ]; then
    echo "diff: the reference run against the fixture pair did not exit 1 (FINDINGS); refusing to write a bad golden" >&2
    exit 1
  fi
  printf '%s\n' "$out" > "$golden"
  echo "diff: wrote $golden"
  exit 0
fi

set +e
out="$(run_diff)"
code=$?
set -e
if [ "$code" -ne 1 ]; then
  echo "diff: expected exit 1 (FINDINGS) against the fixture pair, got $code" >&2
  exit 1
fi

golden_text="$(cat "$golden")"
if [ "$out" != "$golden_text" ]; then
  echo "diff: output differs from the committed golden $golden" >&2
  diff <(printf '%s\n' "$golden_text") <(printf '%s\n' "$out") >&2 || true
  exit 1
fi

echo "diff: matches the committed golden, all five classification buckets exercised"
