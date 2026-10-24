"""json_parity_check - every engine reads JSON under the SPEC 8.7 profile, judged against the shared
case table ``spec/report/json-cases.json`` (item 18.184x).

The check first holds the table to its own shape: unique ids, the row fields each kind needs, at least
one row for every rule the profile names, and the number renderings and comparisons SPEC 8.7 spells
out (``1e+20``, ``5.0``, ``-0.0``; ``1`` vs ``true`` unequal, ``1`` vs ``1.0`` equal). It then feeds
every row to each engine's ``json-cases`` seam (Python ``agentce.tools.json_cases``, the built
TypeScript ``dist/seams.js`` and the Java runnable jar's ``org.agentce.Seams``) and compares each
engine's result with the row's expected value: the canonical text or kept line numbers of an accepted
input, the rule and line of a refusal, the rendered bytes of a number, both directions of a comparison,
whether an integer-typed field accepts the value, and canonical form's refusal text.

``--cli`` runs ten real-CLI rows through the three engines built from source, each against the exit
code, message key and text the profile gives: a bundle manifest with a byte-order mark is read, one
with a repeated ``files`` key is refused, a coverage denominator count ``1e20`` is shown as ``1e+20``,
``diff`` refuses a NaN, ``report --from`` refuses a ``rung`` of ``1.5``, a ``state_version`` of
``true`` is refused, a malformed, a non-UTF-8 and a ``{}`` ``state.json`` are refused with --out left holding no
file, and ``sign`` refuses a claim holding ``5.0``. (The records row, C11, is the
``json-profile-`` cases of VG-QUARANTINE-NO-PASS.)

Usage (from the repository root, after ``pnpm build`` in engines/typescript and
``./gradlew :assemble --offline`` in engines/java):
    json_parity_check.py
    json_parity_check.py --self-test
    json_parity_check.py --cli
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CASES = ROOT / "spec" / "report" / "json-cases.json"
JAVA_ENGINE = ROOT / "engines" / "java"
QUICKSTART = ROOT / "corpus" / "quickstart"
ENGINES = ("python", "typescript", "java")

#: Every rule SPEC 8.7 and the 18.184x contract name; each needs at least one row.
RULES = (
    "invalid_utf8",
    "bom",
    "lone_surrogate",
    "duplicate_key",
    "nan",
    "infinity",
    "comment",
    "trailing_comma",
    "leading_plus",
    "leading_zero",
    "integer_digit_limit",
    "depth_at_limit",
    "depth_over_limit",
    "jsonl_blank_line",
    "jsonl_line_split",
    "extra_value",
    "last_line_no_lf",
    "number_form_by_syntax",
    "integer_field_float_form",
    "number_rendering",
    "compare_by_value",
    "compare_across_types",
    "non_integer_number",
)
COMPARISONS = ("compare_by_value", "compare_across_types")
#: The renderings and comparisons SPEC 8.7 spells out.
REQUIRED_RENDERINGS = {"1e+20": "1e+20", "5.0": "5.0", "-0.0": "-0.0"}
REQUIRED_COMPARISONS = {("1", "true"): False, ("1", "1.0"): True}


def shape_problems(cases: list[dict[str, Any]]) -> list[str]:
    """What is wrong with the table itself, before any engine reads it."""
    problems: list[str] = []
    ids = [str(c.get("id")) for c in cases]
    problems += [
        f"id {i!r} is used more than once"
        for i in sorted({i for i in ids if ids.count(i) > 1})
    ]
    for c in cases:
        name = f"row {c.get('id')!r}"
        rule = c.get("rule")
        if "mode" in c:
            needs = ["bom", "expect"] + (
                ["line"]
                if c.get("expect") == "refuse"
                else ["canonical"]
                if c["mode"] == "json"
                else ["records"]
            )
            if c["mode"] not in ("json", "jsonl") or c.get("bom") not in (
                "ignore",
                "refuse",
            ):
                problems.append(
                    f"{name}: mode must be json|jsonl and bom ignore|refuse"
                )
            if ("text" in c) == ("hex" in c):
                problems.append(f"{name}: give exactly one of text and hex")
        elif rule == "number_rendering":
            needs = ["input", "expect"]
        elif rule in COMPARISONS:
            needs = ["left", "right", "equal"]
        elif rule == "integer_field_float_form":
            needs = ["text", "expect"]
        elif rule == "non_integer_number":
            needs = ["text", "rendered"]
        else:
            problems.append(
                f"{name}: rule {rule!r} needs a reader row (mode, bom, text or hex)"
            )
            continue
        problems += [f"{name}: missing {field}" for field in needs if field not in c]
    present = {c.get("rule") for c in cases}
    problems += [f"no row for rule {r}" for r in RULES if r not in present]
    renderings = {
        c.get("input"): c.get("expect")
        for c in cases
        if c.get("rule") == "number_rendering"
    }
    problems += [
        f"no number_rendering row {lexeme} -> {want}"
        for lexeme, want in REQUIRED_RENDERINGS.items()
        if renderings.get(lexeme) != want
    ]
    pairs = {
        frozenset((c.get("left"), c.get("right"))): c.get("equal")
        for c in cases
        if c.get("rule") in COMPARISONS
    }
    problems += [
        f"no comparison row {left} vs {right} -> equal {want}"
        for (left, right), want in REQUIRED_COMPARISONS.items()
        if pairs.get(frozenset((left, right))) is not want
    ]
    return problems


def expected(case: dict[str, Any]) -> dict[str, Any]:
    """The result an engine's seam must print for one row."""
    if "mode" in case:
        if case["expect"] == "refuse":
            return {"refuse": [case["rule"], case["line"]]}
        if case["mode"] == "json":
            return {"canonical": case["canonical"]}
        return {"records": case["records"]}
    rule = case["rule"]
    if rule == "number_rendering":
        return {"rendered": case["expect"]}
    if rule in COMPARISONS:
        return {"equal": [case["equal"], case["equal"]]}
    if rule == "integer_field_float_form":
        return {"integer": case["expect"] == "accept"}
    return {"refusal": f"non_integer_number: {case['rendered']}"}


