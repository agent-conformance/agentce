"""VG-CLI-OPTIONS: run every case in fixtures/cli_options/cases.json through the real Python,
TypeScript and Java CLIs and require each case's exit code and refusal key, with nothing written under
--out by a refused run.

A command-line option an engine ignores, drops or reads differently is a defect even when the verdict
is unaffected (MAINTAINER-INBOX row 153), so the table holds one row per option behaviour, and later
CLI items add their rows. A case may override the expectation for named engines; every override says
why and which item removes it. The TypeScript and Java engines must already be built (cli_options.sh
builds them). Runs from the repository root, eight engine runs at a time.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CASES = ROOT / "verification/gates/fixtures/cli_options/cases.json"
ENGINES = ("python", "typescript", "java")
#: CI is cleared so Python's automatic junit (CI detected, no --emit/--for) never changes a row.
ENV = {k: v for k, v in os.environ.items() if k not in ("VIRTUAL_ENV", "CI")}


def engine_cmd(name: str) -> list[str]:
    if name == "python":
        return ["uv", "run", "--frozen", "--project", "engines/python", "agentce"]
    if name == "typescript":
        return ["node", "engines/typescript/dist/cli.js"]
    jars = sorted((ROOT / "engines/java/build/libs").glob("agentce-*-all.jar"))
    if not jars:
        sys.exit(
            "cli_options_check: no Java jar under engines/java/build/libs (run cli_options.sh)"
        )
    return ["java", "-jar", str(jars[-1])]


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


def run(case: dict, engine: str, bundle_args: list[str]) -> str | None:
    """Run one case in one engine; return a failure line, or None when it behaves as expected."""
    want = case.get("engines", {}).get(
        engine, {"exit": case["exit"], "key": case["key"]}
    )
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "out"
        argv: list[str] = []
        for token in case["argv"]:
            argv += (
                bundle_args if token == "{B}" else [token.replace("{out}", str(out))]
            )
        # --json goes after the command: TypeScript and Java read it only there.
        p = subprocess.run(
            [*engine_cmd(engine), *argv, "--json"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            env=ENV,
        )
        written = (
            sorted(str(f.relative_to(out)) for f in out.rglob("*") if f.is_file())
            if out.is_dir()
            else []
        )
    key = refusal_key(p.stdout)
    problems = []
    if p.returncode != want["exit"]:
        problems.append(f"exit {p.returncode}, want {want['exit']}")
    if key != want["key"]:
        problems.append(f"key {key}, want {want['key']}")
    if want["exit"] == 3 and written:
        problems.append(
            f"a refused run wrote {len(written)} file(s) under --out: {written[:3]}"
        )
    if not problems:
        return None
    return f"FAIL {case['id']} [{engine}]: " + "; ".join(problems)


def main() -> int:
    spec = json.loads(CASES.read_text(encoding="utf-8"))
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
    jobs = [(c, e) for c in cases for e in ENGINES]
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda job: run(job[0], job[1], spec["B"]), jobs))
    failures = [r for r in results if r]
    for line in failures:
        print(line)
    print(
        f"{'FAIL' if failures else 'OK'} VG-CLI-OPTIONS: {len(cases)} cases x {len(ENGINES)} engines, "
        f"{len(failures)} failure(s)"
    )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
