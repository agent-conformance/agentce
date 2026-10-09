#!/usr/bin/env bash
# Build gate helper for VG-CND05-CHAIN (18.37l): CND-05 fails a tool call that acts on an instruction whose chain passes
# through an untrusted source class (SPEC §7.7.4): the instruction itself, or any instruction or activity on its
# refs.parent / refs.origin lineage, by any number of hops (SPEC §6.2, Appendix F). Each case in
# fixtures/cnd05_chain/cases.json becomes one bundle; the real Python, TypeScript and Java CLIs assess it against the
# baseline catalog and the Conduct overlay, and CND-05's outcome and failing events must match the case's expectation in
# every engine, with the expected exit code and nothing quarantined.
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
fixtures="$root/verification/gates/fixtures/cnd05_chain"
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
        "domain": "cnd05-fixture",
        "files": [
            {
                "path": "events/subject.jsonl",
                "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            }
        ],
        # Declared classes, so the guard's enforcement_point records keep their class (SPEC §6.4).
        "sources": [
            {"id": f"urn:src:{cls}", "class": cls}
            for cls in ("enforcement_point", "self_report")
        ],
    }
    (bundle / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    expected = case["expected"]
    failing = ",".join(sorted(expected["failing"]))
    (bundle.parent / "expected").write_text(f"{expected['outcome']} {failing}\n", encoding="utf-8")
    (bundle.parent / "exit").write_text(f"{expected['exit']}\n", encoding="utf-8")
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

# CND-05's outcome for the fixture subject, then the event ids of its failing focus nodes ("<outcome> <id,id>").
subject="spiffe://corp/agents/cnd05-fixture"
cnd05() {
  jq -r --arg subject "$subject" '[.[]? // .assertions[] | select(.control == "CND-05" and .subject == $subject)][0]
    | "\(.outcome) \([.violations[]?.focus | sub("^agentce:event/"; "")] | unique | join(","))"' "$1"
}

# One engine over every case; returns non-zero on any mismatch. The three engines run side by side (each writes only
# its own out-<engine> and err-<engine>), so the gate's wall time is the slowest engine's, not the sum. The first
# mismatch in any engine stops all three (the $work/.stop marker): the gate has already failed, and a seeded-fault run
# turns red without assessing every case.
check_cases() {
  local engine="$1" status=0 dir name want want_exit out code got
  for dir in "$work"/*/; do
    [ -e "$work/.stop" ] && return 1
    name="$(basename "$dir")"
    want="$(cat "$dir/expected")"
    want_exit="$(cat "$dir/exit")"
    out="$dir/out-$engine"
    code=0
    run "$engine" assess --bundle "$dir/bundle" --profile "$fixtures/applicability.yaml" \
      --domain "$fixtures/domain.linkml.yaml" --out "$out" \
      >/dev/null 2>"$dir/err-$engine" || code=$?
    if [ ! -f "$out/assertions.json" ]; then
      echo "cnd05-chain: $engine wrote no assertions.json for $name (exit $code)" >&2
      tail -3 "$dir/err-$engine" >&2
      touch "$work/.stop"
      return 1
    fi
    if [ "$code" != "$want_exit" ]; then
      echo "cnd05-chain: $engine $name exited $code, expected $want_exit" >&2
      status=1
    fi
    if [ -s "$out/quarantine.jsonl" ]; then
      echo "cnd05-chain: $engine quarantined events of $name, so the case did not read its input" >&2
      status=1
    fi
    got="$(cnd05 "$out/assertions.json")"
    if [ "$got" != "$want" ]; then
      echo "cnd05-chain: $engine $name gave '$got', expected '$want'" >&2
      status=1
    fi
    [ "$status" -eq 0 ] || touch "$work/.stop"
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
  echo "cnd05-chain: stopped at the first case an engine got wrong (above)" >&2
  exit 1
fi
count="$(ls -d "$work"/*/ | wc -l | tr -d ' ')"
echo "cnd05-chain: $count cases, three engines agree with the expected CND-05 outcome"
