#!/usr/bin/env bash
# Build gate helper for VG-CATALOG-SHAPE-SAFETY (18.34): catalog loading in every engine refuses a
# shape that carries sh:sparql or sh:js. Each engine's shape parser only ever reads the specific SHACL
# predicates it recognises (`graph.value`/`store.getQuads`/`Rdf.Store.predicates`); an unrecognised
# predicate like sh:sparql otherwise parses as an ordinary, incomplete shape and is silently accepted,
# letting a catalog carry logic no engine can evaluate identically. Python additionally covers this at
# `agentce catalog lint` time; TypeScript and Java have no `catalog lint` command (confirmed: no `lint`
# entry in either CLI's dispatch), so their scope here is catalog *loading* only, matching the item's
# real per-engine surface.
#
# Two layers: each engine's own unit tests (internals: parseShapesTtl/loadCatalog/Catalog.load
# directly), covering the fixed sparql-before-js priority order a shape carrying BOTH predicates must
# resolve to (matching spec/rules/psp_check.py's own PRIORITY_DENY -- contract-critic round 1 found
# Python's first cut nondeterministic here, disagreeing with TypeScript/Java under PYTHONHASHSEED),
# an aliased SHACL prefix, a bare IRI, a predicate nested inside a property shape, a Turtle Unicode
# escape inside the IRI (round-2 critic B1': Java's hand-written Turtle lexer did not decode
# backslash-u/backslash-U escapes in an IRIREF at all, so `<...shacl#sparql>` -- the same IRI as
# `sh:sparql` once decoded -- loaded clean in Java while Python and TypeScript correctly refused it;
# fixed in `Rdf.java`'s `scanIriRef`), and the no-false-positive cases (a comment or a string literal
# that merely mentions `sh:sparql`/`sh:js` must still load); then one real-CLI check per engine
# (critic round 1 B2: at least one check must reach the behaviour the way a user does, and round 2's
# B2' found the first cut of this checked only the exit code) -- `agentce catalog lint`/`agentce
# assess --catalog-dir ... --allow-unverified-catalog` against a throwaway mutated copy of the
# quickstart's own named catalog (eu-ai-act), asserting both the exit code AND the message key in the
# real output, on both the mutated catalog and an unmutated clean-baseline run (expect exit 0, no
# false positive on real shapes). Built once below and reused by all three engines so the same bytes
# are what every engine refuses.
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
quick="$root/corpus/quickstart"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

status=0

echo "catalog-shape-safety: python unit tests"
# The whole file, not a `-k` keyword filter: round-2 critic (B3') found the prior filter,
# "forbidden_shape_predicate", missed several of this item's own negative tests by name (the
# both-predicates, aliased-prefix/bare-IRI and literal/comment-false-positive cases), so a fault
# removing any of them still passed the gate. One dedicated file, ~13s.
if ! (cd "$root/engines/python" && env -u VIRTUAL_ENV uv run --frozen pytest -q -o addopts= -p no:cacheprovider \
  tests/test_catalog.py); then
  echo "catalog-shape-safety: python's lint/load did not refuse sh:sparql or sh:js" >&2
  status=1
fi

echo "catalog-shape-safety: typescript unit tests"
if ! (cd "$root/engines/typescript" && node --import tsx --test src/psp.test.ts); then
  echo "catalog-shape-safety: typescript's loadCatalog did not refuse sh:sparql or sh:js" >&2
  status=1
fi

echo "catalog-shape-safety: java unit tests"
# The whole class, not a `--tests` name list: round-2 critic (B3') found the prior list left out
# `loadRefusesAShapeUsingShJavascript` and every later negative test by name.
if ! (cd "$root/engines/java" && ./gradlew --quiet --no-daemon test \
  --tests "org.agentce.CatalogTest"); then
  echo "catalog-shape-safety: java's Catalog.load did not refuse sh:sparql or sh:js" >&2
  status=1
fi

# A throwaway mutated copy of eu-ai-act (the catalog the quickstart profile names), shared by every
# engine's real-CLI check below: one real, user-reachable shape carrying sh:sparql.
mutated="$work/mutated-catalog"
cp -r "$root/spec/catalogs/base/eu-ai-act" "$mutated"
python3 - "$mutated/shapes/DAT-01.ttl" <<'PY'
import sys
path = sys.argv[1]
text = open(path, encoding="utf-8").read()
needle = 'sh:name "S1" ] .'
assert text.count(needle) == 1, "fixture shape changed; update this gate's mutation"
open(path, "w", encoding="utf-8").write(text.replace(needle, 'sh:name "S1" ] ; sh:sparql [] .'))
PY

