#!/usr/bin/env bash
# Build gate helper for VG-DEVIATIONS-PARITY: `assess --deviations <register>` (SPEC §13.3.4) gives the
# same result in all three engines. Each engine runs the same scenarios through its real CLI -- no
# register (S0), the auditor-view fixture's register (S1), lint and shape refusals (S2-S9, S12),
# unquoted dates (S10), an empty register (S11), only an expired entry (S13), two subjects (S14), the
# `--deviations=<path>` spelling (S15), a missing value (S16, S23), hostile and mistaken register bytes
# (S17-S21), a `.` segment in the register path (S2D, S6D), the flag given twice, refused (S22, S22b) and an entry on a
# control that fails the SPEC §10.4 human-actor rule, which the register cannot waive (S24, S25, S26 through a
# third-party catalog whose sequence paths reach the failing decision from the action, and S27 through paths that
# repeat a predicate) -- and must give Python's exit code, error key, cause
# and fix, or write byte-identical assertions, OSCAL, activity and blind-spot files with the same
# limitations and register digest. Each engine's test-only `auditor-view` seam must also print the
# bytes Python's compute_auditor_view gives over the same assertions and register, in either order.
#
# Fixtures live under a mktemp directory shared by the three engines, so a register path in an error
# cause is the same string in each.
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
F="$root/verification/gates/fixtures/auditor_view"
P="$root/verification/gates/fixtures/project_view_deviations"
H="$root/verification/gates/fixtures/deviations_human_actor"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
regs="$work/regs"
mkdir -p "$regs" "$regs/s12dir" "$regs/o'brien"

(cd "$root/engines/java" && ./gradlew --no-daemon --quiet installDist)

# The scenario registers (python-reference.md of item 18.17a gives each one's Python result).
entry() { # entry <control> <owner> <approver> <granted> <expiry>
  printf '  - control: %s\n    rationale: "r"\n    compensating_control: "c"\n    owner: "%s"\n    approver: "%s"\n    granted: %s\n    expiry: %s\n' "$@"
}
header() { printf 'deviation_register_version: 1\ndeviations:\n'; }
a=user:a@example.com # owner and approver: reserved example identities (RFC 2606)
b=user:b@example.com
{ header; entry XYZ-99 "$a" "$b" '"2026-01-01T00:00:00Z"' '"2026-03-01T00:00:00Z"'; } > "$regs/s2.yaml"
{ header; entry AUV-04 "$a" "$b" '"2026-01-01T00:00:00Z"' '"2026-03-01T00:00:00Z"'; } > "$regs/s3.yaml"
{ header; entry AUV-01 "$a" "$b" '"2026-01-01T00:00:00Z"' '"2026-03-01T00:00:00Z"'
  entry AUV-01 "$a" "$a" '"2026-01-01T00:00:00Z"' '"2026-03-01T00:00:00Z"'; } > "$regs/s4.yaml"
printf -- '- control: AUV-01\n' > "$regs/s5.yaml"
printf 'deviations: 3\n' > "$regs/s6.yaml"
printf 'deviations:\n  - AUV-01\n' > "$regs/s7.yaml"
printf 'deviations: [\n' > "$regs/s9.yaml"
{ header; entry AUV-01 "$a" "$b" 2026-01-01 2026-06-01; } > "$regs/s10.yaml"
printf 'deviation_register_version: 1\ndeviations: []\n' > "$regs/s11.yaml"
{ header; entry AUV-02 "$a" "$b" '"2025-06-01T00:00:00Z"' '"2025-11-01T00:00:00Z"'; } > "$regs/s13.yaml"
printf 'deviation_register_version: 1\ndeviations:\n  - control: AUV-\xff\n' > "$regs/s17.yaml"
printf 'deviations:\n  - !!python/object:os.system {control: AUV-01}\n' > "$regs/s18.yaml"
{ printf 'deviation_register_version: 1\ndeviations:\n  - &e\n'
  entry AUV-01 "$a" "$b" '"2026-01-01T00:00:00Z"' '"2026-03-01T00:00:00Z"' | sed '1s/^  - /    /'
  printf '  - *e\n'; } > "$regs/s19.yaml"
