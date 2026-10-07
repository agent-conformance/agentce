#!/usr/bin/env bash
# Build gate helper for VG-DOC01-COMPONENTS (18.37a): DOC-01 compares what a subject's BundleLoaded
# manifests declare with the tools and models its ToolCall and ModelCall events exercise. Each case in
# fixtures/doc01_components/cases.json becomes one bundle; the real Python, TypeScript and Java CLIs
# assess it against the baseline catalog, and DOC-01's outcome and failing events must match the case's
# expectation in every engine, with the expected exit code and nothing quarantined.
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
fixtures="$root/verification/gates/fixtures/doc01_components"
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
        "domain": "doc01-fixture",
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
    java) "$root/engines/java/build/install/agentce/bin/agentce" "$@" ;;
  esac
}

# DOC-01's outcome, then the event ids of its failing focus nodes ("<outcome> <id,id>").
doc01() {
  jq -r '[.[]? // .assertions[] | select(.control == "DOC-01")][0]
    | "\(.outcome) \([.violations[]?.focus | sub("^agentce:event/"; "")] | unique | join(","))"' "$1"
}

status=0
for dir in "$work"/*/; do
  name="$(basename "$dir")"
  want="$(cat "$dir/expected")"
  want_exit="$(cat "$dir/exit")"
  for engine in python typescript java; do
    out="$dir/out-$engine"
    code=0
    run "$engine" assess --bundle "$dir/bundle" --profile "$fixtures/applicability.yaml" \
      --domain "$fixtures/domain.linkml.yaml" --out "$out" \
      >/dev/null 2>"$dir/err-$engine" || code=$?
    if [ ! -f "$out/assertions.json" ]; then
      echo "doc01-components: $engine wrote no assertions.json for $name (exit $code)" >&2
      tail -3 "$dir/err-$engine" >&2
      status=1
      continue
    fi
    if [ "$code" != "$want_exit" ]; then
      echo "doc01-components: $engine $name exited $code, expected $want_exit" >&2
      status=1
    fi
    if [ -s "$out/quarantine.jsonl" ]; then
      echo "doc01-components: $engine quarantined events of $name, so the case did not read its input" >&2
      status=1
    fi
    got="$(doc01 "$out/assertions.json")"
    if [ "$got" != "$want" ]; then
      echo "doc01-components: $engine $name gave '$got', expected '$want'" >&2
      status=1
    fi
  done
done
count="$(ls -d "$work"/*/ | wc -l | tr -d ' ')"
if [ "$count" -lt 15 ]; then
  echo "doc01-components: only $count cases in cases.json, expected at least 15" >&2
  status=1
fi
[ "$status" -eq 0 ] && echo "doc01-components: $(ls -d "$work"/*/ | wc -l | tr -d ' ') cases, three engines agree with the expected DOC-01 outcome"
exit "$status"
