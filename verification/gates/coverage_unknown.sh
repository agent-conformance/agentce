#!/usr/bin/env bash
# Build gate helper for VG-COVERAGE-UNKNOWN (18.134): a subject whose capture no independent denominator reconciles has
# unknown coverage, and unknown coverage reads insufficient_evidence on every INT and REC control for that subject
# (SPEC §6.5); agentce readiness never prints READY while coverage is unknown (SPEC §13.3.4). Each case in
# fixtures/coverage_unknown/cases.json becomes one bundle and one applicability profile; the real Python, TypeScript and
# Java CLIs assess it against the baseline catalog, and the named controls' outcomes, the assess exit code, the
# blind-spots coverage_unknown list and the readiness verdict and reason must match the case in every engine.
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
fixtures="$root/verification/gates/fixtures/coverage_unknown"
base="$root/spec/catalogs/base/eu-ai-act"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

# The TypeScript and Java builds run beside the fixture writer; the case loop waits for them.
builds=()
(cd "$root/engines/typescript" && pnpm --silent build >/dev/null) & builds+=("$!")
(cd "$root/engines/java" && ./gradlew --no-daemon --quiet installDist) & builds+=("$!")

(cd "$root/engines/python" && PYTHONPATH="$root/verification/gates" env -u VIRTUAL_ENV uv run --frozen python - "$fixtures/cases.json" "$work" "$root") <<'PY'
import copy
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path

import yaml


class _NoAliasDumper(yaml.SafeDumper):
    """Writes a value met twice in full: an anchor is outside the SPEC 8.7 YAML profile."""

    def ignore_aliases(self, data):
        return True

from fixture_integrity import chain_streams

