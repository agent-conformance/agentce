#!/usr/bin/env bash
# Build gate helper for VG-EUAIACT-CLUSTER2 (18.37e): eight eu-ai-act controls that shared two rules now each test what
# their title says -- DOC-03 a consequential call at an enforcement point AND a declared egress-proxy or firewall count
# that reconciles the captured calls (evaluation.requires_denominator), OVS-06 the call at an enforcement point, ROB-05 and
# ROB-06 (rung 3) not_assessed, INT-03 a consequential call's own record resolves, ROB-03 its authorization, delegation
# and instruction name held events of their SPEC type, REC-06 and INC-04 no record reachable from the call or incident
# carries a dangling reference. Each case in fixtures/eu_cluster2/cases.json becomes one bundle under its own profile;
# the real Python, TypeScript and Java CLIs assess it against the eu-ai-act catalog, and the named control's outcome,
# failing events, quarantine count and exit code must match. A catalog case writes a requires_denominator value into
# DOC-03 of a copy of the catalog; every engine must refuse it with the case's message key and exit code.
# ENGINES (default "python typescript java") narrows the run for local reference captures.
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
fixtures="$root/verification/gates/fixtures/eu_cluster2"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

# The TypeScript and Java builds run beside the fixture writer; the case loop waits for them.
engines="${ENGINES:-python typescript java}"
builds=()
case " $engines " in *" typescript "*) (cd "$root/engines/typescript" && pnpm --silent build >/dev/null) & builds+=("$!") ;; esac
case " $engines " in *" java "*) (cd "$root/engines/java" && ./gradlew --no-daemon --quiet installDist) & builds+=("$!") ;; esac

(cd "$root/engines/python" && PYTHONPATH="$root/verification/gates" env -u VIRTUAL_ENV uv run --frozen python - "$fixtures/cases.json" "$work" "$root/spec/catalogs/base/eu-ai-act") <<'PY'
import hashlib
import json
import shutil
import sys
from collections import Counter
from pathlib import Path

import yaml
from fixture_integrity import chain_streams, event_type_of

