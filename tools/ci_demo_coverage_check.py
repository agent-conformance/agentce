"""ci_demo_coverage_check - the CI demo-fault job actually demos every gate from
``verification/run --list``, and a failed demo actually fails the required `quick` check (18.74).

The verification CI job used to run every gate's seeded-fault demo one after another
(``for gate in $(./verification/run --list); do ./verification/run --demo-fault "$gate"; done``),
so its wall time grew with every new gate and was heading past its own timeout. The fix splits that
loop across a parallel ``demo-fault`` matrix job, where each job runs
``verification/shard.py <job-index> <job-total>`` -- GitHub's own contiguous 0..job-total-1 numbering
of every job the matrix actually creates, so the partition is complete by construction regardless of
matrix shape -- and a thin ``quick`` job (the required check) that fails unless both ``build`` and
``demo-fault`` succeeded. Three review rounds in a row each found a workflow edit that an earlier,
blocklist-style version of this check (naming one bad edit at a time: ``continue-on-error``, a
rewired ``run:`` line, ``needs``, ``exit 0``, ``|| true``, a stray ``if:``) did not reject -- a step
``shell: "true {0}"`` override, for example, changes how ``run:`` text is interpreted and defeats
every blocklist entry at once without tripping any of them. So this check instead asserts the good
shape: a step may carry only ``name``/``run`` (``run:`` matched exactly), a job may carry only the
keys it has today, the workflow root may carry only the keys it has today, and ``quick``'s own
``if:`` must be exactly ``always()`` (or the equivalent ``${{ always() }}``) -- not merely contain
``always()`` as a substring of some larger, possibly-false expression. This covers four independent
ways the design can silently stop proving anything:

1. ``verification/shard.py``'s own partition line could be truncated or off-by-one (for example
   ``mine[:1]``), dropping gates no CI log would call out as missing. Checked by loading the real,
   on-disk ``shard.py`` in-process and calling its ``partition()`` for every index of two different
   totals, confirming the union, against the real ``verification/run --list``, assigns every gate to
   exactly one shard.
2. The demo step could carry an unknown key (``continue-on-error``, ``if:``, ``shell:``) or its
   ``run:`` could stop being exactly ``verification/shard.py "$job-index" "$job-total"``. Checked by
   an allowlist of permitted step/job keys plus an exact (whitespace-stripped) match on ``run:``.
3. The ``quick`` job's own gating logic could stop actually depending on ``demo-fault``'s result (for
   example ``needs: [build]``, ``exit 1`` changed to ``exit 0``, ``!=`` flipped to ``==``, a trailing
   ``|| true``, a stray ``if:``/``continue-on-error``/``shell:``, or an ``if:`` that merely mentions
   ``always()`` without being exactly that), defeating the one property the item asks for by name: a
   failed, cancelled or skipped shard must still fail the required check. Checked by the same
   job/step key allowlist plus exact matches on ``needs``, ``if:``, and the step's ``run:``.
4. A workflow-root key could change how every step's ``run:`` text is interpreted without changing
   the text itself, defeating checks 2 and 3 without tripping either one: ``defaults.run.shell``, or
   an ``env:`` that sets ``BASH_ENV`` (bash sources that file before every ``run:`` step under
   GitHub's default ``bash -e {0}``, so a payload that exits 0 turns every step green). Checked by
   an allowlist of the root's keys (``name``, ``on``, ``permissions``, ``jobs``); the job-level
   forms are already refused by the job and step allowlists in checks 2 and 3.

Usage:
    ci_demo_coverage_check.py              # checks the real workflow and the real shard.py
    ci_demo_coverage_check.py --self-test  # proves the comparator discriminates, without CI
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
import tempfile
from collections.abc import Callable, Collection
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import ModuleType
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "verification.yml"
DEMO_JOB = "demo-fault"
QUICK_JOB = "quick"
QUICK_TIER_JOB = "quick-tier"
SETUP_ACTION = "./.github/actions/setup-verification-env"
SHARD_SCRIPT = "verification/shard.py"
CANONICAL_RUN = 'python3 verification/shard.py "${{ strategy.job-index }}" "${{ strategy.job-total }}"'
CANONICAL_QUICK_TIER_RUN = 'python3 verification/shard.py --quick "${{ strategy.job-index }}" "${{ strategy.job-total }}"'
# Three reviews in a row (contract-critic r1, r2, verifier r1) each found an edit to
# verification.yml that an earlier, blocklist-style version of this check did not reject
# (continue-on-error, a rewired run line, needs, exit 0, || true, a step if:). Listing bad edits one
# at a time cannot converge: a step `shell: "true {0}"` override, for example, changes how `run:` is
# interpreted and defeats every check above without tripping any of them. So this check now asserts
# the good shape instead: a step may carry only `name`/`run` (checked exactly), and a job and the
# workflow root may carry only the keys they have today. A root `env:` setting BASH_ENV, which bash
# sources before every run: step, got past the old root check, which named only
# defaults.run.shell (18.76).
STEP_ALLOWED_KEYS = {"name", "run"}
WORKFLOW_ALLOWED_KEYS = {"jobs", "name", "on", "permissions"}
DEMO_JOB_ALLOWED_KEYS = {"runs-on", "steps", "strategy", "timeout-minutes"}
QUICK_JOB_ALLOWED_KEYS = {"if", "needs", "runs-on", "steps", "timeout-minutes"}


def _unknown_keys(mapping: Collection[Any], allowed: set[str], label: str) -> list[str]:
    extra = sorted(set(mapping) - allowed)
    if not extra:
        return []
    return [f"{label} has key(s) outside the allowed set {sorted(allowed)}: {extra}"]


def workflow_level_problems(doc: Any) -> list[str]:
    if not isinstance(doc, dict):
        return []
    # PyYAML reads the bare key `on` as the boolean True (YAML 1.1); other keys are compared as text.
    keys = {"on" if key is True else str(key) for key in doc}
    return _unknown_keys(keys, WORKFLOW_ALLOWED_KEYS, "the workflow")


# Two arbitrary probe sizes for the generic, total-agnostic partition() function below --
# correctness here does not depend on matching the workflow's real lane count: 7 just catches an
# off-by-one that only shows up once a total doesn't divide the gate count evenly.
PARTITION_TOTALS = (6, 7)


def _load_shard_module(root: Path) -> ModuleType:
    """Load the real, on-disk ``verification/shard.py`` as a module (rather than a subprocess),
    so a seeded fault edited into its source is picked up on the next load with no extra
    `verification/run --list` re-invocation per probed total."""
    spec = importlib.util.spec_from_file_location(
        "ci_demo_coverage_shard", root / SHARD_SCRIPT
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _demo_step(job: dict[str, Any]) -> dict[str, Any] | None:
    steps = job.get("steps")
    if not isinstance(steps, list):
        return None
    for step in steps:
        if isinstance(step, dict) and SHARD_SCRIPT in str(step.get("run", "")):
            return step
    return None


def _step_wiring_problems(
    step: dict[str, Any], label: str, canonical_run: str
) -> list[str]:
    """Shared by `wiring_problems` and `quick_wiring_problems`: the step may carry only an allowed
    key (so a `shell:` override, `continue-on-error`, `if:`, or any other added key is rejected by
    construction, not by naming it), and its `run:` must exactly (whitespace-stripped) match
    `canonical_run` -- so a fix to one job's step-level checks can't silently stay out of sync with
    the other's."""
    problems = _unknown_keys(step, STEP_ALLOWED_KEYS, f"the {label} step")
    run_text = str(step.get("run", "")).strip()
    if run_text != canonical_run:
        problems.append(
            f"the {label} step's run: is not exactly the canonical invocation, so an edit could "
            f"silently defeat it: {run_text!r}"
        )
    return problems


