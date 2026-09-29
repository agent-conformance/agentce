#!/usr/bin/env bash
# Build gate helper for VG-AUDIENCE-PRESETS: `agentce assess --for <preset>` resolves to the exact,
# documented `--emit` set for each of the five audience presets (engineering, compliance, security,
# ci, share), and the `CI` environment variable extends -- never replaces -- the legacy default when
# neither `--for` nor `--emit` is given (contracts/P18-18.7.md).
#
# Seven real CLI invocations over one dedicated fixture catalog (AUD-01, verification/gates/fixtures/
# audience_presets/ -- one control the fixture's evidence bundle never satisfies, so every run reaches
# a non-passing outcome and the `engineering` preset's skill/<subject>/findings/ is never empty):
#   1-5. `assess --for <preset>` for each of the five presets -> the written file set under `--out`
#        equals exactly the hand-written expected table below (never imported from the package under
#        test, so editing PRESET_EMIT cannot also edit what this gate expects).
#   6.   `env CI=true assess` with no `--for`/`--emit` -> the legacy default's files PLUS
#        report.junit.xml (additive, not the minimal `ci` preset's files).
#   7.   `env -u CI assess` with no `--for`/`--emit` -> exactly the legacy default (no junit) -- the
#        control run that an "always extend" regression would fail even though it might pass 6.
# `agentce report --validate` is run over each of the five presets (exit 0 required); `engineering`'s
# remediation-package.json is additionally validated against the vendored schema directly (that
# schema already exists but `validate_report` never applies it to this file).
#
# PYTHONDONTWRITEBYTECODE=1 on every invocation, per VG-DIFF/VG-GOLDEN-LOOP's own bytecode-cache
# lesson (18.6, CI run 36405837177): never leave a .pyc a sibling gate targeting the same fault file
# could serve stale.
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
fixture="$root/verification/gates/fixtures/audience_presets"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

run_assess() {
  # $1: "unset" to run with CI unset, else the value to export as CI; $2: output dir name under
  # $work; remaining args are extra CLI flags (e.g. --for <preset>).
  local ci="$1" out="$2"
  shift 2
  # BSD `env` (macOS) requires every `-u NAME` before any `NAME=value` assignment.
  local -a env_args=(-u VIRTUAL_ENV)
  if [ "$ci" = "unset" ]; then
    env_args+=(-u CI)
  fi
  env_args+=(PYTHONDONTWRITEBYTECODE=1)
  if [ "$ci" != "unset" ]; then
    env_args+=("CI=$ci")
  fi
  (cd "$root/engines/python" && env "${env_args[@]}" uv run --frozen agentce assess \
    --bundle "$fixture/evidence" --profile "$fixture/applicability.yaml" \
    --domain "$fixture/domain.linkml.yaml" --catalog-dir "$fixture/catalog" \
    --allow-unverified-catalog --out "$work/$out" "$@" >/dev/null)
}

run_validate() {
  (cd "$root/engines/python" && env -u VIRTUAL_ENV PYTHONDONTWRITEBYTECODE=1 uv run --frozen agentce report --validate "$work/$1" >/dev/null)
}

for preset in engineering compliance security ci share; do
  run_assess unset "$preset" --for "$preset"
  run_validate "$preset"
done

run_assess true ci-auto
run_assess unset ci-unset

(cd "$root/engines/python" && env -u VIRTUAL_ENV PYTHONDONTWRITEBYTECODE=1 uv run --frozen python - \
  "$work" "$root/spec/report/remediation-package.schema.json" <<'PY'
import json
import pathlib
import sys

import jsonschema

work = pathlib.Path(sys.argv[1])
schema = json.loads(pathlib.Path(sys.argv[2]).read_text(encoding="utf-8"))

# The one subject the fixture's applicability profile declares, path-sanitised the same way every
# other output path in this repository is (colons and slashes to underscores).
SUBJECT = "spiffe___corp_agents_audience-presets-fixture"

# Hand-written, independent of `agentce.commands.PRESET_EMIT`/`ASSESS_DEFAULT_EMIT`: editing either
# in the package under test must never also edit what this gate expects (contracts/P18-18.7.md D2).
ALWAYS = {
    "assertions.json",
    "activity.json",
    "blind-spots.json",
    "manifest.json",
    "claim.json",
    "quarantine.jsonl",
    "integrity.jsonl",
    "graph.sqlite",
    "coverage.json",
    "applicability.jsonl",
    "packaging.json",
}
MD_HTML = {"report.md", "report.html"}
SKILL = {
    f"skill/{SUBJECT}/SKILL.md",
    f"skill/{SUBJECT}/REVERIFY.md",
    f"skill/{SUBJECT}/remediation-package.json",
    f"skill/{SUBJECT}/findings/AUD-01--1.md",
}
PACK = {f"packs/{SUBJECT}/pack.json"}

LEGACY_DEFAULT = ALWAYS | MD_HTML | SKILL | PACK | {"oscal-ar.json", "results.sarif"}

EXPECTED = {
    "engineering": ALWAYS
    | MD_HTML
    | SKILL
    | {
        f"remediation/{SUBJECT}/remediation-package.json",
        f"remediation/{SUBJECT}/remediation.md",
    },
    "compliance": ALWAYS
    | PACK
    | {"oscal-ar.json", "oscal-ar.xml", "public-statement.md", "report.csv"},
    "security": ALWAYS | MD_HTML | {"results.sarif", "security.md", "security.html", "security.json"},
    "ci": ALWAYS | {"results.sarif", "report.junit.xml"},
    "share": ALWAYS | MD_HTML | PACK | {"report.pdf", "public-statement.md"},
    # The CI-detection control pair (D1/N7): additive over the legacy default, never the minimal
    # `ci` preset's files.
    "ci-auto": LEGACY_DEFAULT | {"report.junit.xml"},
    "ci-unset": LEGACY_DEFAULT,
}

problems: list[str] = []
for name, expected in EXPECTED.items():
    out_dir = work / name
    actual = {str(p.relative_to(out_dir)) for p in out_dir.rglob("*") if p.is_file()}
    if actual != expected:
        problems.append(
            f"{name}: missing {sorted(expected - actual)}, extra {sorted(actual - expected)}"
        )

remediation_package = work / "engineering" / "remediation" / SUBJECT / "remediation-package.json"
try:
    jsonschema.validate(json.loads(remediation_package.read_text(encoding="utf-8")), schema)
except jsonschema.ValidationError as exc:
    problems.append(f"remediation-package.json: {exc.message}")

if problems:
    for problem in problems:
        print(f"audience-presets: FAIL: {problem}", file=sys.stderr)
    sys.exit(1)

print(
    "audience-presets: all 5 presets and the CI-detection control pair match their expected file "
    "sets; engineering's remediation-package.json validates against its schema"
)
PY
)
