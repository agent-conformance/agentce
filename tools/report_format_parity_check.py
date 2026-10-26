"""report_format_parity_check - Python, TypeScript and Java write the same JUnit, CSV, OSCAL XML, public statement and PDF.

`report.junit.xml`, `report.csv`, `oscal-ar.xml`, `public-statement.md` and `report.pdf` (SPEC §9, §9.4, §9.5) are
rendered by Python's `render_junit`, `render_csv`, `render_oscal_xml`, `render_public_statement` and `render_pdf`;
18.16c and 18.16d port them to the TypeScript and Java engines. Until `assess --emit` is wired there (18.16b), each
engine's `report-formats` seam renders the five files from a report directory that engine's own `assess` wrote: its
`assertions.json`, its `oscal-ar.json` and its `manifest.json` (catalogs, `run.started_at`, `run.report_language`).

Over `corpus/quickstart` (also with `--report-language de`), the auditor-view fixture (a deviations register, so the
OSCAL carries `risks[]`) and the 30 generated corpus projects, Python's real `assess --emit
junit,csv,oscal_xml,public,pdf` writes the goldens; each other engine assesses the same input and its seam's five
files must equal Python's byte for byte. The clock is the one input that differs between runs: each engine's
`run.started_at` must have Python's shape and is then set to Python's value before its seam runs. Python's own
reconstruction from its report directory must also equal its assess output, so the seam's inputs are the ones
assess renders from. Then every case in `fixtures/report_formats/cases.json` (empty, every outcome, hostile
characters, an odd OSCAL document, code-point order) is rendered by all three engines from the same directory and
compared, and Python's rendering must equal the committed `expected.json` the engines' unit tests read.

The remediation package and remediation.md (SPEC §7, Appendix A2; 18.16e) are compared from the same runs: Python
adds `remediation` to its --emit list, and the other engines run their real assess through their `assess-emit
remediation` seam. Four more runs compare remediation alone: the hostile remediation fixture, a records folder
assessed without and with --profile (Java, which reads no records folder yet, must refuse both at exit 3), and the
no-assertions run (no remediation directory anywhere). Each remediation.md
must equal Python's byte for byte; each package must be its canonical bytes, equal Python's but for
generated_from.engine (the engine's own manifest block) and the $id derived from it, and be listed in the manifest's
outputs with its digest. Then every package in `fixtures/report_formats/remediation_md_cases.json` is rendered by each
engine's `remediation-md` seam and must equal Python's, which must equal the committed `remediation_md_expected.json`.

The fix-list skill folder (SPEC §13.3, Appendix A2 (C); 18.16f) is compared the same way from the same runs: Python
adds `skill` to its --emit list and the other engines' seam is `assess-emit remediation,skill`. Under each
skill/<subject>/ the SKILL.md, findings/*.md and REVERIFY.md must equal Python's byte for byte and the
remediation-package.json gets the remediation package's checks. Then every case in
`fixtures/report_formats/skill_cases.json` is rendered by each engine's `skill-cases` seam and must equal Python's,
which must equal the committed `skill_expected.json`.

The security view (18.16g) and the buyer view (18.16h) are compared the same way. Python writes security.json,
security.md and security.html on a second run of each input with `--for security`; the other engines through their
`assess-emit remediation,skill,for=security` seam. The buyer view runs over its own six inputs (the buyer-view fixture
in English and German, quickstart, the auditor-view fixture, the hostile remediation fixture and no-assertions): Python's
`assess --for buyer` against each engine's `assess-emit for=buyer`, the same exit code and buyer.json, buyer.md and
buyer.html byte for byte, each listed in manifest.outputs with Python's digest. `security_cases.json` and
`buyer_cases.json` are rendered by each engine's `security-cases` and `buyer-cases` seams against the committed goldens.

Modes:
    report_format_parity_check.py                  # three engines (needs `pnpm build` and `:assemble` first)
    report_format_parity_check.py --fail-fast      # the same, stopping at the first mismatching run
    report_format_parity_check.py --engine python  # Python alone, no build needed
    report_format_parity_check.py --self-test      # the comparator catches every tamper class
    report_format_parity_check.py --write-expected # regenerate fixtures/report_formats/expected.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, NamedTuple

import claim_parity_check as cpc

ROOT = cpc.ROOT
CASES = Path(__file__).resolve().parent / "fixtures" / "report_formats" / "cases.json"
#: Python's four renderings of each case, the golden the TypeScript and Java unit tests read;
#: `--write-expected` regenerates it and every check run requires it current.
EXPECTED = CASES.with_name("expected.json")
FILES = (
    "report.junit.xml",
    "report.csv",
    "oscal-ar.xml",
    "public-statement.md",
    "report.pdf",
)
#: report.pdf is Latin-1 bytes with a binary comment line; expected.json holds it as Latin-1 text.
BINARY = {"report.pdf"}
#: A case's manifest.run when it gives none of its own.
CASE_STARTED_AT = "2026-09-01T00:00:00Z"
#: The shape every engine's manifest.run.started_at must have (Python's `_now`).
STARTED_AT_SHAPE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
ENGINES = cpc.ENGINES
MD_CASES = CASES.with_name("remediation_md_cases.json")
#: Python's remediation.md for each package of MD_CASES, the golden the engines' unit tests read.
MD_EXPECTED = CASES.with_name("remediation_md_expected.json")
SKILL_CASES = CASES.with_name("skill_cases.json")
#: Python's rendering of each case of SKILL_CASES, the golden the engines' unit tests read.
SKILL_EXPECTED = CASES.with_name("skill_expected.json")
SECURITY_CASES = CASES.with_name("security_cases.json")
#: Python's security.md and security.html for each view of SECURITY_CASES, the golden the engines'
#: unit tests read.
SECURITY_EXPECTED = CASES.with_name("security_expected.json")
BUYER_CASES = CASES.with_name("buyer_cases.json")
#: Python's buyer.md and buyer.html for each view of BUYER_CASES (en and de), the golden the engines' unit
#: tests read.
BUYER_EXPECTED = CASES.with_name("buyer_expected.json")
#: The buyer-view gate's fixture: every outcome, a hostile clause and evidence ref, a gap step.
BUYER = ROOT / "verification" / "gates" / "fixtures" / "buyer_view"
HOSTILE = CASES.with_name("remediation_hostile")
#: A records folder for the folder-form runs (`assess <folder>`), copied into the work tree.
RECORDS = ROOT / "adapters" / "otel-genai" / "fixtures" / "otel-genai-agent-session"
#: The runs that compare remediation alone: the hostile fixture, the folder without and with
#: --profile, and no-assertions.
REMEDIATION_ONLY_RUNS = 4
#: The emitted folders compared file by file: remediation (18.16e) and the skill folder (18.16f).
EMITTED = ("remediation", "skill")
#: The security view's files (18.16g): Python writes them on a second run with `--for security` (which
#: refuses --emit), the other engines through the seam's `for=security` token.
SECURITY_FILES = ("security.json", "security.md", "security.html")
#: The buyer view's files (18.16h): Python writes them with `--for buyer`, the other engines through
#: the seam's `for=buyer` token.
BUYER_FILES = ("buyer.json", "buyer.md", "buyer.html")
#: The buyer pass's inputs (buyer_runs); quickstart and the generated projects carry no questionnaire
#: answers, so the pass runs over these alone.
BUYER_RUNS = 6
#: Java reads no records folder yet (input.records_unsupported; docs/quickstart-java.md): on the
#: folder-form runs it must refuse at exit 3 and write no remediation output.
JAVA_REFUSES = {"records_folder", "records_folder_profile"}
PACKAGE_ID = "urn:agentce:remediation-package:sha256:"
#: quickstart, quickstart in German, the auditor-view fixture and the 30 generated projects; a checker
#: that silently drops a run or a case must not print OK.
CORPUS_RUNS = 33

#: Python's side of the seam: the same five renderings from a report directory's own files, the way
#: write_report calls them (catalog labels `id@version`, the applied deviation ids, counts from aggregate,
#: the manifest's started_at and report language).
PYTHON_RENDER = """
import json, sys
from pathlib import Path
from agentce.assertions import Assertion, aggregate
from agentce.report import (applied_deviation_ids, render_csv, render_junit, render_oscal_xml, render_pdf,
                            render_public_statement)