python3 -c 'import sys; sys.stdout.write("deviations: " + "["*5000 + "]"*5000 + "\n")' > "$regs/s20.yaml"
printf 'deviations: 3\n' > "$regs/o'brien/reg.yaml"

base=(--bundle "$F/evidence" --profile "$F/applicability.yaml" --domain "$F/domain.linkml.yaml"
  --catalog-dir "$F/catalog" --allow-unverified-catalog)
two=(--bundle "$P/evidence" --profile "$P/applicability.yaml" --domain "$P/domain.linkml.yaml"
  --catalog-dir "$P/catalog" --allow-unverified-catalog)

scenario() {
  # scenario <name> <assess args...>: each engine's envelope in <engine>/<name>.json, its stderr in
  # .err, its exit code in .code and its output directory at <engine>/<name>/. --out and --json go
  # first so a trailing `--deviations` with no value stays last. The three engines run at once; each
  # writes only under its own directory, and each runs from its own engines/<name>, so a path an engine
  # reports relative to its working directory reads the same in all three.
  local name="$1" engine
  shift
  for engine in python typescript java; do
    mkdir -p "$work/$engine"
    (
      out="$work/$engine/$name"
      code=0
      case "$engine" in
        python)
          (cd "$root/engines/python" && env -u VIRTUAL_ENV uv run --frozen agentce assess --out "$out" --json "$@") ;;
        typescript)
          (cd "$root/engines/typescript" && pnpm --silent agentce assess --out "$out" --json "$@") ;;
        java)
          (cd "$root/engines/java" && ./build/install/agentce/bin/agentce assess --out "$out" --json "$@") ;;
      esac > "$out.json" 2> "$out.err" || code=$?
      echo "$code" > "$out.code"
    ) &
  done
  wait
}

scenario S0 "${base[@]}"
scenario S1 "${base[@]}" --deviations "$F/deviations.yaml"
for s in s2 s3 s4 s5 s6 s7 s9 s10 s11 s13 s17 s18 s19 s20; do
  scenario "$(printf %s "$s" | tr s S)" "${base[@]}" --deviations "$regs/$s.yaml"
done
scenario S8 "${base[@]}" --deviations "$regs/nope.yaml"
scenario S12 "${base[@]}" --deviations "$regs/s12dir"
scenario S14 "${two[@]}" --deviations "$P/deviations.yaml"
scenario S15 "${base[@]}" "--deviations=$F/deviations.yaml"
scenario S16 "${base[@]}" --deviations
scenario S21 "${base[@]}" --deviations "$regs/o'brien/reg.yaml"
# A `.` segment in the path: Python names the file as pathlib renders it in a shape error (S6D) and as
# typed in a lint error (S2D).
scenario S6D "${base[@]}" --deviations "$regs/./s6.yaml"
scenario S2D "${base[@]}" --deviations "$regs/./s2.yaml"
scenario S22 "${base[@]}" --deviations "$F/deviations.yaml" --deviations "$regs/s11.yaml"
scenario S22b "${base[@]}" --deviations "$regs/s11.yaml" --deviations "$F/deviations.yaml"
for kase in S24:approval S25:override; do
  d="$H/${kase#*:}"
  scenario "${kase%%:*}" --bundle "$d/bundle" --profile "$d/applicability.yaml" --domain "$d/domain.linkml.yaml" \
    --deviations "$d/deviations.yaml"
done
scenario S26 --bundle "$H/approval/bundle" --profile "$H/approval/applicability.yaml" \
  --domain "$H/approval/domain.linkml.yaml" --catalog-dir "$H/thirdparty/catalog" --allow-unverified-catalog \
  --deviations "$H/thirdparty/deviations.yaml"
scenario S27 --bundle "$H/repeat/bundle" --profile "$H/repeat/applicability.yaml" \
  --domain "$H/approval/domain.linkml.yaml" --catalog-dir "$H/repeat/catalog" --allow-unverified-catalog \
  --deviations "$H/repeat/deviations.yaml"
scenario S23 --bundle "$F/evidence" --deviations --profile "$F/applicability.yaml" \
  --domain "$F/domain.linkml.yaml" --catalog-dir "$F/catalog" --allow-unverified-catalog