def mismatches(engine: str, cases: list[dict[str, Any]], got: list[Any]) -> list[str]:
    """Each row where ``engine``'s printed result differs from the table."""
    if [row[0] for row in got] != [c["id"] for c in cases]:
        return [f"{engine}: the seam printed other rows than the table holds"]
    return [
        f"{engine} {c['rule']} {c['id']!r}: expected {json.dumps(expected(c), ensure_ascii=False)}, "
        f"got {json.dumps(result, ensure_ascii=False)}"
        for c, (_, result) in zip(cases, got)
        if result != expected(c)
    ]


def engine_argv(engine: str) -> list[str]:
    if engine == "python":
        return [
            "uv", "run", "--frozen", "--project", str(ROOT / "engines" / "python"),
            "python", "-m", "agentce.tools.json_cases",
        ]  # fmt: skip
    if engine == "typescript":
        entry = ROOT / "engines" / "typescript" / "dist" / "seams.js"
        if not entry.is_file():
            raise SystemExit(
                f"typescript dist is not built: {entry} is missing (run `pnpm build`)"
            )
        return ["node", str(entry)]
    jars = sorted(
        (JAVA_ENGINE / "build" / "libs").glob("agentce-*-all.jar"),
        key=lambda p: p.stat().st_mtime,
    )
    if not jars:
        raise SystemExit(
            "java runnable jar is not built (run `./gradlew :assemble --offline` in engines/java)"
        )
    return ["java", "-cp", str(jars[-1]), "org.agentce.Seams"]


def run_engine(engine: str, table: Path) -> list[Any]:
    argv = engine_argv(engine)
    if engine != "python":
        argv.append("json-cases")
    proc = subprocess.run(
        [*argv, str(table)], capture_output=True, text=True, cwd=ROOT, check=False
    )
    if proc.returncode != 0:
        raise SystemExit(
            f"{engine} json-cases seam exited {proc.returncode}: {proc.stderr.strip()[-500:]}"
        )
    result: list[Any] = json.loads(proc.stdout)
    return result


def cli_argv(engine: str) -> list[str]:
    if engine == "python":
        return [
            "uv",
            "run",
            "--frozen",
            "--project",
            str(ROOT / "engines" / "python"),
            "agentce",
        ]
    if engine == "typescript":
        return ["node", str(ROOT / "engines" / "typescript" / "dist" / "cli.js")]
    return [str(JAVA_ENGINE / "build" / "install" / "agentce" / "bin" / "agentce")]


def _bundle(work: Path, name: str, member: str, edit: Any) -> Path:
    """A copy of the quickstart bundle with ``member`` edited; a member other than the manifest gets its
    digest in the manifest fixed, so the run reaches the JSON reader."""
    bundle = work / name
    shutil.copytree(QUICKSTART / "evidence", bundle)
    path = bundle / member
    raw = edit(path.read_bytes())
    path.write_bytes(raw)
    if member != "manifest.json":
        manifest = bundle / "manifest.json"
        text = manifest.read_text(encoding="utf-8")
        for entry in json.loads(text)["files"]:
            if entry["path"] == member:
                text = text.replace(entry["sha256"], hashlib.sha256(raw).hexdigest())
        manifest.write_text(text, encoding="utf-8")
    return bundle


