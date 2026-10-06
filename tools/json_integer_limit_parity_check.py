"""json_integer_limit_parity_check - an integer literal over 4300 digits is invalid JSON in all three
engines, and in Python under every interpreter setting (item 18.71).

CPython's ``json.loads`` refuses such a literal with a bare ``ValueError`` and only under the default
``sys.int_max_str_digits``; Java's ``Json.parse`` and TypeScript's ``parseJson`` refuse it as invalid
JSON. This check drives the built engines the way a user does:

* ``validate --bundle --json --out``: a copy of the quickstart bundle with one extra evidence line. A
  5000- or 4301-digit literal is quarantined ``schema_invalid`` with a detail starting ``invalid JSON``;
  4300 digits (with or without a sign) parse. Every engine gives the same exit and counts, and the
  5000-digit line gives what a malformed-JSON line gives.
* ``assess --bundle``: the 5000-digit line is quarantined and the run finishes the same way in all three.
* ``report --validate --json``: ``assertions.json``, ``manifest.json`` or ``runtime_drift.jsonl``
  replaced by a 5000-digit literal is exactly one problem naming the file as invalid JSON, and a
  4300-digit ``runtime_drift.jsonl`` line is valid. Python's text equals Java's.
* Python repeats every leg under ``PYTHONINTMAXSTRDIGITS=0`` and ``=640`` and must give the default's
  result.

Usage (from the repository root, after ``pnpm build`` in engines/typescript and ``./gradlew
installDist`` in engines/java):
    json_integer_limit_parity_check.py
    json_integer_limit_parity_check.py --self-test
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
QUICKSTART = ROOT / "corpus" / "quickstart"
JAVA_ENGINE = ROOT / "engines" / "java"
BENIGN = "events/_benign-quarantine.jsonl"
BIG = "9" * 5000
#: Each extra evidence line, and whether it must be refused as invalid JSON.
LINES = {
    "5000 digits": ('{"n": %s}' % BIG, True),
    "4301 digits": ('{"n": %s}' % ("9" * 4301), True),
    "4300 digits": ('{"n": %s}' % ("9" * 4300), False),
    "-4300 digits": ('{"n": -%s}' % ("9" * 4300), False),
    "malformed JSON": ('{"n": }', True),
}
ARTIFACTS = {
    "assertions.json": "assertions.json: invalid JSON (",
    "manifest.json": "manifest.json: invalid JSON (",
    "runtime_drift.jsonl": "runtime_drift.jsonl: line 1 is not valid JSON (",
}
#: Python's interpreter settings: the default, no limit, and the lowest limit allowed.
PY_SETTINGS = ("default", "0", "640")


def engine_argv(engine: str) -> tuple[list[str], Path]:
    if engine == "python":
        return [
            "uv",
            "run",
            "--frozen",
            "--project",
            str(ROOT / "engines" / "python"),
            "agentce",
        ], ROOT
    if engine == "typescript":
        return ["node", str(ROOT / "engines" / "typescript" / "dist" / "cli.js")], ROOT
    return [
        str(JAVA_ENGINE / "build" / "install" / "agentce" / "bin" / "agentce")
    ], JAVA_ENGINE


def run(engine: str, args: list[str], setting: str = "default") -> tuple[int, str]:
    argv, cwd = engine_argv(engine)
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("VIRTUAL_ENV", "PYTHONINTMAXSTRDIGITS")
    }
    if setting != "default":
        env["PYTHONINTMAXSTRDIGITS"] = setting
    proc = subprocess.run(argv + args, capture_output=True, text=True, cwd=cwd, env=env)
    return proc.returncode, proc.stdout


def runs() -> list[tuple[str, str]]:
    """Every (engine, setting) pair: Python under each setting, TypeScript and Java once."""
    return [("python", s) for s in PY_SETTINGS] + [
        ("typescript", "default"),
        ("java", "default"),
    ]


def write_bundle(dest: Path, line: str) -> Path:
    bundle = dest / "bundle"
    shutil.copytree(QUICKSTART / "evidence", bundle)
    events = bundle / BENIGN
    events.write_text(events.read_text("utf-8") + line + "\n", "utf-8")
    manifest = json.loads((bundle / "manifest.json").read_text("utf-8"))
    for entry in manifest["files"]:
        if entry["path"] == BENIGN:
            entry["sha256"] = hashlib.sha256(events.read_bytes()).hexdigest()
    (bundle / "manifest.json").write_text(json.dumps(manifest, indent=2), "utf-8")
    return bundle


def envelope(stdout: str) -> dict[str, Any]:
    try:
        value = json.loads(stdout)
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def validate_outcome(code: int, stdout: str, out: Path) -> dict[str, Any]:
    """What `validate --bundle` showed: exit, counts, and the one schema_invalid record's detail."""
    data = envelope(stdout)
    data = data.get("data", data)
    details = []
    quarantine = out / "quarantine.jsonl"
    if quarantine.is_file():
        for raw in quarantine.read_text("utf-8").splitlines():
            if raw.strip():
                record = json.loads(raw)
                if record.get("reason") == "schema_invalid":
                    details.append(str(record.get("detail", "")))
    return {
        "exit": code,
        "counts": {
            k: data.get(k) for k in ("accepted", "quarantined", "quarantine_by_reason")
        },
        "details": details,
    }


