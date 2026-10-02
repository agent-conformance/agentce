#!/usr/bin/env bash
# Build gate helper for VG-BUYER-VIEW (18.18, Hill 8's buyer lens): a real `assess --for buyer` run's
# buyer view -- generated CAIQ/AI Controls Matrix questionnaire answers, a one-page summary, and how
# to check this report -- matches a committed golden and is never fabricated (contracts/P18-18.18.md
# C3).
#
# The fixture (verification/gates/fixtures/buyer_view/) is a dedicated four-control catalog loaded
# via --catalog-dir --allow-unverified-catalog, evaluated against one shared Decision event that
# carries both `agent` and `time` (unlike the auditor_view fixture's event, which carries neither):
#   BUY-01: caiq crosswalk `IAM-13.1`, conformant (the shape only requires prov:wasAssociatedWith,
#           which the event carries) -- its own evidence event's own id carries a hostile
#           `<script>alert(1)</script>` fragment, since only a conformant/non-conformant/partial
#           outcome ever carries evidence (assess.py:171-175).
#   BUY-02: caiq crosswalk `LOG-04.1<script>alert(2)</script>` (a second, distinct hostile fragment,
#           in the clause id itself), insufficient_evidence: its shape only requires prov:atTime
#           (satisfied), but its own minimum_evidence requires class enforcement_point, which the
#           shared event's self_report class does not satisfy.
#   BUY-03: eu-ai-act crosswalk only (no caiq/ai-controls-matrix entry), conformant -- must be absent
#           from buyer.json's answers ("maps to nothing in scope, not nothing").
#   BUY-04: no crosswalk entry at all, conformant -- also absent from answers, by a different route.
#
# `agentce assess` returns exit code 2 for this fixture: no non-conformant outcome and no --fail-on
# (assess only adds FINDINGS for those), but BUY-02 is severity: high and insufficient_evidence, which
# SPEC.md:1076 (item 18.30) requires to add INSUFFICIENT_EVIDENCE -- so the exit code is asserted
# explicitly.
#
# The golden (buyer_view_golden.json) is always a capture of the Python engine's own canonicalized
# `buyer.json`. Regenerate with: verification/gates/buyer_view.sh --write
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
fixture="$root/verification/gates/fixtures/buyer_view"
golden="$root/verification/gates/buyer_view_golden.json"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

run_assess() {
  local out="$1"
  set +e
  (cd "$root/engines/python" && env -u VIRTUAL_ENV uv run --frozen agentce assess \
    --bundle "$fixture/evidence" --profile "$fixture/applicability.yaml" \
    --domain "$fixture/domain.linkml.yaml" --catalog-dir "$fixture/catalog" \
    --allow-unverified-catalog --for buyer --out "$out" >/dev/null 2>&1)
  local code=$?
  set -e
  if [ "$code" -ne 2 ]; then
    echo "buyer-view: assess exited $code, expected 2 (BUY-02 is severity: high insufficient_evidence, SPEC.md:1076)" >&2
    return 1
  fi
  return 0
}

#: check (f): buyer.json's counts equal a fresh aggregate(assertions) call over the same run's own
#: assertions.json, never a stand-in report.json.
check_counts_match_assertions() {
  (cd "$root/engines/python" && env -u VIRTUAL_ENV uv run --frozen python3 - "$1" "$2" <<'PY'
import json, sys
from agentce.assertions import Assertion, aggregate
buyer = json.load(open(sys.argv[1], encoding="utf-8"))
assertions = [Assertion.from_json(a) for a in json.load(open(sys.argv[2], encoding="utf-8"))]
sys.exit(0 if buyer.get("counts") == aggregate(assertions) else 1)
PY
  )
}

#: checks (b)/(c)/(d)/(e): the shape and specific facts of a real run's buyer.json.
check_facts() {
  (cd "$root/engines/python" && env -u VIRTUAL_ENV uv run --frozen python3 - "$1" <<'PY'
import json, sys
buyer = json.load(open(sys.argv[1], encoding="utf-8"))
answers = buyer["answers"]
by_control = {(e["framework"], e["control"]): e for e in answers}

problems = []

buy01 = by_control.get(("caiq", "BUY-01"))
if buy01 is None or buy01["outcome"] != "conformant":
    problems.append("BUY-01: expected a caiq answer with outcome conformant")
elif not buy01["evidence"] or "<script>alert(1)</script>" not in buy01["evidence"][0]["ref"]:
    problems.append("BUY-01: expected a raw (unescaped-at-storage) evidence ref carrying the hostile fragment")

buy02 = by_control.get(("caiq", "BUY-02"))
if buy02 is None or buy02["outcome"] != "insufficient_evidence":
    problems.append("BUY-02: expected a caiq answer with outcome insufficient_evidence")
elif buy02["question"] != "LOG-04.1<script>alert(2)</script>":
    problems.append("BUY-02: expected the hostile clause id verbatim in question")
elif buy02["gap_step"] is None:
    problems.append("BUY-02: expected a non-null gap_step")

controls_present = {e["control"] for e in answers}
if "BUY-03" in controls_present:
    problems.append("BUY-03: must be absent from answers (maps only to eu-ai-act, out of buyer scope)")
if "BUY-04" in controls_present:
    problems.append("BUY-04: must be absent from answers (no crosswalk entry at all)")

if by_control.get(("ai-controls-matrix", "BUY-01")) is not None:
    problems.append("unexpected ai-controls-matrix answer for BUY-01")

print("\n".join(problems))
sys.exit(1 if problems else 0)
PY
  )
}

