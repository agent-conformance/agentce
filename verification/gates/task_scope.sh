#!/usr/bin/env bash
# Build gate helper for VG-TASK-SCOPE (18.174): CND-01 holds every tool call in a task to the task's declared effect
# scope (SPEC §7.7.4, §7.7.6 task_scopes in the domain binding). Each case in fixtures/task_scope/cases.json is a domain
# binding and the enforcement-point ToolCalls of one subject; the real Python, TypeScript and Java CLIs assess it
# against the baseline catalog and the Conduct overlay, and CND-01's outcome and failing calls must be the case's in
# every engine, with nothing quarantined. Cases whose domain is "@template" use the get-evidence skill's task-scope
# template filled in as a user would, and lint_profile must read that binding with no task-scope finding.
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
fixtures="$root/verification/gates/fixtures/task_scope"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
subject="spiffe://corp/agents/task-scope-fixture"
other="spiffe://corp/agents/task-scope-other"
template="$root/skills/agentce-get-evidence/assets/task-scope.template.yaml"

(cd "$root/engines/python" && env -u VIRTUAL_ENV uv run --frozen python - "$fixtures/cases.json" "$work" "$subject" "$other" "$template") <<'PY'
import hashlib
import json
import sys
from pathlib import Path

cases = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))["cases"]
work, subject, other = Path(sys.argv[2]), sys.argv[3], sys.argv[4]
# The template as a user fills it in: the two fields CND-01 reads, everything else left as written.
template = Path(sys.argv[5]).read_text(encoding="utf-8")
for field, value in (("<task-type>", "credit-review"), ("<tool-name>", "lookup")):
    template = template.replace(field, value)
(work / "template-domain.yaml").write_text(template, encoding="utf-8")
for name, case in cases.items():
    events = []
    for n, call in enumerate(case["calls"]):
        who = other if call.get("subject") == "other" else subject
        data = {"@type": "ToolCall", "@context": "https://agent-conformance.org/contexts/evidence/v1",
                "agent": {"id": who}}
        if "tool" in call:
            data["tool"] = {"name": call["tool"], "protocol": "native"}
        if "effect_class" in call:
            data["effect_class"] = call["effect_class"]
        if "refs_task" in call:
            data["refs"] = {"task": call["refs_task"]}
        # A call's PolicyDecision deny lands a second after it ("after"), a second before it ("before"), or names an
        # event no record holds ("unheld").
        event = {"id": call["id"], "source": "urn:src:gateway", "subject": who,
                 "type": "org.agent-conformance.evidence.ToolCall.v1", "agentcesourceclass": "enforcement_point",
                 "time": f"2026-01-02T00:00:{3 * n + 1:02d}Z", "specversion": "1.0",
                 "datacontenttype": "application/ld+json", "data": data}
        if "agentcetask" in call:
            event["agentcetask"] = call["agentcetask"]
        events.append(event)
        if "deny" in call:
            at = 3 * n if call["deny"] == "before" else 3 * n + 2
            request = "agentce:event/not-held" if call["deny"] == "unheld" else f"agentce:event/{call['id']}"
            events.append({"id": f"pd-{call['id']}", "source": "urn:src:gateway", "subject": who,
                           "type": "org.agent-conformance.evidence.PolicyDecision.v1",
                           "agentcesourceclass": "enforcement_point", "time": f"2026-01-02T00:00:{at:02d}Z",
                           "specversion": "1.0", "datacontenttype": "application/ld+json",
                           "data": {"@type": "PolicyDecision",
                                    "@context": "https://agent-conformance.org/contexts/evidence/v1",
                                    "decision": "deny", "refs": {"request": request}}})
    bundle = work / name / "bundle"
    (bundle / "events").mkdir(parents=True)
    # a stream is written in time order (a record out of order is quarantined)
    text = "".join(json.dumps(e, sort_keys=True) + "\n" for e in sorted(events, key=lambda e: e["time"]))
    (bundle / "events" / "subject.jsonl").write_text(text, encoding="utf-8")
    manifest = {
        "agentce_bundle_version": 1,
        "domain": "task-scope-fixture",
        "files": [{"path": "events/subject.jsonl", "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}],
        "sources": [{"id": "urn:src:gateway", "class": "enforcement_point"}],
    }
    (bundle / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    domain = template if case["domain"] == "@template" else case["domain"]
    (bundle.parent / "domain.linkml.yaml").write_text(domain, encoding="utf-8")
    lines = [case["expected"]] + ([case["expected_other"]] if "expected_other" in case else [])
    (bundle.parent / "expected").write_text(
        "".join(f"{e['outcome']} {','.join(sorted(e['failing']))}\n" for e in lines), encoding="utf-8")
PY

# The filled-in template is what lint_profile reads too: it must read it (exit 0 or 1, a findings list) and report
# no Conduct or task-scope finding.
code=0
lint="$(cd "$root/skills/agentce-get-evidence" && env -u VIRTUAL_ENV uv run --frozen python scripts/lint_profile.py \
  --profile "$fixtures/applicability.yaml" --domain "$work/template-domain.yaml" --json)" || code=$?
if [ "$code" -gt 1 ] || ! jq -e '(.findings | type == "array")
    and ([.findings[].code | select(startswith("lint.conduct") or startswith("lint.task_scope"))] | length == 0)' \
    <<<"$lint" >/dev/null; then
  echo "task-scope: lint_profile (exit $code) did not read the filled-in template cleanly: $lint" >&2
  exit 1
fi

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

# CND-01's outcome for a subject, then the event ids of its failing focus nodes ("<outcome> <id,id>").
outcome() {
  jq -r --arg subject "$2" \
    '[.[]? // .assertions[] | select(.control == "CND-01" and .subject == $subject)][0]
    | "\(.outcome) \([.violations[]?.focus | sub("^agentce:event/"; "")] | unique | join(","))"' "$1"
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
      echo "task-scope: $engine wrote no assertions.json for $name (exit $code)" >&2
      tail -3 "$dir/err-$engine" >&2
      touch "$work/.stop"
      return 1
    fi
    if [ -s "$out/quarantine.jsonl" ]; then
      echo "task-scope: $engine quarantined events of $name, so the case did not read its input" >&2
      touch "$work/.stop"
      return 1
    fi
    got="$(outcome "$out/assertions.json" "$subject")"
    [ "$(wc -l <"$dir/expected")" -gt 1 ] && got="$got
$(outcome "$out/assertions.json" "$other")"
    if [ "$got" != "$want" ]; then
      echo "task-scope: $engine $name gave CND-01 '$got', expected '$want'" >&2
      touch "$work/.stop"
      return 1
    fi
  done
}

status=0
pids=()
for engine in ${AGENTCE_TASK_SCOPE_ENGINES:-python typescript java}; do
  check_cases "$engine" &
  pids+=("$!")
done
for pid in "${pids[@]}"; do
  wait "$pid" || status=1
done
if [ "$status" -ne 0 ]; then
  echo "task-scope: stopped at the first case an engine got wrong (above)" >&2
  exit 1
fi
echo "task-scope: $(ls -d "$work"/*/ | wc -l | tr -d ' ') cases, every engine gives CND-01 the expected outcome and failing calls"
