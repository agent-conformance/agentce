#!/usr/bin/env bash
# Build gate helper for VG-PROJECT-VIEW (18.14, Hill 7): the three engines' project view -- every
# agent's records side by side, undeclared agents listed, a top gap naming which agents it touches --
# on the SAME fixture, matching a committed golden (so a computation bug shared by all three engines
# cannot hide behind cross-engine agreement alone), and a single-subject run writes no project view
# artifacts at all (a `> 1` vs `>= 1` subject-count regression, seeded fault (2) below, needs BOTH
# fixtures in the same gate run to be caught -- a 3-subject-only fixture can never detect it).
#
# The 3-subject fixture (verification/gates/fixtures/project_view/) declares two subjects, A and B.
# A's own events supply a ToolCall but not a ModelCall (PROJ-01's control is missing exactly one
# requirement for A -- the blind-spots "unlocked" case); B's own events supply neither (missing both --
# the "needed_by" case) and B's one event's `data.agent.id` names a THIRD identity ("delegate") that
# neither subject declares. Both A and B are assessed against the SAME control, so PROJ-01's
# (ModelCall, any) group genuinely spans both of them: this is what makes `top_gaps`' own "agents"
# field a real cross-agent fact, not an artefact of two unrelated controls. `declared_subject_ids` is
# always every subject a `--bundle`/`--profile` run's own profile names (no engine's `assess` CLI
# exposes a narrower override outside the Python-only records-folder path, C4) -- so no row's own
# `declared` flag can ever read `false` here; "undeclared" is instead the real, reachable case this
# fixture exercises: an identity discovered only inside a declared subject's own `agents_observed`
# list (Hill 7's own motivating scenario -- a sub-agent or delegate never declared in the profile).
#
# The 1-subject fixture reuses VG-BLIND-SPOTS' own fixture verbatim (already proven, elsewhere, to run
# cleanly on all three engines): it stays a single subject, so C3's branch must never engage for it.
#
# The deviations fixture (verification/gates/fixtures/project_view_deviations/, 18.14a) reuses
# VG-AUDITOR-VIEW's own proven AUV-01 SHACL pattern as a new control PVD-01. Two subjects, both
# declaring one Decision event each (so neither is insufficient_evidence, which would make
# readiness.deviation_lint reject the whole register): subject A's event has no `agent` field (shape
# fails -> non-conformant, eligible for the register's unexpired PVD-01 deviation, which
# apply_deviations flips to `partial`); subject B's event has an `agent` field (shape passes ->
# conformant), a clean subject the anti-leak fault can leak onto. Python-only (TypeScript/Java refuse
# `--deviations` by design, C5): runs `assess --deviations` directly, asserting the per-subject
# `deviations` property this item's `compute_project_view` now computes, and the rendered
# "Deviations" column in project.md.
#
# The golden (project_view_golden.json) is always a capture of the Python *reference* engine's own
# output -- comparing the other two engines to Python's own output, never to themselves, is what makes
# the parity claim non-vacuous (the same rule what_they_did_engines.sh documents). Rendered
# `project.md` is NOT claimed byte-identical across engines (matching `report.md`'s own precedent,
# RFC 0008/18.14 C3), so each engine gets its OWN committed golden
# (project_view_golden_<engine>.md), a per-engine `cmp -s` only.
# Regenerate with:
#   verification/gates/project_view_engines.sh --write
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
fixture="$root/verification/gates/fixtures/project_view"
one_subject="$root/verification/gates/fixtures/blind_spots"
deviations_fixture="$root/verification/gates/fixtures/project_view_deviations"
golden="$root/verification/gates/project_view_golden.json"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

multi_args=(assess --bundle "$fixture/evidence" --profile "$fixture/applicability.yaml" \
  --domain "$fixture/domain.linkml.yaml" --catalog-dir "$fixture/catalog" --for risk-lead \
  --allow-unverified-catalog)
one_args=(assess --bundle "$one_subject/evidence" --profile "$one_subject/applicability.yaml" \
  --domain "$one_subject/domain.linkml.yaml" --catalog-dir "$one_subject/catalog" --for risk-lead \
  --allow-unverified-catalog)