# Python's own auditor.json for S1, the reference the seam's bytes must also equal.
(cd "$root/engines/python" && env -u VIRTUAL_ENV uv run --frozen agentce assess --out "$work/auditor" \
  --json "${base[@]}" --deviations "$F/deviations.yaml" --for auditor > /dev/null) || true

# The auditor-view seam's fixtures: Python's assertions and the loaded register for S1 and S14, in run
# order and reversed; Python's compute_auditor_view over each is the reference. XW is S1's assertions
# with crosswalk entries that put several controls under one clause, and one control under a clause
# twice, so the sort and dedupe of by_clause's control ids show in the bytes.
for s in S1:"$F/deviations.yaml" S14:"$P/deviations.yaml" XW:"$F/deviations.yaml"; do
  name="${s%%:*}"
  source="$name"
  [ "$name" = XW ] && source=S1
  (cd "$root/engines/python" && env -u VIRTUAL_ENV uv run --frozen python - \
    "$work/python/$source/assertions.json" "${s#*:}" "$work/seam-$name" "$name" <<'PY'
import json
import sys
from pathlib import Path

from agentce.assertions import Assertion
from agentce.auditor_view import compute_auditor_view
from agentce.canonical import canonical_string
from agentce.commands import _load_deviation_register

assertions = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
deviations = _load_deviation_register(Path(sys.argv[2]))
if sys.argv[4] == "XW":
    crosswalks = {
        "AUV-01": [{"framework": "iso-42001", "clause": "6.1"}, {"framework": "eu-ai-act", "clause": "Art. 9"}],
        "AUV-02": [{"framework": "eu-ai-act", "clause": "Art. 12"}, {"framework": "eu-ai-act", "clause": "Art. 9"}],
        "AUV-03": [{"framework": "eu-ai-act", "clause": "Art. 12"}, {"framework": "eu-ai-act", "clause": "Art. 12"}],
    }
    for a in assertions:
        if a["control"] in crosswalks:
            a["crosswalk"] = crosswalks[a["control"]]
for order, a, d in (("run", assertions, deviations), ("reversed", assertions[::-1], deviations[::-1])):
    Path(f"{sys.argv[3]}-{order}.fixture.json").write_text(
        json.dumps({"assertions": a, "deviations": d}), encoding="utf-8"
    )
    view = compute_auditor_view([Assertion.from_json(x) for x in a], d)
    Path(f"{sys.argv[3]}-{order}.python").write_text(canonical_string(view), encoding="utf-8")
PY
  )
  for order in run reversed; do
    fixture="$work/seam-$name-$order.fixture.json"
    (cd "$root/engines/typescript" && pnpm --silent seams auditor-view "$fixture") \
      > "$work/seam-$name-$order.typescript" || true
    (cd "$root/engines/java" && java -cp "$root/engines/java/build/install/agentce/lib/*" org.agentce.Seams auditor-view "$fixture") \
      > "$work/seam-$name-$order.java" || true
  done
done

python3 - "$work" <<'PY'
import json
import os
import sys