if [ "${1:-}" = "--write" ]; then
  run_assess "$work/python"
  if ! check_counts_match_assertions "$work/python/buyer.json" "$work/python/assertions.json"; then
    echo "buyer-view: the run's own buyer.json counts diverged from a fresh aggregate() over its assertions.json; refusing to write a bad golden" >&2
    exit 1
  fi
  cp "$work/python/buyer.json" "$golden"
  echo "buyer-view: wrote $golden from a live Python run"
  exit 0
fi

status=0
py_out="$work/python"
run_assess "$py_out" || status=1

for name in buyer.json buyer.md buyer.html; do
  if [ ! -f "$py_out/$name" ]; then
    echo "buyer-view: python did not write $name for --for buyer" >&2
    status=1
  fi
done
for stray in report.md report.html; do
  if [ -f "$py_out/$stray" ]; then
    echo "buyer-view: python wrote $stray for --for buyer, which PRESET_EMIT['buyer'] must not select" >&2
    status=1
  fi
done

if [ -f "$py_out/buyer.json" ]; then
  if ! cmp -s "$golden" "$py_out/buyer.json"; then
    echo "buyer-view: python buyer.json differs from the committed golden $golden" >&2
    status=1
  fi
  if [ -f "$py_out/assertions.json" ] && ! check_counts_match_assertions "$py_out/buyer.json" "$py_out/assertions.json"; then
    echo "buyer-view: buyer.json's counts diverged from a fresh aggregate() over the run's own assertions.json" >&2
    status=1
  fi
  facts_out="$(check_facts "$py_out/buyer.json")" || { echo "buyer-view: $facts_out" >&2; status=1; }
fi

# check (i): the "not a certification" heading names caiq/4.0.2 verbatim, next to the caiq section.
for f in "$py_out/buyer.md" "$py_out/buyer.html"; do
  if [ -f "$f" ] && ! grep -q 'Evidence against the CAIQ 4.0.2, not a certification.' "$f"; then
    echo "buyer-view: $f missing the caiq 'not a certification' heading verbatim" >&2
    status=1
  fi
done

# check (e): the ai-controls-matrix section shows the disclosed empty-framework text.
for f in "$py_out/buyer.md" "$py_out/buyer.html"; do
  if [ -f "$f" ] && ! grep -q 'no control in this run cites the AI Controls Matrix 1.1 yet' "$f"; then
    echo "buyer-view: $f missing the disclosed empty-ai-controls-matrix text" >&2
    status=1
  fi
done

# check (b)/(c): the hostile evidence ref and the hostile clause id render escaped, never raw.
for f in "$py_out/buyer.md" "$py_out/buyer.html"; do
  if [ -f "$f" ] && grep -qE '<script>alert\(1\)</script>|<script>alert\(2\)</script>' "$f"; then
    echo "buyer-view: $f rendered a hostile fixture fragment unescaped" >&2
    status=1
  fi
done
if [ -f "$py_out/buyer.html" ] && ! grep -q '&lt;script&gt;alert(1)&lt;/script&gt;' "$py_out/buyer.html"; then
  echo "buyer-view: buyer.html never shows BUY-01's evidence ref HTML-escaped -- the escaping check may be vacuous" >&2
  status=1
fi
if [ -f "$py_out/buyer.html" ] && ! grep -q '&lt;script&gt;alert(2)&lt;/script&gt;' "$py_out/buyer.html"; then
  echo "buyer-view: buyer.html never shows BUY-02's clause id HTML-escaped -- the escaping check may be vacuous" >&2
  status=1
fi

# check (c): BUY-02's "not enough evidence" template text, compared against the real catalogue entry.
if [ -f "$py_out/buyer.md" ]; then
  expected="$(cd "$root/engines/python" && env -u VIRTUAL_ENV uv run --frozen python3 -c "