out_root = Path(sys.argv[1])
for index, raw in enumerate(sys.argv[2:]):
    src = Path(raw)
    assertions = [Assertion.from_json(a) for a in json.loads((src / "assertions.json").read_text("utf-8"))]
    oscal = json.loads((src / "oscal-ar.json").read_text("utf-8"))
    manifest = json.loads((src / "manifest.json").read_text("utf-8"))
    catalogs = [f"{c['id']}@{c['version']}" for c in manifest["inputs"]["catalogs"]]
    out = out_root / str(index)
    out.mkdir(parents=True, exist_ok=True)
    rendered = {
        "report.junit.xml": render_junit(assertions),
        "report.csv": render_csv(assertions),
        "oscal-ar.xml": render_oscal_xml(oscal),
        "public-statement.md": render_public_statement(
            assertions, catalogs=catalogs, deviations=applied_deviation_ids(assertions)
        ),
    }
    for name, text in rendered.items():
        (out / name).write_bytes(text.encode("utf-8"))
    run = manifest["run"]
    (out / "report.pdf").write_bytes(render_pdf(
        assertions, aggregate(assertions), started_at=run["started_at"], language=run["report_language"]
    ))
"""


def _env() -> dict[str, str]:
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("AGENTCE_OPERATOR", "VIRTUAL_ENV")
    }
    env["LC_ALL"] = "C.UTF-8"
    return env


def _python(script: str) -> list[str]:
    return [
        "uv",
        "run",
        "--frozen",
        "--project",
        str(cpc.spc.PY_ENGINE),
        "python",
        "-c",
        script,
    ]


def _seam(engine: str) -> list[str]:
    """The TypeScript or Java seam entry point; the seam verb follows."""
    if engine == "typescript":
        return ["node", str(cpc.spc.TS_ENGINE / "dist" / "seams.js")]
    jar = cpc.spc.readiness_parity_check.java_jar()
    return ["java", "-Dstdout.encoding=UTF-8", "-cp", str(jar), "org.agentce.Seams"]


def render_with(engine: str, out_root: Path, sources: list[Path]) -> str:
    """Run one engine's `report-formats` seam over `sources`; output lands in `out_root/<index>/`.
    Returns the engine's stderr when it fails, else ""."""
    cmd = (
        _python(PYTHON_RENDER)
        if engine == "python"
        else [*_seam(engine), "report-formats"]
    )
    proc = subprocess.run(
        [*cmd, str(out_root), *map(str, sources)],
        capture_output=True,
        text=True,
        cwd=ROOT,
        env=_env(),
    )
    if proc.returncode != 0:
        return f"exit {proc.returncode}: {proc.stderr.strip()[-400:]}"
    return ""


def _first_difference(a: bytes, b: bytes) -> str:
    at = next(
        (i for i, (x, y) in enumerate(zip(a, b, strict=False)) if x != y),
        min(len(a), len(b)),
    )
    return (
        f"first difference at byte {at} (python {a[at : at + 40]!r}, "
        f"engine {b[at : at + 40]!r}; lengths {len(a)} and {len(b)})"
    )


def file_problems(engine: str, reference: Path, candidate: Path) -> list[str]:
    """Every one of the five files in `candidate` must equal `reference`'s byte for byte."""
    problems = []
    for name in FILES:
        want = reference / name
        got = candidate / name
        if not want.is_file():
            problems.append(f"python wrote no {name}")
        elif not got.is_file():
            problems.append(f"{engine} wrote no {name}")
        elif want.read_bytes() != got.read_bytes():
            problems.append(
                f"{engine} {name}: "
                + _first_difference(want.read_bytes(), got.read_bytes())
            )
    return problems


def _assess_emit(engine: str, emit: str, argv: list[str], env: dict[str, str]) -> int:
    """TypeScript's or Java's `assess-emit <emit>` seam over `argv`; its exit code."""
    proc = subprocess.run(
        [*_seam(engine), "assess-emit", emit, *argv],
        capture_output=True,
        text=True,
        cwd=ROOT,
        env={**_env(), **env},
    )
    return proc.returncode