work = sys.argv[1]
ENGINES = ("python", "typescript", "java")
SENTENCE = "flag '--deviations' needs a value."
# A refusal's cause carries a parser's own text after these prefixes, which differs per engine.
PREFIX = {
    "S9": "carries a YAML construct the engine refuses to load",
    "S17": "is not valid UTF-8",
    "S18": "carries a YAML construct the engine refuses to load",
}
REFUSALS = ["S2", "S2D", "S3", "S4", "S5", "S6", "S6D", "S7", "S8", "S9", "S12", "S17", "S18", "S19", "S20", "S21", "S22", "S22b"]
SUCCESSES = ["S0", "S1", "S10", "S11", "S13", "S14", "S15"]
FILES = ("assertions.json", "oscal-ar.json", "activity.json", "blind-spots.json")
# SPEC §10.4: the register cannot waive the human-actor rule. Per scenario, the controls whose entry is not applied
# (non-conformant, no deviation, one limitation each) and the ones it still waives (partial, the control's id).
# S26's TPX-01 and TPX-04 reach the failing decision from the ToolCall that executes it through a sequence path, TPX-05
# and TPX-06 through a nested sh:node and sh:qualifiedValueShape, and TPX-02 reads it through an alternative path;
# TPX-03 reads no human-actor predicate (approvedByProxy is not approvedBy). S27's PRB-01 and PRB-03 repeat
# agentce:derivedFrom before agentce:approvedBy (PRB-03 across a nested sh:node), PRB-02 takes it once, and PRB-04 ends
# on approvedByProxy. Last, each scenario's exit code.
HUMAN_ACTOR = {
    "S24": (["CND-02", "OVS-01", "OVS-08", "RSK-02"], ["OVS-05"], 2),
    "S25": (["OVS-07"], [], 2),
    "S26": (["TPX-01", "TPX-02", "TPX-04", "TPX-05", "TPX-06"], ["TPX-03"], 1),
    "S27": (["PRB-01", "PRB-02", "PRB-03"], ["PRB-04"], 1),
}
NOT_WAIVABLE = (": deviation not applied because an approval, override or interrupt in these records fails the"
                " human-actor rule (SPEC §10.4), which no deviation can waive; the control remains non-conformant")
failures = []


def read(path, mode="r"):
    with open(path, mode, **({} if "b" in mode else {"encoding": "utf-8"})) as f:
        return f.read()


def code(engine, s):
    return int(read(f"{work}/{engine}/{s}.code").strip())


def envelope(engine, s):
    try:
        return json.loads(read(f"{work}/{engine}/{s}.json"))
    except ValueError:
        return {}


def error(engine, s):
    # Python's envelope keys an error as key/cause, TypeScript's and Java's as message_key/detail.
    e = envelope(engine, s).get("error") or {}
    cause = e.get("cause", e.get("detail"))
    if s in PREFIX and isinstance(cause, str) and PREFIX[s] in cause:
        cause = cause.split(PREFIX[s])[0] + PREFIX[s]
    return {"key": e.get("key", e.get("message_key")), "cause": cause, "fix": e.get("fix")}


def manifest(engine, s):
    m = json.loads(read(f"{work}/{engine}/{s}/manifest.json"))
    return m.get("limitations"), m.get("inputs", {}).get("deviation_register_digest")


def check(ok, message):
    if not ok:
        failures.append(message)


for s in REFUSALS:
    ref = (code("python", s), error("python", s))
    check(ref[0] == 3 and ref[1]["key"], f"{s}: python did not refuse with a key: {ref}")
    for engine in ENGINES[1:]:
        got = (code(engine, s), error(engine, s))
        check(got == ref, f"{s}: {engine} {got} != python {ref}")
        check(not os.path.exists(f"{work}/{engine}/{s}/assertions.json"), f"{s}: {engine} wrote assertions.json")

for s in ("S16", "S23"):
    for engine in ENGINES:
        check(code(engine, s) == 3, f"{s}: {engine} exited {code(engine, s)}, not 3")
        check(not os.path.exists(f"{work}/{engine}/{s}/assertions.json"), f"{s}: {engine} wrote assertions.json")
    # Every engine keys it with the grammar's needs-value cause (SPEC 8.5).
    for engine in ENGINES:
        e = error(engine, s)
        check(e["key"] == "input.assess_flag_needs_value" and e["cause"] == SENTENCE,
              f"{s}: {engine} refused with {e}")

for s in SUCCESSES:
    ref_code = code("python", s)
    check(ref_code in (0, 1), f"{s}: python exited {ref_code}: {read(f'{work}/python/{s}.err')[-400:]}")
    if ref_code not in (0, 1):
        continue
    names = FILES + (("project.json",) if s == "S14" else ())
    for engine in ENGINES[1:]:
        check(code(engine, s) == ref_code, f"{s}: {engine} exited {code(engine, s)}, python {ref_code}")
        for n in names:
            a, b = f"{work}/python/{s}/{n}", f"{work}/{engine}/{s}/{n}"
            check(os.path.exists(b) and read(a, "rb") == read(b, "rb"), f"{s}: {engine} {n} differs from python's")
        check(os.path.exists(f"{work}/{engine}/{s}/manifest.json") and manifest(engine, s) == manifest("python", s),
              f"{s}: {engine} manifest limitations/digest differ from python's")
    risks = json.loads(read(f"{work}/python/{s}/oscal-ar.json"))["assessment-results"]["results"][0].get("risks")
    digest = manifest("python", s)[1]
    if s == "S0":
        check(digest is None and risks is None, f"S0: a run with no register carries digest {digest} or risks")
    if s in ("S1", "S14", "S15"):
        check(bool(risks) and digest, f"{s}: the register was not applied (risks {risks}, digest {digest})")

