#!/usr/bin/env bash
# Build gate helper for VG-VERIFY-REFUSAL-KEYS (18.64): every verify refusal carries stable message
# keys. Each engine's refusal-key unit tests run; no engine holds a hand copy of a verify.* cause; and
# the real Python, TypeScript and Java CLIs give the same reason and the expected reason_keys on the
# committed catalog-signature fixtures (fixtures/verify_refusal_keys/) and on releases built from the
# deterministic development keys (tools/verify_parity_check.py's build_canonical_fixtures, the same
# builder VG-VERIFY uses, so the signed fixtures never drift from the census's).
#
# --marker-only runs one leg: each engine runs from a copy whose vendored catalogue starts every
# verify.* cause with a marker naming its key, and the markers found in each reason, in order, must
# equal reason_keys, which shows the text came from the catalogue and not a hand copy.
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
fixtures="$root/verification/gates/fixtures/verify_refusal_keys"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
marker_only=0
[ "${1:-}" = "--marker-only" ] && marker_only=1

status=0

# shellcheck source=verification/gates/test_files.sh
. "$root/verification/gates/test_files.sh"

echo "verify-refusal-keys: build the TypeScript and Java engines"
(cd "$root/engines/typescript" && pnpm --silent build >/dev/null)
(cd "$root/engines/java" && ./gradlew --no-daemon --quiet installDist)

if [ "$marker_only" -eq 0 ]; then
  echo "verify-refusal-keys: python, typescript and java refusal-key unit tests"
  if ! (cd "$root/engines/python" && env -u VIRTUAL_ENV uv run --frozen pytest -q -o addopts= -p no:cacheprovider \
    tests/test_verify_refusal_keys.py); then
    echo "verify-refusal-keys: python's refusal-key tests failed" >&2
    status=1
  fi
  if ! ts_tests src/verifyRefusalKeys.test.ts; then
    echo "verify-refusal-keys: typescript's refusal-key tests failed" >&2
    status=1
  fi
  if ! java_tests org.agentce.VerifyRefusalKeysTest; then
    echo "verify-refusal-keys: java's refusal-key tests failed" >&2
    status=1
  fi

  echo "verify-refusal-keys: no engine source holds a verify.* cause by hand"
  if ! (cd "$root/tools" && env -u VIRTUAL_ENV uv run --frozen python - "$root" <<'PY'
import json
import re
import sys
from pathlib import Path

root = Path(sys.argv[1])
flat = json.loads((root / "spec/i18n/messages.en.json").read_text(encoding="utf-8"))
# The static fragments of each verify.* cause, split at its {params}; a fragment of a few
# characters ("signature (") says nothing about where a text came from, so only longer ones count.
fragments = {
    piece.strip(): name[len("errors.") : -len(".cause")]
    for name, text in flat.items()
    if name.startswith("errors.verify.") and name.endswith(".cause")
    for piece in re.split(r"\{\w+\}", text)
    if len(piece.strip()) >= 20
}
# Texts outside 18.64's scope that share a fragment: the trust-root loading cause (18.80) and
# report_unsigned's claim.json text.
allowed = ("is not a usable trust root", "claim.json carries no signature")
sources = [
    *sorted((root / "engines/python/agentce").rglob("*.py")),
    *sorted((root / "engines/typescript/src").glob("*.ts")),
    *sorted((root / "engines/java/src/main/java").rglob("*.java")),
]
problems = []
for path in sources:
    if path.name.endswith(".test.ts"):
        continue
    lines = path.read_text(encoding="utf-8").splitlines()
    for number, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith(("#", "//", "*", "/*")) or any(a in line for a in allowed):
            continue
        for fragment, key in fragments.items():
            quoted = any(q + fragment in line for q in "\"'`")
            # An error raised with its own key next to its cause (error.key, outside 18.64's scope)
            # names the key on one of the three lines before.
            keyed = any(f'"{key}"' in before for before in lines[max(0, number - 4) : number])
            if quoted and not keyed:
                problems.append(f"{path.relative_to(root)}:{number}: holds {fragment!r} by hand")
for problem in problems:
    print(problem, file=sys.stderr)
sys.exit(1 if problems else 0)
PY
  ); then
    echo "verify-refusal-keys: a verify refusal text is hand-copied instead of read from the catalogue" >&2
    status=1
  fi
fi

echo "verify-refusal-keys: build the release fixtures"
cases="$work/cases.txt"
(cd "$root/tools" && env -u VIRTUAL_ENV uv run --frozen python - "$work" "$fixtures" >"$cases" <<'PY'
import json
import shutil
import sys
from pathlib import Path

import verify_parity_check as vpc

work, fixtures = Path(sys.argv[1]), Path(sys.argv[2])
canonical = work / "canonical"
vpc.build_canonical_fixtures(canonical)

for line in (fixtures / "expected.txt").read_text(encoding="utf-8").splitlines():
    name, flag, _keys = line.split("|")
    dest = work / name
    if flag == "--catalog":
        shutil.copytree(vpc.EU_AI_ACT, dest)
        if name == "catalog-unsigned":
            (dest / vpc.CATALOG_SIGNATURE_NAME).unlink()
        else:
            shutil.copyfile(fixtures / f"{name.removeprefix('catalog-')}.sig.json", dest / vpc.CATALOG_SIGNATURE_NAME)
    elif name == "release-tampered":
        tampered = json.loads((canonical / "release-kms.json").read_text(encoding="utf-8"))
        tampered["signatures"][0]["sig"] = vpc._flip_b64_byte(tampered["signatures"][0]["sig"])
        dest = work / f"{name}.json"
        dest.write_text(json.dumps(tampered), encoding="utf-8")
    else:
        shutil.copytree(canonical / "bundle", dest)
        if name in ("release-missing-artifact", "release-joined"):
            (dest / "artifact-a.txt").unlink()
        if name == "release-bad-digest":
            (dest / "artifact-a.txt").write_bytes(b"tampered content\n")
        if name in ("release-no-envelope", "release-joined"):
            signatures = json.loads((dest / "signatures.json").read_text(encoding="utf-8"))
            del signatures[0]["envelope"]
            (dest / "signatures.json").write_text(json.dumps(signatures), encoding="utf-8")
    print(f"{name}|{flag}|{dest}")
PY
)

