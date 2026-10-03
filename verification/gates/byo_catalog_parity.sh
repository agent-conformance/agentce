#!/usr/bin/env bash
# Build gate helper for VG-BYO-CATALOG-PARITY: `assess --catalog-dir` treats an operator's own catalog
# as untrusted input in all three engines (SPEC §8.7). Each engine runs the same scenarios as the
# Python reference capture for item 18.36 -- an untrusted, unsigned, trusted, rebranded catalog and a
# missing, malformed or non-object trust root, plus quickstart under AGENTCE_TRUST_ROOT -- and must
# give Python's exit code, error key, cause and fix, and the same limitation string under
# --allow-unverified-catalog, in both the --json envelope and manifest.json.
#
# Fixtures live under a mktemp directory outside the repository and $HOME, so every engine reduces a
# catalog path to the same basename. Keys and signatures come from the real `agentce catalog sign`.
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
quick="$root/corpus/quickstart"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

sign_catalog() {
  # sign_catalog <dir> <key name> [trust-root path]
  local extra=()
  [ $# -ge 3 ] && extra=(--write-trust-root "$3")
  (cd "$root/engines/python" && env -u VIRTUAL_ENV uv run --frozen agentce catalog sign "$1" \
    --new-key "$work/$2.pem" ${extra[@]+"${extra[@]}"} --force --json >/dev/null)
}

cp -R "$root/spec/catalogs/base/eu-ai-act" "$work/unsigned-cat"
rm "$work/unsigned-cat/catalog.sig.json"
cp -R "$work/unsigned-cat" "$work/untrusted-cat"
sign_catalog "$work/untrusted-cat" stranger
cp -R "$work/unsigned-cat" "$work/trusted-cat"
sign_catalog "$work/trusted-cat" signer "$work/trust-root.json"
cp -R "$work/unsigned-cat" "$work/rebrand-cat"
sed -i.bak 's/^id: eu-ai-act$/id: eu-ai-act-rebrand/' "$work/rebrand-cat/catalog.yaml"
rm "$work/rebrand-cat/catalog.yaml.bak"
sign_catalog "$work/rebrand-cat" rebrand "$work/rebrand-trust-root.json"
printf '{not json' > "$work/bad-root.json"
printf '[]' > "$work/array-root.json"

(cd "$root/engines/java" && ./gradlew --no-daemon --quiet installDist)

run_cli() {
  # run_cli <engine> <args...>: the engine's --json envelope on stdout, its exit code in $work/code.
  local engine="$1"
  shift
  local code=0
  case "$engine" in
    python)
      (cd "$root/engines/python" && env -u VIRTUAL_ENV uv run --frozen agentce "$@" --json) || code=$?
      ;;
    typescript)
      (cd "$root/engines/typescript" && pnpm --silent agentce "$@" --json) || code=$?
      ;;
    java)
      (cd "$root/engines/java" && ./build/install/agentce/bin/agentce "$@" --json) || code=$?
      ;;
  esac
  echo "$code" > "$work/code"
}

