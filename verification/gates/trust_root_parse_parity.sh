#!/usr/bin/env bash
# Build gate helper for VG-TRUST-ROOT-PARSE-PARITY (18.81): the three engines' trust-root loaders parse
# with the shared untrusted-JSON rule, so a trust root nested past 1000 levels gets one fixed cause and
# the engines accept and refuse the same files. Each engine's unit test file must exist and run at least
# one test; then the real Python, TypeScript and Java CLIs read trust roots generated here, by
# --trust-root and by AGENTCE_TRUST_ROOT, and Python's verify --report reads a deep one both ways.
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
quickstart="$root/corpus/quickstart"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
too_deep="is not readable JSON: it nests containers more than 1000 levels deep"

status=0

# shellcheck source=verification/gates/test_files.sh
. "$root/verification/gates/test_files.sh"

echo "trust-root-parse-parity: python unit tests"
if ! (cd "$root/engines/python" && env -u VIRTUAL_ENV uv run --frozen pytest -q -o addopts= -p no:cacheprovider \
  tests/test_trust_root_parse.py); then
  echo "trust-root-parse-parity: python's trust-root parse tests failed" >&2
  status=1
fi

echo "trust-root-parse-parity: typescript unit tests"
if ! ts_tests src/trustRootParse.test.ts; then
  echo "trust-root-parse-parity: typescript's trust-root parse tests failed" >&2
  status=1
fi

echo "trust-root-parse-parity: typescript and java builds"
(cd "$root/engines/typescript" && pnpm --silent build >/dev/null)
(cd "$root/engines/java" && ./gradlew --no-daemon --quiet installDist)

echo "trust-root-parse-parity: java unit tests"
if ! java_tests org.agentce.TrustRootParseTest; then
  echo "trust-root-parse-parity: java's trust-root parse tests failed" >&2
  status=1
fi

fixtures="$work/fixtures"
mkdir -p "$fixtures"
(cd "$root/engines/python" && env -u VIRTUAL_ENV uv run --frozen python - "$fixtures") <<'PY'
import sys
from pathlib import Path

out = Path(sys.argv[1])
cases = {
    "deep-array-10000": b"[" * 10000 + b"]" * 10000,
    "deep-object-10000": b'{"a":' * 10000 + b"1" + b"}" * 10000,
    "keys-1001": b'{"keys":' + b"[" * 1000 + b"]" * 1000 + b"}",
    "keys-999": b'{"keys":' + b"[" * 999 + b"]" * 999 + b"}",
    "nan": b'{"keys":{},"x":NaN}',
    "lone-surrogate": b'{"keys":{},"x":"\\ud800"}',
    "invalid-utf8": b'{"keys":{},"x":"\xff"}',
    "bom": b'\xef\xbb\xbf{"keys":{}}',
}
for name, raw in cases.items():
    (out / f"{name}.json").write_bytes(raw)
PY

run_cli() { # engine, trust root, out dir, via (flag|env)
  local args=(assess --json --bundle "$quickstart/evidence" --profile "$quickstart/applicability.yaml" --out "$3")
  local env_root=""
  if [ "$4" = flag ]; then args+=(--trust-root "$2"); else env_root="$2"; fi
  case "$1" in
    python) (cd "$root/engines/python" && AGENTCE_TRUST_ROOT="$env_root" env -u VIRTUAL_ENV uv run --frozen agentce "${args[@]}") ;;
    typescript) (cd "$root/engines/typescript" && AGENTCE_TRUST_ROOT="$env_root" node bin/agentce.js "${args[@]}") ;;
    java) AGENTCE_TRUST_ROOT="$env_root" "$root/engines/java/build/install/agentce/bin/agentce" "${args[@]}" ;;
  esac
}

# Prints "<exit code>|<error key>|<cause>" for one run.
outcome() {
  local out code
  set +e
  out="$(run_cli "$@" 2>/dev/null)"
  code=$?
  set -e
  printf '%s|%s|%s\n' "$code" \
    "$(jq -r '.error.key // .error.message_key // empty' <<<"$out" 2>/dev/null || true)" \
    "$(jq -r '.error.cause // .error.detail // empty' <<<"$out" 2>/dev/null || true)"
}

check() { # fixture, via, expected cause after "could not be loaded: <path> " (empty: prefix only)
  local name="$1" via="$2" tail="$3" path="$fixtures/$1.json" engine got code key cause want
  for engine in python typescript java; do
    got="$(outcome "$engine" "$path" "$work/out-$engine-$name-$via" "$via")"
    code="${got%%|*}"; got="${got#*|}"; key="${got%%|*}"; cause="${got#*|}"
    want="the trust root '$path' could not be loaded: $path $tail"
    if [ "$code" != 3 ] || [ "$key" != input.trust_root_invalid ]; then
      echo "trust-root-parse-parity: $engine on $name ($via): exit $code key [$key], expected exit 3 and input.trust_root_invalid" >&2
      status=1
    elif [ -n "$tail" ] && [ "$cause" != "$want" ]; then
      echo "trust-root-parse-parity: $engine on $name ($via): cause [$cause], expected [$want]" >&2
      status=1
    elif [ -z "$tail" ] && [[ "$cause" != "the trust root '$path' could not be loaded: $path is not readable JSON"* ]]; then
      echo "trust-root-parse-parity: $engine on $name ($via): cause [$cause] is not a 'not readable JSON' refusal" >&2
      status=1
    fi
  done
}

echo "trust-root-parse-parity: real CLI, trust roots past the depth limit, all three engines"
for name in deep-array-10000 deep-object-10000 keys-1001; do
  check "$name" flag "$too_deep"
done
check deep-object-10000 env "$too_deep"

echo "trust-root-parse-parity: real CLI, a trust root at the limit reaches the shape stage"
check keys-999 flag "is not a usable trust root: keys is not a mapping of key id to key entry"

echo "trust-root-parse-parity: real CLI, NaN, a lone surrogate, invalid UTF-8 and a byte-order mark are refused"
for name in nan lone-surrogate invalid-utf8 bom; do
  check "$name" flag ""
done

echo "trust-root-parse-parity: python verify --report, a deep signer trust root and a deep embedded one"
report="$work/report"
mkdir -p "$report"
printf '{"signatures":[{"role":"claimant"}]}' >"$report/claim.json"
cp "$fixtures/deep-object-10000.json" "$report/trust-root.json"
for via in signer embedded; do
  args=(verify --json --report "$report")
  [ "$via" = signer ] && args+=(--signer-trust-root "$fixtures/deep-object-10000.json")
  set +e
  out="$(cd "$root/engines/python" && env -u VIRTUAL_ENV uv run --frozen agentce "${args[@]}" 2>/dev/null)"
  code=$?
  set -e
  key="$(jq -r '.error.key // empty' <<<"$out" 2>/dev/null || true)"
  cause="$(jq -r '.error.cause // empty' <<<"$out" 2>/dev/null || true)"
  if [ "$code" != 3 ] || [ "$key" != input.trust_root_invalid ] || [[ "$cause" != *"deep-object-10000.json' could not be loaded: "*"$too_deep" && "$cause" != *"trust-root.json' could not be loaded: "*"$too_deep" ]]; then
    echo "trust-root-parse-parity: python verify --report ($via): exit $code key [$key] cause [$cause]" >&2
    status=1
  fi
done

[ "$status" -eq 0 ] && echo "trust-root-parse-parity: the three engines refuse a trust root past 1000 levels with the same cause, reach the shape stage at 1000, and refuse NaN, lone surrogates, invalid UTF-8 and a byte-order mark alike"
exit "$status"