run_verify() { # engine, flag, path
  case "$1" in
    python) (cd "$root/engines/python" && env -u VIRTUAL_ENV uv run --frozen agentce verify "$2" "$3" --json) </dev/null ;;
    typescript) node "$root/engines/typescript/dist/cli.js" verify "$2" "$3" --json </dev/null ;;
    java) "$root/engines/java/build/install/agentce/bin/agentce" verify "$2" "$3" --json </dev/null ;;
  esac
}

if [ "$marker_only" -eq 0 ]; then
  echo "verify-refusal-keys: real CLIs, same reason and the expected reason_keys in all three engines"
  while IFS='|' read -r name flag path; do
    expected="$(grep "^$name|" "$fixtures/expected.txt" | cut -d'|' -f3)"
    reasons=()
    for engine in python typescript java; do
      out="$(run_verify "$engine" "$flag" "$path" || true)"
      keys="$(printf '%s' "$out" | jq -r '(.reason_keys // []) | join(",")' 2>/dev/null || echo "<not json>")"
      if [ "$keys" != "$expected" ]; then
        echo "verify-refusal-keys: $engine on $name: reason_keys [$keys], expected [$expected]" >&2
        status=1
      fi
      reasons+=("$(printf '%s' "$out" | jq -r '.reason // ""' 2>/dev/null || true)")
    done
    if [ "${reasons[0]}" != "${reasons[1]}" ] || [ "${reasons[0]}" != "${reasons[2]}" ]; then
      echo "verify-refusal-keys: $name: the engines' reasons differ: ${reasons[*]}" >&2
      status=1
    fi
  done <"$cases"
  [ "$status" -eq 0 ] && echo "verify-refusal-keys: each engine's refusal keys are in the catalogue, no engine holds a cause by hand, and the three CLIs agree on every fixture's reason and keys"
  exit "$status"
fi

echo "verify-refusal-keys: marked catalogues, each engine run from a copy"
marked="$work/messages.en.json"
(cd "$root/tools" && env -u VIRTUAL_ENV uv run --frozen python - "$root/spec/i18n/messages.en.json" "$marked" <<'PY'
import json
import sys

flat = json.loads(open(sys.argv[1], encoding="utf-8").read())
for name in flat:
    if name.startswith("errors.verify.") and name.endswith(".cause"):
        key = name[len("errors.") : -len(".cause")]
        flat[name] = f"<<{key}>>{flat[name]}"
open(sys.argv[2], "w", encoding="utf-8").write(json.dumps(flat, ensure_ascii=False, indent=2) + "\n")
PY
)
mkdir -p "$work/py" "$work/ts" "$work/java/i18n"
cp -R "$root/engines/python/agentce" "$work/py/agentce"
cp "$marked" "$work/py/agentce/data/i18n/messages.en.json"
cp -R "$root/engines/typescript/dist" "$root/engines/typescript/data" "$root/engines/typescript/package.json" "$work/ts/"
ln -s "$root/engines/typescript/node_modules" "$work/ts/node_modules"
cp "$marked" "$work/ts/data/i18n/messages.en.json"
cp "$marked" "$work/java/i18n/messages.en.json"

run_marked() { # engine, flag, path
  case "$1" in
    # From $work, so the source tree's own agentce/ (the working directory on sys.path) is not found first.
    python) (cd "$work" && env -u VIRTUAL_ENV PYTHONPATH="$work/py" uv run --project "$root/engines/python" --frozen python -c \
      'import sys; from agentce.cli import main; sys.exit(main())' verify "$2" "$3" --json) </dev/null ;;
    typescript) node "$work/ts/dist/cli.js" verify "$2" "$3" --json </dev/null ;;
    java) java -cp "$work/java:$root/engines/java/build/install/agentce/lib/*" org.agentce.Cli verify "$2" "$3" --json </dev/null ;;
  esac
}

while IFS='|' read -r name flag path; do
  for engine in python typescript java; do
    out="$(run_marked "$engine" "$flag" "$path" || true)"
    keys="$(printf '%s' "$out" | jq -r '(.reason_keys // []) | join(",")' 2>/dev/null || echo "<not json>")"
    found="$(printf '%s' "$out" | jq -r '.reason // ""' 2>/dev/null | grep -o '<<verify\.[a-z_]*>>' | sed 's/^<<//; s/>>$//' | paste -sd, - || true)"
    if [ -z "$keys" ] || [ "$found" != "$keys" ]; then
      echo "verify-refusal-keys: $engine on $name: markers [$found] in the reason, reason_keys [$keys]" >&2
      status=1
    fi
  done
done <"$cases"
[ "$status" -eq 0 ] && echo "verify-refusal-keys: every engine's reason is built from its marked catalogue, key for key"
exit "$status"
