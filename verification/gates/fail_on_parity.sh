#!/usr/bin/env bash
# Build gate helper for VG-FAIL-ON-PARITY: `assess --fail-on <expression>` (SPEC §8.5, §7) gives the
# same result in all three engines. Two parts.
#
# (1) The real assess CLI of each engine over the scenarios of item 18.73's python-reference.md:
# matches and misses (F*), the insufficient-evidence project (F10*), Unicode whitespace (F12), a
# 3000-clause chain (D2), refusals (R*), a missing value and values that start with `-` (M1-M9), and
# the refusal order (O1-O4). Each engine must give Python's exit code, exit_status and --json fail_on,
# or Python's refusal (key, cause, fix) with no assertions.json written; a missing value must be
# argparse's own sentence. Some results are also checked against python-reference.md itself, so the
# gate fails when all three engines agree on a wrong answer.
#
# (2) The parser alone, one process per engine: Python's parse_fail_on and the TypeScript and Java
# `fail-on-check` seams read the same fixture (the auditor-view fixture's four assertions plus
# synthetic ones that cover every field) and the same expressions (R1-R31, F1-F14, D1, D2 and 6,000
# expressions fuzzed from a fixed seed), and must print byte-identical lines: each expression's
# refusal or its match vector.
#
# Every scenario runs from its engine's own engines/<name> with LC_ALL=C.UTF-8; the CLI runs go four
# at a time. Fixtures and outputs live under one mktemp directory, so a path in an error cause is the
# same string in each engine.
set -euo pipefail
export LC_ALL=C.UTF-8
start=$SECONDS
root="$(cd "$(dirname "$0")/../.." && pwd)"
F="$root/verification/gates/fixtures/auditor_view"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
mkdir -p "$work/argv" "$work/python" "$work/typescript" "$work/java"

(cd "$root/engines/java" && ./gradlew --no-daemon --quiet installDist)
(cd "$root/engines/typescript" && pnpm install --frozen-lockfile > /dev/null)
env -u VIRTUAL_ENV uv run --project "$root/corpus/generator" --frozen python \
  "$root/corpus/generator/generate.py" --set v1 --out "$work/corpus" > /dev/null
IE="$work/corpus/projects/credit/langgraph/insufficient-evidence"

# The scenarios (one NUL-separated argv file each, plus the job list) and the parser corpus's
# expressions. Written by Python so every expression is the same string whatever the shell.
python3 - "$work" "$F" "$IE" <<'PY'
import json
import random
import sys

work, F, IE = sys.argv[1:4]
A = ["--bundle", f"{F}/evidence", "--profile", f"{F}/applicability.yaml", "--domain",
     f"{F}/domain.linkml.yaml", "--catalog-dir", f"{F}/catalog", "--allow-unverified-catalog"]
I = ["--bundle", f"{IE}/evidence", "--profile", f"{IE}/applicability.yaml", "--domain",
     f"{IE}/domain.linkml.yaml"]
DEV = f"{F}/deviations.yaml"

