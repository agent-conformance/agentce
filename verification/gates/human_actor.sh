#!/usr/bin/env bash
# Build gate helper for VG-HUMAN-ACTOR (18.126): the SPEC §10.4 human-actor rule. An ApprovalDecided, Override or
# Interrupt counts as a human's only when its actor is a human principal whose session_ref names a held
# identity-provider login record (a SessionStart of someone other than the agents it concerns, from an
# independent_system or enforcement_point stream) and who is nowhere in the delegation chain of the activity; two
# approvals on one login, or by one id, are one human under dual control. Each case in fixtures/human_actor/cases.json
# becomes one bundle; the real Python, TypeScript and Java CLIs assess it, and the case's control must give the
# expected outcome and failing events in every engine, with the expected quarantined record count and exit code.
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
fixtures="$root/verification/gates/fixtures/human_actor"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

(cd "$root/engines/python" && env -u VIRTUAL_ENV uv run --frozen python - "$fixtures/cases.json" "$work") <<'PY'
import hashlib
import json
import sys
from pathlib import Path

cases = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))["cases"]
for name, case in cases.items():
    bundle = Path(sys.argv[2]) / name / "bundle"
    (bundle / "events").mkdir(parents=True)
    text = "".join(json.dumps(e, sort_keys=True) + "\n" for e in case["events"])
    (bundle / "events" / "subject.jsonl").write_text(text, encoding="utf-8")
    manifest = {
        "agentce_bundle_version": 1,
        "domain": "human-actor-fixture",
        "files": [
            {
                "path": "events/subject.jsonl",
                "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            }
        ],
        # Declared classes, so the login records keep the class their stream has (SPEC §6.4).
        "sources": [
            {"id": f"urn:src:{cls}", "class": cls}
            for cls in ("enforcement_point", "independent_system", "self_report")
        ],
    }
    (bundle / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    expected = case["expected"]
    failing = ",".join(sorted(expected["failing"]))
    (bundle.parent / "control").write_text(f"{case['control']}\n", encoding="utf-8")
    (bundle.parent / "expected").write_text(
        f"{expected['outcome']} {failing} {expected['quarantined']} {expected['exit']}\n", encoding="utf-8"
    )
PY

(cd "$root/engines/typescript" && pnpm --silent build >/dev/null)
(cd "$root/engines/java" && ./gradlew --no-daemon --quiet installDist)

run() {
  local engine="$1"
  shift
  case "$engine" in
    python) (cd "$root/engines/python" && env -u VIRTUAL_ENV uv run --frozen agentce "$@") ;;
    typescript) (cd "$root/engines/typescript" && node bin/agentce.js "$@") ;;
    # Tier-1 JIT and the serial collector cut each short CLI run's CPU (as VG-CLI-OPTIONS does).
    java) JAVA_OPTS="-XX:TieredStopAtLevel=1 -XX:+UseSerialGC" "$root/engines/java/build/install/agentce/bin/agentce" "$@" ;;
  esac
}

# The case control's outcome and the event ids of its failing focus nodes ("<outcome> <id,id>").
outcome() {
  jq -r --arg control "$2" '[.[]? // .assertions[] | select(.control == $control)][0]
    | "\(.outcome) \([.violations[]?.focus | sub("^agentce:event/"; "")] | unique | join(","))"' "$1"
}

# One engine over every case; returns non-zero on any mismatch. The three engines run side by side, so the gate's
# wall time is the slowest engine's; the first mismatch in any engine stops all three (the $work/.stop marker).
check_cases() {
  local engine="$1" status=0 dir name want out code got quarantined
  for dir in "$work"/*/; do
    [ -e "$work/.stop" ] && return 1
    name="$(basename "$dir")"
    want="$(cat "$dir/expected")"
    out="$dir/out-$engine"
    code=0
    run "$engine" assess --bundle "$dir/bundle" --profile "$fixtures/applicability.yaml" \
      --domain "$fixtures/domain.linkml.yaml" --out "$out" \
      >/dev/null 2>"$dir/err-$engine" || code=$?
    if [ ! -f "$out/assertions.json" ]; then
      echo "human-actor: $engine wrote no assertions.json for $name (exit $code)" >&2
      tail -3 "$dir/err-$engine" >&2
      touch "$work/.stop"
      return 1
    fi
    quarantined=0
    [ -f "$out/quarantine.jsonl" ] && quarantined="$(wc -l <"$out/quarantine.jsonl" | tr -d ' ')"
    got="$(outcome "$out/assertions.json" "$(cat "$dir/control")") $quarantined $code"
    if [ "$got" != "$want" ]; then
      echo "human-actor: $engine $name gave '$got', expected '$want' (outcome failing quarantined exit)" >&2
      status=1
      touch "$work/.stop"
    fi
  done
  return "$status"
}

status=0
pids=()
for engine in python typescript java; do
  check_cases "$engine" &
  pids+=("$!")
done
for pid in "${pids[@]}"; do
  wait "$pid" || status=1
done
if [ "$status" -ne 0 ]; then
  echo "human-actor: stopped at the first case an engine got wrong (above)" >&2
  exit 1
fi
echo "human-actor: $(ls -d "$work"/*/ | wc -l | tr -d ' ') cases agree with the SPEC §10.4 human-actor rule in Python, TypeScript and Java"