def cli_rows(work: Path) -> list[tuple[Any, ...]]:
    """(name, args for an engine, expected exit, text every engine prints, key first when refused)."""
    profile = str(QUICKSTART / "applicability.yaml")
    run_one(
        cli_argv("python") + ["quickstart", "--out", str(work / "qs")],
        "python quickstart",
    )
    assertions = (work / "qs" / "assertions.json").read_text(encoding="utf-8")
    (work / "b-nan.json").write_text(
        assertions.replace('"rung":2', '"rung":NaN', 1), encoding="utf-8"
    )
    (work / "from.json").write_text(
        assertions.replace('"rung":2', '"rung":1.5', 1), encoding="utf-8"
    )
    bom = _bundle(
        work, "manifest-bom", "manifest.json", lambda raw: b"\xef\xbb\xbf" + raw
    )
    files = _bundle(
        work,
        "manifest-files",
        "manifest.json",
        lambda raw: raw.rstrip()[:-1] + b',"files":[]}\n',
    )
    counts = _bundle(
        work, "coverage", "reference/coverage-counts.json",
        lambda raw: raw.replace(b'"BundleLoaded": 1', b'"BundleLoaded": 1e20'),
    )  # fmt: skip
    for engine in ENGINES:
        claim = work / f"claim-{engine}"
        shutil.copytree(work / "qs", claim)
        path = claim / "claim.json"
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                '"claim_id"', '"x_n": 5.0, "claim_id"', 1
            )
        )
        run_one(
            cli_argv(engine) + ["assess", "--bundle", str(QUICKSTART / "evidence"), "--profile", profile,
                                "--state", str(work / f"state-{engine}"), "--out", str(work / f"seed-{engine}")],
            f"{engine} assess --state",
        )  # fmt: skip
        for name, raw in (
            ("malformed", b"{not json"),
            ("nonutf8", b'{"x": "\xff\xfe"}'),
            ("empty_object", b"{}"),
        ):
            bad = work / f"state-{name}-{engine}"
            bad.mkdir()
            (bad / "state.json").write_bytes(raw)
        state = work / f"state-{engine}" / "state.json"
        text = state.read_text(encoding="utf-8")
        state.write_text(
            re.sub(r'"state_version": *1\b', '"state_version": true', text, count=1),
            encoding="utf-8",
        )

    def assess(bundle: Path) -> Any:
        return lambda e: [
            "assess",
            "--bundle",
            str(bundle),
            "--profile",
            profile,
            "--out",
            str(work / f"o-{bundle.name}-{e}"),
        ]

    return [
        ("manifest with a byte-order mark is read", assess(bom), 0, "credit.record_decision"),
        ("manifest with a repeated files key", assess(files), 3,
         "input.bundle_manifest_invalid: manifest.json is outside the JSON profile: duplicate_key at line"),
        ("coverage count 1e20 shown by the number rule", assess(counts), 3,
         "input.coverage_denominator_manifest_invalid: the coverage denominator manifest "
         "'reference/coverage-counts.json' counts 'BundleLoaded' as 1e+20, not a whole number"),
        ("diff of an assertion set holding NaN", lambda e: ["diff", str(work / "qs" / "assertions.json"),
         str(work / "b-nan.json")], 3, "input.report_b_unreadable: the second assertion set"),
        ("report --from with a rung of 1.5", lambda e: ["report", "--from", str(work / "from.json"), "--format", "md",
         "--out", str(work / f"r-{e}.md")], 3, "is outside the JSON profile: integer_field_float_form at [0].rung"),
        ("state.json with state_version true",
         lambda e: ["assess", "--bundle", str(QUICKSTART / "evidence"), "--profile", profile, "--state",
                    str(work / f"state-{e}"), "--out", str(work / f"st-{e}")], 3,
         "input.state_version_incompatible: the state directory at"),
        *(
            (f"{name} state.json refused before --out is written",
             lambda e, n=name: ["assess", "--bundle", str(QUICKSTART / "evidence"), "--profile", profile, "--state",
                                str(work / f"state-{n}-{e}"), "--out", str(work / f"bad-{n}-{e}")], 3,
             "input.state_unreadable: the state file", lambda e, n=name: work / f"bad-{n}-{e}")
            for name in ("malformed", "nonutf8")
        ),
        ("{} state.json refused before --out is written",
         lambda e: ["assess", "--bundle", str(QUICKSTART / "evidence"), "--profile", profile, "--state",
                    str(work / f"state-empty_object-{e}"), "--out", str(work / f"bad-empty_object-{e}")], 3,
         "input.state_version_incompatible: the state directory at", lambda e: work / f"bad-empty_object-{e}"),
        ("sign a claim holding 5.0", lambda e: ["sign", str(work / f"claim-{e}"), "--as", "claimant", "--dry-run"], 3,
         "sign.claim_malformed: claim.json cannot be put in canonical form: non_integer_number: 5.0."),
    ]  # fmt: skip


