#!/usr/bin/env bash
# Build gate helper for VG-BLIND-SPOTS: the three engines compute the same ranked blind-spots
# artifact (RFC 0008) for the same input and write byte-identical blind-spots.json, matching a
# committed golden -- so a computation bug shared by all three engines cannot hide behind
# cross-engine agreement alone.
#
# The fixture (verification/gates/fixtures/blind_spots/) is small and purpose-built, not the
# 132-project corpus or corpus/quickstart: its two controls share one subject whose one event
# supplies neither ModelCall nor ToolCall. NEED-01 requires both at once -- blind-spots.json's
# `needed_by` case, which never occurs naturally in the existing corpus (every insufficient_evidence
# assertion there today has exactly one missing requirement). NEED-02 requires only ToolCall, so
# (ToolCall, any) alone gains a checks_unlocked count -- the two resulting blind spots differ on
# checks_unlocked (1 vs 0), so their correct rank order (ToolCall first) differs from the order their
# groups are first created in (ModelCall first, since NEED-01 alone -- both requirements missing at
# once -- would tie every field and let a missing or inverted sort pass by coincidence).
# `no_population` is deliberately not exercised by this fixture: it is reachable only through
# Python's records-folder auto-derived-profile CLI path, which TypeScript and Java have no
# equivalent of, so no three-engine golden for it is possible (RFC 0008's post-implementation
# amendment); its correctness is covered by each engine's own unit tests instead.
#
# The golden (blind_spots_golden.json) is always a capture of the Python *reference* engine's
# output -- comparing the other two engines to Python's own output, never to themselves, is what
# makes the parity claim non-vacuous (the same rule what_they_did_engines.sh documents for
# activity.json). Regenerate it with:
#   verification/gates/blind_spots_engines.sh --write
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
golden="$root/verification/gates/blind_spots_golden.json"
fixture="$root/verification/gates/fixtures/blind_spots"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
args=(assess --bundle "$fixture/evidence" --profile "$fixture/applicability.yaml" \
  --domain "$fixture/domain.linkml.yaml" --catalog-dir "$fixture/catalog")
# Only the Python engine verifies a --catalog-dir's signature (SPEC §8.7, item 11.3); the fixture
# catalog is deliberately unsigned (a test-only catalog, never published), so Python alone needs the
# explicit override. TypeScript and Java do not verify --catalog-dir signatures at all today (a
# pre-existing scope gap, not this item's to fix) and have no equivalent flag.
py_args=("${args[@]}" --allow-unverified-catalog)

# A regenerated golden could itself lose the needed_by case or the ranking (a bad capture, or a
# Python regression at --write time): check the property once, on the golden itself, rather than
# once per engine -- a per-engine byte match to this same golden (below) already implies the
# property holds for every engine, so re-deriving it three times would only repeat this check, not
# add coverage. NEED-02 makes (ToolCall, any) the only group with checks_unlocked > 0: the two
# groups differ on the primary sort key, so the correct order (ToolCall first) is not also the
# order their groups are first created in (ModelCall first, from NEED-01's own two-missing-keys
# loop, sorted alphabetically) -- a missing, inverted, or discovery-order ranking would put
# ModelCall first instead. Every entry's owner_key/step_kind/ladder_rung combination must also be
# internally consistent (RFC 0008 Sec.3: rung 1/2 <-> code_change/agent_team, rung 3 <->
# request/platform_or_security, rung 4 <-> request/ticketing_or_iam).
check_golden() {
  python3 - "$1" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as f:
    data = json.load(f)
spots = data.get("blind_spots", [])
consistent = {
    1: ("code_change", "agent_team"), 2: ("code_change", "agent_team"),
    3: ("request", "platform_or_security"), 4: ("request", "ticketing_or_iam"),
}
got = [(s["event"], s["checks_unlocked"], s["needed_by"]) for s in spots]
ok = (
    got == [("ToolCall", 1, 1), ("ModelCall", 0, 1)]
    and data.get("no_population") == []
    and all(
        len(s["unlocked_checks"]) == s["checks_unlocked"]
        and consistent.get(s["ladder_rung"]) == (s["step_kind"], s["owner_key"])
        for s in spots
    )
)
sys.exit(0 if ok else 1)
PY
}

if [ "${1:-}" = "--write" ]; then
  # NEED-01 and NEED-02 (both severity: high) are insufficient_evidence by fixture design, so this
  # exits 2 (SPEC.md:1076, item 18.30).
  set +e
  (cd "$root/engines/python" && env -u VIRTUAL_ENV uv run --frozen agentce "${py_args[@]}" --out "$work/python" >/dev/null)
  code=$?
  set -e
  if [ "$code" -ne 2 ]; then
    echo "blind-spots: python assess (--write) exited $code, expected 2 (NEED-01/NEED-02 severity: high insufficient_evidence, SPEC.md:1076)" >&2
    exit 1
  fi
  if ! check_golden "$work/python/blind-spots.json"; then
    echo "blind-spots: the Python reference engine's own output did not honestly surface the fixture's needed_by case or its ranking; refusing to write a bad golden" >&2
    exit 1
  fi
  cp "$work/python/blind-spots.json" "$golden"
  echo "blind-spots: wrote $golden from the Python reference engine"
  exit 0
fi

if ! check_golden "$golden"; then
  echo "blind-spots: the committed golden $golden did not honestly surface the fixture's needed_by case or its ranking" >&2
  exit 1
fi

# NEED-01 and NEED-02 (both severity: high) are insufficient_evidence by fixture design, so every
# engine's assess run below exits 2 (SPEC.md:1076, item 18.30). The Java build itself stays under
# set -euo pipefail: a build failure is a real crash, not an exit-code question.
(cd "$root/engines/java" && ./gradlew --no-daemon --quiet installDist)

set +e
(cd "$root/engines/python" && env -u VIRTUAL_ENV uv run --frozen agentce "${py_args[@]}" --out "$work/python" >/dev/null)
py_code=$?
set -e
if [ "$py_code" -ne 2 ]; then
  echo "blind-spots: python assess exited $py_code, expected 2 (NEED-01/NEED-02 severity: high insufficient_evidence, SPEC.md:1076)" >&2
  exit 1
fi

set +e
(cd "$root/engines/typescript" && pnpm --silent agentce "${args[@]}" --out "$work/typescript" >/dev/null)
ts_code=$?
set -e
if [ "$ts_code" -ne 2 ]; then
  echo "blind-spots: typescript assess exited $ts_code, expected 2 (NEED-01/NEED-02 severity: high insufficient_evidence, SPEC.md:1076)" >&2
  exit 1
fi

set +e
(cd "$root/engines/java" && ./build/install/agentce/bin/agentce "${args[@]}" --out "$work/java" >/dev/null)
java_code=$?
set -e
if [ "$java_code" -ne 2 ]; then
  echo "blind-spots: java assess exited $java_code, expected 2 (NEED-01/NEED-02 severity: high insufficient_evidence, SPEC.md:1076)" >&2
  exit 1
fi

status=0
for engine in python typescript java; do
  if [ ! -f "$work/$engine/blind-spots.json" ]; then
    echo "blind-spots: $engine did not write blind-spots.json" >&2
    status=1
    continue
  fi
  if ! cmp -s "$golden" "$work/$engine/blind-spots.json"; then
    echo "blind-spots: $engine blind-spots.json differs from the committed golden $golden" >&2
    status=1
  fi
done
[ "$status" -eq 0 ] && echo "blind-spots: three engines match the golden, needed_by and ranking honestly surfaced"
exit "$status"