# Every engine verifies a --catalog-dir's signature (SPEC §8.7; TypeScript and Java since 18.36), and
# both fixture catalogs are deliberately unsigned test-only catalogs, so every engine gets the explicit
# override, matching blind_spots_engines.sh.
deviations_args=(assess --bundle "$deviations_fixture/evidence" \
  --profile "$deviations_fixture/applicability.yaml" \
  --domain "$deviations_fixture/domain.linkml.yaml" \
  --catalog-dir "$deviations_fixture/catalog" \
  --deviations "$deviations_fixture/deviations.yaml" \
  --allow-unverified-catalog --for risk-lead)

# The property assertion fault (1) (an inverted undeclared-agents set-difference) catches even if the
# golden comparison were somehow satisfied: the known undeclared id must be present, and at least one
# top_gaps entry must name both fixture subjects.
check_properties() {
  python3 - "$1" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as f:
    data = json.load(f)
undeclared = data.get("undeclared_agents", [])
top_gaps = data.get("top_gaps", [])
ok = (
    undeclared == ["spiffe://corp/agents/project-view-fixture-delegate"]
    and any(
        set(gap.get("agents", []))
        >= {
            "spiffe://corp/agents/project-view-fixture-a",
            "spiffe://corp/agents/project-view-fixture-b",
        }
        for gap in top_gaps
    )
)
sys.exit(0 if ok else 1)
PY
}

# The deviations scenario's property assertion (18.14a): subject A's register entry must be present,
# unleaked onto subject B (fault (i)), and carry its real, unmangled expiry (fault (ii)); project.md
# must render both the control id and the expiry text, proving the rendering path too.
check_deviations_properties() {
  python3 - "$1" "$2" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as f:
    data = json.load(f)
with open(sys.argv[2], encoding="utf-8") as f:
    rendered = f.read()
by_id = {agent["id"]: agent for agent in data["agents"]}
a = by_id.get("spiffe://corp/agents/project-view-deviations-fixture-a", {})
b = by_id.get("spiffe://corp/agents/project-view-deviations-fixture-b", {})
ok = (
    a.get("deviations") == [{"control": "PVD-01", "expiry": "2026-06-01T00:00:00.000Z"}]
    and b.get("deviations") == []
    and "PVD-01" in rendered
    and "2026-06-01T00:00:00.000Z" in rendered
)
sys.exit(0 if ok else 1)
PY
}

assert_code() {
  local label="$1" code="$2"
  if [ "$code" -ne 2 ]; then
    echo "project-view: $label assess exited $code, expected 2 (severity: high insufficient_evidence, SPEC.md:1076)" >&2
    exit 1
  fi
}

if [ "${1:-}" = "--write" ]; then
  # PROJ-01 (severity: high) is insufficient_evidence by fixture design, so every engine's run below
  # exits 2 (SPEC.md:1076, item 18.30).
  set +e
  (cd "$root/engines/python" && env -u VIRTUAL_ENV uv run --frozen agentce "${multi_args[@]}" --out "$work/python" >/dev/null)
  code=$?
  set -e
  assert_code "python (--write)" "$code"
  if ! check_properties "$work/python/project.json"; then
    echo "project-view: the Python reference engine's own output did not honestly surface the fixture's undeclared agent or its cross-agent top gap; refusing to write a bad golden" >&2
    exit 1
  fi
  cp "$work/python/project.json" "$golden"
  cp "$work/python/project.md" "$root/verification/gates/project_view_golden_python.md"
  set +e
  (cd "$root/engines/typescript" && pnpm --silent agentce "${multi_args[@]}" --out "$work/typescript" >/dev/null)
  code=$?
  set -e
  assert_code "typescript (--write)" "$code"
  cp "$work/typescript/project.md" "$root/verification/gates/project_view_golden_typescript.md"
  (cd "$root/engines/java" && ./gradlew --no-daemon --quiet installDist)
  set +e
  (cd "$root/engines/java" && ./build/install/agentce/bin/agentce "${multi_args[@]}" --out "$work/java" >/dev/null)
  code=$?
  set -e
  assert_code "java (--write)" "$code"
  cp "$work/java/project.md" "$root/verification/gates/project_view_golden_java.md"
  echo "project-view: wrote $golden and the three per-engine project.md goldens from a live run"
  exit 0
fi

if ! check_properties "$golden"; then
  echo "project-view: the committed golden $golden did not honestly surface the fixture's undeclared agent or its cross-agent top gap" >&2
  exit 1
fi

