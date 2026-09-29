#!/usr/bin/env bash
# Build gate helper for VG-SECURITY-VIEW (18.16, Hill 8's security lens): a real `assess --for
# security` run's security view -- tool access, actions by effect class, enforcement-point evidence,
# drift, and standards citations restricted to owasp-asi-2026/mitre-atlas/owasp-acs -- matches a
# committed golden and never diverges from the same run's own activity.json, plus a direct
# cross-engine diff of the pure `compute_security_view` function itself over a shared
# activity/assertions payload (the same principle `catalog_digest_check.py` uses for the catalog
# digest: comparing engines to each other, not each to itself, is what makes the parity claim
# non-vacuous).
#
# `--for security` writing security.json/.md/.html through a real `assess` run is Python-only today:
# TypeScript and Java have `compute_security_view`/`SecurityView.compute` (18.16 C4) but no
# `--for`/multi-format `write_report` at all yet (a pre-existing, disclosed scope gap -- see
# `securityView.ts`'s and `SecurityView.java`'s own "not part of the public assess command surface"
# comment, TRADEOFFS.md row 1 for 18.16). So checks (a)-(e) below run against the Python engine's own
# real output only; check (f), the three-engine parity claim, runs against the pure computation via
# each engine's test-only `security-view` CLI verb instead -- the part of this item that genuinely
# is ported three ways.
#
# The fixture (verification/gates/fixtures/security_view/) declares one subject and no
# `declared_tools`/`declared_models`, with an EMPTY `catalogs` list -- not an explicit
# `--catalog`/`--catalog-dir` -- which is what makes the run evaluate the real, vendored baseline
# catalog (`agentce.bundled.DEFAULT_LENS`), the same mechanism `baseline_lens_engines.sh` already
# proves identical across all three engines. The baseline catalog is where 18.16's C1-C3 added the
# mitre-atlas/owasp-acs crosswalk entries this gate's citations come from. Every control in the
# loaded catalog is evaluated for every subject regardless of outcome
# (`agentce.assess._assert_control` attaches a control's own `crosswalk` to its assertion even when
# the outcome is `not_applicable`/`not_assessed`/`insufficient_evidence`), so the fixture's three
# events -- a hostile-named (`<script>alert(1)`), undeclared, `effect_class: irreversible` `ToolCall`;
# a denied `AuthzCheck`; an undeclared `ModelCall` -- plus one `Decision` (so at least one
# (control, subject) pair reaches a judged outcome rather than tripping `input.nothing_evaluated`)
# are enough to exercise every fact this view reads from `activity.py`'s own rollup, real citations
# included. The run's own verdict is genuinely non-conformant (ROB-02 fails its shape's `prov:used`
# property) -- exit code 1 (`ExitCode.FINDINGS`), not 0, which this script checks for explicitly
# rather than letting a bare `set -e` treat a real, expected finding as a script bug.
#
# The golden (security_view_golden.json) is always a capture of the Python engine's own
# canonicalized `security.json`. `security.md`/`.html` are not claimed byte-identical against
# anything else (matching `report.md`'s own precedent) -- only their section order is checked.
# Regenerate with:
#   verification/gates/security_view.sh --write
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
fixture="$root/verification/gates/fixtures/security_view"
golden="$root/verification/gates/security_view_golden.json"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

args=(assess --bundle "$fixture/evidence" --profile "$fixture/applicability.yaml" \
  --domain "$fixture/domain.linkml.yaml" --for security)

#: The three external-standard frameworks the security view cites; a run that leaks a citation from
#: any other framework (eu-ai-act, iso-42001, nist-ai-rmf, aiuc-1), or is missing one of the three,
#: fails check (b) below.
check_citation_frameworks() {
  python3 - "$1" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as f:
    data = json.load(f)
frameworks = {c["framework"] for c in data.get("standards_citations", [])}
cited = {"owasp-asi-2026", "mitre-atlas", "owasp-acs"}
sys.exit(0 if frameworks == cited else 1)
PY
}

#: check (c): security.json's activity-derived facts never diverge from the run's own activity.json.
check_matches_activity() {
  python3 - "$1" "$2" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as f:
    security = json.load(f)
with open(sys.argv[2], encoding="utf-8") as f:
    activity = json.load(f)
ok = (
    security.get("actions_by_effect_class") == activity.get("actions_by_effect_class")
    and security.get("drift", {}).get("tools") == activity.get("undeclared", {}).get("tools")
    and security.get("drift", {}).get("models") == activity.get("undeclared", {}).get("models")
)
sys.exit(0 if ok else 1)
PY
}

# `agentce assess` returns ExitCode.FINDINGS (1) for this fixture's own real, non-conformant verdict
# (ROB-02) -- a genuine finding, not a script failure, so the exit code is captured explicitly
# instead of letting `set -e` treat it as one.
run_assess_python() {
  local out="$1"
  set +e
  (cd "$root/engines/python" && env -u VIRTUAL_ENV uv run --frozen agentce "${args[@]}" --out "$out" >/dev/null 2>&1)
  local code=$?
  set -e
  if [ "$code" -ne 1 ]; then
    echo "security-view: python assess exited $code, expected 1 (ExitCode.FINDINGS -- this fixture's own real non-conformant verdict)" >&2
    return 1
  fi
  return 0
}

