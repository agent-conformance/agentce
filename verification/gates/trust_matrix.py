#!/usr/bin/env python3
"""Build gate VG-TRUST-MATRIX (item 18.176): the trust-model case matrix in all three engines.

fixtures/trust_matrix/matrix.yaml holds the cells; each names its controls, its dimension values, the SPEC section its
expected outcome comes from, and one of: ``fixture`` (a builder in fixtures/trust_matrix/build.py, optional
``fixture_args``) plus ``expected``; ``cannot_occur`` (the reason); ``ruling_needed`` (the readings the SPEC allows).
``expected`` is one word, a list of acceptable words, or a mapping control -> word or list. Words: conformant,
non_conformant, insufficient_evidence, partial, not_applicable, not_assessed, refuse (exit 3, nothing assessed) and
not_conformant (any of non_conformant, insufficient_evidence, partial).

Every selected cell's bundle is built into a temporary folder and ``agentce assess`` runs in each engine's built CLI
(the Python environment's own entry point, ``node dist/cli.js``, the Java jar). The control's outcome for the
fixture's subject and its failing focus ids are read from assertions.json. Status per cell and control, first that
applies:
  GAP       an engine reads a pass (conformant, or not_applicable where no pass is expected) the SPEC rules out
  DIFF      the engines disagree on the outcome, the failing events or the refusal key
  ERROR     the fixture could not be built, or an engine crashed, timed out or wrote no assertion for the control
  AGREE     every engine reads an expected word (flag ``word`` when they agree on another not-passing word)
  MISMATCH  the engines agree and fail closed or refuse where the SPEC expects otherwise
A cell's status is its worst control's. fixtures/trust_matrix/open.json names every cell known not to read AGREE,
with its status and the item that closes it. The gate fails on a cell that reads other than AGREE and is not listed
(open.unlisted), a listed cell that reads AGREE (open.listed_agree) or another status than listed
(open.status_changed), and on any coverage failure; every coverage failure is printed before any cell runs, and the
cells still run, so one run shows all of them:
  coverage.digest          the matrix's SHA-256 is not DIGEST (edit the generator, then re-pin)
  coverage.unused_value    a declared dimension value no runnable cell uses (a cell leaving a dimension out uses
                           its first declared value, the builders' default)
  coverage.catalog_focus   a control in spec/catalogs whose min_source_class is above self_report has no cell for
                           one of the three focus classes (a control named only by a cannot_occur cell is exempt)
  coverage.ruling_ceiling  more ruling_needed cells than RULING_CEILING
  coverage.open_owner      an open entry with no owning item id
  coverage.open_grew       an open entry outside OPEN_PINNED: the list only shrinks
  coverage.quick_missing   a cell of REQUIRED_QUICK left out of the matrix's quick list

    bash verification/gates/trust_matrix.sh [--matrix PATH] [--open PATH]   the gate (builds the engines first)
    ... trust_matrix.py --self-test                                         the gate's own logic on small inputs

Environment: AGENTCE_VERIFY_TIER quick (default: the matrix's quick list) or full (every cell);
AGENTCE_TRUST_MATRIX_ONLY comma-separated cell ids to run instead of the tier's list; AGENTCE_TRUST_MATRIX_OUT a folder
that keeps results.tsv and results.md. Exit 0 pass, 1 fail, 2 a refused option or environment value.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

import yaml
from cli_options_check import engine_cmds

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "verification/gates/fixtures/trust_matrix"
TIER_ENV, ONLY_ENV, OUT_ENV = (
    "AGENTCE_VERIFY_TIER",
    "AGENTCE_TRUST_MATRIX_ONLY",
    "AGENTCE_TRUST_MATRIX_OUT",
)
#: SHA-256 of fixtures/trust_matrix/matrix.yaml: a matrix edit is deliberate.
DIGEST = "320f5f5427995641ea964c6cdc9b2a8ec36b3d19b4ec99ce0872e8b1bcd2e620"
#: The cell ids open.json may hold. Closing a cell removes it here and from open.json in one commit; nothing is added.
OPEN_PINNED: frozenset[str] = frozenset(
    {
        "c05-integ-verified_signed-gateway-user",
        "c05-integ-verified_signed-ingress-user",
        "c06-acted-EP-missing",
        "c06-acted-EP-user",
        "c06-ep-deny-no-refusal",
        "c06-ep-deny-sr-refusal",
        "fx-DOC-03-en-pass-uea",
        "fx-DOC-03-en-pass-uep",
        "fx-INT-01-en-coverage-unknown",
        "fx-INT-04-en-coverage-unknown",
        "fx-ROB-05-en-pass-uea",
        "fx-ROB-05-en-pass-uep",
        "fx-ROB-06-en-pass-uea",
        "fx-ROB-06-en-pass-uep",
        "r02-grid-none-ep_untrusted",
        "r02-grid-sr_trusted-ep_untrusted",
        "r02-grid-unmarked_trusted_origin-ep_untrusted",
        "r02-integ-verified_signed-ep_trusted",
        "r02-other-subject-ep_quarantined-ep_trusted",
        "r02-other-subject-ep_untrusted-ep_trusted",
        "r02-other-subject-sr_untrusted-ep_trusted",
    }
)
#: The cells the quick tier must run: the witness, principal and timing cells of 18.176, the CND-05 cells the
#: seeded faults turn red, and CND-01's declared task-scope cells (18.174).
REQUIRED_QUICK: frozenset[str] = frozenset(
    {
        "c05-acted-Decision_only-se-user",
        "c05-acted-both-se-user",
        "c05-both-twin-after-call",
        "c05-both-twin-before-call",
        "c05-both-twin-same-instant",
        "c05-call-bad-time-twin",
        "c05-call-no-time-twin",
        "c05-corr-SR-user-none-present",
        "c05-cycle-a-pd-b-ep",
        "c05-cycle-self-parent-pd",
        "c05-dec-forged-late-twin-after-call",
        "c05-dec-sr-call-twin-before",
        "c05-dec-twin-after-call",
        "c05-dec-twin-before-call",
        "c05-dec-twin-same-instant",
        "c05-deconly-twin",
        "c05-depth2-SR-corr-ep_pd_principal",
        "c05-depth2-SR-twin",
        "c05-depth3-SR-corr-ep_pd_principal",
        "c05-depth3-SR-twin",
        "c05-pd-after-call",
        "c05-pd-after-call-1s",
        "c05-pd-bad-time",
        "c05-pd-before-call",
        "c05-pd-no-time",
        "c05-pd-other-request",
        "c05-pd-same-instant",
        "c05-pd-same-instant-offset",
        "c05-relabel-SR-user-ep_twin_untrusted",
        "c05-sr-call-forged-late-pd",
        "c05-sr-call-forged-late-twin",
        "c05-sr-call-pd-before",
        "c05-sr-call-twin-before",
        "c05-strip-se-operator",
        "c05-strip-se-user",
        "c05-twin-after-call",
        "c05-twin-after-call-1s",
        "c05-twin-bad-time",
        "c05-twin-before-call",
        "c05-twin-both-no-principal",
        "c05-twin-no-principal-instr-present",
        "c05-twin-no-time",
        "c05-twin-origin-ep_read_untrusted",
        "c05-twin-origin-sr_read",
        "c05-twin-other-authorised-class",
        "c05-twin-other-content",
        "c05-twin-other-principal",
        "c05-twin-other-subject",
        "c05-twin-other-task",
        "c05-twin-parent-tool_output",
        "c05-twin-principal-empty-id",
        "c05-twin-principal-empty-object",
        "c05-twin-principal-string",
        "c05-twin-same-instant",
        "c05-twin-same-instant-offset",
        "fx-CND-01-en-fail-uea",
        "fx-CND-01-en-fail-uep",
        "fx-CND-01-en-pass-uea",
        "fx-CND-01-en-pass-uep",
        "fx-CND-01-se-fail-uea",
        "fx-CND-01-se-fail-uep",
        "fx-CND-01-se-pass-uea",
    }
)
RULING_CEILING = 1
FOCUS_CLASSES = ("enforcement_point", "independent_system", "self_report")
CLASS_DIMS = ("focus_class", "instruction_class", "call_class", "decision_class")
OWNER = re.compile(r"^\d+\.\d+[a-z]?$")

ENGINES = ("python", "typescript", "java")
WORDS = {
    "conformant",
    "non_conformant",
    "insufficient_evidence",
    "partial",
    "not_applicable",
    "not_assessed",
    "refuse",
}
ALIASES = {"not_conformant": ("non_conformant", "insufficient_evidence", "partial")}
PASSING = {"conformant", "not_applicable"}
NOT_PASSING = {"non_conformant", "insufficient_evidence", "partial", "not_assessed"}
SEVERITY = ("GAP", "DIFF", "MISMATCH", "ERROR", "AGREE")
KEY = re.compile(r"(?:^|\[)([a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+)(?:\]|:)", re.M)


class Refused(Exception):
    """An option or environment value the gate refuses (exit 2), with its message key."""

    def __init__(self, key: str, text: str) -> None:
        super().__init__(f"{key}: {text}")
        self.key = key


@dataclass(frozen=True)
class Reading:
    word: str  # the outcome with '-' as '_', or "refuse" or "error"
    focus: tuple[str, ...] = ()
    detail: str = ""  # the refusal key, or what went wrong

    def show(self) -> str:
        text = self.word + (f"[{','.join(self.focus)}]" if self.focus else "")
        return text + (f" ({self.detail})" if self.detail else "")


# ---------------------------------------------------------------------------------------------------- options


def parse_args(argv: list[str]) -> dict[str, Any]:
    opts: dict[str, Any] = {}
    i = 0
    while i < len(argv):
        token = argv[i]
        name, eq, value = token.partition("=")
        if name not in ("--matrix", "--open", "--self-test"):
            raise Refused("trust_matrix.unknown_option", f"unknown option {token!r}")
        if name in opts:
            raise Refused("trust_matrix.repeated_option", f"{name} given twice")
        if name == "--self-test":
            if eq:
                raise Refused(
                    "trust_matrix.unknown_option",
                    f"--self-test takes no value ({token!r})",
                )
            opts[name] = True
        elif eq:
            opts[name] = value
        elif i + 1 < len(argv) and not argv[i + 1].startswith("--"):
            i += 1
            opts[name] = argv[i]
        else:
            raise Refused("trust_matrix.option_value_missing", f"{name} needs a path")
        if name != "--self-test" and not opts[name]:
            raise Refused("trust_matrix.option_value_missing", f"{name} needs a path")
        i += 1
    if "--self-test" in opts and len(opts) > 1:
        raise Refused(
            "trust_matrix.self_test_alone", "--self-test takes no other option"
        )
    return opts


def tier_of(env: dict[str, str]) -> str:
    tier = env.get(TIER_ENV, "quick")
    if tier not in ("quick", "full"):
        raise Refused(
            "trust_matrix.tier_unknown", f"{TIER_ENV}={tier!r} is not quick or full"
        )
    return tier


def select(matrix: dict[str, Any], env: dict[str, str]) -> list[str]:
    """The cell ids this run judges: AGENTCE_TRUST_MATRIX_ONLY's, else the tier's (full: every cell)."""
    tier = tier_of(env)
    ids = [str(c["id"]) for c in matrix["cells"]]
    if ONLY_ENV in env:
        only = [t.strip() for t in env[ONLY_ENV].split(",") if t.strip()]
        if not only:
            raise Refused(
                "trust_matrix.only_empty", f"{ONLY_ENV} is set but names no cell"
            )
        unknown = sorted(set(only) - set(ids))
        if unknown:
            raise Refused(
                "trust_matrix.only_unknown_cell",
                f"{ONLY_ENV} names no cell {', '.join(unknown)}",
            )
        wanted = set(only)
        return [i for i in ids if i in wanted]
    if tier == "full":
        return ids
    quick = {str(q) for q in matrix.get("quick") or []}
    return [i for i in ids if i in quick]


# ---------------------------------------------------------------------------------------------------- matrix


def expected_words(cell: dict[str, Any], control: str) -> tuple[str, ...]:
    exp = cell["expected"]
    exp = exp.get(control) if isinstance(exp, dict) else exp
    words: list[str] = []
    for word in [exp] if isinstance(exp, str) else list(exp or []):
        words.extend(ALIASES.get(str(word), (str(word),)))
    return tuple(sorted(set(words)))


def load_builders(name: str) -> ModuleType:
    path = FIXTURES / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"trust_matrix_{name}", path)
    if spec is None or spec.loader is None or not path.is_file():
        raise SystemExit(f"trust_matrix: no builder module {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve their module by name
    spec.loader.exec_module(module)
    return module


def check_matrix(data: dict[str, Any], module: ModuleType) -> list[str]:
    """The matrix's own form: every problem at once, before anything runs."""
    errors: list[str] = []
    dims = {
        str(d["name"]): {str(v) for v in d.get("values", [])}
        for d in data.get("dimensions") or []
    }
    seen: set[str] = set()
    for n, cell in enumerate(data.get("cells") or []):
        cid = str(cell.get("id") or f"#{n + 1}")
        if cid in seen:
            errors.append(f"{cid}: duplicate id")
        seen.add(cid)
        if not cell.get("controls") or not isinstance(cell["controls"], list):
            errors.append(f"{cid}: controls must be a non-empty list")
        if not cell.get("spec_ref"):
            errors.append(f"{cid}: spec_ref missing")
        for name, value in (cell.get("dims") or {}).items():
            if str(name) not in dims:
                errors.append(f"{cid}: dimension {name!r} is not declared")
            elif str(value) not in dims[str(name)]:
                errors.append(
                    f"{cid}: {name}={value} is not one of the declared values"
                )
        kinds = [k for k in ("fixture", "cannot_occur", "ruling_needed") if cell.get(k)]
        if len(kinds) != 1:
            errors.append(
                f"{cid}: needs exactly one of fixture, cannot_occur, ruling_needed (has {kinds or 'none'})"
            )
        elif kinds == ["fixture"]:
            if not callable(getattr(module, str(cell["fixture"]), None)):
                errors.append(f"{cid}: fixture {cell['fixture']!r} is not a builder")
            for control in cell.get("controls") or []:
                words = expected_words(cell, control) if "expected" in cell else ()
                if not words or not set(words) <= WORDS:
                    errors.append(
                        f"{cid}: expected for {control} must be words from {sorted(WORDS | set(ALIASES))}"
                    )
    runnable = {str(c.get("id")) for c in data.get("cells") or [] if c.get("fixture")}
    errors += [
        f"quick: {q} is not a runnable cell"
        for q in data.get("quick") or []
        if str(q) not in runnable
    ]
    if not data.get("cells"):
        errors.append("no cells")
    return errors


