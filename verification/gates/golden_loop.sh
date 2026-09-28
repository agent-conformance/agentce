#!/usr/bin/env bash
# Build gate helper for VG-GOLDEN-LOOP: a real, scripted `assess -> apply the skill's code-level
# step -> re-assess -> diff` loop, deterministic end to end, over one dedicated fixture catalog
# (LOOP-01). before/ and after/ share one applicability profile, one domain binding, and the one
# catalog, and differ by exactly one added ToolCall event -- the literal stand-in for "apply the
# skill's code-level step" (contracts/P18-18.6.md Dispositions: a small, dedicated fixture is this
# repository's own established pattern for a case that must be exact, the same choice
# blind_spots_engines.sh already makes).
#
# Five real CLI invocations, each checked against real output, never re-derived:
#   1. assess over before/ -> LOOP-01 is insufficient_evidence.
#   2. 18.5's own blind_spots computation over the same before/ bundle independently confirms
#      LOOP-01 is a real blind spot (checks_unlocked >= 1, step_kind == code_change, and the
#      recorded event equals the one event type after/ adds over before/ -- computed here by
#      diffing the two JSONL files, never hard-coded).
#   2b. the before/ bundle's remediation-package.json for LOOP-01 states the same expected
#      transition (insufficient_evidence -> conformant) that step 4's diff observes.
#   3. assess over after/ -> LOOP-01 is conformant.
#   4. `agentce diff` (both --format json and --format md) between the two assertions files shows
#      LOOP-01 closed, matching the remediation package's own expected_transition exactly.
#
# Regenerate nothing here: unlike VG-DIFF, this gate has no committed golden file -- every
# assertion is a live structural check against the real assess/diff output, since the property
# under test is the chain of transitions, not a byte-for-byte rendering.
#
# Every path a check needs is passed as a python argv, never interpolated into the script text
# (blind_spots_engines.sh's own `python3 - "$1" <<'PY'` idiom), so a value with a quote or backslash
# in it cannot corrupt the script.
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
fixture="$root/verification/gates/fixtures/golden_loop"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

run_assess() {
  local bundle="$1" out="$2"
  (cd "$root/engines/python" && env -u VIRTUAL_ENV PYTHONDONTWRITEBYTECODE=1 uv run --frozen agentce assess \
    --bundle "$bundle" --profile "$fixture/applicability.yaml" \
    --domain "$fixture/domain.linkml.yaml" --catalog-dir "$fixture/catalog" \
    --allow-unverified-catalog --out "$out" >/dev/null)
}

run_diff() {
  (cd "$root/engines/python" && env -u VIRTUAL_ENV PYTHONDONTWRITEBYTECODE=1 uv run --frozen agentce diff "$1" "$2" --format "$3")
}

run_assess "$fixture/before/evidence" "$work/before"
run_assess "$fixture/after/evidence" "$work/after"

status=0

(cd "$root/engines/python" && env -u VIRTUAL_ENV PYTHONDONTWRITEBYTECODE=1 uv run --frozen python - "$work/before" "$work/after" <<'PY'
import json
import sys

before_dir, after_dir = sys.argv[1], sys.argv[2]
before = json.load(open(f"{before_dir}/assertions.json"))
after = json.load(open(f"{after_dir}/assertions.json"))
loop_before = next(a for a in before if a["control"] == "LOOP-01")
loop_after = next(a for a in after if a["control"] == "LOOP-01")
if loop_before["outcome"] != "insufficient_evidence":
    print(f"golden-loop: before/ LOOP-01 outcome is {loop_before['outcome']!r}, want insufficient_evidence", file=sys.stderr)
    sys.exit(1)
if loop_after["outcome"] != "conformant":
    print(f"golden-loop: after/ LOOP-01 outcome is {loop_after['outcome']!r}, want conformant", file=sys.stderr)
    sys.exit(1)
print("golden-loop: step 1/3 ok -- before insufficient_evidence, after conformant")
PY
) || status=1