if [ "${1:-}" = "--write" ]; then
  run_assess_python "$work/python"
  if ! check_citation_frameworks "$work/python/security.json"; then
    echo "security-view: the Python engine's own citations were not exactly owasp-asi-2026/mitre-atlas/owasp-acs; refusing to write a bad golden" >&2
    exit 1
  fi
  if ! check_matches_activity "$work/python/security.json" "$work/python/activity.json"; then
    echo "security-view: the Python engine's own security.json diverged from its own activity.json; refusing to write a bad golden" >&2
    exit 1
  fi
  cp "$work/python/security.json" "$golden"
  # activity_and_assertions.json: the shared cross-engine diff input for compute_security_view (C4),
  # built once from the Python engine's own real run.
  python3 - "$work/python/activity.json" "$work/python/assertions.json" "$fixture/activity_and_assertions.json" <<'PY'
import json, sys
activity = json.load(open(sys.argv[1], encoding="utf-8"))
assertions = json.load(open(sys.argv[2], encoding="utf-8"))
with open(sys.argv[3], "w", encoding="utf-8") as f:
    json.dump({"activity": activity, "assertions": assertions}, f)
PY
  echo "security-view: wrote $golden and fixtures/security_view/activity_and_assertions.json from a live Python run"
  exit 0
fi

if ! check_citation_frameworks "$golden"; then
  echo "security-view: the committed golden's citations are not exactly owasp-asi-2026/mitre-atlas/owasp-acs" >&2
  exit 1
fi

status=0

# checks (a)-(e): the Python engine's own real `assess --for security` run.
py_out="$work/python"
run_assess_python "$py_out" || status=1
for name in security.json security.md security.html; do
  if [ ! -f "$py_out/$name" ]; then
    echo "security-view: python did not write $name for --for security" >&2
    status=1
  fi
done
if [ -f "$py_out/security.json" ]; then
  if ! cmp -s "$golden" "$py_out/security.json"; then
    echo "security-view: python security.json differs from the committed golden $golden" >&2
    status=1
  fi
  if ! check_citation_frameworks "$py_out/security.json"; then
    echo "security-view: python's citations were not exactly owasp-asi-2026/mitre-atlas/owasp-acs" >&2
    status=1
  fi
  if ! check_matches_activity "$py_out/security.json" "$py_out/activity.json"; then
    echo "security-view: python's security.json diverged from its own activity.json" >&2
    status=1
  fi
fi
if [ -f "$py_out/security.md" ]; then
  actual_headings="$(grep -o '^## .*' "$py_out/security.md" || true)"
  expected_headings="$(printf '## Tool access\n## Actions by effect class\n## Enforcement-point evidence\n## Drift\n## Standards citations')"
  if [ "$actual_headings" != "$expected_headings" ]; then
    echo "security-view: python's security.md section order is wrong (got: $actual_headings)" >&2
    status=1
  fi
fi
# check (e): `report --validate` (schema validation) exits 0.
if ! (cd "$root/engines/python" && env -u VIRTUAL_ENV uv run --frozen agentce report --validate "$py_out" >/dev/null 2>&1); then
  echo "security-view: agentce report --validate refused the python engine's own output" >&2
  status=1
fi

# check (f): all three engines' compute_security_view on the SAME activity/assertions payload,
# fed through each engine's own test-only `security-view` CLI verb (C4), print byte-identical JSON
# -- normalized through a shared `json.dumps` pass first, since each engine's own JSON serializer
# formats whitespace differently even when the key order and values genuinely agree (the fact this
# gate exists to prove).
combined="$fixture/activity_and_assertions.json"
py_raw="$work/py_security_view.json"
ts_raw="$work/ts_security_view.json"
java_raw="$work/java_security_view.json"
(cd "$root/engines/python" && env -u VIRTUAL_ENV uv run --frozen python3 -c "
import json, sys
from agentce.assertions import Assertion
from agentce.security_view import compute_security_view
data = json.load(open(sys.argv[1], encoding='utf-8'))
assertions = [Assertion.from_json(a) for a in data['assertions']]
print(json.dumps(compute_security_view(data['activity'], assertions)))
" "$combined" > "$py_raw")
(cd "$root/engines/typescript" && pnpm --silent agentce security-view "$combined" > "$ts_raw")
(cd "$root/engines/java" && ./gradlew --no-daemon --quiet installDist)
"$root/engines/java/build/install/agentce/bin/agentce" security-view "$combined" > "$java_raw"

normalize() { python3 -c "import json,sys; print(json.dumps(json.load(open(sys.argv[1])), separators=(',',':')))" "$1"; }
py_norm="$(normalize "$py_raw")"
ts_norm="$(normalize "$ts_raw")"
java_norm="$(normalize "$java_raw")"
if [ "$py_norm" != "$ts_norm" ]; then
  echo "security-view: typescript's compute_security_view differs from python's over the shared fixture" >&2
  status=1
fi
if [ "$py_norm" != "$java_norm" ]; then
  echo "security-view: java's compute_security_view differs from python's over the shared fixture" >&2
  status=1
fi

[ "$status" -eq 0 ] && echo "security-view: python's real assess run matches the golden and its own activity.json, citations stay exactly inside the three named frameworks, and all three engines' compute_security_view agree byte-for-byte over the shared fixture"
exit "$status"