def wiring_problems(
    job: Any, name: str = DEMO_JOB, canonical_run: str = CANONICAL_RUN
) -> list[str]:
    """A shard job (demo-fault or quick-tier) may carry only an allowed key (so
    `continue-on-error`, `if:`, or `defaults.run.shell` are rejected by construction, not by naming
    them one at a time), its steps must be exactly checkout, the setup action (without which most
    gates would skip, and a skip exits 0) and the shard step, and that step must be exactly the
    canonical invocation."""
    if not isinstance(job, dict):
        return [f"jobs.{name} is missing or not a mapping"]
    problems = _unknown_keys(job, DEMO_JOB_ALLOWED_KEYS, f"jobs.{name}")
    steps = job.get("steps")
    uses = [
        str(step.get("uses", "")).split("@")[0] if isinstance(step, dict) else ""
        for step in (steps if isinstance(steps, list) else [])
    ]
    if uses != ["actions/checkout", SETUP_ACTION, ""]:
        problems.append(
            f"jobs.{name}'s steps are not exactly checkout, {SETUP_ACTION} and the shard step: {uses}"
        )
    step = _demo_step(job)
    if step is None:
        return problems + [f"no step in jobs.{name} invokes {SHARD_SCRIPT}"]
    return problems + _step_wiring_problems(
        step, f"{name} {SHARD_SCRIPT}", canonical_run
    )


