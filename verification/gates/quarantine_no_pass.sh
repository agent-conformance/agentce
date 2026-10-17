#!/usr/bin/env bash
# Build gate helper for VG-QUARANTINE-NO-PASS (18.119): quarantine never gives a better outcome. A record ingest refuses
# for where it sits (time_order, a duplicate_id that is not an identical redelivery, class_mismatch, unknown_source) is
# judged with the subject's held records, and a conformant or not_applicable outcome it would change reads
# insufficient_evidence naming it; a record ingest cannot read (schema_invalid, oversize) does the same to every such
# outcome of its subject, or of every subject when it names none. Each case in fixtures/quarantine_no_pass/cases.json
# names the fixture folder whose profile and domain it is assessed under, its subject and its control; a string event
# is written as one raw line. The real Python, TypeScript and Java CLIs assess it, and the control must give the
# expected outcome and failing events in every engine, with the expected quarantined record count and exit code, and an
# insufficient_evidence case's evidence must name the quarantined events it lists.
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
label="quarantine-no-pass"
fixtures="$root/verification/gates/fixtures"
cases="$fixtures/quarantine_no_pass/cases.json"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

(cd "$root/engines/python" && PYTHONPATH="$root/verification/gates" env -u VIRTUAL_ENV uv run --frozen python - "$cases" "$work") <<'PY'
import hashlib
import json
import sys
from pathlib import Path

from fixture_integrity import add_coverage_counts, chain_streams

cases = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))["cases"]
for name, case in cases.items():
    bundle = Path(sys.argv[2]) / name / "bundle"
    (bundle / "events").mkdir(parents=True)
    # Declared classes, so each record keeps the class its stream has (SPEC §6.4).
    sources = [
        {"id": f"urn:src:{cls}", "class": cls}
        for cls in ("enforcement_point", "independent_system", "self_report")
    ]
    events, streams = chain_streams(case["events"], {"agentce_bundle_version": 1, "sources": sources})
    text = "".join((e if isinstance(e, str) else json.dumps(e, sort_keys=True)) + "\n" for e in events)
    (bundle / "events" / "subject.jsonl").write_text(text, encoding="utf-8")
    manifest = {
        "agentce_bundle_version": 1,
        "domain": "quarantine-no-pass-fixture",
        "files": [
            {
                "path": "events/subject.jsonl",
                "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            }
        ],
        "sources": sources,
        "streams": streams,
    }
    add_coverage_counts(bundle, events, Path(sys.argv[1]).parents[1] / case["fixture"] / "applicability.yaml", manifest)
    (bundle / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    expected = case["expected"]
    failing = ",".join(sorted(expected["failing"]))
    (bundle.parent / "control").write_text(f"{case['control']}\n", encoding="utf-8")
    (bundle.parent / "subject").write_text(f"{case['subject']}\n", encoding="utf-8")
    (bundle.parent / "fixture").write_text(f"{case['fixture']}\n", encoding="utf-8")
    (bundle.parent / "expected").write_text(
        f"{expected['outcome']} {failing} {expected['quarantined']} {expected['exit']}\n", encoding="utf-8"
    )
    # Whether the --json output lists the case control under withheld_non_conformant (18.149).
    (bundle.parent / "withheld").write_text(f"{str(expected['withheld']).lower()}\n", encoding="utf-8")
    if "fail_on" in case:
        (bundle.parent / "fail_on").write_text(case["fail_on"], encoding="utf-8")
    if "evidence" in expected:
        # The quarantined events an insufficient_evidence outcome must name (18.119), comma-joined, sorted.
        named = ",".join(sorted(expected["evidence"]))
        (bundle.parent / "evidence").write_text(f"{named}\n", encoding="utf-8")
PY

(cd "$root/engines/typescript" && pnpm --silent build >/dev/null)
(cd "$root/engines/java" && ./gradlew --no-daemon --quiet installDist)

run() {
  local engine="$1"
  shift
  case "$engine" in
    python) (cd "$root/engines/python" && env -u VIRTUAL_ENV uv run --frozen agentce "$@") ;;
    typescript) (cd "$root/engines/typescript" && node bin/agentce.js "$@") ;;
    # Tier-1 JIT and the serial collector cut each short CLI run's CPU (as VG-CLI-OPTIONS does).
    java) JAVA_OPTS="-XX:TieredStopAtLevel=1 -XX:+UseSerialGC" "$root/engines/java/build/install/agentce/bin/agentce" "$@" ;;
  esac
}