doc = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
work, root = Path(sys.argv[2]), Path(sys.argv[3])
for name, case in doc["cases"].items():
    case_dir = work / name
    if case.get("mode") == "records":
        (case_dir / "records").mkdir(parents=True)
        shutil.copy(root / case["records"], case_dir / "records")
        (case_dir / "expected.json").write_text(json.dumps(case["expected"]), encoding="utf-8")
        (case_dir / "mode").write_text("records\n", encoding="utf-8")
        continue
    bundle = case_dir / "bundle"
    (bundle / "events").mkdir(parents=True)
    events = []
    for subject in case.get("subjects", [doc["subject"]]):
        suffix = "" if subject == doc["subject"] else "-" + subject.rsplit("/", 1)[-1]
        for event in doc["event_sets"][case["events"]]:
            # A second subject gets its own copy of the records: its own agent id, ids and references.
            text = json.dumps(event).replace(doc["subject"], subject)
            event = json.loads(re.sub(r'(agentce:event/[A-Za-z0-9]+)"', rf'\1{suffix}"', text))
            event.update(id=event["id"] + suffix, specversion="1.0", datacontenttype="application/ld+json")
            events.append(event)
    events += copy.deepcopy(case.get("extra_events", []))
    sources = doc["sources"]
    events, streams = chain_streams(events, {"agentce_bundle_version": 1, "sources": sources})
    text = "".join(json.dumps(e, sort_keys=True) + "\n" for e in events)
    (bundle / "events" / "subject.jsonl").write_text(text, encoding="utf-8")
    files = [{"path": "events/subject.jsonl", "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}]
    for extra in case.get("files", []):
        body = extra["content"] if isinstance(extra["content"], str) else json.dumps(extra["content"])
        path = bundle / extra["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        if extra.get("listed", True):
            digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
            files.append({"path": extra["path"], "sha256": "0" * 64 if extra.get("tamper") else digest})
    manifest = {"agentce_bundle_version": 1, "domain": "coverage-fixture", "files": files, "sources": sources,
                "streams": streams}
    (bundle / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    profile = {"profile_version": 1, "catalogs": ["eu-ai-act@2026.09"], "subjects": []}
    for subject in case.get("subjects", [doc["subject"]]):
        entry = {"id": subject, "role": "both", "evidence_sources": doc["evidence_sources"]}
        denominators = case.get("denominators_by_subject", {}).get(subject, case.get("denominators", []))
        if denominators:
            entry["coverage_denominators"] = denominators
        profile["subjects"].append(entry)
    (case_dir / "applicability.yaml").write_text(yaml.dump(profile, Dumper=_NoAliasDumper, sort_keys=False), encoding="utf-8")
    (case_dir / "expected.json").write_text(json.dumps(case["expected"]), encoding="utf-8")
    (case_dir / "mode").write_text(case.get("mode", "bundle") + "\n", encoding="utf-8")
    if case.get("deviations"):
        (case_dir / "deviations.yaml").write_text(yaml.dump(case["deviations"], Dumper=_NoAliasDumper, sort_keys=False), encoding="utf-8")
    if case.get("gaps"):
        (case_dir / "gaps.yaml").write_text(yaml.dump(case["gaps"], Dumper=_NoAliasDumper, sort_keys=False), encoding="utf-8")
PY

for pid in "${builds[@]}"; do
  wait "$pid"
done

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

# What the case pins, read from one engine's run: each named control's outcome per subject, the assess exit code, the
# blind-spots coverage_unknown checks, and the readiness verdict line and reasons.
observed() {
  local want="$1" out="$2" code="$3" readiness="$4"
  jq -n --slurpfile want "$want" --slurpfile a "$out/assertions.json" \
    --slurpfile b "$out/blind-spots.json" --arg code "$code" --rawfile r "$readiness" '
    ($want[0]) as $w | ($a[0] | if type == "array" then . else .assertions end) as $as
    | {outcomes: ((($w.outcomes // {}) + {($w.control): $w.outcome}) | with_entries(.key as $k
        | .value = ([$as[] | select("\(.subject) \(.control)" == $k or .control == $k)][0].outcome))),
       exit: ($code | tonumber),
       coverage_unknown: ([$b[0].coverage_unknown[] | "\(.subject) \(.control)"] as $got
         | if $w.coverage_unknown == "none" then ($got == []) else ($w.coverage_unknown - $got == []) end),
       readiness: ($r | fromjson | .verdict),
       reason: ($w.reason as $why | if $why == null then null else ($r | fromjson | .reasons | index([$why]) != null) end),
       no_reason: ($w.no_reason as $why
         | if $why == null then null else ($r | fromjson | [.reasons[] | select(contains($why))] == []) end)}'
}

check_cases() {
  local engine="$1" status=0 dir name code out args
  for dir in "$work"/*/; do
    [ -e "$work/.stop" ] && return 1
    name="$(basename "$dir")"
    out="$dir/out-$engine"
    if [ "$(cat "$dir/mode")" = records ]; then
      args=(assess "$dir/records" --out "$out")
    else
      args=(assess --bundle "$dir/bundle" --profile "$dir/applicability.yaml" --domain "$base/test/domain.yaml"
        --out "$out")
      [ -f "$dir/deviations.yaml" ] && args+=(--deviations "$dir/deviations.yaml")
    fi
    # The case's expectation, with any engine-specific override (Java reads no folder of trace exports).
    exp="$dir/expected-$engine.json"
    jq --arg e "$engine" '. + (.by_engine[$e] // {}) | del(.by_engine)' "$dir/expected.json" >"$exp"
    code=0
    # A refusal's key is on stderr in Python and on stdout in TypeScript and Java (KD-102), so read both.
    run "$engine" "${args[@]}" >"$dir/err-$engine" 2>&1 || code=$?
    want="$(jq -c '{exit} + (if .refusal then {refusal} else {} end)' "$exp")"
    if [ "$(jq -r '.refusal // empty' "$exp")" != "" ]; then
      got="$(jq -cn --arg c "$code" --arg k "$(grep -o 'input\.[a-z_]*' "$dir/err-$engine" | head -1)" \
        '{exit: ($c | tonumber), refusal: $k}')"
    elif [ ! -f "$out/assertions.json" ]; then
      echo "coverage-unknown: $engine wrote no assertions.json for $name (exit $code)" >&2
      tail -3 "$dir/err-$engine" >&2
      touch "$work/.stop"
      return 1
    else
      gaps=()
      [ -f "$dir/gaps.yaml" ] && gaps=(--gaps "$dir/gaps.yaml")
      run "$engine" readiness "$out" --json ${gaps[@]+"${gaps[@]}"} >"$dir/ready-$engine" 2>/dev/null || true
      got="$(observed "$exp" "$out" "$code" "$dir/ready-$engine" | jq -c .)"
      want="$(jq -c '{outcomes: ((.outcomes // {}) + {(.control): .outcome}), exit, coverage_unknown: true, readiness,
        reason: (if .reason then true else null end), no_reason: (if .no_reason then true else null end)}' "$exp")"
    fi
    if [ "$got" != "$want" ]; then
      echo "coverage-unknown: $engine $name gave $got, expected $want" >&2
      status=1
      touch "$work/.stop"
    fi
  done
  return "$status"
}

status=0
pids=()
for engine in python typescript java; do
  check_cases "$engine" &
  pids+=("$!")
done
for pid in "${pids[@]}"; do
  wait "$pid" || status=1
done
if [ "$status" -ne 0 ]; then
  echo "coverage-unknown: stopped at the first case an engine got wrong (above)" >&2
  exit 1
fi
# Beyond the pinned controls, the engines agree with each other on every control's outcome and on the whole
# coverage_unknown list (Java reads no trace-export folder, so records mode compares Python with TypeScript).
for dir in "$work"/*/; do
  digests=()
  for engine in python typescript java; do
    out="$dir/out-$engine"
    [ -f "$out/assertions.json" ] || continue
    digests+=("$(jq -cS --slurpfile b "$out/blind-spots.json" '(if type == "array" then . else .assertions end)
      | {outcomes: ([.[] | [.subject, .control, .outcome]] | sort), coverage_unknown: $b[0].coverage_unknown}' \
      "$out/assertions.json" | shasum | cut -c1-16)")
  done
  if [ "${#digests[@]}" -gt 1 ] && [ "$(printf '%s\n' "${digests[@]}" | sort -u | wc -l | tr -d ' ')" != 1 ]; then
    echo "coverage-unknown: engines disagree on $(basename "$dir") outside the pinned controls (${digests[*]})" >&2
    exit 1
  fi
done
count="$(ls -d "$work"/*/ | wc -l | tr -d ' ')"
echo "coverage-unknown: $count cases, three engines agree with the expected outcomes, exit codes and readiness"