def build_problems(job: Any) -> list[str]:
    """No build step may run a whole tier: the quick tier runs only in the quick-tier shards."""
    steps = job.get("steps") if isinstance(job, dict) else None
    return [
        f"a build step runs a whole tier, which only the {QUICK_TIER_JOB} shards may do: {run!r}"
        for run in (
            str(step.get("run", "")) for step in (steps or []) if isinstance(step, dict)
        )
        if "verification/run" in run and ("--quick" in run or "--full" in run)
    ]


# `${{always()}}` without the inner spaces is also valid GitHub syntax; it is refused on purpose
# (fails closed), so the workflow keeps one of these two spellings.
CANONICAL_QUICK_IF_VALUES = {"always()", "${{ always() }}"}
CANONICAL_QUICK_RUN = (
    'if [ "${{ needs.build.result }}" != "success" ] || '
    '[ "${{ needs.quick-tier.result }}" != "success" ] || '
    '[ "${{ needs.demo-fault.result }}" != "success" ]; then\n'
    '  echo "build: ${{ needs.build.result }}, quick-tier: ${{ needs.quick-tier.result }}, '
    'demo-fault: ${{ needs.demo-fault.result }}"\n'
    '  echo "a required job failed, was cancelled, or was skipped"\n'
    "  exit 1\n"
    "fi\n"
    'echo "build and every quick-tier and demo-fault shard succeeded"'
)


def _quick_step(job: dict[str, Any]) -> dict[str, Any] | None:
    steps = job.get("steps")
    if not isinstance(steps, list) or not steps:
        return None
    step = steps[0]
    return step if isinstance(step, dict) else None


def quick_wiring_problems(job: Any) -> list[str]:
    """`quick` is the required check; GitHub treats a *skipped* required check as passing, so
    `quick` must be a real job whose own logic fails unless both dependencies truly succeeded.
    `quick` may carry only an allowed key (so `continue-on-error` or `defaults.run.shell` are
    rejected by construction); its `needs` must name both dependencies; its `if:` must exactly
    (whitespace-stripped) equal `always()` (a substring search would still match a condition that
    merely mentions `always()` without being exactly that, for example `always() &&
    github.event_name == 'push'`, which can still evaluate false and skip the job -- the same class
    of gap this whole redesign exists to close); and its step's `run:` must exactly (whitespace-
    stripped) match the canonical dependency-check text -- the same exact-match style
    `wiring_problems` already uses for the demo step, not a substring search that
    `exit 1` -> `exit 0`, `!=` -> `==`, or a trailing `|| true` could still slip past."""
    if not isinstance(job, dict):
        return [f"jobs.{QUICK_JOB} is missing or not a mapping"]
    problems = _unknown_keys(job, QUICK_JOB_ALLOWED_KEYS, f"jobs.{QUICK_JOB}")
    needs = job.get("needs")
    needed = set(needs) if isinstance(needs, list) else set()
    for required in ("build", QUICK_TIER_JOB, DEMO_JOB):
        if required not in needed:
            problems.append(
                f"jobs.{QUICK_JOB}.needs does not include {required!r}: {needs!r}"
            )
    if str(job.get("if", "")).strip() not in CANONICAL_QUICK_IF_VALUES:
        problems.append(
            f"jobs.{QUICK_JOB}'s if: is not exactly always() (got {job.get('if')!r}), so a condition "
            "that merely mentions always() without being exactly that -- for example "
            "'always() && github.event_name == \\'push\\'' -- could still evaluate false and skip "
            "this job entirely, and GitHub treats a skipped required check as passing"
        )
    step = _quick_step(job)
    if step is None:
        return problems + [
            f"no step in jobs.{QUICK_JOB} checks the dependencies' results"
        ]
    return problems + _step_wiring_problems(step, QUICK_JOB, CANONICAL_QUICK_RUN)


def _coverage_problems(
    gates: list[Any], assignments: dict[Any, list[int]], total: int
) -> list[str]:
    """Pure judging logic: every gate must appear in exactly one shard's assignment list."""
    problems = [
        f"gate {gate!r} assigned to {len(assignments.get(gate, []))} shard(s) of {total} "
        f"(expected exactly 1): {assignments.get(gate, [])}"
        for gate in gates
        if len(assignments.get(gate, [])) != 1
    ]
    extra = sorted(set(assignments) - set(gates))
    if extra:
        problems.append(
            f"shard.py's partition() (total={total}) produced gate(s) not in `verification/run --list`: {extra}"
        )
    return problems