def _assess(engine: str, args: list[str], env: dict[str, str], out: Path) -> int:
    """One engine's real `assess`, Python's with the five formats, remediation and skill emitted (or, as
    "python-security", with `--for security`), the others' through their `assess-emit
    remediation,skill,for=security` seam; its exit code. Exits 1 and 2 (a non-conformant or unproven
    outcome) still write the full report."""
    argv = ["assess", *args, "--out", str(out)]
    if engine.startswith("python"):
        extra = (
            ["--for", "security"]
            if engine == "python-security"
            else ["--emit", "junit,csv,oscal_xml,public,pdf,remediation,skill"]
        )
        _, code = cpc.run_engine("python", [*argv, *extra], env)
        return code
    return _assess_emit(engine, "remediation,skill,for=security", argv, env)


def _hostile_args() -> list[str]:
    """The hostile remediation fixture's assess argv."""
    return [
        *cpc._project_args(HOSTILE),
        "--catalog-dir",
        str(HOSTILE / "catalog"),
        "--allow-unverified-catalog",
    ]


def remediation_only_runs(tmp: Path, no_assertions: cpc.Run) -> list[cpc.Run]:
    """The hostile fixture, a records folder without and with --profile (the profile Python derives
    from the folder), and no-assertions."""
    folder = tmp / "records" / "rec"
    shutil.copytree(RECORDS, folder)
    derived = tmp / "records" / "derived"
    _assess("python", [str(folder)], {}, derived)
    profile = tmp / "records" / "applicability.yaml"
    shutil.copyfile(derived / "applicability.yaml", profile)
    return [
        ("remediation_hostile", _hostile_args(), {}, False),
        ("records_folder", [str(folder)], {}, False),
        ("records_folder_profile", [str(folder), "--profile", str(profile)], {}, False),
        no_assertions,
    ]


def _emitted_files(report: Path) -> list[str]:
    """Every file under the report's remediation/ and skill/ folders."""
    return sorted(
        p.relative_to(report).as_posix()
        for name in EMITTED
        for p in (report / name).rglob("*")
        if p.is_file()
    )


def _without_identity(package: dict[str, object]) -> dict[str, object]:
    generated = package.get("generated_from")
    rest = {k: v for k, v in package.items() if k != "$id"}
    if isinstance(generated, dict):
        rest["generated_from"] = {k: v for k, v in generated.items() if k != "engine"}
    return rest


def _package_body(package: dict[str, object]) -> dict[str, object]:
    return {k: v for k, v in package.items() if k not in ("$schema", "$id")}


def emitted_problems(
    engine: str, reference: Path, candidate: Path, digest_of
) -> list[str]:
    """`candidate`'s remediation and skill output against Python's in `reference`: Markdown byte for
    byte, each remediation-package.json by value but for its engine identity; `digest_of(value)` is the
    sha256 hex of a value's canonical bytes by Python's canonicalizer."""
    want, got = _emitted_files(reference), _emitted_files(candidate)
    if want != got:
        return [f"{engine} emitted files {got}, python {want}"]
    if not want:
        return []
    ref_manifest = json.loads((reference / "manifest.json").read_text("utf-8"))
    manifest = json.loads((candidate / "manifest.json").read_text("utf-8"))
    ref_out, out = ref_manifest["outputs"], manifest["outputs"]
    prefixes = tuple(f"{name}/" for name in EMITTED)
    listed = sorted(k for k in out if k.startswith(prefixes))
    if listed != sorted(k for k in ref_out if k.startswith(prefixes)):
        return [
            f"{engine} manifest.outputs lists emitted files {listed}, python {want}"
        ]
    engine_block = {
        k: v for k, v in manifest["engine"].items() if k != "package_digest"
    }
    problems = []
    for rel in want:
        a, b = (reference / rel).read_bytes(), (candidate / rel).read_bytes()
        if rel.endswith(".md"):
            if a != b:
                problems.append(f"{engine} {rel}: " + _first_difference(a, b))
            elif out[rel] != ref_out[rel]:
                problems.append(
                    f"{engine} manifest.outputs[{rel}] is not python's digest"
                )
            continue
        own = "sha256:" + hashlib.sha256(b).hexdigest()
        package = json.loads(b)
        if own != "sha256:" + digest_of(package):
            problems.append(f"{engine} {rel}: not the canonical bytes of its value")
        if out[rel] != own:
            problems.append(
                f"{engine} manifest.outputs[{rel}] is not the package's digest"
            )
        if package.get("generated_from", {}).get("engine") != engine_block:
            problems.append(
                f"{engine} {rel}: generated_from.engine is not the manifest's engine"
            )
        if package.get("$id") != PACKAGE_ID + digest_of(_package_body(package)):
            problems.append(f"{engine} {rel}: $id is not the digest of its body")
        if _without_identity(package) != _without_identity(json.loads(a)):
            problems.append(
                f"{engine} {rel}: differs from python outside engine and $id"
            )
    return problems


def view_problems(
    engine: str,
    reference: Path,
    candidate: Path,
    files: tuple[str, ...] = SECURITY_FILES,
) -> list[str]:
    """`candidate`'s audience-view `files` against Python's `--for <preset>` run in `reference`: the
    same files, byte for byte, each listed in manifest.outputs with Python's digest."""
    view = files[0].split(".")[0]
    want = [n for n in files if (reference / n).is_file()]
    got = [n for n in files if (candidate / n).is_file()]
    if want != got:
        return [f"{engine} wrote {view} files {got}, python {want}"]
    if not want:
        return []
    ref_out = json.loads((reference / "manifest.json").read_text("utf-8"))["outputs"]
    out = json.loads((candidate / "manifest.json").read_text("utf-8"))["outputs"]
    listed = [n for n in files if n in out]
    if listed != want:
        return [f"{engine} manifest.outputs lists {view} files {listed}, python {want}"]
    problems = []
    for name in want:
        a, b = (reference / name).read_bytes(), (candidate / name).read_bytes()
        if a != b:
            problems.append(f"{engine} {name}: " + _first_difference(a, b))
        elif out[name] != ref_out.get(name):
            problems.append(f"{engine} manifest.outputs[{name}] is not python's digest")
    return problems


