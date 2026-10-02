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
keys it has today, no workflow- or job-level ``defaults.run.shell`` may exist, and ``quick``'s own
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
4. A workflow- or job-level ``defaults.run.shell`` override could change how any step's ``run:`` text
   is interpreted without changing the text itself, defeating checks 2 and 3 without tripping either
   one. Checked by rejecting ``defaults.run.shell`` at the workflow root (job-level is already
   subsumed by the key allowlists in checks 2 and 3, since ``defaults`` is not an allowed job key).

Usage:
    ci_demo_coverage_check.py              # checks the real workflow and the real shard.py
    ci_demo_coverage_check.py --self-test  # proves the comparator discriminates, without CI
"""

from __future__ import annotations

import argparse
import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "verification.yml"
DEMO_JOB = "demo-fault"
QUICK_JOB = "quick"
SHARD_SCRIPT = "verification/shard.py"
CANONICAL_RUN = 'python3 verification/shard.py "${{ strategy.job-index }}" "${{ strategy.job-total }}"'
# Three reviews in a row (contract-critic r1, r2, verifier r1) each found an edit to
# verification.yml that an earlier, blocklist-style version of this check did not reject
# (continue-on-error, a rewired run line, needs, exit 0, || true, a step if:). Listing bad edits one
# at a time cannot converge: a step `shell: "true {0}"` override, for example, changes how `run:` is
# interpreted and defeats every check above without tripping any of them. So this check now asserts
# the good shape instead: a step may carry only `name`/`run` (checked exactly), a job may carry only
# the keys it has today, and neither a workflow- nor job-level `defaults.run.shell` may exist.
STEP_ALLOWED_KEYS = {"name", "run"}
DEMO_JOB_ALLOWED_KEYS = {"runs-on", "steps", "strategy", "timeout-minutes"}
QUICK_JOB_ALLOWED_KEYS = {"if", "needs", "runs-on", "steps", "timeout-minutes"}


def _unknown_keys(mapping: dict[str, Any], allowed: set[str], label: str) -> list[str]:
    extra = sorted(set(mapping) - allowed)
    if not extra:
        return []
    return [f"{label} has key(s) outside the allowed set {sorted(allowed)}: {extra}"]


def _shell_override_problems(mapping: dict[str, Any], label: str) -> list[str]:
    defaults = mapping.get("defaults")
    run_defaults = defaults.get("run") if isinstance(defaults, dict) else None
    if isinstance(run_defaults, dict) and "shell" in run_defaults:
        return [
            f"{label} sets defaults.run.shell, which can silently change how run: text is interpreted"
        ]
    return []


def workflow_level_problems(doc: Any) -> list[str]:
    if not isinstance(doc, dict):
        return []
    return _shell_override_problems(doc, "the workflow")


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


def wiring_problems(job: Any) -> list[str]:
    """The demo-fault job may carry only an allowed key (so `continue-on-error`, `if:`, or
    `defaults.run.shell` are rejected by construction, not by naming them one at a time), and its
    step must be exactly the canonical invocation."""
    if not isinstance(job, dict):
        return [f"jobs.{DEMO_JOB} is missing or not a mapping"]
    problems = _unknown_keys(job, DEMO_JOB_ALLOWED_KEYS, f"jobs.{DEMO_JOB}")
    step = _demo_step(job)
    if step is None:
        return problems + [f"no step in jobs.{DEMO_JOB} invokes {SHARD_SCRIPT}"]
    return problems + _step_wiring_problems(step, SHARD_SCRIPT, CANONICAL_RUN)


CANONICAL_QUICK_IF_VALUES = {"always()", "${{ always() }}"}
CANONICAL_QUICK_RUN = (
    'if [ "${{ needs.build.result }}" != "success" ] || '
    '[ "${{ needs.demo-fault.result }}" != "success" ]; then\n'
    '  echo "build: ${{ needs.build.result }}, demo-fault: ${{ needs.demo-fault.result }}"\n'
    '  echo "a required job failed, was cancelled, or was skipped"\n'
    "  exit 1\n"
    "fi\n"
    'echo "build and every demo-fault shard succeeded"'
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
    for required in ("build", DEMO_JOB):
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
    gates: list[str], assignments: dict[str, list[int]], total: int
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
    gates: list[str], total: int, root: Path = REPO_ROOT
) -> list[str]:
    """Actually call the real, on-disk ``shard.py``'s ``partition()`` for every index of ``total``
    and confirm the union, against the real gate list, assigns every gate to exactly one shard --
    catching a truncated or off-by-one edit to ``partition()`` that a wiring check alone cannot see."""
    shard = _load_shard_module(root)
    assignments: dict[str, list[int]] = {}
    for index in range(total):
        for gate in shard.partition(gates, index, total):
            assignments.setdefault(gate, []).append(index)
    return _coverage_problems(gates, assignments, total)


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
        + quick_wiring_problems(jobs.get(QUICK_JOB))
    )
    result = subprocess.run(
        ["python3", str(root / "verification" / "run"), "--list"],
        capture_output=True,
        text=True,
        check=True,
        cwd=root,
    )
    gates = [line for line in result.stdout.splitlines() if line]
    if not gates:
        return problems + ["`verification/run --list` printed no gates"]
    for total in PARTITION_TOTALS:
        problems += partition_problems(gates, total, root)
    return problems


def _misjudged(cases: list[tuple[str, Any, bool]], check: Any) -> list[str]:
    """The names of every case where ``check(arg)`` disagrees with the ``should_pass`` it was
    seeded with (a non-empty problem list means the comparator judged it bad)."""
    return [name for name, arg, should_pass in cases if bool(check(arg)) == should_pass]


def _shape_violation_cases(
    prefix: str, good_step: dict[str, Any], good_job: dict[str, Any]
) -> list[tuple[str, Any, bool]]:
    """The four allowlist-violating shapes (a step-level `shell:` override or unknown key, a
    job-level `defaults.run.shell` or unknown key) that both the demo-fault and quick jobs must
    reject identically -- shared so the two case lists can't silently drift apart."""
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
    ]


def self_test() -> int:
    good_step = {"run": CANONICAL_RUN}
    good_job = {"steps": [good_step]}
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
        "needs": ["build", "demo-fault"],
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
    workflow_cases: list[tuple[str, Any, bool]] = [
        ("workflow without defaults", {}, True),
        (
            "workflow with defaults.run.shell",
            {"defaults": {"run": {"shell": "true {0}"}}},
            False,
        ),
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