def partition_problems(
    gates: list[Any],
    total: int,
    slice_of: Callable[[list[Any], int, int], list[Any]],
) -> list[str]:
    """Actually call one of the real, on-disk ``shard.py``'s partition functions for every index
    of ``total`` and confirm the union, against the real gate list, assigns every gate to exactly
    one shard -- catching a truncated or off-by-one edit that a wiring check alone cannot see."""
    assignments: dict[Any, list[int]] = {}
    for index in range(total):
        for gate in slice_of(gates, index, total):
            assignments.setdefault(gate, []).append(index)
    return _coverage_problems(gates, assignments, total)


def demo_lane_total(job: Any) -> int:
    """The number of jobs the demo-fault matrix creates (its one ``lane`` list), or 0."""
    matrix = job.get("strategy", {}).get("matrix", {}) if isinstance(job, dict) else {}
    lanes = matrix.get("lane") if isinstance(matrix, dict) else None
    return len(lanes) if isinstance(lanes, list) else 0


def _stdout_lines(root: Path, script: str, *args: str) -> list[str]:
    return subprocess.run(
        ["python3", str(root / script), *args],
        capture_output=True,
        text=True,
        check=True,
        cwd=root,
    ).stdout.splitlines()


def _balanced(
    shard: ModuleType, durations: dict[str, Any]
) -> Callable[[list[Any], int, int], list[Any]]:
    """``shard.balanced_partition`` with ``durations`` bound, in ``partition_problems``' shape."""
    return lambda pairs, index, total: shard.balanced_partition(
        pairs, index, total, durations
    )


def _run_args(
    shard: ModuleType, durations: dict[str, Any]
) -> Callable[[list[Any], int, int], list[Any]]:
    """The (gate, fault) pairs shard ``index`` actually hands ``./verification/run``: each of its
    chunks' ``demo_args()`` parsed back, in ``partition_problems``' shape."""

    def slice_of(pairs: list[Any], index: int, total: int) -> list[Any]:
        got = []
        for chunk in shard.balanced_shards(pairs, total, durations)[index]:
            args = shard.demo_args(chunk)
            got += [
                (args[1], int(arg.removeprefix("--fault=")))
                for arg in args[2:]
                if arg.startswith("--fault=")
            ]
        return got

    return slice_of


def dry_run_problems(
    pairs: list[Any],
    total: int,
    slice_of: Callable[[list[Any], int, int], list[Any]],
    root: Path,
) -> list[str]:
    """The CI path itself: ``shard.py --dry-run <i> <total>`` run as the demo-fault job runs it
    must print exactly ``balanced_partition()``'s pairs (``<gate> <fault>`` per line) for every
    shard, so main() cannot deal the demos some other way than the one checked here."""
    with ThreadPoolExecutor() as pool:
        printed = list(
            pool.map(
                lambda index: _stdout_lines(
                    root, SHARD_SCRIPT, "--dry-run", str(index), str(total)
                ),
                range(total),
            )
        )
    return [
        f"shard.py --dry-run {index} {total} printed {got}, not balanced_partition()'s {want}"
        for index, got in enumerate(printed)
        if got
        != (
            want := [f"{gate} {fault}" for gate, fault in slice_of(pairs, index, total)]
        )
    ]


