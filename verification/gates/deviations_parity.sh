#!/usr/bin/env bash
# Build gate helper for VG-DEVIATIONS-PARITY: `assess --deviations <register>` (SPEC §13.3.4) gives the
# same result in all three engines. Each engine runs the same scenarios through its real CLI -- no
# register (S0), the auditor-view fixture's register (S1), lint and shape refusals (S2-S9, S12),
# unquoted dates (S10), an empty register (S11), only an expired entry (S13), two subjects (S14), the
# `--deviations=<path>` spelling (S15), a missing value (S16, S23), hostile and mistaken register bytes
# (S17-S21) and the flag given twice (S22, S22b) -- and must give Python's exit code, error key, cause
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
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
regs="$work/regs"
mkdir -p "$regs" "$regs/s12dir" "$regs/o'brien"

(cd "$root/engines/java" && ./gradlew --no-daemon --quiet installDist)

# The scenario registers (python-reference.md of item 18.17a gives each one's Python result).
entry() { # entry <control> <owner> <approver> <granted> <expiry>
  printf '  - control: %s\n    rationale: "r"\n    compensating_control: "c"\n    owner: "%s"\n    approver: "%s"\n    granted: %s\n    expiry: %s\n' "$@"
}
header='deviation_register_version: 1\ndeviations:\n'
a=user:a@example.com # owner and approver: reserved example identities (RFC 2606)
b=user:b@example.com
{ printf "$header"; entry XYZ-99 "$a" "$b" '"2026-01-01T00:00:00Z"' '"2026-03-01T00:00:00Z"'; } > "$regs/s2.yaml"
{ printf "$header"; entry AUV-04 "$a" "$b" '"2026-01-01T00:00:00Z"' '"2026-03-01T00:00:00Z"'; } > "$regs/s3.yaml"
{ printf "$header"; entry AUV-01 "$a" "$b" '"2026-01-01T00:00:00Z"' '"2026-03-01T00:00:00Z"'
  entry AUV-01 "$a" "$a" '"2026-01-01T00:00:00Z"' '"2026-03-01T00:00:00Z"'; } > "$regs/s4.yaml"
printf -- '- control: AUV-01\n' > "$regs/s5.yaml"
printf 'deviations: 3\n' > "$regs/s6.yaml"
printf 'deviations:\n  - AUV-01\n' > "$regs/s7.yaml"
printf 'deviations: [\n' > "$regs/s9.yaml"
{ printf "$header"; entry AUV-01 "$a" "$b" 2026-01-01 2026-06-01; } > "$regs/s10.yaml"
printf 'deviation_register_version: 1\ndeviations: []\n' > "$regs/s11.yaml"
{ printf "$header"; entry AUV-02 "$a" "$b" '"2025-06-01T00:00:00Z"' '"2025-11-01T00:00:00Z"'; } > "$regs/s13.yaml"
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
  # writes only under its own directory.
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
          "$root/engines/java/build/install/agentce/bin/agentce" assess --out "$out" --json "$@" ;;
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
scenario S22 "${base[@]}" --deviations "$F/deviations.yaml" --deviations "$regs/s11.yaml"
scenario S22b "${base[@]}" --deviations "$regs/s11.yaml" --deviations "$F/deviations.yaml"
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
    (cd "$root/engines/typescript" && pnpm --silent agentce auditor-view "$fixture") \
      > "$work/seam-$name-$order.typescript" || true
    "$root/engines/java/build/install/agentce/bin/agentce" auditor-view "$fixture" \
      > "$work/seam-$name-$order.java" || true
  done
done

python3 - "$work" <<'PY'
import json
import os
import sys

work = sys.argv[1]
ENGINES = ("python", "typescript", "java")
SENTENCE = "argument --deviations: expected one argument"
# A refusal's cause carries a parser's own text after these prefixes, which differs per engine.
PREFIX = {
    "S9": "carries a YAML construct the engine refuses to load",
    "S17": "is not valid UTF-8",
    "S18": "carries a YAML construct the engine refuses to load",
}
REFUSALS = ["S2", "S3", "S4", "S5", "S6", "S7", "S8", "S9", "S12", "S17", "S18", "S19", "S20", "S21"]
SUCCESSES = ["S0", "S1", "S10", "S11", "S13", "S14", "S15", "S22", "S22b"]
FILES = ("assertions.json", "oscal-ar.json", "activity.json", "blind-spots.json")
DIGEST = {"S22": "sha256:1c8bd1ab", "S22b": "sha256:ec70a21e"}
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
    check(SENTENCE in read(f"{work}/python/{s}.err"), f"{s}: python's usage error does not say {SENTENCE!r}")
    for engine in ENGINES:
        check(code(engine, s) == 3, f"{s}: {engine} exited {code(engine, s)}, not 3")
        check(not os.path.exists(f"{work}/{engine}/{s}/assertions.json"), f"{s}: {engine} wrote assertions.json")
    for engine in ENGINES[1:]:
        e = error(engine, s)
        check(e["key"] == "input.assess_flag_needs_value" and e["cause"] == SENTENCE,
              f"{s}: {engine} refused with {e}")
    check(error("typescript", s) == error("java", s), f"{s}: typescript and java refuse differently")

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
    if s in DIGEST:
        check(str(digest).startswith(DIGEST[s]), f"{s}: digest {digest} is not the last register's")

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
total = len(REFUSALS) + 2 + len(SUCCESSES)
if failures:
    sys.exit(1)
print(f"deviations parity: {total} scenarios and the auditor-view seam agree across python, typescript and java")
PY
