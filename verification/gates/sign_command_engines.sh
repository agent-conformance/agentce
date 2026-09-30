#!/usr/bin/env bash
# Build gate helper for VG-SIGN: `agentce sign` refuses to sign a report that is not ready to
# publish, and a kms-profile signature verifies offline against the published public key, in all
# three engines.
#
# Reuses tools/sign_parity_check.py's own ready_fixture/not_ready_fixture (the same builders 18.26's
# C3 check uses) to build the READY/NOT_READY report directories, rather than authoring a second,
# independently-drifting fixture pair -- the one departure from golden_loop.sh's own-fixtures
# precedent this repository otherwise follows, justified because C3 and this gate exercise the exact
# same three-engine signing behaviour: a second, independently-authored fixture pair would only risk
# drifting from the first.
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
key="$root/tools/fixtures/sign/test-key.pem"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

fixture_json="$work/fixture.json"
(cd "$root/tools" && env -u VIRTUAL_ENV uv run --frozen python - "$work" > "$fixture_json" <<'PY'
import json
import sys
from pathlib import Path

import sign_parity_check as spc

work = Path(sys.argv[1])
ready = spc.ready_fixture(work / "ready")
not_ready = spc.not_ready_fixture(work / "not-ready")
ready_wtr = spc.ready_fixture(work / "ready-wtr", {"claimant": {"org": "acme corp"}})
keyid, pub_b64 = spc.known_test_key()
print(
    json.dumps(
        {
            "ready": str(ready),
            "not_ready": str(not_ready),
            "ready_wtr": str(ready_wtr),
            "keyid": keyid,
            "pub_b64": pub_b64,
        }
    )
)
PY
)
ready="$(jq -r .ready "$fixture_json")"
not_ready="$(jq -r .not_ready "$fixture_json")"
ready_wtr="$(jq -r .ready_wtr "$fixture_json")"
keyid="$(jq -r .keyid "$fixture_json")"
pub_b64="$(jq -r .pub_b64 "$fixture_json")"

(cd "$root/engines/java" && ./gradlew --no-daemon --quiet installDist)

run_sign() {
  local engine="$1"
  shift
  case "$engine" in
    python)
      (cd "$root/engines/python" && env -u VIRTUAL_ENV uv run --frozen agentce sign "$@" --json)
      ;;
    typescript)
      (cd "$root/engines/typescript" && pnpm --silent agentce sign "$@" --json)
      ;;
    java)
      (cd "$root/engines/java" && ./build/install/agentce/bin/agentce sign "$@" --json)
      ;;
  esac
}

verify_offline() {
  # Independently verifies a DSSE envelope against the known test key's public half by calling
  # tools/sign_parity_check.py's own `verify_offline` (which itself shells into engines/python's
  # reference `signing.verify_envelope`) rather than re-embedding that DSSE bootstrap a second time.
  # Run from tools/ (not $work) so `import sign_parity_check` resolves via the script's own directory.
  local detached_file="$1"
  (cd "$root/tools" && env -u VIRTUAL_ENV uv run --frozen python - "$detached_file" "$pub_b64" "$keyid" <<'PY'
import json
import sys

import sign_parity_check as spc

detached = json.load(open(sys.argv[1]))
ok, out = spc.verify_offline(detached, sys.argv[2], sys.argv[3])
print("VERIFIED" if ok else out)
PY
)
}

status=0

for engine in python typescript java; do
  out="$(run_sign "$engine" "$not_ready" --as claimant --profile kms --key "$key")" && code=0 || code=$?
  # Python's `--json` envelope keys an error as `error.key`; TypeScript/Java as `error.message_key`
  # (the same key/message_key split tools/readiness_parity_check.py's _error_fields normalizes).
  key_got="$(printf '%s' "$out" | jq -r '.error.key // .error.message_key // empty')"
  if [ "$code" -ne 3 ] || [ "$key_got" != "sign.not_ready" ]; then
    echo "sign: $engine did not refuse the NOT_READY report (exit $code, key ${key_got:-none}, expected exit 3 and sign.not_ready)" >&2
    status=1
  fi
done

for engine in python typescript java; do
  out="$(run_sign "$engine" "$ready" --as claimant --profile kms --key "$key")" && code=0 || code=$?
  if [ "$code" -ne 0 ]; then
    echo "sign: $engine refused the READY report (exit $code: $out)" >&2
    status=1
    continue
  fi
  sig_path="$(printf '%s' "$out" | jq -r '.signature // empty')"
  if [ -z "$sig_path" ] || [ ! -f "$sig_path" ]; then
    echo "sign: $engine wrote no detached signature file for the READY report" >&2
    status=1
    continue
  fi
  verified="$(verify_offline "$sig_path")"
  if [ "$verified" != "VERIFIED" ]; then
    echo "sign: $engine's READY signature did not verify offline against the known test key ($verified)" >&2
    status=1
  fi
done

for engine in python typescript java; do
  out="$(run_sign "$engine" "$ready_wtr" --as assessor --profile kms --key "$key" --write-trust-root)" && code=0 || code=$?
  if [ "$code" -ne 0 ]; then
    echo "sign: $engine refused --write-trust-root on the READY report (exit $code: $out)" >&2
    status=1
    continue
  fi
  trust_root_path="$(printf '%s' "$out" | jq -r '.trust_root // empty')"
  if [ -z "$trust_root_path" ] || [ ! -f "$trust_root_path" ]; then
    echo "sign: $engine wrote no trust-root.json for --write-trust-root" >&2
    status=1
    continue
  fi
  got_pub="$(jq -r --arg k "$keyid" '.keys[$k].public_key // empty' "$trust_root_path")"
  if [ "$got_pub" != "$pub_b64" ]; then
    echo "sign: $engine's trust-root.json keys[$keyid].public_key does not match the known test key" >&2
    status=1
  fi
done

[ "$status" -eq 0 ] && echo "sign: all three engines refuse the NOT_READY report and the READY kms signature verifies offline"
exit "$status"