def buyer_runs() -> list[tuple[str, list[str], dict[str, str]]]:
    """The buyer pass's inputs: (label, assess argv, extra env)."""
    fixture = [
        *cpc._project_args(BUYER),
        "--catalog-dir",
        str(BUYER / "catalog"),
        "--allow-unverified-catalog",
    ]
    auditor = [
        *cpc._project_args(cpc.AUDITOR),
        "--catalog-dir",
        str(cpc.AUDITOR / "catalog"),
        "--allow-unverified-catalog",
        "--deviations",
        str(cpc.AUDITOR / "deviations.yaml"),
    ]
    return [
        ("buyer_fixture", fixture, {}),
        ("buyer_fixture_de", [*fixture, "--report-language", "de"], {}),
        ("buyer_quickstart", cpc._project_args(cpc.QUICKSTART), {}),
        ("buyer_auditor_view", auditor, {"AGENTCE_OPERATOR": "Zo\u00eb Corp"}),
        ("buyer_remediation_hostile", _hostile_args(), {}),
        (
            "buyer_no-assertions",
            cpc._project_args(cpc.QUICKSTART, cpc.NO_ASSERTIONS),
            {},
        ),
    ]


def _assess_buyer(engine: str, args: list[str], env: dict[str, str], out: Path) -> int:
    """Python's real `assess --for buyer`, or another engine's through its `assess-emit for=buyer` seam."""
    argv = ["assess", *args, "--out", str(out)]
    if engine == "python":
        _, code = cpc.run_engine("python", [*argv, "--for", "buyer"], env)
        return code
    return _assess_emit(engine, "for=buyer", argv, env)


def check_buyer(
    tmp: Path, engines: tuple[str, ...], fail_fast: bool
) -> tuple[int, int]:
    """Each buyer run: every engine's exit code is Python's, and its buyer files are Python's."""
    runs = buyer_runs()
    jobs = [
        (e, args, env, tmp / e / label) for label, args, env in runs for e in engines
    ]
    with ThreadPoolExecutor(max_workers=min(8, os.cpu_count() or 2)) as pool:
        codes = list(pool.map(lambda job: _assess_buyer(*job), jobs))
    exits = {(job[0], job[3]): code for job, code in zip(jobs, codes, strict=True)}
    failures = 0
    for label, _, _ in runs:
        problems: list[str] = []
        reference = tmp / "python" / label
        py_exit = exits[("python", reference)]
        if py_exit not in ((3,) if label == "buyer_no-assertions" else (0, 1, 2)):
            problems.append(f"python assess --for buyer exited {py_exit}")
        for engine in engines[1:]:
            candidate = tmp / engine / label
            code = exits[(engine, candidate)]
            if code != py_exit:
                problems.append(f"{engine} assess exited {code}, python {py_exit}")
            else:
                problems += view_problems(engine, reference, candidate, BUYER_FILES)
        failures += _report(label, problems)
        if problems and fail_fast:
            break
    return failures, len(runs)


def canonical_digest_table(packages: list[Path]):
    """A `digest_of` over every package and its body in one Python canonicalizer run."""
    values = []
    for path in packages:
        package = json.loads(path.read_bytes())
        values += [package, _package_body(package)]
    keys = [json.dumps(v, sort_keys=True) for v in values]
    table = (
        dict(zip(keys, cpc.canonical_digests(values), strict=True)) if values else {}
    )
    return lambda value: table.get(json.dumps(value, sort_keys=True), "")


def check_corpus(
    tmp: Path, engines: tuple[str, ...], fail_fast: bool
) -> tuple[int, int]:
    every = cpc.runs(cpc.generate_corpus(tmp / "corpus"))
    runs = [r for r in every if r[3]]
    quickstart = next(r for r in runs if r[0] == "quickstart")
    runs.insert(
        1,
        ("quickstart_de", [*quickstart[1], "--report-language", "de"], {}, True),
    )
    no_assertions = next(r for r in every if r[0] == "no-assertions")
    runs += remediation_only_runs(tmp, no_assertions)
    formats = [i for i, r in enumerate(runs) if r[3]]
    labels = [label for label, _, _, _ in runs]
    dirs = {
        e: [tmp / "assess" / e / label.replace("/", "_") for label in labels]
        for e in ("python-security", *engines)
    }
    jobs = [
        (e, args, env, dirs[e][i])
        for i, (_, args, env, _) in enumerate(runs)
        for e in ("python-security", *engines)
    ]
    with ThreadPoolExecutor(max_workers=min(8, os.cpu_count() or 2)) as pool:
        codes = list(pool.map(lambda job: _assess(*job), jobs))
    exits = {(job[0], job[3]): code for job, code in zip(jobs, codes, strict=True)}
    clock_problems = {
        (e, i): pin_started_at(dirs["python"][i], dirs[e][i])
        for e in engines[1:]
        for i in formats
    }
    seam_errors = {
        e: render_with(e, tmp / "seam" / e, [dirs[e][i] for i in formats])
        for e in engines
    }
    seam_index = {i: n for n, i in enumerate(formats)}
    digest_of = canonical_digest_table(
        [
            d / rel
            for e in engines[1:]
            for d in dirs[e]
            for rel in _emitted_files(d)
            if rel.endswith(".json")
        ]
    )
    failures = 0
    for i, label in enumerate(labels):
        problems: list[str] = []
        py_exit = exits[("python", dirs["python"][i])]
        # no-assertions' profile names no subject: assess refuses it (exit 3) and writes no remediation or skill.
        if py_exit not in ((0, 1, 2, 3) if label == "no-assertions" else (0, 1, 2)):
            problems.append(f"python assess exited {py_exit}")
        security_exit = exits[("python-security", dirs["python-security"][i])]
        if security_exit != py_exit:
            problems.append(
                f"python assess --for security exited {security_exit}, --emit {py_exit}"
            )
        for engine in engines:
            code = exits[(engine, dirs[engine][i])]
            if engine == "java" and label in JAVA_REFUSES:
                if (
                    code != 3
                    or _emitted_files(dirs[engine][i])
                    or any((dirs[engine][i] / n).exists() for n in SECURITY_FILES)
                ):
                    problems.append(
                        f"java assess <folder> exited {code}; want 3 (input.records_unsupported), no remediation, skill or security files"
                    )
                continue
            if code != py_exit:
                problems.append(f"{engine} assess exited {code}, python {py_exit}")
            elif clock_problems.get((engine, i)):
                problems.append(f"{engine} {clock_problems[(engine, i)]}")
            elif i in seam_index and seam_errors[engine]:
                problems.append(f"{engine} report-formats seam: {seam_errors[engine]}")
            elif not problems:
                if i in seam_index:
                    problems += file_problems(
                        engine,
                        dirs["python"][i],
                        tmp / "seam" / engine / str(seam_index[i]),
                    )
                if engine != "python":
                    problems += emitted_problems(
                        engine, dirs["python"][i], dirs[engine][i], digest_of
                    )
                    problems += view_problems(
                        engine, dirs["python-security"][i], dirs[engine][i]
                    )
        failures += _report(label, problems)
        if problems and fail_fast:
            break
    return failures, len(labels)