# The expressions of python-reference.md (capture.sh), by scenario.
EXPR = {
    "F1": 'outcome=="non-conformant" and severity=="high"',
    "F2": 'severity=="critical"',
    "F3": 'control=="AUV-04"',
    "F4": 'control=="AUV-04" or control=="AUV-01" and severity=="low"',
    "F4b": 'control=="AUV-01" and severity=="low" or control=="AUV-02" and severity=="low"',
    "F5": 'rung=="0" and mode=="manual" and family=="AUV"',
    "F6": "subject=='spiffe://corp/agents/auditor-view-fixture' and control=='AUV-0\\4'",
    "F7": 'severity=="critical"',
    "F8": 'control=="none"',
    "F8b": 'control=="AUV-01"',
    "F9": 'outcome=="non-conformant"',
    "F9b": 'control=="AUV-01" and outcome=="non-conformant"',
    "F9c": 'outcome=="partial"',
    "F10b": 'control=="nope"',
    "F10c": 'outcome=="non-conformant"',
    "F12": '\x1c severity ==　"high"\t\n\x85',
    "F13": 'control=="é"',
    "F14": 'rung=="2.0"',
    "R1": "", "R2": "", "R3": "   ", "R4": 'foo=="x"', "R5": 'and=="x"', "R6": 'outcome "x"',
    "R7": 'outcome="x"', "R8": "outcome==x", "R9": 'outcome=="x', "R10": 'outcome=="x" severity',
    "R11": '__import__("os").system("id")', "R12": "outcome==`id`",
    "R13": 'outcome=="x" or os.system("id")', "R14": '(outcome=="x")', "R15": 'outcomé=="x"',
    "R16": "\U0001F600", "R17": 'outcome=="x"\x01', "R18": "​", "R19": '"\U0001F600" \x01',
    "R20": 'outcome=="x" and', "R21": "==", "R22": "outcome==", "R23": '1outcome=="x"',
    "R24": 'a²=="x"', "R25": 'outcome=="x\\', "R26": "it's", "R27": 'outcome==="x"',
    "R28": '"x"=="x"', "R29": 'outcome=="\U0001F600"\U0001F600', "R30": 'Outcome=="x"',
    "R31": 'outcome=="x" OR severity=="y"',
    "D1": " or ".join(['control=="x"'] * 3000),
    "D2": " or ".join(['control=="x"'] * 2999 + ['control=="AUV-01"']),
}
fo = lambda name: ["--fail-on", EXPR[name]]
SCENARIOS = {
    "F0": A,
    **{n: A + fo(n) for n in ("F1", "F2", "F3", "F4", "F4b", "F5", "F6", "F12", "F14", "D2",
                              "R1", "R4", "R11", "R19", "R24")},
    "F7": A + ["--fail-on=" + EXPR["F7"]],
    "F8": A + fo("F8b") + fo("F8"),
    "F8b": A + fo("F8") + fo("F8b"),
    **{n: A + ["--deviations", DEV] + fo(n) for n in ("F9", "F9b", "F9c")},
    "F10": I,
    "F10b": I + fo("F10b"),
    "F10c": I + fo("F10c"),
    "M1": A + ["--fail-on"],
    "M2": A[:2] + ["--fail-on"] + A[2:],
    "M3": A + ["--fail-on", "-x"],
    "M4": A + ["--fail-on=-x"],
    "M5": A + ["--fail-on", "--deviations", DEV],
    "M6": A + ["--deviations", "--fail-on", 'control=="x"'],
    "M7": A + ["--fail-on", "-1"],
    "M8": A + ["--fail-on", "-a b"],
    "M9": A + ["--fail-on", "-١"],
    "O1": ["--bundle", f"{work}/nope", "--profile", f"{F}/applicability.yaml", "--fail-on", "foo"],
    "O2": ["--bundle", f"{F}/evidence", "--profile", f"{work}/nope.yaml", "--fail-on", "foo"],
    "O3": A + ["--catalog", "nope@1", "--fail-on", "foo"],
    "O4": A + ["--deviations", f"{work}/nope.yaml", "--fail-on", "foo"],
}
jobs = []
for name, argv in SCENARIOS.items():
    with open(f"{work}/argv/{name}", "wb") as f:
        f.write(b"".join(a.encode("utf-8") + b"\0" for a in argv))
    jobs += [f"{engine} {name}" for engine in ("python", "typescript", "java")]
with open(f"{work}/jobs", "w", encoding="utf-8") as f:
    f.write("\n".join(jobs) + "\n")

# The fuzz corpus: 3,000 expressions of random pieces and 3,000 mutated well-formed ones, from a fixed
# seed. Every character is assigned in Unicode 15.0 (contract 18.73, Dispositions).
rng = random.Random(18073)
WS = [chr(c) for c in (0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x1C, 0x1D, 0x1E, 0x1F, 0x20, 0x85, 0xA0,
                       0x1680, *range(0x2000, 0x200B), 0x2028, 0x2029, 0x202F, 0x205F, 0x3000)]
assert len(WS) == 29 and all(c.isspace() for c in WS)
ASTRAL = ["\U0001F600", "\U0001D518", "\U00010400", "\U0001F004", "\U0001D7D9", "\U00020000",
          "\U0001F1E6", "\U000E0041"]