from agentce import messages, i18n_format
cat = messages.catalogue()
print(i18n_format.format_message(cat['report.buyer_answer_insufficient_evidence'], control='BUY-02'))
")"
  if ! grep -qF "$expected" "$py_out/buyer.md"; then
    echo "buyer-view: buyer.md missing BUY-02's exact insufficient_evidence answer text" >&2
    status=1
  fi
fi

# check (h): agentce report --validate exits 0.
if ! (cd "$root/engines/python" && env -u VIRTUAL_ENV uv run --frozen agentce report --validate "$py_out" >/dev/null 2>&1); then
  echo "buyer-view: agentce report --validate refused the python engine's own output" >&2
  status=1
fi

# check (j): the unverified-clause label (SPEC §7.3) appears next to BUY-01/IAM-13.1 and
# BUY-02/LOG-04.1's row in both pages -- both fixture crosswalk entries carry
# verified_against_text: false (verifier round-1 F1: this label was computed but never rendered).
for f in "$py_out/buyer.md" "$py_out/buyer.html"; do
  if [ -f "$f" ]; then
    hits="$(grep -o 'clause reference unverified' "$f" | wc -l | tr -d ' ')"
    if [ "$hits" -lt 2 ]; then
      echo "buyer-view: $f shows the unverified-clause label $hits time(s), expected at least 2 (BUY-01 and BUY-02)" >&2
      status=1
    fi
  fi
done

# check (k): the "not enough evidence" gap step (BUY-02's own missing enforcement_point requirement,
# not just the fixed per-outcome sentence check (c) already covers) renders in both pages
# (verifier round-1 F2: this line could be deleted with the gate still green).
for f in "$py_out/buyer.md" "$py_out/buyer.html"; do
  if [ -f "$f" ] && ! grep -q 'Decision.*enforcement_point.*a request to platform or security' "$f"; then
    echo "buyer-view: $f missing BUY-02's specific gap-step text (Decision/enforcement_point)" >&2
    status=1
  fi
done

# check (l): the summary counts table renders this run's real counts (3 conformant, 1 insufficient
# evidence, the rest zero) -- the table itself was never checked before (verifier round-1 F2).
if [ -f "$py_out/buyer.md" ] && ! grep -qF '| 3 | 0 | 0 | 0 | 0 | 1 |' "$py_out/buyer.md"; then
  echo "buyer-view: buyer.md's summary table is missing or does not show this run's real counts" >&2
  status=1
fi
if [ -f "$py_out/buyer.html" ] && ! grep -qF '<td>3</td><td>0</td><td>0</td><td>0</td><td>0</td><td>1</td>' "$py_out/buyer.html"; then
  echo "buyer-view: buyer.html's summary table is missing or does not show this run's real counts" >&2
  status=1
fi

# check (m): BUY-01's evidence ref renders in buyer.md too, not only buyer.html (verifier round-1 F2).
if [ -f "$py_out/buyer.md" ] && ! grep -q 'Evidence: `agentce:event/buyer-view-fixture-dec1' "$py_out/buyer.md"; then
  echo "buyer-view: buyer.md is missing BUY-01's evidence ref line" >&2
  status=1
fi

# check (n): a packaged run (--package-for-sharing) exercises the packaged "how to check this report"
# line (report.buyer_how_to_check, N6) -- the unpackaged branch above never reaches it, so it had no
# coverage at all (verifier round-1 F2).
pkg_out="$work/packaged"
set +e
(cd "$root/engines/python" && env -u VIRTUAL_ENV uv run --frozen agentce assess \
  --bundle "$fixture/evidence" --profile "$fixture/applicability.yaml" \
  --domain "$fixture/domain.linkml.yaml" --catalog-dir "$fixture/catalog" \
  --allow-unverified-catalog --for buyer --package-for-sharing --out "$pkg_out" >/dev/null 2>&1)
pkg_code=$?
set -e
if [ "$pkg_code" -ne 2 ]; then
  echo "buyer-view: packaged assess exited $pkg_code, expected 2 (BUY-02 is severity: high insufficient_evidence, SPEC.md:1076)" >&2
  status=1
elif [ -f "$pkg_out/buyer.md" ] && ! grep -q 'agentce verify --report' "$pkg_out/buyer.md"; then
  echo "buyer-view: packaged buyer.md missing the packaged how-to-check line (agentce verify --report ...)" >&2
  status=1
fi

[ "$status" -eq 0 ] && echo "buyer-view: python's real assess --for buyer run matches the golden, every answer's facts (conformant/insufficient_evidence, hostile escaping, out-of-scope absence, unverified-clause label, gap step, counts table, packaged how-to-check) hold, and report --validate accepts the output"
exit "$status"