(cd "$root/engines/python" && env -u VIRTUAL_ENV PYTHONDONTWRITEBYTECODE=1 uv run --frozen python - \
  "$work/before" "$fixture/before/evidence/events/subject.jsonl" "$fixture/after/evidence/events/subject.jsonl" <<'PY'
import json
import sys

before_dir, before_events, after_events = sys.argv[1], sys.argv[2], sys.argv[3]
blind_spots = json.load(open(f"{before_dir}/blind-spots.json"))
entry = next(
    (b for b in blind_spots["blind_spots"]
     if any(c["control"] == "LOOP-01" for c in b["unlocked_checks"])),
    None,
)
if entry is None:
    print("golden-loop: LOOP-01 does not appear in any blind-spots.json entry's unlocked_checks", file=sys.stderr)
    sys.exit(1)
if entry["checks_unlocked"] < 1:
    print(f"golden-loop: LOOP-01's blind-spot entry has checks_unlocked={entry['checks_unlocked']}, want >= 1", file=sys.stderr)
    sys.exit(1)
if entry["step_kind"] != "code_change":
    print(f"golden-loop: LOOP-01's blind-spot entry has step_kind={entry['step_kind']!r}, want code_change", file=sys.stderr)
    sys.exit(1)

before_types = {json.loads(line)["data"]["@type"] for line in open(before_events)}
after_types = {json.loads(line)["data"]["@type"] for line in open(after_events)}
added = after_types - before_types
if added != {entry["event"]}:
    print(f"golden-loop: after/ adds event type(s) {added}, but the blind-spot entry names {entry['event']!r}", file=sys.stderr)
    sys.exit(1)
print(f"golden-loop: step 2 ok -- LOOP-01 unlocked, step_kind=code_change, event={entry['event']!r} matches the added event")
PY
) || status=1

(cd "$root/engines/python" && env -u VIRTUAL_ENV PYTHONDONTWRITEBYTECODE=1 uv run --frozen python - "$work/before" <<'PY'
import glob
import json
import sys

before_dir = sys.argv[1]
paths = glob.glob(f"{before_dir}/skill/*/remediation-package.json")
if not paths:
    print("golden-loop: no remediation-package.json under before/skill/", file=sys.stderr)
    sys.exit(1)
pkg = json.load(open(paths[0]))
finding = next((f for f in pkg["findings"] if f["control"] == "LOOP-01"), None)
if finding is None:
    print("golden-loop: no LOOP-01 finding in remediation-package.json", file=sys.stderr)
    sys.exit(1)
transition = finding["acceptance"]["expected_transition"]
if transition != {"from": "insufficient_evidence", "to": "conformant"}:
    print(f"golden-loop: LOOP-01's expected_transition is {transition}, want insufficient_evidence -> conformant", file=sys.stderr)
    sys.exit(1)
print("golden-loop: step 2b ok -- remediation package promises insufficient_evidence -> conformant")
PY
) || status=1

run_diff "$work/before/assertions.json" "$work/after/assertions.json" json > "$work/diff.json" || true
(cd "$root/engines/python" && env -u VIRTUAL_ENV PYTHONDONTWRITEBYTECODE=1 uv run --frozen python - "$work/diff.json" <<'PY'
import json
import sys

data = json.load(open(sys.argv[1]))
what_changed = data["what_changed"]
closed = what_changed["closed"]
if not any(c["control"] == "LOOP-01" and c["from"] == "insufficient_evidence" and c["to"] == "conformant" for c in closed):
    print(f"golden-loop: LOOP-01 is not in what_changed.closed with the expected from/to: {closed}", file=sys.stderr)
    sys.exit(1)
for group in ("opened", "other"):
    if any(c["control"] == "LOOP-01" for c in what_changed[group]):
        print(f"golden-loop: LOOP-01 unexpectedly appears in what_changed.{group}", file=sys.stderr)
        sys.exit(1)
print("golden-loop: step 4 ok (--format json) -- LOOP-01 closed, insufficient_evidence -> conformant")
PY
) || status=1

diff_md="$(run_diff "$work/before/assertions.json" "$work/after/assertions.json" md || true)"
if ! printf '%s\n' "$diff_md" | grep -q '^### Closed'; then
  echo "golden-loop: --format md has no ### Closed section" >&2
  status=1
elif ! printf '%s\n' "$diff_md" | grep -q 'LOOP-01'; then
  echo "golden-loop: --format md's Closed section does not name LOOP-01" >&2
  status=1
else
  echo "golden-loop: step 4 ok (--format md) -- Closed section names LOOP-01"
fi

[ "$status" -eq 0 ] && echo "golden-loop: the full assess -> code-level fix -> re-assess -> diff loop closed LOOP-01 end to end"
exit "$status"