CONTROL = ["\x00", "\x01", "\x07", "\x1b", "\x7f", "\x80", "\x9f", "\x08"]
ZERO_WIDTH = ["​", "‌", "‍", "⁠", "﻿", "­", "᠎", "‎"]
QUOTES = ['"', "'", "\\", "`", '\\"', "\\'", "\\\\"]
LETTERS = ["a", "z", "Z", "_", "é", "ß", "Ω", "ж", "あ", "中", "ǅ", "ʰ", "İ", "ﬁ", "ª", "Å"]
DIGITS = ["0", "7", "²", "٣", "Ⅻ", "½", "३", "¹", "⑤"]
PUNCT = ["=", "==", "===", "(", ")", ".", "-", "!", "<", ",", ";", "&&", "||", "!=", "#", "$"]
FIELDS = ["control", "subject", "outcome", "severity", "family", "rung", "mode"]
WORDS = ["and", "or", "AND", "OR", "And", "Or", "not", "in", "Outcome", "controls", "__import__",
         "os", "x", "rung2", "_"] + FIELDS
VALUES = ["AUV-01", "AUV-02", "AUV-03", "AUV-04", "spiffe://corp/agents/auditor-view-fixture",
          "non-conformant", "not_assessed", "conformant", "partial", "high", "medium", "low",
          "critical", "AUV", "automated", "manual", "0", "2", "3", "10", "2.0", "", "x", "é",
          "XYZ-07", "XYZ", "hybrid", "a\"b'c\\d", "spiffe://other/agent é \U0001F600", "not_applicable"]


def piece():
    pools = (WS, ASTRAL, CONTROL, ZERO_WIDTH, QUOTES, LETTERS, DIGITS, PUNCT, WORDS)
    return rng.choice(rng.choice(pools))


def literal():
    value = rng.choice(VALUES) if rng.random() < 0.8 else "".join(piece() for _ in range(rng.randint(0, 3)))
    quote = rng.choice(['"', "'"])
    body = ""
    for ch in value:
        if ch in (quote, "\\") or rng.random() < 0.04:
            body += "\\"
        body += ch
    return quote + body + quote


def separator():
    return rng.choice([" "] * 8 + WS + ["", "  "])


def mutated():
    tokens = []
    for k in range(rng.randint(1, 5)):
        if k:
            tokens.append(rng.choice(["and", "or"]))
        tokens += [rng.choice(FIELDS), "==", literal()]
    for _ in range(rng.choice([0, 0, 1, 1, 1, 2, 3])):
        op = rng.choice(["drop", "swap", "insert", "replace", "upper", "dup"])
        at = rng.randrange(len(tokens))
        extra = rng.choice([rng.choice(WORDS), rng.choice(PUNCT), literal(), piece(),
                            rng.choice(["AND", "OR", "and", "or"])])
        if op == "drop" and len(tokens) > 1:
            del tokens[at]
        elif op == "swap":
            other = rng.randrange(len(tokens))
            tokens[at], tokens[other] = tokens[other], tokens[at]
        elif op == "insert":
            tokens.insert(at, extra)
        elif op == "replace":
            tokens[at] = extra
        elif op == "upper":
            tokens[at] = tokens[at].upper()
        elif op == "dup":
            tokens.insert(at, tokens[at])
    out = separator() if rng.random() < 0.2 else ""
    for i, token in enumerate(tokens):
        out += (separator() if i else "") + token
    return out + (separator() if rng.random() < 0.2 else "")


fuzz = ["".join(piece() + (separator() if rng.random() < 0.3 else "") for _ in range(rng.randint(1, 10)))
        for _ in range(3000)]
fuzz += [mutated() for _ in range(3000)]
named = [EXPR[k] for k in EXPR if k[0] in "RFD"]
with open(f"{work}/expressions.json", "w", encoding="utf-8") as f:
    json.dump({"named": len(named), "expressions": named + fuzz}, f)
PY

