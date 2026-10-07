"""psp_profile_check - every engine refuses a shape outside the Portable Shape Profile (VG-PSP-SHAPE-PROFILE-ENFORCED).

spec/rules/psp.md lists what a catalog shape may use, and spec/rules/psp_check.py checks it at authoring
time. This gate holds the three engines to the same answer when they load a catalog: each scenario in
tools/psp_profile_expected.json puts one shape into a three-control copy of the eu-ai-act base catalog and runs it through Python, TypeScript and Java ``assess --catalog-dir`` and
through Python's ``catalog lint``. The expected answers are pinned in that file, not read from
psp_check.py, so a change to the checker cannot move the gate with it:

    "OK"                 -> assess exits 0 and lint reports no catalog.shape.* problem
    "REFUSED <feature>"  -> assess exits 3 with the feature's key and <feature> in the message, and lint
                            reports "catalog.shape.<key>: ..." naming it
    "PARSE"              -> assess exits 3 with catalog.shape.parse_error (never internal.unexpected),
                            and lint reports it

Python runs under PYTHONHASHSEED=0 and =1. The gate rebuilds the TypeScript dist and the Java jar first,
calls each CLI directly, and stops at the first mismatch.

Usage:
    psp_profile_check.py               # the gate
    psp_profile_check.py --installed   # sh:closed and garbage Turtle through a freshly installed wheel,
                                       # npm package and jar (built here, installed under a temp dir)
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXPECTED = Path(__file__).with_name("psp_profile_expected.json")
SOURCE_CATALOG = ROOT / "spec/catalogs/base/eu-ai-act"
QUICK = ROOT / "corpus/quickstart"
CONTROLS = ("DAT-01", "DAT-02", "INC-02")
# Set at the first mismatch so scenarios still running stop between engine calls: a seeded-fault run
# then costs the rebuild and a few scenarios, not the whole table.
STOP = threading.Event()
FEATURE_KEYS = {
    "sh:sparql": "catalog.shape.sparql_forbidden",
    "sh:js": "catalog.shape.script_forbidden",
    "sh:javascript": "catalog.shape.script_forbidden",
}


def expected_key(expect: str) -> str | None:
    if expect == "OK":
        return None
    if expect == "PARSE":
        return "catalog.shape.parse_error"
    return FEATURE_KEYS.get(expect.split(" ", 1)[1], "catalog.shape.outside_profile")


def make_catalog(dest: Path, shapes: dict[str, str]) -> Path:
    """A three-control copy of the eu-ai-act catalog with ``shapes`` (control id -> Turtle) swapped in.
    INC-02 is there because the quickstart evidence evaluates it; without one evaluated control every
    engine stops with input.nothing_evaluated before the shape question is asked."""
    shutil.copytree(
        SOURCE_CATALOG,
        dest,
        ignore=lambda d, names: [
            n
            for n in names
            if Path(d).name in ("controls", "shapes") and Path(n).stem not in CONTROLS
        ],
    )
    for control, ttl in shapes.items():
        (dest / "shapes" / f"{control}.ttl").write_text(ttl, encoding="utf-8")
    return dest


def engine_commands(
    python: list[str], ts: list[str], java: list[str]
) -> dict[str, tuple[list[str], dict[str, str]]]:
    return {
        "python/seed0": (python, {"PYTHONHASHSEED": "0"}),
        "python/seed1": (python, {"PYTHONHASHSEED": "1"}),
        "typescript": (ts, {}),
        "java": (java, {}),
    }


def run(cmd: list[str], env: dict[str, str], cwd: Path) -> tuple[int, str]:
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=cwd,
        env={**os.environ, "NO_COLOR": "1", **env},
        timeout=240,
    )
    return proc.returncode, proc.stdout + proc.stderr


def assess_problem(
    engine: str,
    cmd: list[str],
    env: dict[str, str],
    catalog: Path,
    expect: str,
    work: Path,
) -> str | None:
    code, out = run(
        cmd
        + [
            "assess",
            "--bundle",
            str(QUICK / "evidence"),
            "--profile",
            str(QUICK / "applicability.yaml"),
            "--catalog-dir",
            str(catalog),
            "--out",
            str(work / f"out-{engine.replace('/', '-')}"),
            "--allow-unverified-catalog",
            "--json",
        ],
        env,
        work,
    )
    key = expected_key(expect)
    if key is None:
        return (
            None
            if code == 0
            else f"{engine}: expected exit 0, got {code}: {out[-400:]}"
        )
    if code != 3 or key not in out:
        return f"{engine}: expected exit 3 and {key}, got {code}: {out[-400:]}"
    if expect.startswith("REFUSED") and expect.split(" ", 1)[1] not in out:
        return f"{engine}: the message does not name {expect.split(' ', 1)[1]!r}: {out[-400:]}"
    return None


def lint_problem(
    python: list[str], target: Path, expect: str, prefix: str, work: Path, shown: str
) -> str | None:
    _, out = run(python + ["catalog", "lint", str(target), "--json"], {}, work)
    try:
        problems = [
            str(p) for p in json.loads(out[: out.rindex("}") + 1]).get("problems", [])
        ]
    except ValueError:
        return f"lint: no JSON result: {out[-400:]}"
    shape = [p for p in problems if p.startswith(f"{prefix}catalog.shape.")]
    key = expected_key(expect)
    if key is None:
        return f"lint: unexpected shape problem {shape}" if shape else None
    want = f"{prefix}{key}: "
    if not any(p.startswith(want) for p in shape):
        return f"lint: no problem starting {want!r} in {problems}"
    if expect.startswith("REFUSED") and not any(
        expect.split(" ", 1)[1] in p for p in shape
    ):
        return f"lint: the problem does not name {expect.split(' ', 1)[1]!r}: {shape}"
    if key in (
        "catalog.shape.outside_profile",
        "catalog.shape.parse_error",
    ) and not any(shown in p for p in shape):
        return f"lint: the problem does not name the shape file {shown}: {shape}"
    return None


def probe_problem(python_exe: str, ttl: str, expect: str, work: Path) -> str | None:
    shapes = work / "shapes.ttl"
    shapes.write_text(ttl, encoding="utf-8")
    script = (
        "import sys\nfrom pathlib import Path\nfrom agentce import probe\nfrom agentce.errors import InputError\n"
        "try:\n    probe.parse_shape_file(Path(sys.argv[1]))\nexcept InputError as e:\n    print(e.key, e.cause)\n"
        "else:\n    print('OK')\n"
    )
    _, out = run([python_exe, "-c", script, str(shapes)], {}, work)
    key = expected_key(expect)
    if key is None:
        return None if out.strip() == "OK" else f"probe: expected OK, got {out[-300:]}"
    if not out.startswith(key) or (
        expect.startswith("REFUSED") and expect.split(" ", 1)[1] not in out
    ):
        return f"probe: expected {expect}, got {out[-300:]}"
    return None


def check_scenario(
    name: str,
    row: dict[str, object],
    engines: dict[str, tuple[list[str], dict[str, str]]],
    python_exe: str,
) -> str | None:
    expect = str(row["expect"])
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        if row.get("surface") == "probe":
            problem = probe_problem(python_exe, str(row["ttl"]), expect, work)
            return problem and f"{name}: {problem}"
        shapes = {
            str(k): str(v)
            for k, v in dict(row.get("shapes") or {"DAT-01": row["ttl"]}).items()
        }
        python = engines["python/seed0"][0]
        if row.get("surface") == "lint-two-catalogs":
            parent = work / "catalogs"
            make_catalog(parent / "good", {})
            make_catalog(parent / "bad", shapes)
            problem = lint_problem(
                python, parent, expect, "bad: ", work, f"shapes/{max(shapes)}.ttl"
            )
            return problem and f"{name}: {problem}"
        catalog = make_catalog(work / "cat", shapes)
        for engine, (cmd, env) in engines.items():
            if STOP.is_set():
                return None
            problem = assess_problem(engine, cmd, env, catalog, expect, work)
            if problem:
                return f"{name}: {problem}"
        problem = lint_problem(
            python, catalog, expect, "", work, f"shapes/{max(shapes)}.ttl"
        )
        if problem:
            return f"{name}: {problem}"
    return None


def build_engines() -> None:
    subprocess.run(
        ["pnpm", "build"],
        cwd=ROOT / "engines/typescript",
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["./gradlew", "--no-daemon", "-q", "runnableJar"],
        cwd=ROOT / "engines/java",
        check=True,
        capture_output=True,
    )
    if not (ROOT / "engines/python/.venv/bin/agentce").exists():
        subprocess.run(
            ["uv", "sync", "--frozen"],
            cwd=ROOT / "engines/python",
            check=True,
            capture_output=True,
        )


def newest_jar(directory: Path) -> Path:
    return max(directory.glob("agentce-*-all.jar"), key=lambda p: p.stat().st_mtime)


def gate() -> int:
    table = json.loads(EXPECTED.read_text(encoding="utf-8"))
    rows: dict[str, dict[str, object]] = table["scenarios"]
    if len(rows) != table["count"]:
        print(
            f"psp-profile: {len(rows)} scenarios, but the table pins {table['count']}",
            file=sys.stderr,
        )
        return 1
    build_engines()
    venv = ROOT / "engines/python/.venv/bin"
    engines = engine_commands(
        [str(venv / "agentce")],
        ["node", str(ROOT / "engines/typescript/dist/cli.js")],
        ["java", "-jar", str(newest_jar(ROOT / "engines/java/build/libs"))],
    )
    ran = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=os.cpu_count() or 4) as pool:
        # Refusals first: they stop at catalog load and are cheap, and they are where a seeded fault
        # shows, so a faulted run stops before the slower full assessments of the OK rows.
        order = sorted(rows, key=lambda n: rows[n]["expect"] == "OK")
        futures = {
            pool.submit(check_scenario, n, rows[n], engines, str(venv / "python")): n
            for n in order
        }
        for future in concurrent.futures.as_completed(futures):
            problem = future.result()
            ran += 1
            if problem:
                print(f"psp-profile: FAIL {problem}", file=sys.stderr)
                STOP.set()
                pool.shutdown(wait=False, cancel_futures=True)
                return 1
    print(
        f"psp-profile: OK, {ran} scenarios agree in Python (two hash seeds), TypeScript, Java and catalog lint"
    )
    return 0 if ran == table["count"] else 1


def installed() -> int:
    table = json.loads(EXPECTED.read_text(encoding="utf-8"))["scenarios"]
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        subprocess.run(
            ["uv", "build", "--wheel", "--out-dir", str(t / "dist")],
            cwd=ROOT / "engines/python",
            check=True,
            capture_output=True,
        )
        subprocess.run(["uv", "venv", str(t / "venv")], check=True, capture_output=True)
        wheel = next((t / "dist").glob("agentce-*.whl"))
        subprocess.run(
            [
                "uv",
                "pip",
                "install",
                "--python",
                str(t / "venv/bin/python"),
                str(wheel),
            ],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["pnpm", "build"],
            cwd=ROOT / "engines/typescript",
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["pnpm", "pack", "--pack-destination", str(t)],
            cwd=ROOT / "engines/typescript",
            check=True,
            capture_output=True,
        )
        tarball = next(t.glob("agentce-*.tgz"))
        subprocess.run(
            ["npm", "install", "--prefix", str(t / "node"), str(tarball)],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["./gradlew", "--no-daemon", "-q", "runnableJar"],
            cwd=ROOT / "engines/java",
            check=True,
            capture_output=True,
        )
        jar = shutil.copy(
            newest_jar(ROOT / "engines/java/build/libs"), t / "agentce.jar"
        )
        engines = engine_commands(
            [str(t / "venv/bin/agentce")],
            [str(t / "node/node_modules/.bin/agentce")],
            ["java", "-jar", str(jar)],
        )
        for name in ("baseline", "closed", "garbage-turtle"):
            catalog = make_catalog(
                t / f"cat-{name}", {"DAT-01": str(table[name]["ttl"])}
            )
            for engine, (cmd, env) in engines.items():
                problem = assess_problem(
                    engine, cmd, env, catalog, str(table[name]["expect"]), t
                )
                print(
                    f"installed {engine:13} {name:15} {'FAIL ' + problem if problem else 'ok'}"
                )
                if problem:
                    return 1
    print(
        "psp-profile: OK, the installed wheel, npm package and jar refuse sh:closed and garbage Turtle"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--installed",
        action="store_true",
        help="run against freshly installed artifacts",
    )
    args = parser.parse_args(argv)
    return installed() if args.installed else gate()


if __name__ == "__main__":
    sys.exit(main())