def fault_selection_problems(scratch: Path) -> list[str]:
    """``./verification/run --demo-fault <ID> --fault <N>`` runs only fault N, on a throwaway
    registry whose fault 0 never turns its gate RED: selecting fault 1 alone must exit 0 and
    report index 1 only, the whole gate must exit 1, and a bad selection must be refused (2)."""
    (scratch / "verification").mkdir(parents=True, exist_ok=True)
    (scratch / "AGENTS.md").write_text("an invariant\n", encoding="utf-8")
    (scratch / "verification" / "CHANGELOG.md").write_text(
        "## 1.0.0\n\nVG-PICK\n", encoding="utf-8"
    )
    (scratch / "a.txt").write_text("ok\n", encoding="utf-8")
    (scratch / "b.txt").write_text("ok\n", encoding="utf-8")
    fault = {"search": "ok", "replace": "no"}
    registry = {
        "version": 1,
        "suite_version": "1.0.0",
        "gates": [
            {
                "id": "VG-PICK",
                "title": "b.txt says ok",
                "tier": "quick",
                "cmd": ["grep", "-q", "ok", "b.txt"],
                "timeout_s": 30,
                "invariant": "an invariant",
                "rubric": {"pass": "ok", "fail": "not ok"},
                "faults": [
                    {**fault, "file": "a.txt", "description": "a.txt (not judged)"},
                    {**fault, "file": "b.txt", "description": "b.txt (judged)"},
                ],
            }
        ],
    }
    path = scratch / "gates.json"
    path.write_text(json.dumps(registry), encoding="utf-8")

    def run(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "python3",
                str(REPO_ROOT / "verification" / "run"),
                *args,
                "--registry",
                str(path),
                "--root",
                str(scratch),
            ],
            capture_output=True,
            text=True,
            check=False,
        )

    problems = []
    picked = run("--demo-fault", "VG-PICK", "--fault", "1", "--json")
    outcome = json.loads(picked.stdout) if picked.returncode in (0, 1) else {}
    if (
        picked.returncode != 0
        or outcome.get("fault_indexes") != [1]
        or outcome.get("faults") != ["b.txt (judged)"]
    ):
        problems.append(
            f"--demo-fault VG-PICK --fault 1 exited {picked.returncode} with {outcome}, not 0 with fault 1 only"
        )
    whole = run("--demo-fault", "VG-PICK")
    if whole.returncode != 1:
        problems.append(
            f"--demo-fault VG-PICK (fault 0 never RED) exited {whole.returncode}, not 1"
        )
    refusals = [
        (["--fault", "1"], "suite.fault_without_demo"),
        (["--list", "--fault", "1"], "suite.fault_without_demo"),
        (["--lint-registry", "--fault", "1"], "suite.fault_without_demo"),
        (["--demo-fault", "VG-PICK", "--fault", "2"], "suite.unknown_fault"),
        (
            ["--demo-fault", "VG-PICK", "--fault", "1", "--fault", "1"],
            "suite.repeated_fault",
        ),
        (["--quick", "--demo-reject"], "suite.demo_reject_without_lint"),
        (["--list", "--json"], "suite.json_not_supported"),
        (["--lint-registry", "--json"], "suite.json_not_supported"),
        (["--gate", "VG-PICK", "--demo-fault", "VG-PICK"], "not allowed with"),
    ]
    for args, key in refusals:
        got = run(*args)
        if got.returncode != 2 or key not in got.stderr:
            problems.append(
                f"{' '.join(args)} exited {got.returncode}, not 2 with {key}"
            )
    return problems


def check_workflow(
    workflow_path: Path = WORKFLOW_PATH, root: Path = REPO_ROOT
) -> list[str]:
    if not workflow_path.is_file():
        return [f"{workflow_path}: missing"]
    try:
        doc = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        return [f"{workflow_path}: not valid YAML: {exc}"]
    raw_jobs = doc.get("jobs") if isinstance(doc, dict) else None
    jobs: dict[str, Any] = raw_jobs if isinstance(raw_jobs, dict) else {}
    problems = (
        workflow_level_problems(doc)
        + wiring_problems(jobs.get(DEMO_JOB))
        + wiring_problems(
            jobs.get(QUICK_TIER_JOB), QUICK_TIER_JOB, CANONICAL_QUICK_TIER_RUN
        )
        + quick_wiring_problems(jobs.get(QUICK_JOB))
        + build_problems(jobs.get("build"))
    )
    gates = [line for line in _stdout_lines(root, "verification/run", "--list") if line]
    if not gates:
        return problems + ["`verification/run --list` printed no gates"]
    shard = _load_shard_module(root)
    durations = shard.load_durations()
    registry = json.loads(
        (root / "verification" / "gates.json").read_text(encoding="utf-8")
    )
    # every registered fault of every listed gate, independent of shard.demo_pairs()
    counts = {gate["id"]: len(gate["faults"]) for gate in registry["gates"]}
    pairs = [(gate, fault) for gate in gates for fault in range(counts[gate])]
    if shard.demo_pairs(gates, registry) != pairs:
        problems.append(
            "shard.py's demo_pairs() is not every registered fault in order"
        )
    balanced = _balanced(shard, durations)
    lanes = demo_lane_total(jobs.get(DEMO_JOB))
    for total in (*PARTITION_TOTALS, *([lanes] if lanes else [])):
        problems += partition_problems(pairs, total, balanced)
        problems += [
            f"what shard.py runs: {p}"
            for p in partition_problems(pairs, total, _run_args(shard, durations))
        ]
    if lanes:
        problems += [
            f"demo shards at {lanes} lanes: {p}"
            for p in shard.balance_problems(pairs, durations, lanes)
        ]
        problems += dry_run_problems(pairs, lanes, balanced, root)
    else:
        problems.append(f"jobs.{DEMO_JOB} has no strategy.matrix.lane list")
    with tempfile.TemporaryDirectory() as scratch:
        problems += fault_selection_problems(Path(scratch))
    quick = [gate["id"] for gate in registry["gates"] if gate["tier"] == "quick"]
    from_shard = shard.quick_ids(registry)
    if from_shard != quick:
        problems.append(
            f"shard.py's quick_ids() is not the registry's quick tier in order: {from_shard} != {quick}"
        )
    for total in PARTITION_TOTALS:
        problems += [
            f"quick tier: {p}"
            for p in partition_problems(quick, total, shard.partition)
        ]
    return problems


