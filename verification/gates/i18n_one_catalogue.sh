#!/usr/bin/env bash
# Build gate helper for VG-I18N-ONE-CATALOGUE (18.35): TypeScript and Java load the vendored
# spec/i18n/ message catalogue instead of hand-copied dictionaries (restores item 14.1's one-catalogue
# invariant). Three layers: a direct cmp of spec/i18n/ against both vendored copies, each engine's own
# sync test (vendored bytes byte-identical to spec/i18n/) and catalogue unit test (the loaded report
# catalogue deep-equals a live read of the spec file -- catches a reverted loader even if it still
# hard-codes a few correct-looking spot values, which the sync test alone cannot); then a real-CLI leg
# reusing VG-PROJECT-VIEW's own fixture and multi_args (the same real path a user hits the bug on),
# reading the delegate agent's own report.md and asserting the exact line under its "Not yet declared
# in your profile" heading -- not a substring-anywhere grep for "Agents: ...", which is already true at
# base in every engine via an unrelated line (the activity row renders the same key at a different,
# unaffected position).
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
fixture="$root/verification/gates/fixtures/project_view"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

status=0

echo "i18n-one-catalogue: direct cmp of spec/i18n against both vendored copies"
for f in "$root"/spec/i18n/messages.*.json; do
  b="$(basename "$f")"
  if ! cmp -s "$f" "$root/engines/typescript/data/i18n/$b"; then
    echo "i18n-one-catalogue: engines/typescript/data/i18n/$b differs from $f" >&2
    status=1
  fi
  if ! cmp -s "$f" "$root/engines/java/src/main/resources/i18n/$b"; then
    echo "i18n-one-catalogue: engines/java/src/main/resources/i18n/$b differs from $f" >&2
    status=1
  fi
done

echo "i18n-one-catalogue: python sync test"
if ! (cd "$root/engines/python" && env -u VIRTUAL_ENV uv run --frozen pytest -q -o addopts= -p no:cacheprovider \
  tests/test_bundled_data.py -k i18n); then
  echo "i18n-one-catalogue: python's vendored i18n catalogue is out of sync" >&2
  status=1
fi

echo "i18n-one-catalogue: typescript sync and catalogue unit tests"
if ! (cd "$root/engines/typescript" && node --import tsx --test src/bundledData.test.ts src/messages.test.ts); then
  echo "i18n-one-catalogue: typescript's vendored catalogue or loader did not match the spec" >&2
  status=1
fi

echo "i18n-one-catalogue: java build"
(cd "$root/engines/java" && ./gradlew --no-daemon --quiet installDist)

echo "i18n-one-catalogue: java sync and catalogue unit tests"
if ! (cd "$root/engines/java" && ./gradlew --no-daemon --quiet test \
  --tests org.agentce.BundledDataTest --tests org.agentce.MessagesTest); then
  echo "i18n-one-catalogue: java's vendored catalogue or loader did not match the spec" >&2
  status=1
fi

# Real-CLI leg: the project_view fixture, the exact multi_args project_view_engines.sh already runs.
multi_args=(assess --bundle "$fixture/evidence" --profile "$fixture/applicability.yaml" \
  --domain "$fixture/domain.linkml.yaml" --catalog-dir "$fixture/catalog" --for risk-lead \
  --allow-unverified-catalog)

check_delegate_line() {
  python3 - "$1" <<'PY'
import glob
import sys

out_dir = sys.argv[1]
expected = "- Agents: spiffe://corp/agents/project-view-fixture-delegate"
found = False
for path in glob.glob(f"{out_dir}/agents/*/report.md"):
    with open(path, encoding="utf-8") as f:
        lines = [line.rstrip("\n") for line in f]
    for i, line in enumerate(lines):
        if line.strip() == "### Not yet declared in your profile":
            rest = [l for l in lines[i + 1 :] if l.strip()]
            if rest and rest[0] == expected:
                found = True
            break
sys.exit(0 if found else 1)
PY
}

echo "i18n-one-catalogue: real CLI (project_view fixture), all three engines"
set +e
(cd "$root/engines/python" && env -u VIRTUAL_ENV uv run --frozen agentce "${multi_args[@]}" --out "$work/python" >"$work/python.log" 2>&1)
py_code=$?
(cd "$root/engines/typescript" && pnpm --silent agentce "${multi_args[@]}" --out "$work/typescript" >"$work/typescript.log" 2>&1)
ts_code=$?
(cd "$root/engines/java" && ./build/install/agentce/bin/agentce "${multi_args[@]}" --out "$work/java" >"$work/java.log" 2>&1)
java_code=$?
set -e

# PROJ-01 (severity: high) is insufficient_evidence by fixture design (matching VG-PROJECT-VIEW's own
# multi-subject run), so every engine's run above exits 2.
for engine_code in "python $py_code" "typescript $ts_code" "java $java_code"; do
  engine="${engine_code%% *}"
  code="${engine_code##* }"
  if [ "$code" -ne 2 ]; then
    echo "i18n-one-catalogue: $engine's real CLI exited $code, expected 2 (severity: high insufficient_evidence)" >&2
    status=1
  fi
done

for engine in python typescript java; do
  if ! check_delegate_line "$work/$engine"; then
    echo "i18n-one-catalogue: $engine's delegate agent report.md does not read exactly '$(printf '%s' "- Agents: spiffe://corp/agents/project-view-fixture-delegate")' under 'Not yet declared in your profile'" >&2
    status=1
  fi
done

[ "$status" -eq 0 ] && echo "i18n-one-catalogue: the vendored catalogue in both engines matches spec/i18n/, each engine's sync and catalogue-load unit tests pass against a live read of the spec, and the real CLI's delegate-agent report correctly names the undeclared agents label"
exit "$status"
