#!/usr/bin/env bash
# Build gate helper for VG-VERIFY: a tampered or unsigned catalog or release artifact is refused with
# a stable, non-crashing result in all three engines; a validly-signed one -- key-based or
# certificate-based -- verifies offline.
#
# Reuses tools/verify_parity_check.py's own build_canonical_fixtures (the same cryptographic fixture
# builder 18.28's C3 check uses, which itself shells into engines/python's own `conformance.dev_trust`
# derivation) rather than authoring a second, independently-drifting fixture pair -- the same
# departure from golden_loop.sh's own-fixtures precedent sign_command_engines.sh already takes,
# justified for the same reason: C3 and this gate exercise the exact same three-engine verify
# behaviour, so a second fixture pair would only risk drifting from the first.
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

fixture_json="$work/fixture.json"
(cd "$root/tools" && env -u VIRTUAL_ENV uv run --frozen python - "$work" > "$fixture_json" <<'PY'
import json
import shutil
import sys
from pathlib import Path

import verify_parity_check as vpc

work = Path(sys.argv[1])
canonical = work / "canonical"
vpc.build_canonical_fixtures(canonical)

kms_release = work / "release-kms.json"
kms_release.write_bytes((canonical / "release-kms.json").read_bytes())

tampered = json.loads((canonical / "release-kms.json").read_text(encoding="utf-8"))
tampered["signatures"][0]["sig"] = vpc._flip_b64_byte(tampered["signatures"][0]["sig"])
tampered_release = work / "release-tampered.json"
tampered_release.write_text(json.dumps(tampered), encoding="utf-8")

unsigned_catalog = work / "catalog-unsigned"
shutil.copytree(vpc.EU_AI_ACT, unsigned_catalog)
(unsigned_catalog / vpc.CATALOG_SIGNATURE_NAME).unlink()

unsigned_bundle = work / "bundle-unsigned"
vpc.write_bundle_with_manifest(unsigned_bundle, {"artifacts": []})

# Three keyless certificates the development authority re-signed after one field change (the census's
# certificate-field rows), each with the exact refusal Python, the reference, gives.
causes = json.loads((Path("..") / "spec/i18n/messages.en.json").read_text(encoding="utf-8"))
certificate_legs = []
for name in (".algorithm=ecdsa-p256", ".not_before=space-separator", "window=swapped"):
    key = vpc.verify_flow_census.CERTIFICATE_FIELD_EXPECTED[f"certificate:{name}"]
    cause = causes[f"errors.{key}.cause"].removesuffix(".")
    certificate_legs.append(
        {
            "release": str(canonical / "signed" / f"certificate__{name}.json"),
            "reason": f"no signature verified against the trust root: {key}: {cause}",
        }
    )

print(
    json.dumps(
        {
            "certificate_legs": certificate_legs,
            "kms_release": str(kms_release),
            "tampered_release": str(tampered_release),
            "unsigned_catalog": str(unsigned_catalog),
            "unsigned_sentence": vpc.UNSIGNED_SENTENCE,
            "unsigned_bundle": str(unsigned_bundle),
            "unsigned_bundle_sentence": vpc.UNSIGNED_BUNDLE_SENTENCE,
        }
    )
)
PY
)
kms_release="$(jq -r .kms_release "$fixture_json")"
tampered_release="$(jq -r .tampered_release "$fixture_json")"
unsigned_catalog="$(jq -r .unsigned_catalog "$fixture_json")"
unsigned_sentence="$(jq -r .unsigned_sentence "$fixture_json")"
unsigned_bundle="$(jq -r .unsigned_bundle "$fixture_json")"
unsigned_bundle_sentence="$(jq -r .unsigned_bundle_sentence "$fixture_json")"

(cd "$root/engines/java" && ./gradlew --no-daemon --quiet installDist)

run_verify() {
  local engine="$1"
  shift
  case "$engine" in
    python)
      (cd "$root/engines/python" && env -u VIRTUAL_ENV uv run --frozen agentce verify "$@" --json)
      ;;
    typescript)
      (cd "$root/engines/typescript" && pnpm --silent agentce verify "$@" --json)
      ;;
    java)
      (cd "$root/engines/java" && ./build/install/agentce/bin/agentce verify "$@" --json)
      ;;
  esac
}

status=0