# run_one <engine> <scenario>: the engine's envelope in <engine>/<name>.json, its stderr in .err, its
# exit code in .code and its output directory at <engine>/<name>/. --out and --json go first so a
# trailing value flag with no value stays last.
run_one() {
  local engine="$1" name="$2" out="$work/$1/$2" code=0 arg
  local args=()
  while IFS= read -r -d '' arg; do args+=("$arg"); done < "$work/argv/$name"
  case "$engine" in
    python)
      (cd "$root/engines/python" && env -u VIRTUAL_ENV uv run --frozen agentce assess --out "$out" --json "${args[@]}") ;;
    typescript)
      (cd "$root/engines/typescript" && pnpm --silent agentce assess --out "$out" --json "${args[@]}") ;;
    java)
      (cd "$root/engines/java" && ./build/install/agentce/bin/agentce assess --out "$out" --json "${args[@]}") ;;
  esac > "$out.json" 2> "$out.err" || code=$?
  echo "$code" > "$out.code"
}
export -f run_one
export root work
xargs -P 4 -n 2 bash -c 'run_one "$@"' _ < "$work/jobs"

# The parser corpus fixture: Python's F0 assertions plus synthetic ones that change every field.
python3 - "$work" <<'PY'
import copy
import json
import sys

work = sys.argv[1]
assertions = json.load(open(f"{work}/python/F0/assertions.json", encoding="utf-8"))
base = assertions[0]
for fields in (
    {"control": "XYZ-07", "subject": "spiffe://other/agent é \U0001F600", "outcome": "partial",
     "severity": "critical", "family": "XYZ", "rung": 3, "mode": "hybrid"},
    {"control": "é", "subject": "", "outcome": "not_applicable", "severity": "low", "family": "é",
     "rung": 10, "mode": "manual"},
    {"control": "a\"b'c\\d", "subject": "x", "outcome": "conformant", "severity": "medium",
     "family": "AUV", "rung": 0, "mode": "automated"},
):
    synthetic = copy.deepcopy(base)
    synthetic.update(fields)
    assertions.append(synthetic)
expressions = json.load(open(f"{work}/expressions.json", encoding="utf-8"))["expressions"]
with open(f"{work}/fixture.json", "w", encoding="utf-8") as f:
    json.dump({"assertions": assertions, "expressions": expressions}, f)
PY

fixture="$work/fixture.json"
(cd "$root/engines/python" && env -u VIRTUAL_ENV uv run --frozen python - "$fixture" "$work/corpus.python" <<'PY'
import json
import sys

from agentce.assertions import Assertion
from agentce.canonical import canonical_string
from agentce.errors import InputError
from agentce.fail_on import parse_fail_on

fixture = json.load(open(sys.argv[1], encoding="utf-8"))
assertions = [Assertion.from_json(a) for a in fixture["assertions"]]
lines = []
for expression in fixture["expressions"]:
    try:
        predicate = parse_fail_on(expression)
        line = {"matched": [predicate(a) for a in assertions]}
    except InputError as exc:
        line = {"error": {"key": exc.key, "cause": exc.cause, "fix": exc.fix}}
    lines.append(canonical_string(line) + "\n")
with open(sys.argv[2], "w", encoding="utf-8", newline="\n") as f:
    f.write("".join(lines))
PY
) &
(cd "$root/engines/typescript" && pnpm --silent agentce fail-on-check "$fixture") > "$work/corpus.typescript" 2> "$work/corpus.typescript.err" &
# The seam prints with System.out, whose encoding follows the locale; macOS has no C.UTF-8 locale, so
# UTF-8 is named here rather than left to the platform.
(cd "$root/engines/java" && JAVA_OPTS=-Dstdout.encoding=UTF-8 ./build/install/agentce/bin/agentce fail-on-check "$fixture") \
  > "$work/corpus.java" 2> "$work/corpus.java.err" &
wait

python3 - "$work" "$((SECONDS - start))" <<'PY'
import json
import os
import sys

work, elapsed = sys.argv[1], sys.argv[2]
ENGINES = ("python", "typescript", "java")
FIX = ('use comparisons of the form field=="literal" joined by and/or, over: '
       "control, family, mode, outcome, rung, severity, subject.")
