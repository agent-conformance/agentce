#!/usr/bin/env bash
# Build gate helper for VG-EUAIACT-CLUSTER1 (18.37c): eight eu-ai-act controls that shared one rule
# (a consequential decision is reviewed) now each test what their title says -- INC-03 incident-triggering
# decisions reviewed, OVS-01 every consequential decision reviewed, OVS-07 overrides and interrupts
# effective and by a human, OVS-08 enough distinct human reviewers (two under dual_control), ROB-07
# incidents detected and responded to, RSK-02 a policy decision or a review gates each consequential
# decision, and DAT-03 and RSK-03 (rung 3) read not_assessed. Each case in fixtures/eu_cluster1/cases.json
# becomes one bundle; the real Python, TypeScript and Java CLIs assess it against the eu-ai-act catalog,
# and the named control's outcome, failing events and the exit code must match, with nothing quarantined.
# ENGINES (default "python typescript java") narrows the run for local reference captures.
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
fixtures="$root/verification/gates/fixtures/eu_cluster1"
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
        "domain": "eu-cluster1-fixture",
        "files": [
            {
                "path": "events/subject.jsonl",
                "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            }
        ],
    }
    (bundle / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    expected = case["expected"]
    failing = ",".join(sorted(expected["failing"]))
    (bundle.parent / "control").write_text(f"{case['control']}\n", encoding="utf-8")
    (bundle.parent / "expected").write_text(f"{expected['outcome']} {failing}\n", encoding="utf-8")
    (bundle.parent / "exit").write_text(f"{expected['exit']}\n", encoding="utf-8")
PY

engines="${ENGINES:-python typescript java}"
case " $engines " in *" typescript "*) (cd "$root/engines/typescript" && pnpm --silent build >/dev/null) ;; esac
case " $engines " in *" java "*) (cd "$root/engines/java" && ./gradlew --no-daemon --quiet installDist) ;; esac

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

# The case's control outcome, then the event ids of its failing focus nodes ("<outcome> <id,id>").
outcome() {
  jq -r --arg c "$2" '[.[]? // .assertions[] | select(.control == $c)][0]
    | "\(.outcome) \([.violations[]?.focus | sub("^agentce:event/"; "")] | unique | join(","))"' "$1"
}

status=0
for dir in "$work"/*/; do
  name="$(basename "$dir")"
  want="$(cat "$dir/expected")"
  want_exit="$(cat "$dir/exit")"
  control="$(cat "$dir/control")"
  for engine in $engines; do
    out="$dir/out-$engine"
    code=0
    run "$engine" assess --bundle "$dir/bundle" --profile "$fixtures/applicability.yaml" \
      --domain "$fixtures/domain.linkml.yaml" --out "$out" \
      >/dev/null 2>"$dir/err-$engine" || code=$?
    if [ ! -f "$out/assertions.json" ]; then
      echo "eu-cluster1: $engine wrote no assertions.json for $name (exit $code)" >&2
      tail -3 "$dir/err-$engine" >&2
      status=1
      continue
    fi
    if [ "$code" != "$want_exit" ]; then
      echo "eu-cluster1: $engine $name exited $code, expected $want_exit" >&2
      status=1
    fi
    if [ -s "$out/quarantine.jsonl" ]; then
      echo "eu-cluster1: $engine quarantined events of $name, so the case did not read its input" >&2
      status=1
    fi
    got="$(outcome "$out/assertions.json" "$control")"
    if [ "$got" != "$want" ]; then
      echo "eu-cluster1: $engine $name gave $control '$got', expected '$want'" >&2
      status=1
    fi
  done
done
count="$(ls -d "$work"/*/ | wc -l | tr -d ' ')"
if [ "$count" -lt 30 ]; then
  echo "eu-cluster1: only $count cases in cases.json, expected at least 30" >&2
  status=1
fi
[ "$status" -eq 0 ] && echo "eu-cluster1: $count cases, every engine in [$engines] agrees with each expected outcome"
exit "$status"
