"""VG-CLI-OPTIONS: run every case in fixtures/cli_options/cases.json through the real Python,
TypeScript and Java CLIs and require each case's exit code and refusal key, with nothing written under
--out by a refused run.

A command-line option an engine ignores, drops or reads differently is a defect even when the verdict
is unaffected (MAINTAINER-INBOX row 153), so the table holds one row per option behaviour, and later
CLI items add their rows. A case may override the expectation for named engines; every override says
why and which item removes it. Each run starts in a fresh empty working directory, so a case can check
what a default path writes; a case can also require what the run writes, a token it names, stdout
identical across engines (usage text) and Python's --debug log records (18.106). The TypeScript and Java
engines must already be built (cli_options.sh builds them). Eight engine runs at a time.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
#: The case table: cases.json (VG-CLI-OPTIONS) unless the gate names another file in this folder
#: (top_level.json, assess.json, report.json, utility.json: one per gate), so each gate's seeded-fault
#: demo stays inside its CI lane.
FIXTURES = ROOT / "verification/gates/fixtures/cli_options"
ENGINES = ("python", "typescript", "java")
#: CI is cleared so Python's automatic junit (CI detected, no --emit/--for) never changes a row, and
#: COLUMNS fixed so argparse wraps its help text the same way on every terminal.
ENV = {k: v for k, v in os.environ.items() if k not in ("VIRTUAL_ENV", "CI")} | {
    "COLUMNS": "80"
}
CONFORMANCE = FIXTURES / "conformance"
#: Paths a case names inside a token, so its = form reads them too ({engine} and {out} are per run;
#: {B} as a whole token is the case file's B list). {A} and {A2} are the diff fixture's two assertion
#: sets; under conformance/, {C} an empty corpus, {C1} a one-project corpus whose ECS claim is full, and
#: three sample adapters directories whose conformance.py reports one adapter: {S} identical with its
#: round trip (claim full), {Sp} identical without it (claim partial), {Sn} no JSON at all (18.111).
PLACES = {
    "{root}": str(ROOT),
    "{A}": str(ROOT / "verification/gates/fixtures/diff/before/assertions.json"),
    "{A2}": str(ROOT / "verification/gates/fixtures/diff/after/assertions.json"),
    "{C}": str(CONFORMANCE / "corpus"),
    "{C1}": str(CONFORMANCE / "corpus-one"),
    "{S}": str(CONFORMANCE / "adapters-pass"),
    "{Sp}": str(CONFORMANCE / "adapters-partial"),
    "{Sn}": str(CONFORMANCE / "adapters-nojson"),
}
#: The fields of each --debug record, as Python's logsetup.JsonFormatter writes them.
DEBUG_RECORDS = [
    ("command.start", ["command", "event", "level", "logger", "ts"]),
    ("command.end", ["command", "event", "exit_code", "level", "logger", "ts"]),
]


def engine_cmds() -> dict[str, list[str]]:
    """Each engine's command line. Python is this interpreter's own `agentce` script (the check runs in
    engines/python's environment), so no case pays for a `uv run`."""
    jars = sorted((ROOT / "engines/java/build/libs").glob("agentce-*-all.jar"))
    if not jars:
        sys.exit(
            "cli_options_check: no Java jar under engines/java/build/libs (run cli_options.sh)"
        )
    # The JIT flags cut each short-lived JVM's CPU about threefold, which the seeded-fault demo pays
    # once per case per fault; they change compilation only, never what the jar does.
    return {
        "python": [str(Path(sys.executable).parent / "agentce")],
        # The npm bin, which loads dist/cli.js: the entry point npm users run (18.108).
        "typescript": ["node", str(ROOT / "engines/typescript/bin/agentce.js")],
        "java": [
            "java",
            "-XX:TieredStopAtLevel=1",
            "-XX:+UseSerialGC",
            "-jar",
            str(jars[-1]),
        ],
    }


def refusal_key(stdout: str) -> str | None:
    """The key of a --json error envelope (`key` in Python, `message_key` in TypeScript and Java,
    KD-017), or None when the run printed no envelope (an argparse usage error, or success)."""
    try:
        error = json.loads(stdout).get("error")
    except (ValueError, AttributeError):
        return None
    if not isinstance(error, dict):
        return None
    return error.get("key") or error.get("message_key")


def debug_log_problem(stderr: str, command: str) -> str | None:
    """Why stderr does not hold exactly Python's two --debug records naming the command, or None."""
    records = []
    for line in stderr.splitlines():
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict) and record.get("logger") == "agentce":
            if record.get("command") != command:
                return f"--debug record names command {record.get('command')!r}, want {command!r}"
            records.append((record.get("event"), list(record)))
    if records != DEBUG_RECORDS:
        return f"--debug records {records}, want {DEBUG_RECORDS}"
    return None