def check_validate(
    label: str, refused: bool, outcomes: dict[str, dict[str, Any]]
) -> list[str]:
    """The validate leg's rules, over each run's outcome (keyed `engine` or `python@setting`)."""
    fails = []
    first_key = next(iter(outcomes))
    for key, got in outcomes.items():
        if (got["exit"], got["counts"]) != (
            outcomes[first_key]["exit"],
            outcomes[first_key]["counts"],
        ):
            fails.append(
                f"validate {label}: {key} gave {got['exit']} {got['counts']}, {first_key} gave "
                f"{outcomes[first_key]['exit']} {outcomes[first_key]['counts']}"
            )
        if len(got["details"]) != 1:
            fails.append(
                f"validate {label}: {key} wrote {len(got['details'])} schema_invalid records, expected 1"
            )
        elif got["details"][0].startswith("invalid JSON") != refused:
            fails.append(
                f"validate {label}: {key} detail {got['details'][0][:80]!r}, expected "
                f"{'an' if refused else 'no'} 'invalid JSON' detail"
            )
    return fails


def check_report(
    label: str, prefix: str | None, outcomes: dict[str, tuple[int, Any]]
) -> list[str]:
    """`prefix` None: the folder must be valid; else exactly one problem with that prefix and `4300`
    (`malformed` controls carry no `4300`, so the caller passes the prefix and checks it alone)."""
    fails = []
    for key, (code, data) in outcomes.items():
        problems = data.get("problems") if isinstance(data, dict) else None
        valid = data.get("valid") if isinstance(data, dict) else None
        if prefix is None:
            if (code, valid, problems) != (0, True, []):
                fails.append(
                    f"report {label}: {key} gave exit {code} valid {valid} {problems}, expected a valid folder"
                )
            continue
        too_long = "malformed" not in label
        ok = (
            code == 3
            and valid is False
            and isinstance(problems, list)
            and len(problems) == 1
            and problems[0].startswith(prefix)
            and (("4300" in problems[0]) == too_long)
        )
        if not ok:
            fails.append(
                f"report {label}: {key} gave exit {code} valid {valid} {problems}, expected one problem {prefix!r}"
            )
    return fails


def same_python_text(label: str, outcomes: dict[str, tuple[int, Any]]) -> list[str]:
    """Python, under each setting, prints the problem Java prints."""
    java = outcomes["java"][1].get("problems")
    return [
        f"report {label}: {key} problems {data.get('problems')} differ from java's {java}"
        for key, (_, data) in outcomes.items()
        if key.startswith("python") and data.get("problems") != java
    ]


def key_of(engine: str, setting: str) -> str:
    return engine if setting == "default" else f"{engine}@{setting}"