def catalog_minimums(root: Path) -> dict[str, str]:
    """Each catalog control's min_source_class, by control id (spec/catalogs/**/controls/*.yaml)."""
    found: dict[str, str] = {}
    for path in sorted((root / "spec/catalogs").rglob("controls/*.yaml")):
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        floor = _find(doc, "min_source_class")
        if isinstance(floor, str):
            found[str(doc.get("id") or path.stem)] = floor
    return found


def _find(doc: Any, key: str) -> Any:
    if isinstance(doc, dict):
        if key in doc:
            return doc[key]
        for value in doc.values():
            hit = _find(value, key)
            if hit is not None:
                return hit
    return None


def coverage(
    raw: bytes,
    matrix: dict[str, Any],
    open_map: dict[str, Any],
    minimums: dict[str, str],
    *,
    digest: str = DIGEST,
    pinned: frozenset[str] = OPEN_PINNED,
    required_quick: frozenset[str] = REQUIRED_QUICK,
    ceiling: int = RULING_CEILING,
) -> list[str]:
    """Every coverage failure, each led by its key."""
    problems: list[str] = []
    actual = hashlib.sha256(raw).hexdigest()
    if actual != digest:
        problems.append(
            f"coverage.digest: the matrix's SHA-256 is {actual}, the pin is {digest}"
        )
    cells = matrix["cells"]
    runnable = [c for c in cells if c.get("fixture")]
    for dim in matrix.get("dimensions") or []:
        # A cell that leaves a dimension out takes its default, the first value declared.
        default = str((dim.get("values") or [None])[0])
        used = {str((c.get("dims") or {}).get(dim["name"], default)) for c in runnable}
        for value in dim.get("values") or []:
            if str(value) not in used:
                problems.append(
                    f"coverage.unused_value: {dim['name']}={value} is used by no runnable cell"
                )
    for control, floor in sorted(minimums.items()):
        if floor == "self_report":
            continue
        named = [c for c in cells if control in c["controls"]]
        if named and all(c.get("cannot_occur") for c in named):
            continue
        classes = {
            str(v)
            for c in named
            if not c.get("cannot_occur")
            for k, v in (c.get("dims") or {}).items()
            if k in CLASS_DIMS
        }
        for missing in [f for f in FOCUS_CLASSES if f not in classes]:
            problems.append(
                f"coverage.catalog_focus: {control} (min_source_class {floor}) has no cell whose "
                f"focus class is {missing}"
            )
    rulings = sum(1 for c in cells if c.get("ruling_needed"))
    if rulings > ceiling:
        problems.append(
            f"coverage.ruling_ceiling: {rulings} ruling_needed cells, the ceiling is {ceiling}"
        )
    for cid, entry in sorted(open_map.items()):
        if not (isinstance(entry, dict) and OWNER.match(str(entry.get("owner") or ""))):
            problems.append(
                f"coverage.open_owner: open entry {cid} names no owning item"
            )
        if cid not in pinned:
            problems.append(
                f"coverage.open_grew: open entry {cid} is not in OPEN_PINNED (the open list only shrinks)"
            )
    quick = {str(q) for q in matrix.get("quick") or []}
    for cid in sorted(required_quick - quick):
        problems.append(
            f"coverage.quick_missing: {cid} must be in the matrix's quick list"
        )
    return problems