for s, (kept, waived, exit_code) in HUMAN_ACTOR.items():
    for engine in ENGINES:
        check(code(engine, s) == exit_code, f"{s}: {engine} exited {code(engine, s)}, not {exit_code}: {read(f'{work}/{engine}/{s}.err')[-400:]}")
        if not os.path.exists(f"{work}/{engine}/{s}/manifest.json"):
            check(False, f"{s}: {engine} wrote no manifest.json")
            continue
        by_control = {a["control"]: a for a in json.loads(read(f"{work}/{engine}/{s}/assertions.json"))
                      if a["subject"] == "spiffe://corp/agents/cnd05-fixture"}
        expected = [(c, ("non-conformant", None)) for c in kept] + [(c, ("partial", c)) for c in waived]
        for c, want in expected:
            a = by_control.get(c, {})
            check((a.get("outcome"), a.get("deviation")) == want, f"{s}: {engine} {c} reads {a.get('outcome')}, {a.get('deviation')}, not {want}")
        lines = [x for x in manifest(engine, s)[0] or [] if x.endswith(NOT_WAIVABLE)]
        # One line per kept control, in assertion order.
        check(lines == [c + NOT_WAIVABLE for c in by_control if c in kept], f"{s}: {engine} not-applied limitations are {lines}")
        if engine != "python":
            for n in FILES:
                a, b = f"{work}/python/{s}/{n}", f"{work}/{engine}/{s}/{n}"
                check(os.path.exists(b) and read(a, "rb") == read(b, "rb"), f"{s}: {engine} {n} differs from python's")
            check(manifest(engine, s) == manifest("python", s), f"{s}: {engine} manifest limitations/digest differ from python's")

for engine in ENGINES:
    check(read(f"{work}/{engine}/S15/assertions.json", "rb") == read(f"{work}/{engine}/S1/assertions.json", "rb")
          and manifest(engine, "S15") == manifest(engine, "S1"),
          f"S15: {engine}'s --deviations=<path> run differs from its --deviations <path> run")
check("<script>alert(1)</script>" in read(f"{work}/python/S1/oscal-ar.json"),
      "S1: the hostile rationale does not reach the OSCAL risk statement verbatim")

for s in ("S1", "S14", "XW"):
    ref = read(f"{work}/seam-{s}-run.python")
    check(read(f"{work}/seam-{s}-reversed.python") == ref, f"seam {s}: python's view depends on input order")
    for engine in ENGINES[1:]:
        for order in ("run", "reversed"):
            got = read(f"{work}/seam-{s}-{order}.{engine}").rstrip("\n")
            check(got == ref, f"seam {s} ({order} order): {engine}'s auditor view differs from python's")
check(json.loads(read(f"{work}/seam-XW-run.python"))["by_clause"] == {
    "eu-ai-act": {"Art. 12": ["AUV-02", "AUV-03", "AUV-04"], "Art. 9": ["AUV-01", "AUV-02"]},
    "iso-42001": {"6.1": ["AUV-01"]},
}, "seam XW: python's by_clause is not the sorted, deduplicated index the fixture is built to give")
check(os.path.exists(f"{work}/auditor/auditor.json") and read(f"{work}/auditor/auditor.json") == read(f"{work}/seam-S1-run.python"),
      "seam S1: python's compute_auditor_view differs from the auditor.json `assess --for auditor` writes")

for f in failures:
    print(f"FAIL {f}")
total = len(REFUSALS) + 2 + len(SUCCESSES) + len(HUMAN_ACTOR)
if failures:
    sys.exit(1)
print(f"deviations parity: {total} scenarios and the auditor-view seam agree across python, typescript and java")
PY
