#!/usr/bin/env bash
# Build gate helper for VG-BASELINE-LENS-ENGINES: the three engines evaluate the same baseline when
# nothing names a catalog, and write byte-identical assertions.json for the same input.
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
quick="$root/corpus/quickstart"

# The quickstart profile with its `catalogs:` list removed.
awk '/^catalogs:$/ {skip=1; next} skip && /^  - / {next} {skip=0; print}' \
  "$quick/applicability.yaml" > "$work/no-catalog.yaml"
args=(assess --bundle "$quick/evidence" --profile "$work/no-catalog.yaml" --domain "$quick/domain.linkml.yaml")

(cd "$root/engines/python" && env -u VIRTUAL_ENV uv run --frozen agentce "${args[@]}" --out "$work/python" >/dev/null)
(cd "$root/engines/typescript" && pnpm --silent agentce "${args[@]}" --out "$work/typescript" >/dev/null)
(cd "$root/engines/java" && ./gradlew --no-daemon --quiet installDist \
  && ./build/install/agentce/bin/agentce "${args[@]}" --out "$work/java" >/dev/null)

status=0
for engine in python typescript java; do
  labels="$(jq -r '[.inputs.catalogs[] | "\(.id)@\(.version)"] | join(",")' "$work/$engine/manifest.json")"
  if [ "$labels" != "baseline@2026.09" ]; then
    echo "baseline-lens: $engine evaluated '$labels', expected baseline@2026.09" >&2
    status=1
  fi
done
for engine in typescript java; do
  if ! cmp -s "$work/python/assertions.json" "$work/$engine/assertions.json"; then
    echo "baseline-lens: $engine assertions.json differs from python" >&2
    status=1
  fi
done
[ "$status" -eq 0 ] && echo "baseline-lens: three engines identical over the baseline"
exit "$status"