# The case control's outcome for the case subject and the event ids of its failing focus nodes ("<outcome> <id,id>").
outcome() {
  jq -r --arg control "$2" --arg subject "$3" '[.[]? // .assertions[] | select(.control == $control and .subject == $subject)][0]
    | "\(.outcome) \([.violations[]?.focus | sub("^agentce:event/"; "")] | unique | join(","))"' "$1"
}

# The event ids the case control's evidence names, sorted and comma-joined.
evidence() {
  jq -r --arg control "$2" --arg subject "$3" '[.[]? // .assertions[] | select(.control == $control and .subject == $subject)][0]
    | [.evidence[]?.ref | sub("^agentce:event/"; "")] | sort | join(",")' "$1"
}

# One engine over its share of the cases (every case whose position modulo $3 is $2); returns non-zero on any
# mismatch. All workers run side by side, Python on three since its CLI starts slowest, so the gate's wall time is
# the slowest worker's; the first mismatch in any worker stops them all (the $work/.stop marker).
check_cases() {
  local engine="$1" worker="$2" workers="$3" position=-1 dir name want out code got quarantined fixture extra
  for dir in "$work"/*/; do
    [ -e "$work/.stop" ] && return 1
    position=$((position + 1))
    [ $((position % workers)) -eq "$worker" ] || continue
    name="$(basename "$dir")"
    want="$(cat "$dir/expected")"
    out="$dir/out-$engine"
    code=0
    fixture="$fixtures/$(cat "$dir/fixture")"
    extra=()
    [ -f "$dir/fail_on" ] && extra=(--fail-on "$(cat "$dir/fail_on")")
    run "$engine" assess --json --bundle "$dir/bundle" --profile "$fixture/applicability.yaml" \
      --domain "$fixture/domain.linkml.yaml" --out "$out" ${extra[@]+"${extra[@]}"} \
      >"$dir/json-$engine" 2>"$dir/err-$engine" || code=$?
    if [ ! -f "$out/assertions.json" ]; then
      echo "$label: $engine wrote no assertions.json for $name (exit $code)" >&2
      tail -3 "$dir/err-$engine" >&2
      touch "$work/.stop"
      return 1
    fi
    quarantined=0
    [ -f "$out/quarantine.jsonl" ] && quarantined="$(wc -l <"$out/quarantine.jsonl" | tr -d ' ')"
    got="$(outcome "$out/assertions.json" "$(cat "$dir/control")" "$(cat "$dir/subject")") $quarantined $code"
    if [ "$got" != "$want" ]; then
      echo "$label: $engine $name gave '$got', expected '$want' (outcome failing quarantined exit)" >&2
      touch "$work/.stop"
      return 1
    fi
    # The control is listed under withheld_non_conformant, with the stable message key, exactly when expected.
    # A listed control's finding is in the JSON exit codes even when a higher code wins the process exit (18.149).
    withheld="$(cat "$dir/withheld")"
    read -r listed has_one < <(jq -r --arg control "$(cat "$dir/control")" --arg subject "$(cat "$dir/subject")" \
      '"\(any(.withheld_non_conformant[]?; .control == $control and .subject == $subject
        and .key == "withheld_reads_non_conformant")) \(.exit_codes | index(1) != null)"' "$dir/json-$engine")
    if [ "$listed" != "$withheld" ]; then
      echo "$label: $engine $name lists the control under withheld_non_conformant: $listed, expected $withheld" >&2
      touch "$work/.stop"
      return 1
    fi
    if [ "$withheld" = true ] && [ "$has_one" != true ]; then
      echo "$label: $engine $name lists the control under withheld_non_conformant but exit_codes lacks 1" >&2
      touch "$work/.stop"
      return 1
    fi
    if [ -f "$dir/evidence" ]; then
      got="$(evidence "$out/assertions.json" "$(cat "$dir/control")" "$(cat "$dir/subject")")"
      if [ "$got" != "$(cat "$dir/evidence")" ]; then
        echo "$label: $engine $name names '$got' in its evidence, expected '$(cat "$dir/evidence")'" >&2
        touch "$work/.stop"
        return 1
      fi
    fi
  done
}

status=0
pids=()
for share in "python 0 3" "python 1 3" "python 2 3" "typescript 0 1" "java 0 1"; do
  # shellcheck disable=SC2086 # the share is three words on purpose
  check_cases $share &
  pids+=("$!")
done
for pid in "${pids[@]}"; do
  wait "$pid" || status=1
done
if [ "$status" -ne 0 ]; then
  echo "$label: stopped at the first case an engine got wrong (above)" >&2
  exit 1
fi
echo "$label: $(ls -d "$work"/*/ | wc -l | tr -d ' ') cases agree in Python, TypeScript and Java"