# ---------------------------------------------------------------------------------------------------- running


def run_engine(
    cmd: list[str], root: Path, fx: Any, out: Path, timeout: float = 300.0
) -> dict[str, Any]:
    args = [
        "assess",
        "--bundle",
        str(fx.bundle),
        "--profile",
        str(fx.profile),
        "--domain",
        str(fx.domain),
        "--out",
        str(out),
    ]
    try:
        p = subprocess.run(
            cmd + args, cwd=root, capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        return {"exit": None, "error": f"timed out after {timeout:.0f}s"}
    run: dict[str, Any] = {
        "exit": p.returncode,
        "text": (p.stdout + p.stderr)[-4000:],
        "assertions": None,
        "quarantined": (out / "quarantine.jsonl").is_file()
        and bool((out / "quarantine.jsonl").read_text(encoding="utf-8").strip()),
    }
    try:
        run["assertions"] = json.loads(
            (out / "assertions.json").read_text(encoding="utf-8")
        )
    except FileNotFoundError:
        pass
    except ValueError as exc:
        run["error"] = f"assertions.json is not JSON ({exc})"
    return run


def read_outcome(run: dict[str, Any], control: str, subject: str) -> Reading:
    """The control's outcome for the subject and its failing focus ids, as VG-CND05-CHAIN reads them."""
    if run.get("error"):
        return Reading("error", (), run["error"])
    rows = run["assertions"]
    if rows is None:
        if run["exit"] == 3:
            key = KEY.search(run.get("text", ""))
            return Reading("refuse", (), key.group(1) if key else "no message key")
        last = (run.get("text") or "").strip().splitlines()[-1:] or ["no output"]
        return Reading(
            "error", (), f"exit {run['exit']}, no assertions.json: {last[0][:160]}"
        )
    rows = rows if isinstance(rows, list) else rows.get("assertions", [])
    hits = [
        r
        for r in rows
        if isinstance(r, dict)
        and r.get("control") == control
        and r.get("subject") == subject
    ]
    if not hits:
        return Reading("error", (), f"no {control} assertion for {subject}")
    hit = hits[0]
    focus = {
        re.sub(r"^agentce:event/", "", str(v.get("focus")))
        for v in hit.get("violations") or []
        if isinstance(v, dict)
    }
    remarks = hit.get("remarks", hit.get("remark")) or []
    extra = (
        [
            f"remarks={','.join(sorted(map(str, [remarks] if isinstance(remarks, str) else remarks)))}"
        ]
        if remarks
        else []
    )
    extra += (
        ["source_class_satisfied=false"]
        if hit.get("source_class_satisfied") is False
        else []
    )
    return Reading(
        str(hit.get("outcome")).replace("-", "_"),
        tuple(sorted(focus)),
        "; ".join(extra),
    )


def classify(
    expected: tuple[str, ...], readings: dict[str, Reading]
) -> tuple[str, list[str]]:
    exp = set(expected)
    ran = [r for r in readings.values() if r.word != "error"]
    words = {r.word for r in ran}
    flags = ["split"] if len({(r.word, r.focus, r.detail) for r in ran}) > 1 else []
    if any(
        (r.word == "conformant" and "conformant" not in exp)
        or (r.word == "not_applicable" and not exp & PASSING)
        for r in ran
    ):
        return "GAP", flags
    if flags:
        return "DIFF", flags
    if len(ran) < len(readings) or not ran:
        return "ERROR", flags
    if words <= exp:
        return "AGREE", flags
    if words <= NOT_PASSING and exp <= NOT_PASSING:
        return "AGREE", flags + ["word"]
    return "MISMATCH", flags


def sweep(
    cells: list[dict[str, Any]],
    module: ModuleType,
    root: Path,
    cmds: dict[str, list[str]],
    work: Path,
) -> list[dict[str, Any]]:
    """Build and run every runnable cell; one row per cell and control."""
    fixtures: dict[str, Any] = {}
    for cell in cells:
        if not cell.get("fixture"):
            continue
        dest = work / str(cell["id"])
        dest.mkdir(parents=True)
        try:
            builder = getattr(module, str(cell["fixture"]))
            fixtures[cell["id"]] = builder(
                dest,
                root,
                dict(cell.get("dims") or {}),
                dict(cell.get("fixture_args") or {}),
            )
        except (
            Exception
        ) as exc:  # a builder's own error is the cell's ERROR, never the gate's end
            fixtures[cell["id"]] = (
                f"fixture {cell['fixture']} failed: {type(exc).__name__}: {exc}"
            )
    tasks = [
        (cid, eng)
        for cid, fx in fixtures.items()
        if not isinstance(fx, str)
        for eng in cmds
    ]
    with ThreadPoolExecutor(max_workers=os.cpu_count() or 4) as pool:
        futures = {
            t: pool.submit(
                run_engine,
                cmds[t[1]],
                root,
                fixtures[t[0]],
                work / str(t[0]) / f"out-{t[1]}",
            )
            for t in tasks
        }
        runs = {t: f.result() for t, f in futures.items()}
    rows: list[dict[str, Any]] = []
    for cell in cells:
        fx: Any = fixtures.get(cell["id"])
        for control in cell["controls"]:
            row: dict[str, Any] = {
                "id": str(cell["id"]),
                "control": control,
                "dims": dict(cell.get("dims") or {}),
                "spec_ref": cell["spec_ref"],
                "note": " ".join(str(cell.get("note") or "").split()),
                "flags": [],
                "readings": {},
                "exits": {},
                "expected": (),
            }
            if not cell.get("fixture"):
                row["status"] = "CANNOT_OCCUR" if cell.get("cannot_occur") else "RULING"
                rows.append(row)
                continue
            row["expected"] = expected_words(cell, control)
            if isinstance(fx, str):
                row.update(status="ERROR", note=f"{fx} | {row['note']}")
            else:
                for eng in cmds:
                    run = runs[(cell["id"], eng)]
                    row["readings"][eng] = read_outcome(run, control, fx.subject)
                    row["exits"][eng] = run.get("exit")
                row["status"], row["flags"] = classify(row["expected"], row["readings"])
                if len(set(row["exits"].values())) > 1:
                    row["flags"].append("exit-split")
                if any(runs[(cell["id"], eng)].get("quarantined") for eng in cmds):
                    row["flags"].append("quarantined")
            rows.append(row)
    return rows


def cell_status(rows: list[dict[str, Any]]) -> dict[str, str]:
    """Each run cell's worst status over its controls."""
    status: dict[str, str] = {}
    for row in rows:
        if row["status"] in SEVERITY:
            prior = status.get(row["id"], "AGREE")
            status[row["id"]] = min(prior, row["status"], key=SEVERITY.index)
    return status


def judge(status: dict[str, str], open_map: dict[str, Any]) -> list[str]:
    """Compare each run cell with open.json; a listed cell not run in this tier is not judged."""
    problems: list[str] = []
    for cid, got in sorted(status.items()):
        entry = open_map.get(cid)
        if entry is None and got != "AGREE":
            problems.append(
                f"open.unlisted: {cid} reads {got} and open.json does not list it (fix the engine, or "
                "the cell if the SPEC says otherwise)"
            )
        elif entry is not None and got == "AGREE":
            problems.append(
                f"open.listed_agree: {cid} reads AGREE: remove it from open.json and from OPEN_PINNED "
                f"(owner {entry.get('owner')})"
            )
        elif entry is not None and got != entry.get("status"):
            problems.append(
                f"open.status_changed: {cid} reads {got}, open.json lists {entry.get('status')} "
                f"(owner {entry.get('owner')})"
            )
    return problems


# ---------------------------------------------------------------------------------------------------- outputs


def write_outputs(out: Path, rows: list[dict[str, Any]], line: str) -> None:
    out.mkdir(parents=True, exist_ok=True)

    def clean(text: Any) -> str:
        return " ".join(str(text).split())

    cols = [
        "id",
        "control",
        "dims",
        "expected",
        "spec_ref",
        *ENGINES,
        "status",
        "flags",
        "note",
    ]
    tsv, md = (
        ["\t".join(cols)],
        [
            "# VG-TRUST-MATRIX",
            "",
            line,
            "",
            "| " + " | ".join(cols) + " |",
            "|" + "---|" * len(cols),
        ],
    )
    for r in rows:
        values = [
            r["id"],
            r["control"],
            "; ".join(f"{k}={v}" for k, v in r["dims"].items()) or "-",
            "|".join(r["expected"]) or "-",
            r["spec_ref"],
            *[
                f"{r['readings'][e].show()} exit {r['exits'][e]}"
                if e in r["readings"]
                else "-"
                for e in ENGINES
            ],
            r["status"],
            ",".join(r["flags"]) or "-",
            r["note"] or "-",
        ]
        tsv.append("\t".join(clean(v) for v in values))
        md.append(
            "| " + " | ".join(clean(v).replace("|", "\\|") for v in values) + " |"
        )
    (out / "results.tsv").write_text("\n".join(tsv) + "\n", encoding="utf-8")
    (out / "results.md").write_text("\n".join(md) + "\n", encoding="utf-8")


def summary(rows: list[dict[str, Any]]) -> str:
    count = {s: sum(1 for r in rows if r["status"] == s) for s in SEVERITY}
    cells = len({r["id"] for r in rows if r["status"] in SEVERITY})
    return f"{cells} cells run ({len(rows)} control rows): " + ", ".join(
        f"{s} {count[s]}" for s in SEVERITY
    )


# ---------------------------------------------------------------------------------------------------- main


def main(argv: list[str], env: dict[str, str]) -> int:
    try:
        opts = parse_args(argv)
        if opts.get("--self-test"):
            return self_test()
        matrix_path = Path(opts.get("--matrix") or FIXTURES / "matrix.yaml")
        open_path = Path(opts.get("--open") or FIXTURES / "open.json")
        raw = matrix_path.read_bytes()
        matrix = yaml.safe_load(raw) or {}
        open_map = json.loads(open_path.read_text(encoding="utf-8"))
        module = load_builders(str(matrix.get("fixtures", "build")))
        form = check_matrix(matrix, module)
        if form:
            print("trust_matrix FAIL [trust_matrix.matrix_form] " + "; ".join(form))
            return 1
        chosen = set(select(matrix, env))
    except Refused as exc:
        print(f"trust_matrix: refused [{exc.key}] {exc}", file=sys.stderr)
        return 2
    problems = coverage(raw, matrix, open_map, catalog_minimums(ROOT))
    for problem in problems:
        print(f"trust_matrix FAIL [{problem.split(':', 1)[0]}] {problem}")
    cells = [c for c in matrix["cells"] if str(c["id"]) in chosen]
    with tempfile.TemporaryDirectory() as tmp:
        rows = sweep(cells, module, ROOT, engine_cmds(), Path(tmp) / "work")
        line = summary(rows)
        write_outputs(Path(env.get(OUT_ENV) or Path(tmp) / "out"), rows, line)
    print(f"trust_matrix: {tier_of(env)} tier, {line}")
    failures = judge(cell_status(rows), open_map)
    by_id = {
        r["id"]: r
        for r in rows
        if r["status"] not in ("AGREE", "CANNOT_OCCUR", "RULING")
    }
    for failure in failures:
        cid = failure.split(": ", 1)[1].split(" ", 1)[0]
        row = by_id.get(cid)
        readings = (
            "; ".join(f"{e} {x.show()}" for e, x in row["readings"].items())
            if row
            else ""
        )
        detail = (
            f"\n    {row['control']} expected {'|'.join(row['expected'])}: {readings}"
            if row
            else ""
        )
        print(f"trust_matrix FAIL [{failure.split(':', 1)[0]}] {failure}{detail}")
    if failures or problems:
        return 1
    print("trust_matrix: PASS")
    return 0


def self_test() -> int:
    """Every status, coverage key and option key produced by its own small case; each key is printed as it holds."""
    seen: list[str] = []

    def expect(key: str, problems: list[str]) -> None:
        assert any(p.startswith(key + ":") for p in problems), (key, problems)
        seen.append(key)

    def refused(key: str, fn: Any, *args: Any) -> None:
        try:
            fn(*args)
        except Refused as exc:
            assert exc.key == key, (key, exc.key)
            seen.append(key)
            return
        raise AssertionError(f"{key} not refused")

    base: dict[str, Any] = {
        "quick": ["a"],
        "dimensions": [{"name": "focus_class", "values": list(FOCUS_CLASSES)}],
        "cells": [
            {
                "id": i,
                "controls": ["X-01"],
                "dims": {"focus_class": f},
                "spec_ref": "s",
                "fixture": "f",
                "expected": "conformant",
            }
            for i, f in zip("abc", FOCUS_CLASSES)
        ],
    }
    raw = b"matrix"
    ok: dict[str, Any] = {
        "digest": hashlib.sha256(raw).hexdigest(),
        "pinned": frozenset({"b"}),
        "required_quick": frozenset({"a"}),
        "ceiling": 1,
    }
    open_ok = {"b": {"status": "GAP", "owner": "18.122"}}
    assert coverage(raw, base, open_ok, {"X-01": "enforcement_point"}, **ok) == []
    expect("coverage.digest", coverage(b"other", base, open_ok, {}, **ok))
    unused = {
        **base,
        "dimensions": [{"name": "focus_class", "values": [*FOCUS_CLASSES, "sideways"]}],
    }
    expect("coverage.unused_value", coverage(raw, unused, open_ok, {}, **ok))
    expect(
        "coverage.catalog_focus",
        coverage(
            raw,
            base,
            open_ok,
            {"X-01": "enforcement_point", "Y-01": "independent_system"},
            **ok,
        ),
    )
    assert (
        coverage(
            raw,
            {
                **base,
                "cells": [
                    *base["cells"],
                    {"id": "o", "controls": ["Z-01"], "cannot_occur": "r"},
                ],
            },
            open_ok,
            {"Z-01": "independent_system"},
            **ok,
        )
        == []
    )
    rulings = {
        **base,
        "cells": [
            *base["cells"],
            *(
                {"id": f"r{n}", "controls": ["X-01"], "ruling_needed": "?"}
                for n in range(2)
            ),
        ],
    }
    expect("coverage.ruling_ceiling", coverage(raw, rulings, open_ok, {}, **ok))
    expect(
        "coverage.open_owner",
        coverage(raw, base, {"b": {"status": "GAP", "owner": ""}}, {}, **ok),
    )
    expect(
        "coverage.open_grew",
        coverage(
            raw, base, {**open_ok, "c": {"status": "GAP", "owner": "18.1"}}, {}, **ok
        ),
    )
    expect(
        "coverage.quick_missing",
        coverage(raw, {**base, "quick": []}, open_ok, {}, **ok),
    )
    open_all = {
        "a": {"status": "GAP", "owner": "18.1"},
        "b": {"status": "GAP", "owner": "18.1"},
    }
    expect("open.listed_agree", judge({"a": "AGREE"}, open_all))
    for status in ("GAP", "DIFF", "ERROR", "MISMATCH"):
        expect("open.unlisted", judge({"z": status}, open_all))
    expect("open.status_changed", judge({"b": "MISMATCH"}, open_all))
    assert judge({"a": "GAP", "y": "AGREE"}, open_all) == []
    for bad in ("FULL", "quik", ""):
        refused("trust_matrix.tier_unknown", select, base, {TIER_ENV: bad})
    refused("trust_matrix.only_empty", select, base, {ONLY_ENV: " , "})
    refused("trust_matrix.only_unknown_cell", select, base, {ONLY_ENV: "a,nope"})
    assert select(base, {}) == ["a"] and select(base, {TIER_ENV: "full"}) == [
        "a",
        "b",
        "c",
    ]
    assert select(base, {ONLY_ENV: "c"}) == ["c"]
    refused("trust_matrix.unknown_option", parse_args, ["--bogus"])
    refused("trust_matrix.repeated_option", parse_args, ["--matrix=x", "--matrix", "y"])
    refused("trust_matrix.self_test_alone", parse_args, ["--self-test", "--open=x"])
    refused("trust_matrix.option_value_missing", parse_args, ["--open="])
    refused("trust_matrix.option_value_missing", parse_args, ["--matrix"])
    assert parse_args(["--matrix", "m", "--open=o"]) == {"--matrix": "m", "--open": "o"}
    nc, ie, ok_r = (
        Reading("non_conformant", ("tc1",)),
        Reading("insufficient_evidence"),
        Reading("conformant"),
    )
    for expected, readings, want in [
        (("conformant",), {"p": ok_r, "t": ok_r, "j": ok_r}, ("AGREE", [])),
        (("insufficient_evidence",), {"p": nc, "t": nc, "j": nc}, ("AGREE", ["word"])),
        (("non_conformant",), {"p": nc, "t": ok_r, "j": nc}, ("GAP", ["split"])),
        (("non_conformant",), {"p": nc, "t": ie, "j": nc}, ("DIFF", ["split"])),
        (
            ("non_conformant",),
            {"p": nc, "t": Reading("error", (), "x"), "j": nc},
            ("ERROR", []),
        ),
        (("conformant",), {"p": nc, "t": nc, "j": nc}, ("MISMATCH", [])),
        (("non_conformant",), {"p": Reading("not_applicable")}, ("GAP", [])),
        (("refuse",), {"p": Reading("refuse", (), "input.x")}, ("AGREE", [])),
    ]:
        assert classify(expected, readings) == want, (expected, readings)
    assert cell_status(
        [
            {"id": "a", "status": "AGREE"},
            {"id": "a", "status": "MISMATCH"},
            {"id": "b", "status": "RULING"},
        ]
    ) == {"a": "MISMATCH"}
    assert (
        read_outcome(
            {"exit": 3, "assertions": None, "text": "error [input.bundle_x]: no"},
            "C",
            "s",
        ).detail
        == "input.bundle_x"
    )
    for key in dict.fromkeys(seen):
        print(f"self-test ok: {key}")
    print("trust_matrix self-test: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:], dict(os.environ)))
