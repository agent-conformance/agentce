#!/usr/bin/env bash
# Build gate helper for VG-WHAT-THEY-DID: the three engines compute the same "what your agents did"
# activity summary for the same input and write byte-identical activity.json, and the quickstart
# bundle's one real tool call -- never declared in its profile -- honestly shows up as undeclared.
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
quick="$root/corpus/quickstart"
args=(assess --bundle "$quick/evidence" --profile "$quick/applicability.yaml" --domain "$quick/domain.linkml.yaml")

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
  undeclared="$(jq -c '.undeclared.tools' "$work/$engine/activity.json")"
  if [ "$undeclared" != '["credit.record_decision"]' ]; then
    echo "what-they-did: $engine undeclared.tools was $undeclared, expected [\"credit.record_decision\"]" >&2
    status=1
  fi
done
for engine in typescript java; do
  if ! cmp -s "$work/python/activity.json" "$work/$engine/activity.json"; then
    echo "what-they-did: $engine activity.json differs from python" >&2
    status=1
  fi
done
[ "$status" -eq 0 ] && echo "what-they-did: three engines identical, undeclared tool honestly surfaced"
exit "$status"
