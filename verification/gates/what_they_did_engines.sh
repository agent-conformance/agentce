#!/usr/bin/env bash
# Build gate helper for VG-WHAT-THEY-DID: the three engines compute the same "what your agents did"
# activity summary for the same input and write byte-identical activity.json, matching a committed
# golden (so a computation bug shared by all three engines cannot hide behind cross-engine agreement
# alone), and the quickstart bundle's one real tool call -- never declared in its profile -- honestly
# shows up as undeclared.
#
# The golden (what_they_did_golden.json) is always a capture of the Python *reference* engine's
# output -- comparing the other two engines to Python's own output, never to themselves, is what
# makes the parity claim non-vacuous (the same rule engines/java/src/test/resources/testdata/
# generate_goldens.py documents for the Java engine's test goldens). Regenerate it with:
#   verification/gates/what_they_did_engines.sh --write
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
golden="$root/verification/gates/what_they_did_golden.json"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
quick="$root/corpus/quickstart"
args=(assess --bundle "$quick/evidence" --profile "$quick/applicability.yaml" --domain "$quick/domain.linkml.yaml")

if [ "${1:-}" = "--write" ]; then
  (cd "$root/engines/python" && env -u VIRTUAL_ENV uv run --frozen agentce "${args[@]}" --out "$work/python" >/dev/null)
  cp "$work/python/activity.json" "$golden"
  echo "what-they-did: wrote $golden from the Python reference engine"
  exit 0
fi

(cd "$root/engines/python" && env -u VIRTUAL_ENV uv run --frozen agentce "${args[@]}" --out "$work/python" >/dev/null)
(cd "$root/engines/typescript" && pnpm --silent agentce "${args[@]}" --out "$work/typescript" >/dev/null)
(cd "$root/engines/java" && ./gradlew --no-daemon --quiet installDist \
  && ./build/install/agentce/bin/agentce "${args[@]}" --out "$work/java" >/dev/null)

status=0
for engine in python typescript java; do
  if [ ! -f "$work/$engine/activity.json" ]; then
    echo "what-they-did: $engine did not write activity.json" >&2
    status=1
    continue
  fi
  if ! cmp -s "$golden" "$work/$engine/activity.json"; then
    echo "what-they-did: $engine activity.json differs from the committed golden $golden" >&2
    status=1
  fi
  # A regenerated golden could itself lose the undeclared tool (a bad capture, or a Python
  # regression at --write time); check the property directly too, not only "matches the golden".
  if ! python3 -c "
import json, sys
with open('$work/$engine/activity.json', encoding='utf-8') as f:
    data = json.load(f)
sys.exit(0 if data.get('undeclared', {}).get('tools') == ['credit.record_decision'] else 1)
"; then
    echo "what-they-did: $engine's undeclared.tools did not honestly surface credit.record_decision" >&2
    status=1
  fi
done
[ "$status" -eq 0 ] && echo "what-they-did: three engines match the golden, undeclared tool honestly surfaced"
exit "$status"
