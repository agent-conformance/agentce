"""argv_parity_check - run every case of spec/cli/argv-cases.json through the three real CLIs (SPEC 8.5).

Each case runs in Python (`uv run agentce`), TypeScript (the npm bin) and Java (the runnable jar),
once as written and once with `--json` before the command word. The check fails when an engine's
exit code or key differs from the table, when the engines' keys or first message lines differ, when
a `help_after_command` case prints a different usage in one engine, or when a refused run writes a
file: the working directory stays empty but for the test keys, and a case with `unchanged` leaves
that report directory byte-identical. Each run's working directory holds copies of
`tools/fixtures/sign/test-key.pem` as `a.pem`, `b.pem` and `k.pem`, so a sign that failed to refuse a
repeated option would really sign and be caught (C14).

Usage:
    argv_parity_check.py              # needs `pnpm build` (TS) and `:assemble` (Java) run first
    argv_parity_check.py --self-test  # the comparator finds a planted key, message and help drift
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "spec" / "cli" / "argv-cases.json"
KEY_FILE = ROOT / "tools" / "fixtures" / "sign" / "test-key.pem"
KEYS = ("a.pem", "b.pem", "k.pem")
ENGINES = ("python", "typescript", "java")
TEXT_ERROR = re.compile(r"^(?:error \[([^\]]+)\]|([a-z][a-z0-9_]*\.[a-z0-9_.]+)): (.*)$")
#: COLUMNS fixed so argparse wraps its help text as the TypeScript and Java usage literals do.
ENV = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"} | {"COLUMNS": "80"}


@dataclass(frozen=True)
class Result:
    exit: int
    key: str | None
    message: str | None
    stdout: str
    wrote: tuple[str, ...] = ()


def _java_jar() -> Path:
    jars = sorted(
        (ROOT / "engines" / "java" / "build" / "libs").glob("agentce-*-all.jar"),
        key=lambda p: p.stat().st_mtime,
    )
    if not jars:
        raise SystemExit("java runnable jar is not built (run `./gradlew :assemble -q` first)")
    return jars[-1]


def _command(engine: str) -> list[str]:
    if engine == "python":
        return ["uv", "run", "--project", str(ROOT / "engines" / "python"), "--frozen", "agentce"]
    if engine == "typescript":
        return ["node", str(ROOT / "engines" / "typescript" / "bin" / "agentce.js")]
    return ["java", "-jar", str(_java_jar())]


def _digest(directory: Path) -> dict[str, str]:
    return {
        str(p.relative_to(directory)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(directory.rglob("*"))
        if p.is_file()
    }


def _read(proc: subprocess.CompletedProcess[str], run: Path) -> tuple[str | None, str | None]:
    """The refusal's key and cause, from the --json envelope or the first stderr line."""
    try:
        error = json.loads(proc.stdout).get("error") or {}
        key = error.get("key") or error.get("message_key")
        cause = error.get("cause") or error.get("detail")
        if key:
            return key, cause.replace(str(run), "<run>") if cause else cause
    except (ValueError, AttributeError):
        pass
    # Text mode: Python writes `error [key]: cause` to stderr, TypeScript and Java `key: cause` to
    # stdout (docs/verification.md, known differences).
    for line in (proc.stderr + "\n" + proc.stdout).splitlines():
        match = TEXT_ERROR.match(line.strip())
        if match:
            return match.group(1) or match.group(2), match.group(3).replace(str(run), "<run>")
    return None, None