def pin_started_at(reference: Path, candidate: Path) -> str:
    """Set `candidate`'s manifest.run.started_at to `reference`'s, once it has Python's shape; the
    problem, else "". A missing manifest is left for the seam to report."""
    path = candidate / "manifest.json"
    if not path.is_file() or not (reference / "manifest.json").is_file():
        return ""
    manifest = json.loads(path.read_text("utf-8"))
    started_at = manifest.get("run", {}).get("started_at")
    if not isinstance(started_at, str) or not STARTED_AT_SHAPE.fullmatch(started_at):
        return f"manifest.run.started_at {started_at!r} is not YYYY-MM-DDTHH:MM:SSZ"
    want = json.loads((reference / "manifest.json").read_text("utf-8"))["run"][
        "started_at"
    ]
    manifest["run"]["started_at"] = want
    path.write_text(json.dumps(manifest), "utf-8")
    return ""


def write_case_dirs(root: Path) -> list[tuple[str, Path]]:
    """Each case as a report directory: assertions.json, oscal-ar.json (Python's render_oscal of the
    assertions unless the case gives its own document) and a manifest naming the case's catalogs,
    started_at and report language.
    Keys are written in the order given, not sorted, so a renderer that stops sorting is seen."""
    cases = json.loads(CASES.read_text("utf-8"))["cases"]
    script = """
import json, sys
from agentce.assertions import Assertion
from agentce.report import render_oscal
print(json.dumps([render_oscal([Assertion.from_json(a) for a in c["assertions"]])
                  for c in json.load(open(sys.argv[1], encoding="utf-8"))["cases"]]))
"""
    proc = subprocess.run(
        [
            "uv",
            "run",
            "--frozen",
            "--project",
            str(cpc.spc.PY_ENGINE),
            "python",
            "-c",
            script,
            str(CASES),
        ],
        capture_output=True,
        text=True,
        check=True,
        cwd=ROOT,
        env=_env(),
    )
    oscals = json.loads(proc.stdout)
    out = []
    for case, oscal in zip(cases, oscals, strict=True):
        d = root / case["name"]
        d.mkdir(parents=True)
        catalogs = [
            dict(zip(("id", "version"), label.rsplit("@", 1), strict=True))
            for label in case["catalogs"]
        ]
        for name, value in (
            ("assertions.json", case["assertions"]),
            ("oscal-ar.json", case.get("oscal", oscal)),
            (
                "manifest.json",
                {
                    "inputs": {"catalogs": catalogs},
                    "run": {
                        "started_at": case.get("started_at", CASE_STARTED_AT),
                        "report_language": case.get("report_language", "en"),
                    },
                },
            ),
        ):
            (d / name).write_text(json.dumps(value, indent=2) + "\n", "utf-8")
        out.append((case["name"], d))
    return out


def python_case_outputs(tmp: Path) -> dict[str, dict[str, str]]:
    """{case name: {file: text}} as Python renders the case table."""
    cases = write_case_dirs(tmp / "cases")
    error = render_with("python", tmp / "case-out" / "python", [d for _, d in cases])
    if error:
        raise SystemExit(f"python report-formats: {error}")
    return {
        name: {
            f: (tmp / "case-out" / "python" / str(i) / f)
            .read_bytes()
            .decode("latin-1" if f in BINARY else "utf-8")
            for f in FILES
        }
        for i, (name, _) in enumerate(cases)
    }


def _expected_text(outputs: dict[str, Any]) -> str:
    return json.dumps(outputs, indent=2, ensure_ascii=True) + "\n"


def check_cases(
    tmp: Path, engines: tuple[str, ...], fail_fast: bool
) -> tuple[int, int]:
    outputs = python_case_outputs(tmp)
    failures = 0
    if not EXPECTED.is_file() or EXPECTED.read_text("utf-8") != _expected_text(outputs):
        print(
            f"MISMATCH: {EXPECTED.name} is not Python's current rendering of {CASES.name}; "
            "run report_format_parity_check.py --write-expected",
            file=sys.stderr,
        )
        failures += 1
    sources = [tmp / "cases" / name for name in outputs]
    errors = {e: render_with(e, tmp / "case-out" / e, sources) for e in engines[1:]}
    for i, name in enumerate(outputs):
        problems = []
        for engine in engines[1:]:
            if errors[engine]:
                problems.append(f"{engine} report-formats seam: {errors[engine]}")
            else:
                problems += file_problems(
                    engine,
                    tmp / "case-out" / "python" / str(i),
                    tmp / "case-out" / engine / str(i),
                )
        failures += _report(f"case {name}", problems)
        if problems and fail_fast:
            break
    return failures, len(outputs)


PYTHON_MD = """
import json, sys
from agentce.report import render_remediation_md
cases = json.load(open(sys.argv[1], encoding="utf-8"))["cases"]
json.dump({c["name"]: render_remediation_md(c["package"]) for c in cases}, open(sys.argv[2], "w", encoding="utf-8"))
"""

PYTHON_SKILL = """
import json, sys
from agentce.report import (_skill_finding_filename, _tool_names_for_subject, render_reverify_md,
                            render_skill_finding_md, render_skill_md)
def one(c):
    kind = c["kind"]
    if kind == "skill":
        return render_skill_md(spec_version=c["spec_version"], cli_version=c["cli_version"],
                               catalog_version=c["catalog_version"], assertions_digest=c["assertions_digest"])
    if kind == "finding":
        return render_skill_finding_md(c["finding"], c["tool_calls"])
    if kind == "reverify":
        return render_reverify_md(c["argv"])
    if kind == "tool_names":
        return "\\n".join(_tool_names_for_subject(c["events"]))
    if kind == "filename":
        return _skill_finding_filename(c["control"], c["index"])
    raise SystemExit("unknown kind " + kind)
cases = json.load(open(sys.argv[1], encoding="utf-8"))["cases"]
json.dump({c["name"]: one(c) for c in cases}, open(sys.argv[2], "w", encoding="utf-8"))
"""