def _misjudged(cases: list[tuple[str, Any, bool]], check: Any) -> list[str]:
    """The names of every case where ``check(arg)`` disagrees with the ``should_pass`` it was
    seeded with (a non-empty problem list means the comparator judged it bad)."""
    return [name for name, arg, should_pass in cases if bool(check(arg)) == should_pass]


# Verifier round 3's payload for 18.74: bash expands the command substitution and sources the file
# it names, which exits 0 before the step's own script runs.
BASH_ENV_PAYLOAD = '$(f=$(mktemp); echo "exit 0" > $f; echo $f)'


def _shape_violation_cases(
    prefix: str, good_step: dict[str, Any], good_job: dict[str, Any]
) -> list[tuple[str, Any, bool]]:
    """The allowlist-violating shapes (a step-level `shell:` override, `env:` BASH_ENV or unknown
    key, a job-level `defaults.run.shell`, `env:` BASH_ENV or unknown key) that both the demo-fault
    and quick jobs must reject identically -- shared so the two case lists can't silently drift apart."""
    return [
        (
            f"{prefix} step with a shell: override",
            {**good_job, "steps": [{**good_step, "shell": "true {0}"}]},
            False,
        ),
        (
            f"{prefix} step with an unknown key",
            {**good_job, "steps": [{**good_step, "mystery": True}]},
            False,
        ),
        (
            f"{prefix} job with defaults.run.shell",
            {**good_job, "defaults": {"run": {"shell": "true {0}"}}},
            False,
        ),
        (f"{prefix} job with an unknown key", {**good_job, "mystery": True}, False),
        (
            f"{prefix} step with env: BASH_ENV",
            {
                **good_job,
                "steps": [{**good_step, "env": {"BASH_ENV": BASH_ENV_PAYLOAD}}],
            },
            False,
        ),
        (
            f"{prefix} job with env: BASH_ENV",
            {**good_job, "env": {"BASH_ENV": BASH_ENV_PAYLOAD}},
            False,
        ),
    ]


def _shard_quick_self_test(shard: ModuleType) -> list[str]:
    """shard.py's quick mode on a synthetic registry and injected runners: quick_ids() keeps only
    quick-tier ids in registry order, and run_slice() exits 1 unless every run exits 0."""
    failures = []
    registry = {
        "gates": [
            {"id": "B", "tier": "quick"},
            {"id": "F", "tier": "full"},
            {"id": "A", "tier": "quick"},
        ]
    }
    got = shard.quick_ids(registry)
    print(f"shard quick: quick_ids drops the full-tier gate and keeps order: {got}")
    if got != ["B", "A"]:
        failures.append(f"quick_ids -> {got}")
    codes = {
        "all gates exit 0": ({}, 0),
        "a gate exits 1": ({"A": 1}, 1),
        "a gate exits 2": ({"B": 2}, 1),
    }
    for name, (exits, want) in codes.items():
        got_code = shard.run_slice(
            ["B", "A"], lambda gate: exits.get(gate, 0), "self-test", "ok"
        )
        print(f"shard quick: run_slice when {name}: exit {got_code} (want {want})")
        if got_code != want:
            failures.append(f"run_slice when {name} -> {got_code}")
    return failures


