#!/usr/bin/env bash
# Build gate helper for VG-I18N-ERROR-CATALOGUE (18.80): every engine holds an error-key registry read
# from the vendored spec/i18n catalogue, with a completeness check over its own raise sites, a loader
# that refuses a corrupt catalogue, one stable cause per malformed trust-root shape, and a keyed
# digest-read error. Each engine's unit test file must exist and run at least one test (a missing or
# empty file fails the gate); then the real Python, TypeScript and Java CLIs read the committed
# trust_root_shapes fixtures and must give the same key, exit code and cause tail.
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
fixtures="$root/verification/gates/fixtures/trust_root_shapes"
quickstart="$root/corpus/quickstart"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

status=0

# shellcheck source=verification/gates/test_files.sh
. "$root/verification/gates/test_files.sh"

echo "i18n-error-catalogue: python registry, loader, trust-root and digest unit tests"
if ! (cd "$root/engines/python" && env -u VIRTUAL_ENV uv run --frozen pytest -q -o addopts= -p no:cacheprovider \
  tests/test_error_catalogue.py); then
  echo "i18n-error-catalogue: python's error catalogue tests failed" >&2
  status=1
fi

echo "i18n-error-catalogue: typescript registry, loader, trust-root and digest unit tests"
if ! ts_tests src/errorCatalogue.test.ts; then
  echo "i18n-error-catalogue: typescript's error catalogue tests failed" >&2
  status=1
fi

echo "i18n-error-catalogue: java build"
(cd "$root/engines/java" && ./gradlew --no-daemon --quiet installDist)

echo "i18n-error-catalogue: java registry, loader, trust-root and digest unit tests"
if ! java_tests org.agentce.ErrorCatalogueTest; then
  echo "i18n-error-catalogue: java's error catalogue tests failed" >&2
  status=1
fi

run_cli() { # engine, trust root, out dir
  local args=(assess --bundle "$quickstart/evidence" --profile "$quickstart/applicability.yaml" --trust-root "$2" --out "$3")
  case "$1" in
    python) (cd "$root/engines/python" && env -u VIRTUAL_ENV uv run --frozen agentce "${args[@]}") ;;
    typescript) (cd "$root/engines/typescript" && pnpm --silent agentce "${args[@]}") ;;
    java) "$root/engines/java/build/install/agentce/bin/agentce" "${args[@]}" ;;
  esac
}

echo "i18n-error-catalogue: real CLI, malformed trust-root shapes, all three engines"
while IFS='|' read -r name tail; do
  for engine in python typescript java; do
    set +e
    out="$(run_cli "$engine" "$fixtures/$name.json" "$work/$engine-$name" 2>&1)"
    code=$?
    set -e
    line="$(printf '%s\n' "$out" | grep -m1 'input.trust_root_invalid' || true)"
    got="${line##*is not a usable trust root: }"
    if [ "$code" -ne 3 ] || [ -z "$line" ] || [ "$got" != "$tail" ]; then
      echo "i18n-error-catalogue: $engine on $name: exit $code, cause tail [$got], expected exit 3 and [$tail]" >&2
      status=1
    fi
  done
done <"$fixtures/expected.txt"

echo "i18n-error-catalogue: real CLI, an empty keys mapping is no keys, all three engines"
codes=()
for engine in python typescript java; do
  set +e
  out="$(run_cli "$engine" "$fixtures/empty-keys.json" "$work/$engine-empty" 2>&1)"
  code=$?
  set -e
  if printf '%s\n' "$out" | grep -q 'input.trust_root_invalid'; then
    echo "i18n-error-catalogue: $engine refused an empty keys mapping as input.trust_root_invalid" >&2
    status=1
  fi
  codes+=("$code")
done
if [ "$(printf '%s\n' "${codes[@]}" | sort -u | wc -l | tr -d ' ')" != "1" ]; then
  echo "i18n-error-catalogue: an empty keys mapping exits differently across engines: ${codes[*]}" >&2
  status=1
fi

[ "$status" -eq 0 ] && echo "i18n-error-catalogue: each engine's registry matches spec/i18n and covers its raise sites, corrupt catalogues are refused, the digest read is keyed, and the three CLIs give the same cause for every malformed trust-root shape"
exit "$status"