PYTHON_SECURITY = """
import json, sys
from agentce.report import render_security_html, render_security_md
cases = json.load(open(sys.argv[1], encoding="utf-8"))["cases"]
out = {}
for c in cases:
    out[c["name"] + ".md"] = render_security_md(c["view"], language=c["language"])
    out[c["name"] + ".html"] = render_security_html(c["view"], language=c["language"])
json.dump(out, open(sys.argv[2], "w", encoding="utf-8"))
"""


PYTHON_BUYER = """
import json, sys
from types import SimpleNamespace
from agentce.report import render_buyer_html, render_buyer_md
cases = json.load(open(sys.argv[1], encoding="utf-8"))["cases"]
out = {}
for c in cases:
    kw = dict(language=c["language"], reverify_command=c["argv"], packaged=c["packaged"],
              report_folder=c["folder"], catalogs=[SimpleNamespace(id=i, version=v) for i, v in c["catalogs"]])
    out[c["name"] + ".md"] = render_buyer_md(c["view"], **kw)
    out[c["name"] + ".html"] = render_buyer_html(c["view"], **kw)
json.dump(out, open(sys.argv[2], "w", encoding="utf-8"))
"""


class CaseTable(NamedTuple):
    """A fixed case table each engine renders through a seam verb, with Python's golden and renderer."""

    label: str
    verb: str
    cases: Path
    expected: Path
    script: str


CASE_TABLES = (
    CaseTable("remediation case", "remediation-md", MD_CASES, MD_EXPECTED, PYTHON_MD),
    CaseTable("skill case", "skill-cases", SKILL_CASES, SKILL_EXPECTED, PYTHON_SKILL),
    CaseTable(
        "security case",
        "security-cases",
        SECURITY_CASES,
        SECURITY_EXPECTED,
        PYTHON_SECURITY,
    ),
    CaseTable("buyer case", "buyer-cases", BUYER_CASES, BUYER_EXPECTED, PYTHON_BUYER),
)


def render_table_with(
    engine: str, out: Path, verb: str, cases: Path, script: str
) -> dict[str, str] | str:
    """One engine's rendering of every case of `cases` through its `verb` seam, or the engine's error."""
    cmd = _python(script) if engine == "python" else [*_seam(engine), verb]
    proc = subprocess.run(
        [*cmd, str(cases), str(out)],
        capture_output=True,
        text=True,
        cwd=ROOT,
        env=_env(),
    )
    if proc.returncode != 0:
        return f"exit {proc.returncode}: {proc.stderr.strip()[-400:]}"
    return json.loads(out.read_text("utf-8"))


def check_case_table(
    tmp: Path, engines: tuple[str, ...], fail_fast: bool, table: CaseTable
) -> tuple[int, int]:
    label, verb, cases, expected, script = table
    python = render_table_with(
        "python", tmp / f"{verb}-python.json", verb, cases, script
    )
    if isinstance(python, str):
        raise SystemExit(f"python {verb}: {python}")
    failures = 0
    if not expected.is_file() or expected.read_text("utf-8") != _expected_text(python):
        print(
            f"MISMATCH: {expected.name} is not Python's current rendering of {cases.name}; "
            "run report_format_parity_check.py --write-expected",
            file=sys.stderr,
        )
        failures += 1
    rendered = {
        e: render_table_with(e, tmp / f"{verb}-{e}.json", verb, cases, script)
        for e in engines[1:]
    }
    for name, want in python.items():
        problems = []
        for engine, got in rendered.items():
            if isinstance(got, str):
                problems.append(f"{engine} {verb} seam: {got}")
            elif got.get(name) != want:
                problems.append(
                    f"{engine} {verb}: "
                    + _first_difference(want.encode(), str(got.get(name)).encode())
                )
        failures += _report(f"{label} {name}", problems)
        if problems and fail_fast:
            break
    return failures, len(python)


def write_expected() -> int:
    with tempfile.TemporaryDirectory(prefix="report-format-expected-") as raw:
        EXPECTED.write_text(_expected_text(python_case_outputs(Path(raw))), "utf-8")
        for _, verb, cases, expected, script in CASE_TABLES:
            rendered = render_table_with(
                "python", Path(raw) / f"{verb}.json", verb, cases, script
            )
            if isinstance(rendered, str):
                raise SystemExit(f"python {verb}: {rendered}")
            expected.write_text(_expected_text(rendered), "utf-8")
            print(f"wrote {expected}")
    print(f"wrote {EXPECTED}")
    return 0


def _report(label: str, problems: list[str]) -> int:
    for problem in problems:
        print(f"MISMATCH: {label}: {problem}", file=sys.stderr)
    if not problems:
        print(f"MATCH: {label}")
    return 1 if problems else 0


def run_check(engines: tuple[str, ...], fail_fast: bool) -> int:
    with tempfile.TemporaryDirectory(prefix="report-format-parity-") as raw:
        tmp = Path(raw)
        # The case table runs first: it takes seconds, so --fail-fast stops on a renderer fault before
        # the corpus assess. Python's own seam over its assess output must give assess's own bytes, so
        # the seam's inputs (assertions.json, oscal-ar.json, the manifest's catalogs) are what assess
        # renders from.
        failures, runs = check_cases(tmp, engines, fail_fast)
        for table in CASE_TABLES:
            if not (failures and fail_fast):
                table_failures, table_runs = check_case_table(
                    tmp, engines, fail_fast, table
                )
                failures += table_failures
                runs += table_runs
        if not (failures and fail_fast):
            corpus_failures, corpus_runs = check_corpus(
                tmp / "corpus-run", engines, fail_fast
            )
            failures += corpus_failures
            runs += corpus_runs
        if not (failures and fail_fast):
            buyer_failures, buyer_count = check_buyer(
                tmp / "buyer-run", engines, fail_fast
            )
            failures += buyer_failures
            runs += buyer_count
    if failures:
        print(f"{failures} of {runs} runs differ", file=sys.stderr)
        return 1
    expected = (
        CORPUS_RUNS
        + REMEDIATION_ONLY_RUNS
        + BUYER_RUNS
        + len(json.loads(CASES.read_text("utf-8"))["cases"])
        + sum(
            len(json.loads(table.expected.read_text("utf-8"))) for table in CASE_TABLES
        )
    )
    if runs != expected:
        print(f"ran {runs} runs, expected {expected}", file=sys.stderr)
        return 1
    print(f"REPORT FORMAT PARITY OK ({runs} runs)")
    return 0