# The quickstart profile with its own named `catalogs:` list removed, so `--catalog-dir <dir>` is the
# only catalog each real assess run evaluates. Shared by both the mutated run (below) and the clean
# baseline run (the unmutated eu-ai-act catalog), so a false positive on real, unmutated shapes would
# also be caught here, not just the unit tests' own `lint_catalog(_BASE) == []` assertion.
clean="$root/spec/catalogs/base/eu-ai-act"
awk '/^catalogs:$/ {skip=1; next} skip && /^  - / {next} {skip=0; print}' \
  "$quick/applicability.yaml" > "$work/no-catalog.yaml"
assess_args_for() {
  local dir="$1"
  echo assess --bundle "$quick/evidence" --profile "$work/no-catalog.yaml" \
    --domain "$quick/domain.linkml.yaml" --catalog-dir "$dir" --allow-unverified-catalog
}

check_exit_code() {
  local engine="$1" code="$2" expected="$3"
  if [ "$code" -ne "$expected" ]; then
    echo "catalog-shape-safety: $engine's real CLI exited $code, expected $expected" >&2
    status=1
  fi
}

check_key_in() {
  local engine="$1" file="$2" key="$3"
  if ! grep -q "$key" "$file"; then
    echo "catalog-shape-safety: $engine's real CLI output did not name $key" >&2
    status=1
  fi
}

echo "catalog-shape-safety: python real CLI (catalog lint + assess), mutated and clean"
set +e
lint_out="$(cd "$root/engines/python" && env -u VIRTUAL_ENV uv run --frozen agentce catalog lint "$mutated" --json 2>&1)"
lint_code=$?
clean_lint_out="$(cd "$root/engines/python" && env -u VIRTUAL_ENV uv run --frozen agentce catalog lint "$clean" --json 2>&1)"
clean_lint_code=$?
(cd "$root/engines/python" && env -u VIRTUAL_ENV uv run --frozen agentce $(assess_args_for "$mutated") --out "$work/py-assess" >"$work/py-assess.log" 2>&1)
py_assess_code=$?
(cd "$root/engines/python" && env -u VIRTUAL_ENV uv run --frozen agentce $(assess_args_for "$clean") --out "$work/py-assess-clean" >"$work/py-assess-clean.log" 2>&1)
py_assess_clean_code=$?
set -e
check_exit_code "python catalog lint (mutated)" "$lint_code" 1
check_exit_code "python catalog lint (clean)" "$clean_lint_code" 0
check_exit_code "python assess (mutated)" "$py_assess_code" 3
check_exit_code "python assess (clean)" "$py_assess_clean_code" 0
check_key_in "python catalog lint" <(echo "$lint_out") "catalog.shape.sparql_forbidden"
check_key_in "python assess" "$work/py-assess.log" "catalog.shape.sparql_forbidden"

echo "catalog-shape-safety: typescript real CLI (assess), mutated and clean"
set +e
(cd "$root/engines/typescript" && pnpm --silent agentce $(assess_args_for "$mutated") --out "$work/ts-assess" >"$work/ts-assess.log" 2>&1)
ts_assess_code=$?
(cd "$root/engines/typescript" && pnpm --silent agentce $(assess_args_for "$clean") --out "$work/ts-assess-clean" >"$work/ts-assess-clean.log" 2>&1)
ts_assess_clean_code=$?
set -e
check_exit_code "typescript assess (mutated)" "$ts_assess_code" 3
check_exit_code "typescript assess (clean)" "$ts_assess_clean_code" 0
check_key_in "typescript assess" "$work/ts-assess.log" "catalog.shape.sparql_forbidden"

echo "catalog-shape-safety: java real CLI (assess), mutated and clean"
(cd "$root/engines/java" && ./gradlew --no-daemon --quiet installDist)
set +e
"$root/engines/java/build/install/agentce/bin/agentce" $(assess_args_for "$mutated") --out "$work/java-assess" >"$work/java-assess.log" 2>&1
java_assess_code=$?
"$root/engines/java/build/install/agentce/bin/agentce" $(assess_args_for "$clean") --out "$work/java-assess-clean" >"$work/java-assess-clean.log" 2>&1
java_assess_clean_code=$?
set -e
check_exit_code "java assess (mutated)" "$java_assess_code" 3
check_exit_code "java assess (clean)" "$java_assess_clean_code" 0
check_key_in "java assess" "$work/java-assess.log" "catalog.shape.sparql_forbidden"

[ "$status" -eq 0 ] && echo "catalog-shape-safety: all three engines refuse sh:sparql and sh:js at catalog load (unit tests) and over a real CLI run (Python's catalog lint + all three engines' assess), by key and exit code, on both the mutated and the clean catalog"
exit "$status"
