#!/usr/bin/env bash
# Build gate helper for VG-SOURCE-CLASS (18.127): every control's min_source_class holds per assertion (SPEC HR-5,
# §6.4). A focus whose judged class is below the control's minimum reads insufficient_evidence, never conformant; for
# CND-04 and CND-05 the judged class includes the acted-on instruction's, which counts as enforcement_point only when an
# enforcement-point record attests it (SPEC §7.7.5, §10.4). Each event case in fixtures/source_class/cases.json becomes
# one bundle; the real Python, TypeScript and Java CLIs assess it against the baseline catalog and the Conduct overlay,
# and the named control's outcome and failing events must match the case's expectation in every engine, with the
# expected exit code and nothing quarantined. Each unknown-class case is a copy of the Conduct overlay whose CND-05
# min_source_class is not a source class; every engine must refuse it when the catalog loads, with one key and exit.
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
fixtures="$root/verification/gates/fixtures/source_class"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

(cd "$root/engines/python" && PYTHONPATH="$root/verification/gates" env -u VIRTUAL_ENV uv run --frozen python - "$fixtures/cases.json" "$work" \
  "$root/spec/catalogs/overlays/conduct") <<'PY'
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path

from fixture_integrity import chain_streams

cases = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))["cases"]
work, overlay = Path(sys.argv[2]), Path(sys.argv[3])
for name, case in cases.items():
    expected = case["expected"]
    if "catalog" in case:
        # A copy of the Conduct overlay whose control declares the given source_class_from, or the given
        # min_source_class, or no min_source_class.
        target = case["catalog"]
        field = "source_class_from" if "source_class_from" in target else "min_source_class"
        copy = work / ".catalogs" / name
        shutil.copytree(overlay, copy)
        control = copy / "controls" / f"{target['control']}.yaml"
        text = control.read_text(encoding="utf-8")
        line = re.compile(rf"^  {field}: .*\n", re.MULTILINE)
        assert len(line.findall(text)) == 1, f"{control} has no single {field} line"
        value = f"  {field}: {json.dumps(target[field])}\n" if field in target else ""
        control.write_text(line.sub(lambda _: value, text), encoding="utf-8")
        refusals = work / ".refusals"
        refusals.mkdir(exist_ok=True)
        (refusals / name).write_text(f"{expected['refusal']} {expected['exit']}\n", encoding="utf-8")
        continue
    bundle = work / name / "bundle"
    (bundle / "events").mkdir(parents=True)
    # Declared classes, so each record keeps the class its source has (SPEC §6.4).
    sources = [
        {"id": f"urn:src:{cls}", "class": cls}
        for cls in ("enforcement_point", "independent_system", "self_report")
    ]
    events, streams = chain_streams(case["events"], {"agentce_bundle_version": 1, "sources": sources})
    text = "".join(json.dumps(e, sort_keys=True) + "\n" for e in events)
    (bundle / "events" / "subject.jsonl").write_text(text, encoding="utf-8")
    manifest = {
        "agentce_bundle_version": 1,
        "domain": "source-class-fixture",
        "streams": streams,
        "files": [
            {
                "path": "events/subject.jsonl",
                "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            }
        ],
        "sources": sources,
    }
    (bundle / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    failing = ",".join(sorted(expected["failing"]))
    (bundle.parent / "control").write_text(f"{case['control']}\n", encoding="utf-8")
    (bundle.parent / "expected").write_text(f"{expected['outcome']} {failing}\n", encoding="utf-8")
    (bundle.parent / "exit").write_text(f"{expected['exit']}\n", encoding="utf-8")
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

# The control's outcome for the fixture subject, then the event ids of its failing focus nodes ("<outcome> <id,id>").
subject="spiffe://corp/agents/source-class-fixture"
outcome() {
  jq -r --arg subject "$subject" --arg control "$1" \
    '[.[]? // .assertions[] | select(.control == $control and .subject == $subject)][0]
    | "\(.outcome) \([.violations[]?.focus | sub("^agentce:event/"; "")] | unique | join(","))"' "$2"
}

# One engine over every event case, then every unknown-class catalog; returns non-zero on any mismatch. The three
# engines run side by side (each writes only its own out-<engine> and err-<engine>). The first mismatch in any engine
# stops all three (the $work/.stop marker): the gate has already failed, and a seeded-fault run turns red without
# assessing every case.
check_cases() {
  local engine="$1" dir name want want_exit out code got copy key
  for dir in "$work"/*/; do
    [ -e "$work/.stop" ] && return 1
    name="$(basename "$dir")"
    want="$(cat "$dir/expected")"
    want_exit="$(cat "$dir/exit")"
    out="$dir/out-$engine"
    code=0
    run "$engine" assess --bundle "$dir/bundle" --profile "$fixtures/applicability.yaml" \
      --domain "$fixtures/domain.linkml.yaml" --out "$out" \
      >/dev/null 2>"$dir/err-$engine" || code=$?
    if [ ! -f "$out/assertions.json" ]; then
      echo "source-class: $engine wrote no assertions.json for $name (exit $code)" >&2
      tail -3 "$dir/err-$engine" >&2
      touch "$work/.stop"
      return 1
    fi
    if [ "$code" != "$want_exit" ]; then
      echo "source-class: $engine $name exited $code, expected $want_exit" >&2
      touch "$work/.stop"
      return 1
    fi
    if [ -s "$out/quarantine.jsonl" ]; then
      echo "source-class: $engine quarantined events of $name, so the case did not read its input" >&2
      touch "$work/.stop"
      return 1
    fi
    got="$(outcome "$(cat "$dir/control")" "$out/assertions.json")"
    if [ "$got" != "$want" ]; then
      echo "source-class: $engine $name gave $(cat "$dir/control") '$got', expected '$want'" >&2
      touch "$work/.stop"
      return 1
    fi
  done
  # An unknown minimum or source_class_from is refused when the catalog loads, never read as none. Python writes the refusal to
  # stderr, TypeScript and Java to stdout; either carries the key.
  for copy in "$work"/.catalogs/*/; do
    [ -e "$work/.stop" ] && return 1
    name="$(basename "$copy")"
    read -r key want_exit <"$work/.refusals/$name"
    out="$work/.refusals/out-$engine-$name"
    code=0
    run "$engine" assess --bundle "$work/probe-a-attested-ingress-twin/bundle" --profile "$fixtures/applicability.yaml" \
      --domain "$fixtures/domain.linkml.yaml" --catalog-dir "$copy" --allow-unverified-catalog --out "$out" \
      >"$out.err" 2>&1 || code=$?
    if [ "$code" != "$want_exit" ] || ! grep -q "$key" "$out.err"; then
      echo "source-class: $engine assess with $name exited $code, expected $want_exit and $key" >&2
      tail -3 "$out.err" >&2
      touch "$work/.stop"
      return 1
    fi
  done
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
  echo "source-class: stopped at the first case an engine got wrong (above)" >&2
  exit 1
fi
# Catalog lint (Python only; TypeScript and Java do not implement it) refuses every unknown minimum and source_class_from with the same key.
for copy in "$work"/.catalogs/*/; do
  name="$(basename "$copy")"
  read -r key _ <"$work/.refusals/$name"
  code=0
  lint="$(run python catalog lint "$copy" 2>&1)" || code=$?
  if [ "$code" = 0 ] || ! grep -q "$key" <<<"$lint"; then
    echo "source-class: catalog lint did not refuse $name with $key (exit $code)" >&2
    status=1
  fi
done
cases="$(ls -d "$work"/*/ | wc -l | tr -d ' ')"
catalogs="$(ls -d "$work"/.catalogs/*/ | wc -l | tr -d ' ')"
[ "$status" -eq 0 ] && echo "source-class: $cases cases and $catalogs unknown catalog values, three engines agree with the expected outcome"
exit "$status"
