#!/usr/bin/env bash
# Build gate helper for VG-BYO-CATALOG-PARITY (`trust`) and VG-BYO-CATALOG-WALK (`walk`): `assess
# --catalog-dir` treats an operator's own catalog as untrusted input in all three engines (SPEC §8.7).
# `trust` runs the signature, identity and trust-root scenarios (1 to 11); `walk` runs the scenarios on
# what the catalog walk and the loaders read (12 to 29). They are two gates so that each one's
# seeded-fault demo fits one CI demo shard (18.71b). Each engine runs the same scenarios as the
# Python reference capture for item 18.36 -- an untrusted, unsigned, trusted, rebranded catalog and a
# missing, malformed or non-object trust root, plus quickstart under AGENTCE_TRUST_ROOT -- and must
# give Python's exit code, error key, cause and fix, and the same limitation string under
# --allow-unverified-catalog, in both the --json envelope and manifest.json.
#
# Fixtures live under a mktemp directory outside the repository and $HOME, so every engine reduces a
# catalog path to the same basename. Keys and signatures come from the real `agentce catalog sign`.
set -euo pipefail
part="${1:-}"
case "$part" in
  trust | walk) ;;
  *)
    echo "usage: byo_catalog_parity.sh trust|walk" >&2
    exit 2
    ;;
esac
root="$(cd "$(dirname "$0")/../.." && pwd)"
quick="$root/corpus/quickstart"
work="$(mktemp -d)"
# Scenario 18 leaves a folder that cannot be entered: restore every mode before removing the work folder.
trap 'chmod -R u+rwx "$work" 2>/dev/null; rm -rf "$work"' EXIT