def run_case(engine: str, case: dict, json_mode: bool, places: dict[str, str]) -> Result:
    with tempfile.TemporaryDirectory(prefix="argv-") as tmp:
        run = Path(tmp)
        work = run / "work"
        work.mkdir()
        for name in KEYS:
            shutil.copy(KEY_FILE, work / name)
        report = run / "report"
        shutil.copytree(places["{report}"], report)
        fill = {"{report}": str(report), "{space}": places["{space}"]}
        argv = []
        for token in case["argv"]:
            for name, path in fill.items():
                token = token.replace(name, path)
            argv.append(token)
        if json_mode:
            argv = ["--json", *argv]
        before = _digest(report)
        proc = subprocess.run(
            [*_command(engine), *argv],
            cwd=work,
            env=ENV,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        key, message = _read(proc, run)
        wrote = sorted(str(p.relative_to(work)) for p in work.rglob("*") if p.name not in KEYS)
        if case.get("unchanged") and _digest(report) != before:
            wrote.append("{report} changed")
        stdout = proc.stdout.replace(str(run), "<run>")
        return Result(proc.returncode, key, message, stdout, tuple(wrote))


def compare(case: dict, mode: str, results: dict[str, Result]) -> list[str]:
    """Every way the engines' results for one case break the table or differ from each other."""
    where = f"{case['id']} [{mode}]"
    problems = []
    for engine, got in results.items():
        if got.exit != case["exit"]:
            problems.append(f"{where} {engine}: exit {got.exit}, want {case['exit']}")
        if case["exit"] == 3 and got.key != case["key"]:
            problems.append(f"{where} {engine}: key {got.key}, want {case['key']}")
        if case["exit"] == 3 and got.wrote:
            problems.append(f"{where} {engine}: a refused run wrote {list(got.wrote)}")
    for field in ("key", "message"):
        seen = {e: getattr(r, field) for e, r in results.items()}
        if len(set(seen.values())) > 1:
            problems.append(f"{where}: {field} differs across engines: {seen}")
    if case["rule"] == "help_after_command" and len({r.stdout for r in results.values()}) > 1:
        problems.append(f"{where}: the usage text differs across engines")
    return problems


def _places(root: Path) -> dict[str, str]:
    report = root / "quickstart"
    subprocess.run(
        [*_command("python"), "quickstart", "--out", str(report)],
        env=ENV,
        capture_output=True,
        check=True,
    )
    space = root / "sp ace"
    space.mkdir()
    shutil.copytree(ROOT / "spec" / "catalogs" / "base" / "eu-ai-act", space / "cat")
    (space / "gaps.md").write_text("", encoding="utf-8")
    return {"{report}": str(report), "{space}": str(space)}


def check() -> int:
    cases = json.loads(CASES.read_text(encoding="utf-8"))["cases"]
    jobs = []
    for case in cases:
        modes = ["text"]
        if not case["argv"][0].startswith("-") and "--json" not in case["argv"]:
            modes.append("json")
        for mode in modes:
            for engine in case.get("engines", ENGINES):
                jobs.append((case, mode, engine))
    with tempfile.TemporaryDirectory(prefix="argv-parity-") as tmp:
        places = _places(Path(tmp))
        with ThreadPoolExecutor(max_workers=8) as pool:
            outputs = list(
                pool.map(lambda j: run_case(j[2], j[0], j[1] == "json", places), jobs)
            )
    grouped: dict[tuple[str, str], dict[str, Result]] = {}
    for (case, mode, engine), result in zip(jobs, outputs, strict=True):
        grouped.setdefault((case["id"], mode), {})[engine] = result
    by_id = {c["id"]: c for c in cases}
    problems = [p for (cid, mode), res in grouped.items() for p in compare(by_id[cid], mode, res)]
    for problem in problems:
        print(f"FAIL {problem}")
    print(
        f"{'FAIL' if problems else 'OK'} argv_parity_check: {len(cases)} cases, {len(jobs)} runs "
        f"across {len(ENGINES)} engines, {len(problems)} problem(s)"
    )
    return 1 if problems else 0


def self_test() -> int:
    refusal = {"id": "t-refuse", "rule": "repeated_option", "exit": 3, "key": "input.x"}
    help_case = {"id": "t-help", "rule": "help_after_command", "exit": 0, "key": None}
    good = Result(3, "input.x", "flag '--gaps' was given more than once.", "")
    usage = Result(0, None, None, "usage: agentce readiness\n  --gaps\n")
    planted = {
        "a different key": (refusal, {"java": Result(3, "input.y", good.message, "")}),
        "a different first line": (
            refusal,
            {"typescript": Result(3, "input.x", "flag '--gaps' needs a value.", "")},
        ),
        "a help text differing in its last line": (
            help_case,
            {"java": Result(0, None, None, "usage: agentce readiness\n  --gap\n")},
        ),
    }
    failures = []
    if compare(refusal, "text", dict.fromkeys(ENGINES, good)) or compare(
        help_case, "text", dict.fromkeys(ENGINES, usage)
    ):
        failures.append("identical results were reported as a difference")
    for name, (case, drift) in planted.items():
        base = good if case is refusal else usage
        if not compare(case, "text", dict.fromkeys(ENGINES, base) | drift):
            failures.append(f"the comparator missed {name}")
    for failure in failures:
        print(f"FAIL self-test: {failure}")
    print(f"{'FAIL' if failures else 'OK'} argv_parity_check --self-test: {len(planted)} planted drifts")
    return 1 if failures else 0


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        raise SystemExit(self_test())
    if sys.argv[1:]:
        raise SystemExit("usage: argv_parity_check.py [--self-test]")
    raise SystemExit(check())