def run_one(argv: list[str], what: str) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(argv, capture_output=True, text=True, cwd=ROOT, check=False)
    if proc.returncode != 0:
        raise SystemExit(
            f"{what} exited {proc.returncode}: {(proc.stdout + proc.stderr).strip()[-500:]}"
        )
    return proc


def check_cli() -> int:
    problems: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        rows = cli_rows(Path(tmp))
        for name, args, code, text, *untouched in rows:
            for engine in ENGINES:
                proc = subprocess.run(
                    cli_argv(engine) + args(engine),
                    capture_output=True,
                    text=True,
                    cwd=ROOT,
                    check=False,
                )
                # Python prints `error [key]: text`, TypeScript and Java `key: text`.
                out = re.sub(
                    r"error \[([a-z_.]+)\]: ", r"\1: ", proc.stdout + proc.stderr
                )
                if proc.returncode != code or text not in out:
                    problems.append(
                        f"{engine} {name}: expected exit {code} and {text!r}, got exit {proc.returncode}: "
                        f"{out.strip()[:400]}"
                    )
                # A row naming an --out folder must leave it holding no file (SPEC 8.7, 18.184s).
                for out_dir in (u(engine) for u in untouched):
                    written = (
                        sorted(p.name for p in out_dir.rglob("*"))
                        if out_dir.exists()
                        else []
                    )
                    if written:
                        problems.append(
                            f"{engine} {name}: --out holds {written}, expected no file"
                        )
    for problem in problems:
        print(f"FAIL {problem}")
    if problems:
        return 1
    print(
        f"PASS {len(rows)} CLI rows give the profile's exit code, key and text in python, typescript and java"
    )
    return 0


def check() -> int:
    cases = json.loads(CASES.read_text(encoding="utf-8"))["cases"]
    problems = shape_problems(cases)
    if not problems:
        for engine in ENGINES:
            problems += mismatches(engine, cases, run_engine(engine, CASES))
    for problem in problems:
        print(f"FAIL {problem}")
    if problems:
        return 1
    print(
        f"PASS {len(cases)} case-table rows give the table's result in python, typescript and java"
    )
    return 0


def self_test() -> int:
    """Seeded faults: each must be caught by the same comparison ``check`` runs."""
    cases = json.loads(CASES.read_text(encoding="utf-8"))["cases"]
    failures: list[str] = []
    if shape_problems(cases):
        failures.append(
            f"the committed table has shape problems: {shape_problems(cases)}"
        )
    good = [[c["id"], expected(c)] for c in cases]
    if mismatches("clean", cases, good):
        failures.append(
            "a seam printing the table's own results is reported as a mismatch"
        )

    def seeded(fault: str, change: Any) -> None:
        got = copy.deepcopy(good)
        for (row, result), case in zip(got, cases):
            change(case, result)
        if not mismatches(fault, cases, got):
            failures.append(f"seeded fault not caught: {fault}")

    def wrong_renderer(case: dict[str, Any], result: dict[str, Any]) -> None:
        if "rendered" in result and result["rendered"].endswith(".0"):
            result["rendered"] = result["rendered"][:-2]

    def last_wins(case: dict[str, Any], result: dict[str, Any]) -> None:
        if case["rule"] == "duplicate_key" and "refuse" in result:
            result.clear()
            result["canonical"] = "{}"

    def int_equals_true(case: dict[str, Any], result: dict[str, Any]) -> None:
        if case.get("left") == "1" and case.get("right") == "true":
            result["equal"] = [True, True]

    seeded("a renderer that drops .0", wrong_renderer)
    seeded("a reader that accepts a repeated key", last_wins)
    seeded("1 compares equal to true", int_equals_true)

    for rule in ("duplicate_key", "number_rendering"):
        if not any(
            f"rule {rule}" in p
            for p in shape_problems([c for c in cases if c["rule"] != rule])
        ):
            failures.append(f"a table with no {rule} row is not refused")
    renamed = copy.deepcopy(cases)
    renamed[1]["id"] = renamed[0]["id"]
    if not any("more than once" in p for p in shape_problems(renamed)):
        failures.append("a repeated id is not refused")

    for failure in failures:
        print(f"FAIL {failure}")
    if failures:
        return 1
    print(
        "PASS self-test: three seeded engine faults and three broken tables are each caught"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--self-test", action="store_true")
    mode.add_argument("--cli", action="store_true")
    args = parser.parse_args(argv)
    return self_test() if args.self_test else check_cli() if args.cli else check()


if __name__ == "__main__":
    sys.exit(main())