cases = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))["cases"]
fixtures = Path(sys.argv[1]).parent
catalog = Path(sys.argv[3])
for name, case in cases.items():
    bundle = Path(sys.argv[2]) / name / "bundle"
    (bundle / "events").mkdir(parents=True)
    # Declared classes, so each record keeps its stream's class (SPEC §6.4).
    sources = [
        {"id": f"urn:src:{cls}", "class": cls}
        for cls in ("enforcement_point", "independent_system", "self_report")
    ]
    events, streams = chain_streams(case["events"], {"agentce_bundle_version": 1, "sources": sources})
    text = "".join(json.dumps(e, sort_keys=True) + "\n" for e in events)
    (bundle / "events" / "subject.jsonl").write_text(text, encoding="utf-8")
    manifest = {
        "agentce_bundle_version": 1,
        "domain": "eu-cluster2-fixture",
        "files": [
            {
                "path": "events/subject.jsonl",
                "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            }
        ],
        "sources": sources,
        "streams": streams,
    }
    # Each declared denominator counts the subject's records per type, plus the case's extra records for that kind.
    profile = yaml.safe_load((fixtures / case["profile"]).read_text(encoding="utf-8"))
    for subject in profile["subjects"]:
        for denominator in subject.get("coverage_denominators", []):
            counts = Counter(
                event_type_of(e) for e in events if e.get("subject") == subject["id"]
            )
            counts.update(case["extra"].get(denominator["kind"], {}))
            body = json.dumps({"counts": dict(sorted(counts.items()))}, sort_keys=True, indent=2) + "\n"
            (bundle / denominator["manifest"]).parent.mkdir(parents=True, exist_ok=True)
            (bundle / denominator["manifest"]).write_text(body, encoding="utf-8")
            manifest["files"].append(
                {"path": denominator["manifest"], "sha256": hashlib.sha256(body.encode("utf-8")).hexdigest()}
            )
    (bundle / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    expected = case["expected"]
    if "catalog_value" in case:
        # A copy of the catalog whose DOC-03 declares the case's requires_denominator value.
        copy = bundle.parent / "catalog"
        shutil.copytree(catalog, copy)
        doc03 = copy / "controls" / "DOC-03.yaml"
        lines = [
            f"  requires_denominator: {case['catalog_value']}" if line.startswith("  requires_denominator:") else line
            for line in doc03.read_text(encoding="utf-8").splitlines()
        ]
        doc03.write_text("\n".join(lines) + "\n", encoding="utf-8")
        (bundle.parent / "expected").write_text(f"refused {expected['key']}\n", encoding="utf-8")
    else:
        failing = ",".join(sorted(expected["failing"]))
        (bundle.parent / "expected").write_text(f"{expected['outcome']} {failing}\n", encoding="utf-8")
    (bundle.parent / "control").write_text(f"{case['control']}\n", encoding="utf-8")
    (bundle.parent / "profile").write_text(f"{case['profile']}\n", encoding="utf-8")
    (bundle.parent / "quarantined").write_text(f"{case['quarantined']}\n", encoding="utf-8")
    (bundle.parent / "exit").write_text(f"{expected['exit']}\n", encoding="utf-8")
PY
for pid in ${builds[@]+"${builds[@]}"}; do
  wait "$pid"
done
cases=("$work"/*/)
if [ "${#cases[@]}" -lt 60 ]; then
  echo "eu-cluster2: only ${#cases[@]} cases in cases.json, expected at least 60" >&2
  exit 1
fi

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

# The case's control outcome, then the event ids of its failing focus nodes ("<outcome> <id,id>").
outcome() {
  jq -r --arg c "$2" '[.[]? // .assertions[] | select(.control == $c)][0]
    | "\(.outcome) \([.violations[]?.focus | sub("^agentce:event/"; "")] | unique | join(","))"' "$1"
}

# One engine over every case; returns non-zero on any mismatch. The engines run side by side (each writes only its own
# out-<engine> and err-<engine>), so the gate's wall time is the slowest engine's, not the sum. The first mismatch in
# any engine stops them all (the $work/.stop marker): the gate has already failed, and a seeded-fault run turns red
# without assessing every case.
check_cases() {
  local engine="$1" status=0 dir name want want_exit control out code got got_q
  for dir in "${cases[@]}"; do
    [ -e "$work/.stop" ] && return 1
    name="$(basename "$dir")"
    want="$(cat "$dir/expected")"
    want_exit="$(cat "$dir/exit")"
    control="$(cat "$dir/control")"
    out="$dir/out-$engine"
    code=0
    if [ -d "$dir/catalog" ]; then
      # Both streams: Python writes a refusal on stderr, the TypeScript CLI on stdout.
      run "$engine" assess --bundle "$dir/bundle" --profile "$fixtures/$(cat "$dir/profile")" \
        --domain "$fixtures/domain.linkml.yaml" --catalog-dir "$dir/catalog" --allow-unverified-catalog --out "$out" \
        >"$dir/err-$engine" 2>&1 || code=$?
      if [ "$code" != "$want_exit" ]; then
        echo "eu-cluster2: $engine $name exited $code, expected $want_exit" >&2
        status=1
      fi
      if ! grep -qF "${want#refused }" "$dir/err-$engine"; then
        echo "eu-cluster2: $engine $name did not refuse with ${want#refused }" >&2
        tail -3 "$dir/err-$engine" >&2
        status=1
      fi
      [ "$status" -eq 0 ] || touch "$work/.stop"
      continue
    fi
    run "$engine" assess --bundle "$dir/bundle" --profile "$fixtures/$(cat "$dir/profile")" \
      --domain "$fixtures/domain.linkml.yaml" --out "$out" \
      >/dev/null 2>"$dir/err-$engine" || code=$?
    if [ ! -f "$out/assertions.json" ]; then
      echo "eu-cluster2: $engine wrote no assertions.json for $name (exit $code)" >&2
      tail -3 "$dir/err-$engine" >&2
      touch "$work/.stop"
      return 1
    fi
    if [ "$code" != "$want_exit" ]; then
      echo "eu-cluster2: $engine $name exited $code, expected $want_exit" >&2
      status=1
    fi
    got_q="$(cat "$out/quarantine.jsonl" 2>/dev/null | wc -l | tr -d ' ')"
    if [ "$got_q" != "$(cat "$dir/quarantined")" ]; then
      echo "eu-cluster2: $engine quarantined $got_q records of $name, expected $(cat "$dir/quarantined")" >&2
      status=1
    fi
    got="$(outcome "$out/assertions.json" "$control")"
    if [ "$got" != "$want" ]; then
      echo "eu-cluster2: $engine $name gave $control '$got', expected '$want'" >&2
      status=1
    fi
    [ "$status" -eq 0 ] || touch "$work/.stop"
  done
  return "$status"
}

status=0
pids=()
for engine in $engines; do
  check_cases "$engine" &
  pids+=("$!")
done
for pid in "${pids[@]}"; do
  wait "$pid" || status=1
done
if [ "$status" -ne 0 ]; then
  echo "eu-cluster2: stopped at the first case an engine got wrong (above)" >&2
  exit 1
fi
echo "eu-cluster2: ${#cases[@]} cases, every engine in [$engines] agrees with each expected outcome"