def self_test() -> int:
    """A correct copy passes; each tamper class on one of the five files is caught, and an engine
    started_at of the wrong shape is refused before its clock is pinned."""
    good = {
        "report.junit.xml": b'<?xml version="1.0" encoding="UTF-8"?>\n<testsuite name="agentce" />\n',
        "report.csv": b"control,subject,outcome,control_version,rung,mode\nA-01,s,conformant,1,2,automated\n",
        "oscal-ar.xml": b'<?xml version="1.0" encoding="UTF-8"?>\n<assessment-results><a>x</a></assessment-results>\n',
        "public-statement.md": b"# Public conformance statement\n\n## Basis\nx\n",
        "report.pdf": b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n(AgentCE-Konformit\xe4tsbericht) Tj\n%%EOF\n",
    }
    tampers: dict[str, tuple[str, bytes | None]] = {
        "one byte changed": (
            "report.csv",
            good["report.csv"].replace(b"A-01", b"A-02"),
        ),
        "file missing": ("oscal-ar.xml", None),
        "extra trailing newline": (
            "report.junit.xml",
            good["report.junit.xml"] + b"\n",
        ),
        "CRLF line endings": (
            "public-statement.md",
            good["public-statement.md"].replace(b"\n", b"\r\n"),
        ),
        "pdf Latin-1 byte written as UTF-8": (
            "report.pdf",
            good["report.pdf"].replace(b"\xe4", "\u00e4".encode()),
        ),
        "pdf missing": ("report.pdf", None),
    }
    failures = []
    with tempfile.TemporaryDirectory(prefix="report-format-self-test-") as raw:
        tmp = Path(raw)

        def write(d: Path, files: dict[str, bytes | None]) -> Path:
            d.mkdir()
            for name, data in files.items():
                if data is not None:
                    (d / name).write_bytes(data)
            return d

        reference = write(tmp / "reference", dict(good))
        if file_problems("typescript", reference, write(tmp / "same", dict(good))):
            failures.append("a correct copy was flagged")
        for i, (name, (target, data)) in enumerate(tampers.items()):
            candidate = write(tmp / f"t{i}", {**good, target: data})
            if file_problems("java", reference, candidate):
                print(f"caught: {name}")
            else:
                failures.append(f"tamper not caught: {name}")
        failures += _clock_self_test(tmp / "clock")
        failures += _emitted_self_test(tmp / "emitted")
        failures += _view_self_test(tmp / "security", SECURITY_FILES)
        failures += _view_self_test(tmp / "buyer", BUYER_FILES)
        failures += _seam_for_self_test(tmp / "seam-for")
    for failure in failures:
        print(f"SELF-TEST FAIL: {failure}", file=sys.stderr)
    if failures:
        return 1
    print("SELF-TEST OK")
    return 0


def _canonical(value: object) -> bytes:
    """RFC 8785 bytes for the self-test's plain ASCII values (sorted keys, no whitespace)."""
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _emitted_self_test(tmp: Path) -> list[str]:
    """emitted_problems passes a correct copy and catches each remediation and skill tamper class."""
    rel, skill = "remediation/s", "skill/s"
    engine = {"impl": "agentce-ts", "version": "0.1.0", "spec_version": "0.6"}

    def package(impl: dict[str, str], subject: str = "s") -> dict[str, object]:
        body = {
            "package_version": 1,
            "generated_from": {
                "assertions_digest": "sha256:ab",
                "engine": impl,
                "catalogs": [],
            },
            "subject": subject,
            "findings": [],
            "not_assessed": [],
            "deviations_applied": [],
        }
        digest = hashlib.sha256(_canonical(body)).hexdigest()
        return {"$schema": "x", "$id": PACKAGE_ID + digest, **body}

    md = b"# Remediation prompt\n"
    skill_md = b"---\nname: agentce-fix-gaps\n---\n"
    note = b"# A-01 \xe2\x80\x94 x\n"
    reverify = b"# Re-verify\n\n`agentce assess .`\n"
    good_pkg = package(engine)

    def report(
        d: Path, files: dict[str, bytes], outputs: dict[str, bytes] | None = None
    ) -> Path:
        for name, data in files.items():
            (d / name).parent.mkdir(parents=True, exist_ok=True)
            (d / name).write_bytes(data)
        listed = files if outputs is None else outputs
        manifest = {
            "engine": {**engine, "package_digest": "sha256:cd"},
            "outputs": {
                n: "sha256:" + hashlib.sha256(b).hexdigest() for n, b in listed.items()
            },
        }
        (d / "manifest.json").write_text(json.dumps(manifest), "utf-8")
        return d

    files = {
        f"{rel}/remediation-package.json": _canonical(good_pkg),
        f"{rel}/remediation.md": md,
        f"{skill}/SKILL.md": skill_md,
        f"{skill}/findings/A-01--1.md": note,
        f"{skill}/REVERIFY.md": reverify,
        f"{skill}/remediation-package.json": _canonical(good_pkg),
    }
    reference = report(tmp / "reference", files)
    stale = dict(good_pkg, subject="t")
    tampers: dict[str, tuple[dict[str, bytes], dict[str, bytes] | None]] = {
        "a key changed outside engine and $id": (
            {
                **files,
                f"{rel}/remediation-package.json": _canonical(package(engine, "t")),
            },
            None,
        ),
        "a wrong engine block": (
            {
                **files,
                f"{rel}/remediation-package.json": _canonical(
                    package({**engine, "impl": "x"})
                ),
            },
            None,
        ),
        "a stale $id": (
            {**files, f"{rel}/remediation-package.json": _canonical(stale)},
            None,
        ),
        "non-canonical package bytes": (
            {
                **files,
                f"{rel}/remediation-package.json": json.dumps(
                    good_pkg, indent=1
                ).encode(),
            },
            None,
        ),
        "a remediation file missing": (
            {k: v for k, v in files.items() if k != f"{rel}/remediation-package.json"},
            None,
        ),
        "an extra remediation file": (
            {**files, "remediation/u/remediation.md": md},
            None,
        ),
        "a remediation file missing from manifest.outputs": (
            files,
            {f"{rel}/remediation.md": md},
        ),
        "remediation.md one byte changed": (
            {**files, f"{rel}/remediation.md": md.replace(b"#", b"=")},
            None,
        ),
        "a skill file missing": (
            {k: v for k, v in files.items() if k != f"{skill}/REVERIFY.md"},
            None,
        ),
        "an extra skill file": (
            {**files, f"{skill}/findings/A-01--2.md": note},
            None,
        ),
        "a skill file missing from manifest.outputs": (
            files,
            {k: v for k, v in files.items() if k != f"{skill}/SKILL.md"},
        ),
        "SKILL.md one byte changed": (
            {**files, f"{skill}/SKILL.md": skill_md.replace(b"fix", b"fax")},
            None,
        ),
        "a finding note one byte changed": (
            {**files, f"{skill}/findings/A-01--1.md": note.replace(b"x", b"y")},
            None,
        ),
        "REVERIFY.md one byte changed": (
            {**files, f"{skill}/REVERIFY.md": reverify.replace(b".", b"/")},
            None,
        ),
        "a skill package key changed outside engine and $id": (
            {
                **files,
                f"{skill}/remediation-package.json": _canonical(package(engine, "t")),
            },
            None,
        ),
    }

    def digest_of(value: object) -> str:
        return hashlib.sha256(_canonical(value)).hexdigest()

    failures = []
    if emitted_problems(
        "typescript", reference, report(tmp / "same", files), digest_of
    ):
        failures.append("a correct remediation copy was flagged")
    for i, (name, (candidate, outputs)) in enumerate(tampers.items()):
        if emitted_problems(
            "java", reference, report(tmp / f"t{i}", candidate, outputs), digest_of
        ):
            print(f"caught: {name}")
        else:
            failures.append(f"tamper not caught: {name}")
    return failures