sign_catalog() {
  # sign_catalog <dir> <key name> [trust-root path]
  local extra=()
  [ $# -ge 3 ] && extra=(--write-trust-root "$3")
  (cd "$root/engines/python" && env -u VIRTUAL_ENV uv run --frozen agentce catalog sign "$1" \
    --new-key "$work/$2.pem" ${extra[@]+"${extra[@]}"} --force --json >/dev/null)
}

cp -R "$root/spec/catalogs/base/eu-ai-act" "$work/unsigned-cat"
rm "$work/unsigned-cat/catalog.sig.json"
cp -R "$work/unsigned-cat" "$work/trusted-cat"
sign_catalog "$work/trusted-cat" signer "$work/trust-root.json"
if [ "$part" = trust ]; then
cp -R "$work/unsigned-cat" "$work/untrusted-cat"
sign_catalog "$work/untrusted-cat" stranger
cp -R "$work/unsigned-cat" "$work/rebrand-cat"
sed -i.bak 's/^id: eu-ai-act$/id: eu-ai-act-rebrand/' "$work/rebrand-cat/catalog.yaml"
rm "$work/rebrand-cat/catalog.yaml.bak"
sign_catalog "$work/rebrand-cat" rebrand "$work/rebrand-trust-root.json"
fi
if [ "$part" = walk ]; then
# 18.164: a catalog whose controls/ is a symlink, signed, then edited behind the link; and one with a link
# back to an ancestor folder. Signed before controls/ moves out: `catalog sign` refuses a folder linked
# outside (18.184l, inbox row 397), and the moved folder holds the same bytes, so the signature still covers it.
cp -R "$work/unsigned-cat" "$work/linked-cat"
sign_catalog "$work/linked-cat" linked "$work/linked-trust-root.json"
mv "$work/linked-cat/controls" "$work/linked-controls"
ln -s "$work/linked-controls" "$work/linked-cat/controls"
cp -R "$work/linked-cat" "$work/edited-cat"
rm "$work/edited-cat/controls"
cp -R "$work/linked-controls" "$work/edited-controls"
ln -s "$work/edited-controls" "$work/edited-cat/controls"
sed -i.bak '1,/^title: /s/^title: /title: EDITED AFTER SIGNING /' "$work/edited-controls/$(ls "$work/edited-controls" | head -1)"
rm "$work"/edited-controls/*.bak
cp -R "$work/unsigned-cat" "$work/loop-cat"
ln -s .. "$work/loop-cat/controls/up"
# 18.168: a signed catalog that gains a dot-named control file after signing (text no loader can read as a
# control, so a loader that reads it errors); and two catalogs whose DOC-01 shape names a file the digest
# skips, signed by the Python library with no lint, as an older release would sign them.
cp -R "$work/trusted-cat" "$work/dot-control-cat"
printf 'not: [a control\n' > "$work/dot-control-cat/controls/.DOC-99.yaml"
sign_without_lint() {
  # sign_without_lint <dir> <trust-root path>
  (cd "$root/engines/python" && env -u VIRTUAL_ENV uv run --frozen python - "$1" "$2" <<'PY'
import json, sys
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from agentce import signing
cat, tr = Path(sys.argv[1]), Path(sys.argv[2])
signer = signing.KmsSigner(private_key=Ed25519PrivateKey.generate())
digest = signing.digest_tree(cat, exclude=frozenset({signing.CATALOG_SIGNATURE_NAME}))
st = signing.intoto_statement("eu-ai-act", digest, "https://agent-conformance.org/attestation/catalog/v1",
                              {"kind": "catalog", "id": "eu-ai-act"})
(cat / signing.CATALOG_SIGNATURE_NAME).write_text(json.dumps(signing.sign_statement(st, signer), indent=2))
tr.write_text(json.dumps(signing.TrustRoot.document(signer.keyid, signer.public_key_b64, "unset")))
PY
  )
}
shape_elsewhere() {
  # shape_elsewhere <dir> <where the file goes> <what DOC-01 names>
  cp -R "$work/unsigned-cat" "$1"
  mkdir -p "$(dirname "$1/$2")"
  mv "$1/shapes/DOC-01.ttl" "$1/$2"
  sed -i.bak "s#^  shape: shapes/DOC-01.ttl\$#  shape: $3#" "$1/controls/DOC-01.yaml"
  rm "$1/controls/DOC-01.yaml.bak"
  sign_without_lint "$1" "$1-trust-root.json"
}
shape_elsewhere "$work/dot-shape-cat" shapes/.DOC-01.ttl shapes/.DOC-01.ttl
shape_elsewhere "$work/pycache-shape-cat" shapes/__pycache__/DOC-01.ttl shapes/__PYCACHE__/DOC-01.ttl
# 18.170: a trusted signed catalog that gains a folder, holding a file, which can be listed but not entered;
# every engine's walk must refuse what it cannot stat rather than walk past it.
cp -R "$work/trusted-cat" "$work/unenterable-cat"
mkdir "$work/unenterable-cat/controls/extra"
printf 'x' > "$work/unenterable-cat/controls/extra/x.txt"
chmod 400 "$work/unenterable-cat/controls/extra"
# 18.181: entries that are there but are not files (a FIFO, a dangling link, a folder named like a
# control, a FIFO named catalog.yaml), and covered files linked to a file outside the catalog folder.
mkdir "$work/outside"
cp -R "$work/unsigned-cat" "$work/fifo-cat"
rm "$work/fifo-cat/controls/DOC-01.yaml"
mkfifo "$work/fifo-cat/controls/DOC-01.yaml"
cp -R "$work/unsigned-cat" "$work/dangling-cat"
rm "$work/dangling-cat/controls/DOC-01.yaml"
ln -s "$work/absent.yaml" "$work/dangling-cat/controls/DOC-01.yaml"
cp -R "$work/unsigned-cat" "$work/fifo-meta-cat"
rm "$work/fifo-meta-cat/catalog.yaml"
mkfifo "$work/fifo-meta-cat/catalog.yaml"
cp -R "$work/unsigned-cat" "$work/folder-control-cat"
rm "$work/folder-control-cat/controls/DOC-01.yaml"
mkdir "$work/folder-control-cat/controls/DOC-01.yaml"
link_out() {
  # link_out <catalog dir> <covered file>: move the file out of the catalog and link it back.
  local moved
  moved="$work/outside/$(basename "$1")-$(basename "$2")"
  mv "$1/$2" "$moved"
  ln -s "$moved" "$1/$2"
}
cp -R "$work/trusted-cat" "$work/control-out-cat"
link_out "$work/control-out-cat" controls/DOC-01.yaml
cp -R "$work/trusted-cat" "$work/shape-out-cat"
link_out "$work/shape-out-cat" shapes/DOC-01.ttl
cp -R "$work/unsigned-cat" "$work/unsigned-out-cat"
link_out "$work/unsigned-out-cat" controls/DOC-01.yaml
# A control that is an absolute link to a file inside the catalog: containment is judged by folder
# identity, so the catalog path spelled in another case (a case-insensitive filesystem) changes nothing.
cp -R "$work/unsigned-cat" "$work/inside-cat"
mkdir -p "$work/inside-cat/checklists"
mv "$work/inside-cat/controls/DOC-01.yaml" "$work/inside-cat/checklists/DOC-01.yaml.real"
ln -s "$work/inside-cat/checklists/DOC-01.yaml.real" "$work/inside-cat/controls/DOC-01.yaml"
sign_catalog "$work/inside-cat" inside "$work/inside-trust-root.json"
fi
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

if [ "$part" = trust ]; then
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

else
scenario "12 catalog signed with controls/ linked" 0 - - -- \
  "${base[@]}" --catalog-dir "$work/linked-cat" --trust-root "$work/linked-trust-root.json"
scenario "13 linked catalog edited behind the link" 3 input.catalog_unverified - -- \
  "${base[@]}" --catalog-dir "$work/edited-cat" --trust-root "$work/linked-trust-root.json"
scenario "14 catalog with a link to an ancestor folder" 3 input.catalog_unreadable - -- \
  "${base[@]}" --catalog-dir "$work/loop-cat" --allow-unverified-catalog
scenario "15 signed catalog with a dot-named control file added after signing" 0 - - -- \
  "${base[@]}" --catalog-dir "$work/dot-control-cat" --trust-root "$work/trust-root.json"
scenario "16 shape named as a dot-named file the digest skips" 3 catalog.shape.unresolved - -- \
  "${base[@]}" --catalog-dir "$work/dot-shape-cat" --trust-root "$work/dot-shape-cat-trust-root.json"
scenario "17 shape named through __PYCACHE__, a folder the digest skips on any host" 3 catalog.shape.unresolved - -- \
  "${base[@]}" --catalog-dir "$work/pycache-shape-cat" --trust-root "$work/pycache-shape-cat-trust-root.json"
if [ "$(id -u)" -ne 0 ]; then
  scenario "18 signed catalog that gains a folder no engine can enter" 3 input.catalog_unreadable - -- \
    "${base[@]}" --catalog-dir "$work/unenterable-cat" --trust-root "$work/trust-root.json"
else
  echo "byo-catalog: scenario 18 skipped: running as root, which enters any folder" >&2
fi
scenario "19 control that is a FIFO, with the override" 3 input.catalog_unreadable - -- \
  "${base[@]}" --catalog-dir "$work/fifo-cat" --allow-unverified-catalog
scenario "20 control that is a dangling link, with the override" 3 input.catalog_unreadable - -- \
  "${base[@]}" --catalog-dir "$work/dangling-cat" --allow-unverified-catalog
scenario "21 catalog.yaml that is a FIFO, refused unopened" 3 input.catalog_unreadable - -- \
  "${base[@]}" --catalog-dir "$work/fifo-meta-cat" --allow-unverified-catalog
scenario "22 signed catalog whose control links outside it" 3 input.catalog_unverified - -- \
  "${base[@]}" --catalog-dir "$work/control-out-cat" --trust-root "$work/trust-root.json"
scenario "23 signed catalog whose shape links outside it" 3 input.catalog_unverified - -- \
  "${base[@]}" --catalog-dir "$work/shape-out-cat" --trust-root "$work/trust-root.json"
scenario "24 signed catalog whose shape links outside it, with the override" 0 - - -- \
  "${base[@]}" --catalog-dir "$work/shape-out-cat" --trust-root "$work/trust-root.json" \
  --allow-unverified-catalog
scenario "25 unsigned catalog whose control links outside it, with the override" 0 - - -- \
  "${base[@]}" --catalog-dir "$work/unsigned-out-cat" --allow-unverified-catalog
scenario "26 folder named controls/DOC-01.yaml, with the override" 3 input.catalog_unreadable - -- \
  "${base[@]}" --catalog-dir "$work/folder-control-cat" --allow-unverified-catalog
scenario "27 signed catalog whose control is an absolute link to a file inside it" 0 - - -- \
  "${base[@]}" --catalog-dir "$work/inside-cat" --trust-root "$work/inside-trust-root.json"
folded="$(printf '%s' "$work" | tr '[:lower:]' '[:upper:]')"
if [ "$folded" != "$work" ] && [ -d "$folded/inside-cat" ]; then
  scenario "28 the same catalog through its path in another case" 0 - - -- \
    "${base[@]}" --catalog-dir "$folded/inside-cat" --trust-root "$work/inside-trust-root.json"
else
  echo "byo-catalog: scenario 28 skipped: the filesystem is case-sensitive" >&2
fi
firmlink="/System/Volumes/Data$(cd "$work" && pwd -P)"
if [ -d "$firmlink/inside-cat" ]; then
  scenario "29 the same catalog through a second spelling of its folder (a macOS firmlink)" 0 - - -- \
    "${base[@]}" --catalog-dir "$firmlink/inside-cat" --trust-root "$work/inside-trust-root.json"
else
  echo "byo-catalog: scenario 29 skipped: no firmlink spelling of the work folder" >&2
fi
fi

[ "$status" -eq 0 ] && echo "byo-catalog ($part): all three engines verify --catalog-dir signatures identically across $((n / 3)) scenarios"
exit "$status"