def check_engines(tmp: Path) -> list[str]:
    fails: list[str] = []
    for label, (line, refused) in LINES.items():
        bundle = write_bundle(tmp / f"bundle-{len(line)}-{label[0]}", line)
        outcomes = {}
        for engine, setting in runs():
            out = tmp / f"validate-{key_of(engine, setting)}-{len(line)}-{label[0]}"
            code, stdout = run(
                engine,
                ["validate", "--bundle", str(bundle), "--json", "--out", str(out)],
                setting,
            )
            outcomes[key_of(engine, setting)] = validate_outcome(code, stdout, out)
        fails += check_validate(label, refused, outcomes)
        if label == "5000 digits":
            big = outcomes["python"]
        if label == "malformed JSON" and (big["exit"], big["counts"]) != (
            outcomes["python"]["exit"],
            outcomes["python"]["counts"],
        ):
            fails.append(
                f"validate: the 5000-digit line gave {big['counts']}, the malformed line {outcomes['python']['counts']}"
            )

    bundle = write_bundle(tmp / "assess-bundle", LINES["5000 digits"][0])
    assessed = {}
    for engine, setting in runs():
        out = tmp / f"assess-{key_of(engine, setting)}"
        code, _ = run(
            engine,
            [
                "assess",
                "--bundle",
                str(bundle),
                "--profile",
                str(QUICKSTART / "applicability.yaml"),
                "--domain",
                str(QUICKSTART / "domain.linkml.yaml"),
                "--out",
                str(out),
            ],
            setting,
        )
        quarantine = out / "quarantine.jsonl"
        reasons = (
            sorted(
                json.loads(r)["reason"]
                for r in quarantine.read_text("utf-8").splitlines()
                if r.strip()
            )
            if quarantine.is_file()
            else None
        )
        assessed[key_of(engine, setting)] = (code, reasons)
    if len(set(map(json.dumps, assessed.values()))) != 1 or "schema_invalid" not in (
        assessed["java"][1] or []
    ):
        fails.append(f"assess 5000 digits: {assessed}")

    report = tmp / "base-report"
    for artifact, prefix in ARTIFACTS.items():
        for label, body, expect in (
            (f"{artifact} 5000 digits", '{"n": %s}' % BIG, prefix),
            (f"{artifact} malformed", '{"n": }', prefix),
        ):
            outcomes = {}
            for engine, setting in runs():
                folder = (
                    tmp / f"report-{key_of(engine, setting)}-{artifact}-{len(body)}"
                )
                shutil.copytree(report, folder)
                (folder / artifact).write_text(body + "\n", "utf-8")
                code, stdout = run(
                    engine, ["report", "--validate", str(folder), "--json"], setting
                )
                outcomes[key_of(engine, setting)] = (code, envelope(stdout))
            fails += check_report(label, expect, outcomes)
            if "malformed" not in label:
                fails += same_python_text(label, outcomes)
    outcomes = {}
    for engine, setting in runs():
        folder = tmp / f"report-{key_of(engine, setting)}-drift-4300"
        shutil.copytree(report, folder)
        (folder / "runtime_drift.jsonl").write_text(
            '{"n": %s}\n' % ("9" * 4300), "utf-8"
        )
        code, stdout = run(
            engine, ["report", "--validate", str(folder), "--json"], setting
        )
        outcomes[key_of(engine, setting)] = (code, envelope(stdout))
    fails += check_report("runtime_drift.jsonl 4300 digits", None, outcomes)
    return fails


def self_test() -> int:
    good = {
        "exit": 1,
        "counts": {"accepted": 5, "quarantined": 3},
        "details": ["invalid JSON: x"],
    }
    assert check_validate("t", True, {"python": good, "java": dict(good)}) == []
    assert check_validate("t", True, {"python": good, "java": {**good, "exit": 3}})
    assert check_validate("t", False, {"python": good})
    assert check_validate("t", True, {"python": {**good, "details": []}})
    one = (
        3,
        {
            "valid": False,
            "problems": ["a.json: invalid JSON (integer literal exceeds 4300 digits)"],
        },
    )
    assert check_report("a 5000", "a.json: invalid JSON (", {"python": one}) == []
    assert check_report(
        "a 5000",
        "a.json: invalid JSON (",
        {"python": (0, {"valid": True, "problems": []})},
    )
    assert check_report(
        "a 5000",
        "a.json: invalid JSON (",
        {"python": (3, {"valid": False, "problems": one[1]["problems"] * 2})},
    )
    assert check_report("a malformed", "a.json: invalid JSON (", {"python": one})
    assert check_report("d", None, {"java": (0, {"valid": True, "problems": []})}) == []
    assert check_report("d", None, {"java": (3, {"valid": False, "problems": ["x"]})})
    assert same_python_text("t", {"java": one, "python": one}) == []
    assert same_python_text(
        "t", {"java": one, "python@0": (3, {"problems": ["other"]})}
    )
    print("SELF_TEST_OK")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--self-test", action="store_true")
    if parser.parse_args().self_test:
        return self_test()
    tmp = Path(tempfile.mkdtemp(prefix="json-integer-limit-"))
    try:
        code, _ = run(
            "python",
            [
                "assess",
                "--bundle",
                str(QUICKSTART / "evidence"),
                "--profile",
                str(QUICKSTART / "applicability.yaml"),
                "--out",
                str(tmp / "base-report"),
            ],
        )
        if code not in (0, 1):
            print(f"FAIL: could not build the base report (python assess exit {code})")
            return 1
        fails = check_engines(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    for fail in fails:
        print(f"FAIL: {fail}")
    if fails:
        return 1
    print("PARITY_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
