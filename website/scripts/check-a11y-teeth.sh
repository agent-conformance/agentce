#!/usr/bin/env bash
# Proves the axe gate has teeth by running mutated copies of it and its real CLI on a bounded directory.
# Run from website/ after `pnpm install` and `pnpm exec playwright install chromium`. Exits 0 only when
# every mutant is caught and every CLI expectation holds; exits 1 naming the first that is not.
set -u
cd "$(dirname "$0")/.."
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
fail() { echo "A11Y TEETH FAILED: $1" >&2; exit 1; }

mkcopy() { # <name>: a writable copy of the gate with the real fixtures and the installed deps
  mkdir -p "$work/$1/website/scripts" "$work/$1/website/tests"
  cp scripts/check-a11y.mjs "$work/$1/website/scripts/"
  cp -R tests/fixtures "$work/$1/website/tests/fixtures"
  cp package.json "$work/$1/website/"
  ln -s "$PWD/node_modules" "$work/$1/website/node_modules"
}

expect_selftest_fails() { # <copy> <what>
  (cd "$work/$1/website" && node scripts/check-a11y.mjs --self-test >/dev/null 2>&1) && fail "self-test passed with $2"
  return 0
}

mkcopy swapped-bad; cp tests/fixtures/accessible.html "$work/swapped-bad/website/tests/fixtures/inaccessible.html"
expect_selftest_fails swapped-bad "the inaccessible fixture replaced by the accessible one"
mkcopy swapped-good; cp tests/fixtures/inaccessible.html "$work/swapped-good/website/tests/fixtures/accessible.html"
expect_selftest_fails swapped-good "the accessible control replaced by the inaccessible one"
mkcopy narrowed; sed -i.bak "s/, 'wcag22aa'\]/]/" "$work/narrowed/website/scripts/check-a11y.mjs"
grep -q "^const WCAG_TAGS = .*wcag22aa" "$work/narrowed/website/scripts/check-a11y.mjs" && fail "could not narrow the tag set for the mutant"
expect_selftest_fails narrowed "the wcag22aa tag dropped"

# The real CLI, the way CI runs it, over a bounded directory (two pages, never the built site).
mkdir -p "$work/both" "$work/clean"
cp tests/fixtures/inaccessible.html "$work/both/bad.html"; cp tests/fixtures/accessible.html "$work/both/good.html"
cp tests/fixtures/accessible.html "$work/clean/good.html"
out=$(AGENTCE_SKIP_A11Y=0 node scripts/check-a11y.mjs --dir "$work/both" 2>&1) && fail "the enforce run passed a directory holding a bad page"
grep -q "2 pages × 2 themes = 4 axe scans" <<<"$out" || fail "the enforce run did not cover 2 pages in 2 themes"
AGENTCE_SKIP_A11Y=0 node scripts/check-a11y.mjs --report --dir "$work/both" >/dev/null 2>&1 || fail "--report did not exit 0 over a bad page"
out=$(AGENTCE_SKIP_A11Y=0 node scripts/check-a11y.mjs --dir "$work/clean" 2>&1) || fail "the enforce run failed a directory holding only a clean page"
grep -q "A11Y CHECK OK" <<<"$out" || fail "the clean run did not print A11Y CHECK OK"
echo "A11Y TEETH OK — swapped fixtures, a narrowed tag set and the real enforce CLI all behave as required."