def _view_self_test(tmp: Path, files: tuple[str, ...]) -> list[str]:
    """view_problems passes a correct copy of `files` and catches each tamper class."""
    view = files[0].split(".")[0]
    good = {
        files[0]: b'{"drift":{"models":[],"tools":[]}}',
        files[1]: b"# A view\n",
        files[2]: b"<!doctype html><title>A view</title>\n",
    }

    def report(
        d: Path, files: dict[str, bytes], outputs: dict[str, bytes] | None = None
    ) -> Path:
        d.mkdir(parents=True)
        for name, data in files.items():
            (d / name).write_bytes(data)
        listed = files if outputs is None else outputs
        digests = {
            n: "sha256:" + hashlib.sha256(b).hexdigest() for n, b in listed.items()
        }
        (d / "manifest.json").write_text(json.dumps({"outputs": digests}), "utf-8")
        return d

    reference = report(tmp / "reference", good)
    tampers: dict[str, tuple[dict[str, bytes], dict[str, bytes] | None]] = {
        f"a {view} file missing": (
            {k: v for k, v in good.items() if k != files[1]},
            None,
        ),
        f"an extra {view} file": (good, None),
        f"a {view} file missing from manifest.outputs": (
            good,
            {k: v for k, v in good.items() if k != files[2]},
        ),
        f"a {view} digest not python's": (good, {**good, files[0]: b"x"}),
        **{
            f"{name} one byte changed": (
                {**good, name: data[:-1] + bytes([data[-1] ^ 1])},
                None,
            )
            for name, data in good.items()
        },
    }
    failures = []
    if view_problems("typescript", reference, report(tmp / "same", good), files):
        failures.append(f"a correct {view} copy was flagged")
    for i, (name, (written, outputs)) in enumerate(tampers.items()):
        ref = reference
        if name == f"an extra {view} file":
            ref = report(
                tmp / f"r{i}", {k: v for k, v in good.items() if k != files[2]}
            )
        if view_problems("java", ref, report(tmp / f"t{i}", written, outputs), files):
            print(f"caught: {name}")
        else:
            failures.append(f"tamper not caught: {name}")
    return failures


def _seam_for_self_test(tmp: Path) -> list[str]:
    """Each engine's assess-emit seam refuses for= with an unknown, empty or repeated preset (exit 3)."""
    failures = []
    for engine in ENGINES[1:]:
        for token in (
            "for=nonsense",
            "for=",
            "for=security,for=security",
            "for=buyer,for=buyer",
        ):
            proc = subprocess.run(
                [
                    *_seam(engine),
                    "assess-emit",
                    token,
                    "assess",
                    "--out",
                    str(tmp / engine),
                ],
                capture_output=True,
                text=True,
                cwd=ROOT,
                env=_env(),
            )
            if proc.returncode != 3:
                failures.append(
                    f"{engine} assess-emit {token!r} exited {proc.returncode}, want 3"
                )
            else:
                print(f"caught: {engine} assess-emit {token!r}")
    return failures


def _clock_self_test(tmp: Path) -> list[str]:
    """pin_started_at copies Python's started_at onto a well-shaped engine value and refuses any other."""
    failures = []
    for name, value, refused in (
        ("well-shaped", "2026-10-25T22:32:40Z", False),
        ("milliseconds", "2026-10-25T22:32:40.123Z", True),
        ("offset", "2026-10-25T22:32:40+00:00", True),
        ("missing", None, True),
    ):
        reference, candidate = tmp / name / "python", tmp / name / "engine"
        for d, started_at in ((reference, "2026-10-25T22:32:38Z"), (candidate, value)):
            d.mkdir(parents=True)
            run = {} if started_at is None else {"started_at": started_at}
            (d / "manifest.json").write_text(json.dumps({"run": run}), "utf-8")
        problem = pin_started_at(reference, candidate)
        pinned = json.loads((candidate / "manifest.json").read_text("utf-8"))["run"]
        if bool(problem) != refused:
            failures.append(f"started_at {name}: refused={bool(problem)}")
        elif not refused and pinned["started_at"] != "2026-10-25T22:32:38Z":
            failures.append(f"started_at {name}: not pinned to python's value")
        else:
            print(
                f"caught: started_at {name}"
                if refused
                else f"pinned: started_at {name}"
            )
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="report_format_parity_check", description=__doc__.split("\n")[0]
    )
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--fail-fast", action="store_true")
    parser.add_argument("--engine", choices=["python"], default=None)
    parser.add_argument("--write-expected", action="store_true")
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()
    if args.write_expected:
        return write_expected()
    return run_check(("python",) if args.engine else ENGINES, args.fail_fast)


if __name__ == "__main__":
    raise SystemExit(main())