# PROJ-01 and (in the reused one-subject fixture) NEED-01/NEED-02 are severity: high
# insufficient_evidence by fixture design, so every run below exits 2 (SPEC.md:1076, item 18.30). The
# Java build itself stays under set -euo pipefail: a build failure is a real crash, not an exit-code
# question.
(cd "$root/engines/java" && ./gradlew --no-daemon --quiet installDist)

set +e
(cd "$root/engines/python" && env -u VIRTUAL_ENV uv run --frozen agentce "${multi_args[@]}" --out "$work/python-multi" >/dev/null)
code=$?; assert_code "python-multi" "$code"
(cd "$root/engines/typescript" && pnpm --silent agentce "${multi_args[@]}" --out "$work/typescript-multi" >/dev/null)
code=$?; assert_code "typescript-multi" "$code"
(cd "$root/engines/java" && ./build/install/agentce/bin/agentce "${multi_args[@]}" --out "$work/java-multi" >/dev/null)
code=$?; assert_code "java-multi" "$code"
(cd "$root/engines/python" && env -u VIRTUAL_ENV uv run --frozen agentce "${one_args[@]}" --out "$work/python-one" >/dev/null)
code=$?; assert_code "python-one" "$code"
(cd "$root/engines/typescript" && pnpm --silent agentce "${one_args[@]}" --out "$work/typescript-one" >/dev/null)
code=$?; assert_code "typescript-one" "$code"
(cd "$root/engines/java" && ./build/install/agentce/bin/agentce "${one_args[@]}" --out "$work/java-one" >/dev/null)
code=$?; assert_code "java-one" "$code"
set -e

status=0
for engine in python typescript java; do
  multi="$work/$engine-multi"
  if [ ! -f "$multi/project.json" ]; then
    echo "project-view: $engine did not write project.json for the 3-subject fixture" >&2
    status=1
    continue
  fi
  if ! cmp -s "$golden" "$multi/project.json"; then
    echo "project-view: $engine project.json differs from the committed golden $golden" >&2
    status=1
  fi
  if ! check_properties "$multi/project.json"; then
    echo "project-view: $engine's project.json did not honestly surface the undeclared agent or the cross-agent top gap" >&2
    status=1
  fi
  engine_golden="$root/verification/gates/project_view_golden_$engine.md"
  if [ ! -f "$multi/project.md" ] || ! cmp -s "$engine_golden" "$multi/project.md"; then
    echo "project-view: $engine's rendered project.md differs from its own committed golden $engine_golden" >&2
    status=1
  fi
  one="$work/$engine-one"
  for name in project.json project.md project.html; do
    if [ -e "$one/$name" ]; then
      echo "project-view: $engine wrote $name for a single-subject run (fault (2)'s target -- the subject-count gate must be strictly > 1)" >&2
      status=1
    fi
  done
  if [ -d "$one/agents" ]; then
    echo "project-view: $engine wrote an agents/ directory for a single-subject run" >&2
    status=1
  fi
done

# The deviations scenario (18.14a, C6): Python only -- TypeScript/Java refuse --deviations by design
# (C5), so there is nothing to compare cross-engine here.
set +e
(cd "$root/engines/python" && env -u VIRTUAL_ENV uv run --frozen agentce "${deviations_args[@]}" --out "$work/python-deviations" >/dev/null)
code=$?
set -e
# Both subjects clear PVD-01 (A via the applied deviation, B conformant outright): no
# non-conformant or severity-high insufficient_evidence assertion survives, so this run exits 0
# (confirmed live), unlike the 3-subject fixture's severity-high insufficient_evidence exit 2 above.
if [ "$code" -ne 0 ]; then
  echo "project-view: the deviations scenario's python assess exited $code, expected 0" >&2
  status=1
elif ! check_deviations_properties "$work/python-deviations/project.json" "$work/python-deviations/project.md"; then
  echo "project-view: the deviations scenario's project.json/project.md did not honestly surface subject A's applied deviation (control id + expiry), left subject B's deviations empty, or leaked between the two" >&2
  status=1
fi

[ "$status" -eq 0 ] && echo "project-view: three engines match the golden, the undeclared delegate and the cross-agent top gap are honestly surfaced, a single-subject run stays unchanged, and the deviations scenario surfaces subject A's applied deviation without leaking onto subject B"
exit "$status"
