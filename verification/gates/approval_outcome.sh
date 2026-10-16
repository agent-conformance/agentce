#!/usr/bin/env bash
# Build gate helper for VG-APPROVAL-OUTCOME (18.136) and, given a fixture name, VG-APPROVAL-ORDER (approval_order,
# 18.141), VG-APPROVAL-WITHDRAWAL (approval_withdrawal, 18.142), VG-DUPLICATE-ID-CLASS (duplicate_id_class, 18.143:
# a record of a lower source class never displaces a higher-class record with the same event id), VG-STREAM-SOURCE-BOUND
# (stream_source_bound, 18.147: a record's claimed stream never joins another source's time-order group, nor, 18.151,
# another source's integrity chain) and
# VG-TIME-ORDER-INSTANT (time_order_instant, 18.145: ingest compares stream times as instants). An approval gates a decision only when its outcome
# is approve (SPEC §6.2 ApprovalDecided.outcome approve|edit|reject), it is recorded at or before every action that
# executes the decision, and the same human did not withdraw it before the action ran. CND-02 and OVS-08 count
# only such an approve (the agentce:approvedBy edge and OVS-08's distinct approvers); OVS-01, INC-03 and RSK-02 ask for
# a review, which a reject, an edit or a late approval also is. Each case in fixtures/<name>/cases.json (the profiles
# and the domain are fixtures/approval_outcome's for both) becomes one bundle assessed under its profile
# (eu: eu-ai-act and Conduct; nist: nist-ai-rmf); the real Python, TypeScript and Java CLIs assess it, and the case's
# control must give the expected outcome and failing events in every engine, with the expected quarantined record count
# and exit code, and, where the case gives expected.integrity, every integrity stream and status integrity.jsonl holds.
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
name="${1:-approval_outcome}"
label="${name//_/-}"
fixtures="$root/verification/gates/fixtures/approval_outcome"
cases="$root/verification/gates/fixtures/$name/cases.json"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

(cd "$root/engines/python" && PYTHONPATH="$root/verification/gates" env -u VIRTUAL_ENV uv run --frozen python - "$cases" "$work") <<'PY'
import hashlib
import json
import sys
from pathlib import Path

from fixture_integrity import chain_streams

cases = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))["cases"]
for name, case in cases.items():
    bundle = Path(sys.argv[2]) / name / "bundle"
    (bundle / "events").mkdir(parents=True)
    # Declared classes, so the login records keep the class their stream has (SPEC §6.4); a source named
    # urn:src:unclassed... is declared with no class, so ingest trusts its records as self_report.
    sources = [
        {"id": f"urn:src:{cls}", "class": cls}
        for cls in ("enforcement_point", "independent_system", "self_report")
    ] + [
        {"id": source}
        for source in sorted({e["source"] for e in case["events"]})
        if source.startswith("urn:src:unclassed")
    ]
    # A case that pins integrity.jsonl writes its own chains; every other stream is chained over its accepted records.
    events, streams = (
        (case["events"], [])
        if "integrity" in case["expected"]
        else chain_streams(case["events"], {"agentce_bundle_version": 1, "sources": sources})
    )
    text = "".join(json.dumps(e, sort_keys=True) + "\n" for e in events)
    (bundle / "events" / "subject.jsonl").write_text(text, encoding="utf-8")
    for path, body in case.get("files", {}).items():  # a case's other bundle files (signature envelopes)
        (bundle / path).parent.mkdir(parents=True, exist_ok=True)
        (bundle / path).write_text(body, encoding="utf-8")
    manifest = {
        "agentce_bundle_version": 1,
        "domain": "approval-outcome-fixture",
        "files": [
            {
                "path": "events/subject.jsonl",
                "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            }
        ],
        "sources": sources,
    }
    if "streams" in case:  # a case's integrity streams and their anchors (SPEC §6.6), as the manifest lists them
        streams = case["streams"] + streams
    if streams:
        manifest["streams"] = streams
    (bundle / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    expected = case["expected"]
    failing = ",".join(sorted(expected["failing"]))
    (bundle.parent / "control").write_text(f"{case['control']}\n", encoding="utf-8")
    profile = "applicability-nist.yaml" if case["profile"] == "nist" else "applicability.yaml"
    (bundle.parent / "profile").write_text(f"{profile}\n", encoding="utf-8")
    (bundle.parent / "expected").write_text(
        f"{expected['outcome']} {failing} {expected['quarantined']} {expected['exit']}\n", encoding="utf-8"
    )
    if "trust_root" in case:  # the run's own trust root (--trust-root): the keys a source signature verifies against
        (bundle.parent / "trust-root.json").write_text(json.dumps(case["trust_root"]) + "\n", encoding="utf-8")
    if "integrity" in expected:
        lines = "".join(f"{e['stream']} {e['status']}\n" for e in expected["integrity"])
        (bundle.parent / "integrity").write_text(lines, encoding="utf-8")
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

# The case control's outcome and the event ids of its failing focus nodes ("<outcome> <id,id>").
outcome() {
  jq -r --arg control "$2" '[.[]? // .assertions[] | select(.control == $control)][0]
    | "\(.outcome) \([.violations[]?.focus | sub("^agentce:event/"; "")] | unique | join(","))"' "$1"
}

# One engine over its share of the cases (every case whose position modulo $3 is $2); returns non-zero on any
# mismatch. All workers run side by side, Python on three since its CLI starts slowest, so the gate's wall time is
# the slowest worker's; the first mismatch in any worker stops them all (the $work/.stop marker).
check_cases() {
  local engine="$1" worker="$2" workers="$3" position=-1 dir name want out code got quarantined
  for dir in "$work"/*/; do
    [ -e "$work/.stop" ] && return 1
    position=$((position + 1))
    [ $((position % workers)) -eq "$worker" ] || continue
    name="$(basename "$dir")"
    want="$(cat "$dir/expected")"
    out="$dir/out-$engine"
    code=0
    trust=()
    [ -f "$dir/trust-root.json" ] && trust=(--trust-root "$dir/trust-root.json")
    run "$engine" assess --bundle "$dir/bundle" --profile "$fixtures/$(cat "$dir/profile")" \
      --domain "$fixtures/domain.linkml.yaml" --out "$out" ${trust[@]+"${trust[@]}"} \
      >/dev/null 2>"$dir/err-$engine" || code=$?
    if [ ! -f "$out/assertions.json" ]; then
      echo "$label: $engine wrote no assertions.json for $name (exit $code)" >&2
      tail -3 "$dir/err-$engine" >&2
      touch "$work/.stop"
      return 1
    fi
    quarantined=0
    [ -f "$out/quarantine.jsonl" ] && quarantined="$(wc -l <"$out/quarantine.jsonl" | tr -d ' ')"
    got="$(outcome "$out/assertions.json" "$(cat "$dir/control")") $quarantined $code"
    if [ "$got" != "$want" ]; then
      echo "$label: $engine $name gave '$got', expected '$want' (outcome failing quarantined exit)" >&2
      touch "$work/.stop"
      return 1
    fi
    # A case with expected integrity also pins every {stream, status} of integrity.jsonl, in the order written.
    if [ -f "$dir/integrity" ] &&
      ! jq -r '"\(.stream) \(.status)"' "$out/integrity.jsonl" | cmp -s - "$dir/integrity"; then
      echo "$label: $engine $name wrote integrity streams" >&2
      jq -r '"  \(.stream) \(.status)"' "$out/integrity.jsonl" >&2
      echo "expected" >&2
      sed 's/^/  /' "$dir/integrity" >&2
      touch "$work/.stop"
      return 1
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