EMPTY = "the --fail-on expression is empty"
DASH = "unexpected character '-' at position 0"
UNKNOWN_FOO = "unknown field 'foo'; choose from: control, family, mode, outcome, rung, severity, subject"
# Python's results (python-reference.md): exit, exit_status, fail_on.matched (None: no --fail-on).
SUCCESSES = {
    "F0": (1, ["findings"], None), "F1": (1, ["findings"], 2), "F2": (0, ["ok"], 0),
    "F3": (1, ["findings"], 1), "F4": (1, ["findings"], 1), "F4b": (0, ["ok"], 0),
    "F5": (1, ["findings"], 1), "F6": (1, ["findings"], 1), "F7": (0, ["ok"], 0),
    "F8": (0, ["ok"], 0), "F8b": (1, ["findings"], 1), "F9": (1, ["findings"], 1),
    "F9b": (0, ["ok"], 0), "F9c": (1, ["findings"], 1),
    "F10": (2, ["findings", "insufficient_evidence"], None),
    "F10b": (2, ["insufficient_evidence"], 0),
    "F10c": (2, ["findings", "insufficient_evidence"], 7),
    "F12": (1, ["findings"], 2), "F14": (0, ["ok"], 0), "D2": (1, ["findings"], 1),
}
# Refusals: key and the end of Python's cause.
REFUSALS = {
    "R1": ("input.fail_on_invalid_expression", EMPTY),
    "R4": ("input.fail_on_invalid_expression", UNKNOWN_FOO),
    "R11": ("input.fail_on_invalid_expression", "unexpected character '(' at position 10"),
    "R19": ("input.fail_on_invalid_expression", "unexpected character '\\x01' at position 4"),
    "R24": ("input.fail_on_invalid_expression",
            "unknown field 'a²'; choose from: control, family, mode, outcome, rung, severity, subject"),
    "M4": ("input.fail_on_invalid_expression", DASH),
    "M7": ("input.fail_on_invalid_expression", DASH),
    "M8": ("input.fail_on_invalid_expression", DASH),
    "M9": ("input.fail_on_invalid_expression", DASH),
    "O1": ("input.bundle_not_a_directory", "is not an existing directory."),
    "O2": ("input.profile_not_a_file", "is not an existing file."),
    "O3": ("input.fail_on_invalid_expression", UNKNOWN_FOO),
    "O4": ("input.fail_on_invalid_expression", UNKNOWN_FOO),
}
# A value flag with no value: argparse's sentence and the TypeScript/Java fix.
NEEDS_VALUE = {
    "M1": ("--fail-on", "pass --fail-on <expression>."),
    "M2": ("--fail-on", "pass --fail-on <expression>."),
    "M3": ("--fail-on", "pass --fail-on <expression>."),
    "M5": ("--fail-on", "pass --fail-on <expression>."),
    "M6": ("--deviations", "pass --deviations <file>."),
}
failures = []


def check(ok, message):
    if not ok:
        failures.append(message)