def envelope_problem(stdout: str, command: str) -> str | None:
    """Why stdout is not the --json envelope of a successful run of the command, or None."""
    try:
        envelope = json.loads(stdout)
    except ValueError:
        return "stdout is not a --json envelope"
    if not isinstance(envelope, dict) or (
        envelope.get("command"),
        envelope.get("exit_code"),
    ) != (command, 0):
        return f"envelope is not command {command!r} with exit_code 0"
    return None


def run_cli(
    argv: list[str], cwd: str, late_reader: float
) -> subprocess.CompletedProcess:
    """Run one command line. With late_reader > 0 nothing reads its stdout until it exits or that many
    seconds pass, so a pipe it leaves undrained at exit is cut whatever the machine's speed (18.110: a
    reader racing the writer let a cut rendering arrive whole on Linux)."""
    if not late_reader:
        return subprocess.run(argv, cwd=cwd, capture_output=True, text=True, env=ENV)
    with subprocess.Popen(
        argv,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=ENV,
    ) as proc:
        try:
            proc.wait(timeout=late_reader)
        except subprocess.TimeoutExpired:
            pass  # still writing into a full pipe: it is waiting for its reader, as it should
        stdout, stderr = proc.communicate()
    return subprocess.CompletedProcess(argv, proc.returncode, stdout, stderr)


def run(
    case: dict, engine: str, cmd: list[str], bundle_args: list[str]
) -> tuple[str | None, str]:
    """Run one case in one engine; return a failure line (None when it behaves as expected) and stdout."""
    want = case.get("engines", {}).get(
        engine, {"exit": case["exit"], "key": case["key"]}
    )
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "out"
        places = PLACES | {
            "{engine}": str(ROOT / "engines" / engine),
            "{out}": str(out),
        }
        argv: list[str] = []
        for token in case["argv"]:
            for t in bundle_args if token == "{B}" else [token]:
                for place, value in places.items():
                    t = t.replace(place, value)
                argv.append(t)
        # --json goes right after the first token by default, so a case can end on a value-less flag or
        # put tokens after `--`; "front" puts it first, "none" leaves it out (18.108, top-level rows).
        placed = {
            "after": argv[:1] + ["--json"] + argv[1:],
            "front": ["--json", *argv],
            "none": argv,
        }[case.get("json", "after")]
        p = run_cli([*cmd, *placed], tmp, case.get("late_reader", 0))
        written = (
            sorted(str(f.relative_to(out)) for f in out.rglob("*") if f.is_file())
            if out.is_dir()
            else []
        )
        entries = sorted(e.name for e in Path(tmp).iterdir())
        # 'has' and 'first_line' judge an engine that runs as the case expects (no override), 18.109.
        own = engine not in case.get("engines", {})
        # 'has' is one path or a list of paths, each of which the run must write (18.111).
        has = case.get("has", [])
        missing = [
            h
            for h in ([has] if isinstance(has, str) else has)
            if own and not (Path(tmp) / h).exists()
        ]
        line = None
        if own and "first_line" in case:
            target = Path(tmp) / case["first_line"]["file"]
            text = target.read_text("utf-8") if target.is_file() else ""
            line = text.splitlines()[0] if text else ""
        # 'file_says' names a file the run writes and a token it must hold (18.111).
        unsaid = None
        if own and "file_says" in case:
            target = Path(tmp) / case["file_says"]["file"]
            text = target.read_text("utf-8") if target.is_file() else ""
            unsaid = case["file_says"]["says"] not in text
        filled = [
            e for e in entries if any(f.is_file() for f in (Path(tmp) / e).rglob("*"))
        ]
    key = refusal_key(p.stdout)
    problems = []
    if "writes" in case:
        want_entries = [] if case["writes"] is None else [case["writes"]]
        if entries != want_entries or filled != want_entries:
            problems.append(
                f"wrote {entries} (with files: {filled}), want {want_entries}"
            )
    if missing:
        problems.append(f"did not write {missing}")
    if unsaid:
        problems.append(
            f"{case['file_says']['file']} does not name {case['file_says']['says']!r}"
        )
    if line is not None and line != case["first_line"]["line"]:
        problems.append(
            f"{case['first_line']['file']} starts {line!r}, want {case['first_line']['line']!r}"
        )
    # 'says' is one token or a list of tokens, each of which must appear (18.110).
    says = case.get("says", [])
    for token in [says] if isinstance(says, str) else says:
        if token not in p.stdout + p.stderr:
            problems.append(f"output does not name {token!r}")
    if case.get("debug_log") and (
        problem := debug_log_problem(p.stderr, case["debug_log"])
    ):
        problems.append(problem)
    if "envelope" in case and (problem := envelope_problem(p.stdout, case["envelope"])):
        problems.append(problem)
    if p.returncode != want["exit"]:
        problems.append(f"exit {p.returncode}, want {want['exit']}")
    if key != want["key"]:
        problems.append(f"key {key}, want {want['key']}")
    if want["exit"] == 3 and written:
        problems.append(
            f"a refused run wrote {len(written)} file(s) under --out: {written[:3]}"
        )
    if not problems:
        return None, p.stdout
    return f"FAIL {case['id']} [{engine}]: " + "; ".join(problems), p.stdout


