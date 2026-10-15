#!/usr/bin/env bash
# Build gate helper for VG-TASK-BUDGET (18.175): CND-07 holds every ToolCall, and every ModelCall in a task, to the
# resource budgets its task's declared scopes set (SPEC §7.7.4 CND-07, §7.7.6 task_scopes budgets in the domain
# binding). Each case in fixtures/task_budget/cases.json is a domain binding and the records of one subject (tool
# calls, model calls with their token usage, refusals); the real Python, TypeScript and Java CLIs assess it against the
# baseline catalog and the Conduct overlay, and CND-07's outcome and failing records must be the case's in every
# engine, with nothing quarantined.
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
fixtures="$root/verification/gates/fixtures/task_budget"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
subject="spiffe://corp/agents/task-budget-fixture"
other="spiffe://corp/agents/task-budget-other"

(cd "$root/engines/python" && env -u VIRTUAL_ENV uv run --frozen python - "$fixtures/cases.json" "$work" "$subject" "$other") <<'PY'
import hashlib
import json
import sys
from pathlib import Path

cases = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))["cases"]
work, subject, other = Path(sys.argv[2]), sys.argv[3], sys.argv[4]
context = "https://agent-conformance.org/contexts/evidence/v1"
# One source per class: a provider usage export (independent_system), a gateway (enforcement_point), the agent itself.
sources = {"independent_system": "urn:src:provider", "enforcement_point": "urn:src:gateway", "self_report": "urn:src:agent"}
for name, case in cases.items():
    events = []
    for n, record in enumerate(case["records"]):
        who = other if record.get("subject") == "other" else subject
        kind = record["kind"]
        data = {"@type": kind, "@context": context, "agent": {"id": who}}
        if kind == "ToolCall":
            data["tool"] = {"name": record.get("tool", "lookup"), "protocol": "native"}
            if "effect_class" in record:
                data["effect_class"] = record["effect_class"]
        elif kind == "ModelCall":
            data.update({"operation": "chat", "model": {"provider": "p", "name": "m", "version_or_digest": "1"}})
            if "usage" in record:
                data["usage"] = record["usage"]
        else:
            data["reason_class"] = record["reason_class"]
            data["refs"] = {"instruction": "agentce:event/not-held", "request": f"agentce:event/{record['request']}"}
        if "refs_task" in record:
            data.setdefault("refs", {})["task"] = record["refs_task"]
        cls = record.get("class", "independent_system")
        event = {"id": record["id"], "source": sources[cls], "subject": who,
                 "type": f"org.agent-conformance.evidence.{kind}.v1", "agentcesourceclass": cls,
                 "time": record.get("time", f"2026-01-02T00:00:{n + 1:02d}Z"), "specversion": "1.0",
                 "datacontenttype": "application/ld+json", "data": data}
        if "agentcetask" in record:
            event["agentcetask"] = record["agentcetask"]
        events.append(event)
    bundle = work / name / "bundle"
    (bundle / "events").mkdir(parents=True)
    # records are listed in time order (a record out of order is quarantined)
    text = "".join(json.dumps(e, sort_keys=True) + "\n" for e in events)
    (bundle / "events" / "subject.jsonl").write_text(text, encoding="utf-8")
    manifest = {
        "agentce_bundle_version": 1,
        "domain": "task-budget-fixture",
        "files": [{"path": "events/subject.jsonl", "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}],
        "sources": [{"id": s, "class": c} for c, s in sorted(sources.items())],
    }
    (bundle / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    (bundle.parent / "domain.linkml.yaml").write_text(case["domain"], encoding="utf-8")
    lines = [case["expected"]] + ([case["expected_other"]] if "expected_other" in case else [])
    (bundle.parent / "expected").write_text(
        "".join(f"{e['outcome']} {','.join(sorted(e['failing']))}\n" for e in lines), encoding="utf-8")
PY

# shellcheck source=build_engines.sh
. "$root/verification/gates/build_engines.sh"
build_engines
jar="$(ls "$root"/engines/java/build/libs/agentce-*-all.jar | tail -1)"

run() {
  local engine="$1"
  shift
  case "$engine" in
    python) (cd "$root/engines/python" && env -u VIRTUAL_ENV uv run --frozen agentce "$@") ;;
    typescript) (cd "$root/engines/typescript" && node bin/agentce.js "$@") ;;
    # Tier-1 JIT and the serial collector cut each short CLI run's CPU (as VG-CLI-OPTIONS does).
    java) java -XX:TieredStopAtLevel=1 -XX:+UseSerialGC -jar "$jar" "$@" ;;
  esac
}

# CND-07's outcome for a subject, "/class" when it is insufficient_evidence only because a record is below the
# control's source-class minimum (the budgets were judged), then the event ids of its failing focus nodes
# ("<outcome>[/class] <id,id>").
outcome() {
  jq -r --arg subject "$2" \
    '[.[]? // .assertions[] | select(.control == "CND-07" and .subject == $subject)][0]
    | "\(.outcome)\(if .source_class_satisfied == false then "/class" else "" end) \([.violations[]?.focus | sub("^agentce:event/"; "")] | unique | join(","))"' "$1"
}

# One engine over every case; the first mismatch in any engine stops all three (the $work/.stop marker), so a
# seeded-fault run turns red without assessing every case.
check_cases() {
  local engine="$1" dir name want out code got
  for dir in "$work"/*/; do
    [ -e "$work/.stop" ] && return 1
    name="$(basename "$dir")"
    want="$(cat "$dir/expected")"
    out="$dir/out-$engine"
    code=0
    run "$engine" assess --bundle "$dir/bundle" --profile "$fixtures/applicability.yaml" \
      --domain "$dir/domain.linkml.yaml" --out "$out" >/dev/null 2>"$dir/err-$engine" || code=$?
    if [ ! -f "$out/assertions.json" ]; then
      echo "task-budget: $engine wrote no assertions.json for $name (exit $code)" >&2
      tail -3 "$dir/err-$engine" >&2
      touch "$work/.stop"
      return 1
    fi
    if [ -s "$out/quarantine.jsonl" ]; then
      echo "task-budget: $engine quarantined events of $name, so the case did not read its input" >&2
      touch "$work/.stop"
      return 1
    fi
    got="$(outcome "$out/assertions.json" "$subject")"
    [ "$(wc -l <"$dir/expected")" -gt 1 ] && got="$got
$(outcome "$out/assertions.json" "$other")"
    if [ "$got" != "$want" ]; then
      echo "task-budget: $engine $name gave CND-07 '$got', expected '$want'" >&2
      touch "$work/.stop"
      return 1
    fi
  done
}

status=0
pids=()
for engine in ${AGENTCE_TASK_BUDGET_ENGINES:-python typescript java}; do
  check_cases "$engine" &
  pids+=("$!")
done
for pid in "${pids[@]}"; do
  wait "$pid" || status=1
done
if [ "$status" -ne 0 ]; then
  echo "task-budget: stopped at the first case an engine got wrong (above)" >&2
  exit 1
fi
echo "task-budget: $(ls -d "$work"/*/ | wc -l | tr -d ' ') cases, every engine gives CND-07 the expected outcome and failing calls"