# One comparable line per run. Python's envelope keys an error as key/cause, TypeScript's and Java's
# as message_key/detail (the split sign_command_engines.sh also normalises). A cause_prefix scenario
# compares the cause only up to the JSON parser's own message, which differs per engine.
summarise() {
  local envelope="$1" out="$2" cause_prefix="$3"
  local manifest_limitations=null
  [ -f "$out/manifest.json" ] && manifest_limitations="$(jq -c '.limitations // null' "$out/manifest.json")"
  printf '%s' "$envelope" | jq -c --arg code "$(cat "$work/code")" --arg prefix "$cause_prefix" \
    --argjson manifest "$manifest_limitations" '{
      exit: ($code | tonumber),
      key: (.error.key // .error.message_key // null),
      cause: ((.error.cause // .error.detail // null) as $c
        | if $prefix != "" and $c != null and ($c | contains($prefix))
          then ($c | split($prefix)[0] + $prefix) else $c end),
      fix: (.error.fix // null),
      limitations: (.limitations // null),
      manifest_limitations: $manifest
    }'
}

status=0
n=0
scenario() {
  # scenario <name> <expected exit> <expected key|-> <cause prefix|-> -- <args...>
  local name="$1" want_exit="$2" want_key="$3" prefix="$4"
  shift 5
  local reference="" engine line
  for engine in python typescript java; do
    n=$((n + 1))
    local out="$work/out-$n"
    local args=("$@")
    if [ "${args[0]}" = "assess" ] || [ "${args[0]}" = "quickstart" ]; then
      args+=(--out "$out")
    fi
    local envelope
    envelope="$(run_cli "$engine" "${args[@]}" 2>/dev/null)"
    line="$(summarise "$envelope" "$out" "${prefix#-}")"
    if [ "$engine" = python ]; then
      reference="$line"
      local got_exit got_key
      got_exit="$(printf '%s' "$line" | jq -r .exit)"
      got_key="$(printf '%s' "$line" | jq -r '.key // "-"')"
      if [ "$got_exit" != "$want_exit" ] || [ "$got_key" != "$want_key" ]; then
        echo "byo-catalog: $name: python gave exit $got_exit key $got_key, expected exit $want_exit key $want_key" >&2
        status=1
      fi
    elif [ "$line" != "$reference" ]; then
      echo "byo-catalog: $name: $engine differs from python" >&2
      echo "  python:  $reference" >&2
      echo "  $engine: $line" >&2
      status=1
    fi
  done
}

base=(assess --bundle "$quick/evidence" --profile "$quick/applicability.yaml" \
  --domain "$quick/domain.linkml.yaml" --catalog eu-ai-act@2026.09)
unset AGENTCE_TRUST_ROOT

scenario "1 untrusted catalog" 3 input.catalog_unverified - -- \
  "${base[@]}" --catalog-dir "$work/untrusted-cat"
scenario "2 unsigned catalog" 3 input.catalog_unverified - -- \
  "${base[@]}" --catalog-dir "$work/unsigned-cat"
scenario "3 unsigned catalog with the override" 0 - - -- \
  "${base[@]}" --catalog-dir "$work/unsigned-cat" --allow-unverified-catalog
scenario "4 trusted catalog with --trust-root" 0 - - -- \
  "${base[@]}" --catalog-dir "$work/trusted-cat" --trust-root "$work/trust-root.json"
scenario "5 --trust-root not a file" 3 input.trust_root_not_a_file - -- \
  "${base[@]}" --catalog-dir "$work/unsigned-cat" --trust-root "$work/absent.json"
scenario "6 --trust-root not readable JSON" 3 input.trust_root_invalid "is not readable JSON: " -- \
  "${base[@]}" --trust-root "$work/bad-root.json"
scenario "7 rebranded catalog" 3 input.catalog_mismatch - -- \
  "${base[@]}" --catalog-dir "$work/rebrand-cat" --trust-root "$work/rebrand-trust-root.json"
scenario "7 rebranded catalog with the override" 3 input.catalog_mismatch - -- \
  "${base[@]}" --catalog-dir "$work/rebrand-cat" --trust-root "$work/rebrand-trust-root.json" \
  --allow-unverified-catalog
scenario "8 ordinary run" 0 - - -- "${base[@]}"
scenario "9 bad --trust-root with no --catalog-dir" 3 input.trust_root_not_a_file - -- \
  "${base[@]}" --trust-root "$work/absent.json"
scenario "11 trust root that is not an object" 3 input.trust_root_invalid - -- \
  "${base[@]}" --catalog-dir "$work/unsigned-cat" --allow-unverified-catalog \
  --trust-root "$work/array-root.json"
export AGENTCE_TRUST_ROOT="$work/bad-root.json"
scenario "10 quickstart under AGENTCE_TRUST_ROOT" 3 input.trust_root_invalid "is not readable JSON: " -- \
  quickstart
unset AGENTCE_TRUST_ROOT

[ "$status" -eq 0 ] && echo "byo-catalog: all three engines verify --catalog-dir signatures identically across 12 scenarios"
exit "$status"