def main() -> int:
    spec = json.loads(
        (FIXTURES / (sys.argv[1:] or ["cases.json"])[0]).read_text(encoding="utf-8")
    )
    cases = spec["cases"]
    ids = [c["id"] for c in cases]
    if len(ids) != len(set(ids)):
        print("FAIL cases.json repeats a case id")
        return 1
    for c in cases:
        if any(e not in ENGINES for e in c.get("engines", {})) or (
            c.get("engines") and not c.get("why")
        ):
            print(f"FAIL {c['id']}: an override must name a known engine and say why")
            return 1
    cmds = engine_cmds()
    # The slow runs (a case expected to exit 0 runs a whole assessment) start first.
    jobs = sorted(
        ((c, e) for c in cases for e in ENGINES), key=lambda job: job[0]["exit"] != 0
    )
    # The first failing run stops the runs not yet started: the gate is red either way, and the
    # seeded-fault demo, which runs it once per fault, stays inside its CI lane (18.107).
    stop = threading.Event()

    def run_unless_stopped(job: tuple[dict, str]) -> tuple[str | None, str] | None:
        if stop.is_set():
            return None
        result = run(job[0], job[1], cmds[job[1]], spec["B"])
        if result[0]:
            stop.set()
        return result

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(run_unless_stopped, jobs))
    failures = [r[0] for r in results if r and r[0]]
    skipped = results.count(None)
    stdouts: dict[str, set[str]] = {}
    for (case, _), result in zip(jobs, results):
        if result:
            stdouts.setdefault(case["id"], set()).add(result[1])
    if not failures:
        failures = [
            f"FAIL {case['id']}: stdout differs across engines"
            for case in cases
            if case.get("same_stdout") and len(stdouts[case["id"]]) != 1
        ]
    for line in sorted(failures):
        print(line)
    if skipped:
        print(
            f"(stopped at the first failure: {skipped} run(s) not yet started were skipped)"
        )
    print(
        f"{'FAIL' if failures else 'OK'} {spec['about'].split(':', 1)[0]}: {len(cases)} cases x {len(ENGINES)} engines, "
        f"{len(failures)} failure(s)"
    )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