def _shard_balance_self_test(shard: ModuleType) -> list[str]:
    """shard.py's demo balancing on synthetic durations: balanced_partition() and the chunks
    handed to ``./verification/run`` place every (gate, fault) pair exactly once at two totals,
    a gate over max_chunk_seconds is cut into consecutive runs of its faults, and
    balance_problems() flags an untimed gate only when no default applies, a heaviest shard over
    max(1.25 x mean, longest chunk), and a shard over lane_bound_minutes."""
    failures = []
    gates = ["A", "B", "C", "D", "E", "F", "G"]
    faults = dict(zip(gates, [5, 1, 3, 2, 4, 1, 2]))
    pairs = [(g, f) for g in gates for f in range(faults[g])]
    timed: dict[str, Any] = {
        "gates": dict(zip(gates, [600, 60, 300, 120, 240, 30, 90])),
        "max_chunk_seconds": 250,
    }
    for total in (6, 3):
        failures += partition_problems(pairs, total, _balanced(shard, timed))
        failures += partition_problems(pairs, total, _run_args(shard, timed))
    # A (600 s, 5 faults) over 250 s cuts into three runs: faults 0-1, 2-3, 4
    a_chunks = [c for c in shard.demo_chunks(pairs, timed) if c[0][0] == "A"]
    if a_chunks != [[("A", 0), ("A", 1)], [("A", 2), ("A", 3)], [("A", 4)]]:
        failures.append(f"demo_chunks cut A as {a_chunks}")
    untimed = {"gates": {"A": 60}}
    five = [(g, 0) for g in ["A", "B", "C", "D", "E"]]
    cases: list[tuple[str, list[Any], dict[str, Any], int, bool]] = [
        ("every gate timed, 3 shards", pairs, timed, 3, True),
        ("a gate with no duration and no default", pairs, untimed, 2, False),
        (
            "the same gate with a default",
            pairs,
            {**untimed, "default_seconds": 60},
            2,
            True,
        ),
        # five one-fault 100 s gates over 4 shards: one shard carries 200 s, over max(1.25 x 125, 100)
        (
            "heaviest shard over the bound",
            five,
            {"gates": {}, "default_seconds": 100},
            4,
            False,
        ),
        # the same at 5 shards is balanced, until a 1-minute lane bound applies to its 1.7-minute lanes
        ("one gate per shard", five, {"gates": {}, "default_seconds": 100}, 5, True),
        (
            "a shard over lane_bound_minutes",
            five,
            {"gates": {}, "default_seconds": 100, "lane_bound_minutes": 1.0},
            5,
            False,
        ),
    ]
    for name, case_pairs, durations, total, should_pass in cases:
        got = shard.balance_problems(case_pairs, durations, total)
        print(f"shard balance: {name}: {got or 'balanced'}")
        if bool(got) == should_pass:
            failures.append(f"balance_problems when {name} -> {got}")
    return failures