def read(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def run(engine, s):
    """(exit code, exit_status, fail_on, error) of one engine's run of one scenario."""
    code = int(read(f"{work}/{engine}/{s}.code").strip())
    try:
        env = json.loads(read(f"{work}/{engine}/{s}.json"))
    except ValueError:
        env = {}
    # Python's envelope keys an error as key/cause, TypeScript's and Java's as message_key/detail.
    e = env.get("error") or {}
    error = {"key": e.get("key", e.get("message_key")), "cause": e.get("cause", e.get("detail")),
             "fix": e.get("fix")} if e else None
    return code, env.get("exit_status"), env.get("fail_on"), error


def wrote(engine, s):
    return os.path.exists(f"{work}/{engine}/{s}/assertions.json")


for s, (exit_code, status, matched) in SUCCESSES.items():
    ref = run("python", s)
    check(ref[:2] == (exit_code, status) and ref[3] is None and (ref[2] is None) == (matched is None)
          and (matched is None or ref[2].get("matched") == matched),
          f"{s}: python gives {ref}, python-reference.md says exit {exit_code} {status} matched {matched}")
    for engine in ENGINES[1:]:
        got = run(engine, s)
        check(got == ref, f"{s}: {engine} {got} != python {ref}")
for s, given in (("F2", 'severity=="critical"'), ("F7", 'severity=="critical"'), ("F8", 'control=="none"'),
                 ("F8b", 'control=="AUV-01"')):
    check((run("python", s)[2] or {}).get("expression") == given, f"{s}: python's fail_on.expression is not {given!r}")

for s, (key, tail) in REFUSALS.items():
    ref = run("python", s)
    check(ref[0] == 3 and ref[1] == ["input_error"] and ref[3] is not None and ref[3]["key"] == key
          and str(ref[3]["cause"]).endswith(tail)
          and (key != "input.fail_on_invalid_expression" or ref[3]["fix"] == FIX),
          f"{s}: python gives {ref}, python-reference.md says {key} ending {tail!r}")
    for engine in ENGINES:
        check(not wrote(engine, s), f"{s}: {engine} wrote assertions.json for a refused run")
    for engine in ENGINES[1:]:
        got = run(engine, s)
        check(got == ref, f"{s}: {engine} {got} != python {ref}")

for s, (flag, fix) in NEEDS_VALUE.items():
    sentence = f"argument {flag}: expected one argument"
    check(read(f"{work}/python/{s}.err").rstrip().endswith(sentence),
          f"{s}: python's usage error does not end {sentence!r}")
    for engine in ENGINES:
        check(run(engine, s)[0] == 3, f"{s}: {engine} exited {run(engine, s)[0]}, not 3")
        check(not wrote(engine, s), f"{s}: {engine} wrote assertions.json")
    for engine in ENGINES[1:]:
        e = run(engine, s)[3]
        check(e == {"key": "input.assess_flag_needs_value", "cause": sentence, "fix": fix},
              f"{s}: {engine} refused with {e}, not argparse's {sentence!r}")

# Part (2): the parser corpus.
expressions = json.load(open(f"{work}/expressions.json", encoding="utf-8"))
exprs, named = expressions["expressions"], expressions["named"]
outputs = {}
for engine in ENGINES:
    with open(f"{work}/corpus.{engine}", "rb") as f:
        outputs[engine] = f.read()
lines = outputs["python"].decode("utf-8").split("\n")[:-1]
check(len(lines) == len(exprs), f"corpus: python printed {len(lines)} lines for {len(exprs)} expressions")
for engine in ENGINES[1:]:
    if outputs[engine] == outputs["python"]:
        continue
    got = outputs[engine].decode("utf-8", errors="replace").split("\n")
    err = read(f"{work}/corpus.{engine}.err").strip()[-300:]
    failures.append(f"corpus: {engine}'s output differs from python's ({len(got) - 1} lines){': ' + err if err else ''}")
    shown = 0
    for i, line in enumerate(lines):
        other = got[i] if i < len(got) else "<missing>"
        if other != line and shown < 3:
            shown += 1
            failures.append(f"  expression #{i} {exprs[i][:120]!r}:\n    python: {line[:400]}\n    {engine}: {other[:400]}")
if len(lines) == len(exprs):
    parsed = [json.loads(line) for line in lines]
    accepted = [p for p in parsed if "matched" in p]
    # A corpus that only exercises refusals, or never matches, would not test the evaluator.
    check(len(accepted) >= 300 and sum(any(p["matched"]) for p in accepted) >= 100
          and len(parsed) - len(accepted) >= 3000,
          f"corpus: {len(accepted)} accepted ({sum(any(p['matched']) for p in accepted)} matching) and "
          f"{len(parsed) - len(accepted)} refused is not a corpus that tests both")
    check(all(p.get("error", {}).get("key", "input.fail_on_invalid_expression") == "input.fail_on_invalid_expression"
              for p in parsed), "corpus: a refusal carries a key other than input.fail_on_invalid_expression")

for f in failures:
    print(f"FAIL {f}")
total = len(SUCCESSES) + len(REFUSALS) + len(NEEDS_VALUE)
print(f"fail-on parity: {elapsed} s")
if failures:
    sys.exit(1)
print(f"fail-on parity: {total} CLI scenarios and {len(exprs)} parser expressions ({named} named, "
      f"{len(exprs) - named} fuzzed) agree across python, typescript and java")
PY