# Leg 1: the kms-signed release with its signature byte-flipped -- the item's own named defect. A
# stable refusal is required; a crash (an "error" field, the internal.unexpected shape) fails this
# leg for that engine only.
for engine in python typescript java; do
  out="$(run_verify "$engine" --release "$tampered_release")" && code=0 || code=$?
  verified="$(printf '%s' "$out" | jq -r '.verified | tostring')"
  reason="$(printf '%s' "$out" | jq -r '.reason // empty')"
  has_error="$(printf '%s' "$out" | jq -r 'has("error")')"
  if [ "$code" -ne 3 ] || [ "$verified" != "false" ] || [ -z "$reason" ] || [ "$has_error" = "true" ]; then
    echo "verify: $engine did not cleanly refuse the tampered release (exit $code, verified=${verified:-none}, reason=${reason:-none}, has_error=$has_error; expected exit 3, verified=false, a reason, and no crash)" >&2
    status=1
  fi
done

# Leg 2: the real vendored eu-ai-act catalog with catalog.sig.json deleted -- the exact unsigned
# sentence, byte-identical across all three engines.
for engine in python typescript java; do
  out="$(run_verify "$engine" --catalog "$unsigned_catalog")" && code=0 || code=$?
  verified="$(printf '%s' "$out" | jq -r '.verified | tostring')"
  reason="$(printf '%s' "$out" | jq -r '.reason // empty')"
  if [ "$code" -ne 3 ] || [ "$verified" != "false" ] || [ "$reason" != "$unsigned_sentence" ]; then
    echo "verify: $engine did not refuse the unsigned catalog with the exact sentence (exit $code, verified=${verified:-none}, reason=${reason:-none}; expected exit 3, verified=false, reason=$unsigned_sentence)" >&2
    status=1
  fi
done

# Leg 3: the validly kms-signed release must still verify -- so a stub that always answers
# verified:false (or that never checks the signature at all and answers verified:true for garbage)
# cannot pass this gate on either side.
for engine in python typescript java; do
  out="$(run_verify "$engine" --release "$kms_release")" && code=0 || code=$?
  verified="$(printf '%s' "$out" | jq -r '.verified | tostring')"
  if [ "$code" -ne 0 ] || [ "$verified" != "true" ]; then
    echo "verify: $engine did not verify the validly kms-signed release (exit $code, verified=${verified:-none}; expected exit 0, verified=true)" >&2
    status=1
  fi
done

# Leg 4 (verifier round-1 Finding 1, HIGH/security): a directory-bundle release with zero
# signature entries must refuse with the fixed sentence, not report verified:true -- a gate cannot
# claim an unsigned release artifact is refused (this script's own header) without a leg that
# proves it for the bundle form, not only the single-file form leg 1 already covers.
for engine in python typescript java; do
  out="$(run_verify "$engine" --release "$unsigned_bundle")" && code=0 || code=$?
  verified="$(printf '%s' "$out" | jq -r '.verified | tostring')"
  reason="$(printf '%s' "$out" | jq -r '.reason // empty')"
  if [ "$code" -ne 3 ] || [ "$verified" != "false" ] || [ "$reason" != "$unsigned_bundle_sentence" ]; then
    echo "verify: $engine did not refuse the unsigned bundle with the exact sentence (exit $code, verified=${verified:-none}, reason=${reason:-none}; expected exit 3, verified=false, reason=$unsigned_bundle_sentence)" >&2
    status=1
  fi
done

# Legs 5-7 (item 18.63): a keyless certificate the authority signed but that names ecdsa-p256, has a
# not_before with a space separator, or has its window swapped is refused with the exact keyed reason.
for leg in 0 1 2; do
  release="$(jq -r ".certificate_legs[$leg].release" "$fixture_json")"
  expected="$(jq -r ".certificate_legs[$leg].reason" "$fixture_json")"
  for engine in python typescript java; do
    out="$(run_verify "$engine" --release "$release")" && code=0 || code=$?
    verified="$(printf '%s' "$out" | jq -r '.verified | tostring')"
    reason="$(printf '%s' "$out" | jq -r '.reason // empty')"
    if [ "$code" -ne 3 ] || [ "$verified" != "false" ] || [ "$reason" != "$expected" ]; then
      echo "verify: $engine did not refuse $(basename "$release") with the keyed certificate reason (exit $code, verified=${verified:-none}, reason=${reason:-none}; expected exit 3, verified=false, reason=$expected)" >&2
      status=1
    fi
  done
done

[ "$status" -eq 0 ] && echo "verify: all three engines refuse the tampered release, the unsigned catalog, the unsigned bundle and three bad keyless certificates without crashing, and verify the validly kms-signed release"
exit "$status"