def self_test() -> int:
    good_step = {"run": CANONICAL_RUN}
    good_job = {
        "steps": [
            {"uses": "actions/checkout"},
            {"uses": SETUP_ACTION},
            good_step,
        ]
    }
    cases: list[tuple[str, Any, bool]] = [
        ("correctly wired demo-fault job", good_job, True),
        (
            "demo step with a trailing || true",
            {"steps": [{"run": CANONICAL_RUN + " || true"}]},
            False,
        ),
        (
            "demo step prefixed with echo",
            {"steps": [{"run": "echo " + CANONICAL_RUN}]},
            False,
        ),
        (
            "demo step prefixed with exit 0;",
            {"steps": [{"run": "exit 0; " + CANONICAL_RUN}]},
            False,
        ),
        (
            "demo step rekeyed to matrix.lane",
            {
                "steps": [
                    {
                        "run": 'python3 verification/shard.py "${{ matrix.lane }}" "${{ strategy.job-total }}"'
                    }
                ]
            },
            False,
        ),
        (
            "demo step with continue-on-error",
            {"steps": [{**good_step, "continue-on-error": True}]},
            False,
        ),
        (
            "demo job with continue-on-error",
            {**good_job, "continue-on-error": True},
            False,
        ),
        (
            "demo step with an if:",
            {"steps": [{**good_step, "if": "matrix.lane < 4"}]},
            False,
        ),
        ("demo job with an if:", {**good_job, "if": "false"}, False),
        *_shape_violation_cases("demo", good_step, good_job),
    ]
    failures = _misjudged(cases, wiring_problems)
    quick_good_step: dict[str, Any] = {"run": CANONICAL_QUICK_RUN}
    quick_good: dict[str, Any] = {
        "needs": ["build", "quick-tier", "demo-fault"],
        "if": "always()",
        "steps": [quick_good_step],
    }
    quick_cases: list[tuple[str, Any, bool]] = [
        ("correctly wired quick job", quick_good, True),
        ("quick needs only build", {**quick_good, "needs": ["build"]}, False),
        ("quick with no if: always()", {**quick_good, "if": "success()"}, False),
        (
            "quick if: mentions always() but is not exactly that (verifier round 2)",
            {**quick_good, "if": "always() && github.event_name == 'push'"},
            False,
        ),
        (
            "quick if: negates always() (verifier round 2)",
            {**quick_good, "if": "${{ !always() }}"},
            False,
        ),
        (
            "quick if: the ${{ }}-wrapped canonical form",
            {**quick_good, "if": "${{ always() }}"},
            True,
        ),
        (
            "quick step checking only needs.build.result",
            {
                **quick_good,
                "steps": [
                    {
                        "run": 'if [ "${{ needs.build.result }}" != "success" ]; then exit 1; fi'
                    }
                ],
            },
            False,
        ),
        (
            "quick job with continue-on-error",
            {**quick_good, "continue-on-error": True},
            False,
        ),
        (
            "quick step with continue-on-error",
            {
                **quick_good,
                "steps": [{**quick_good_step, "continue-on-error": True}],
            },
            False,
        ),
        (
            "quick step with an if:",
            {**quick_good, "steps": [{**quick_good_step, "if": "false"}]},
            False,
        ),
        (
            "quick step's exit 1 changed to exit 0",
            {
                **quick_good,
                "steps": [{"run": CANONICAL_QUICK_RUN.replace("exit 1", "exit 0")}],
            },
            False,
        ),
        (
            "quick step's != flipped to ==",
            {
                **quick_good,
                "steps": [{"run": CANONICAL_QUICK_RUN.replace("!=", "==")}],
            },
            False,
        ),
        (
            "quick step with a trailing || true",
            {**quick_good, "steps": [{"run": CANONICAL_QUICK_RUN + " || true"}]},
            False,
        ),
        *_shape_violation_cases("quick", quick_good_step, quick_good),
    ]
    failures += _misjudged(quick_cases, quick_wiring_problems)
    good_root: dict[Any, Any] = {
        "name": "verification",
        True: {"push": None},
        "permissions": {"contents": "read"},
        "jobs": {},
    }
    workflow_cases: list[tuple[str, Any, bool]] = [
        ("workflow root as PyYAML reads it (on as True)", good_root, True),
        (
            "workflow root with on as a string",
            {**{k: v for k, v in good_root.items() if k is not True}, "on": {}},
            True,
        ),
        ("empty workflow root", {}, True),
        (
            "workflow with defaults.run.shell",
            {**good_root, "defaults": {"run": {"shell": "true {0}"}}},
            False,
        ),
        (
            "root env BASH_ENV",
            {**good_root, "env": {"BASH_ENV": BASH_ENV_PAYLOAD}},
            False,
        ),
        ("root env ENV", {**good_root, "env": {"ENV": "/tmp/payload"}}, False),
        ("root env unrelated", {**good_root, "env": {"FOO": "bar"}}, False),
        ("root concurrency", {**good_root, "concurrency": "x"}, False),
        ("root with a non-string key (off as False)", {**good_root, False: 1}, False),
    ]
    failures += _misjudged(workflow_cases, workflow_level_problems)
    gates = ["A", "B", "C", "D", "E", "F"]
    partition_cases: list[tuple[str, dict[str, list[int]], bool]] = [
        ("complete 6-way assignment", {g: [i] for i, g in enumerate(gates)}, True),
        (
            "truncated assignment (F never assigned, the `mine[:1]`-style bug)",
            {g: [i] for i, g in enumerate(gates[:5])},
            False,
        ),
        (
            "duplicated assignment (A assigned to two shards)",
            {**{g: [i] for i, g in enumerate(gates)}, "A": [0, 1]},
            False,
        ),
    ]
    failures += _misjudged(
        partition_cases,
        lambda assignments: _coverage_problems(gates, assignments, len(gates)),
    )
    failures += _shard_quick_self_test(_load_shard_module(REPO_ROOT))
    failures += _shard_balance_self_test(_load_shard_module(REPO_ROOT))
    build_cases: list[tuple[str, Any, bool]] = [
        (
            "build with lint steps only",
            {"steps": [{"run": "python3 verification/probe.py"}]},
            True,
        ),
        (
            "a renamed build step running --quick --json",
            {"steps": [{"name": "x", "run": "./verification/run --quick --json"}]},
            False,
        ),
        (
            "a build step running --full",
            {"steps": [{"run": "python3 verification/run --full"}]},
            False,
        ),
    ]
    failures += _misjudged(build_cases, build_problems)
    if failures:
        print(f"self-test FAIL: misjudged case(s): {failures}")
        return 1
    print(
        "self-test PASS: comparator discriminates correct wiring from a rekeyed, swallowed, "
        "conditional or under-checked invocation"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        return self_test()
    problems = check_workflow()
    if problems:
        print(
            "FAIL: the demo-fault job's guarantee of a complete, fail-closed demo run is broken:"
        )
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print(
        "PASS: the demo-fault job is wired to strategy.job-index/strategy.job-total with no "
        "continue-on-error or if:, its partition covers every gate from `verification/run --list` "
        "exactly once, and `quick` fails unless both `build` and `demo-fault` succeeded"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
