"""ci_demo_coverage_check - the CI demo-fault job actually demos every gate from
``verification/run --list``, and a failed demo actually fails the required `quick` check (18.74).

The verification CI job used to run every gate's seeded-fault demo one after another
(``for gate in $(./verification/run --list); do ./verification/run --demo-fault "$gate"; done``),
so its wall time grew with every new gate and was heading past its own timeout. The fix splits that
loop across a parallel ``demo-fault`` matrix job, where each job runs
``verification/shard.py <job-index> <job-total>`` -- GitHub's own contiguous 0..job-total-1 numbering
of every job the matrix actually creates, so the partition is complete by construction regardless of
matrix shape -- and a thin ``quick`` job (the required check) that fails unless both ``build`` and
``demo-fault`` succeeded. Contract-critic review on this item found, and this check now covers, three
independent ways that design can silently stop proving anything:

1. ``verification/shard.py``'s own partition line could be truncated or off-by-one (for example
   ``mine[:1]``), dropping gates no CI log would call out as missing. Checked by actually running
   ``shard.py --dry-run`` for every index of a real total and confirming the union, against the real
   ``verification/run --list``, assigns every gate to exactly one shard.
2. The demo step's ``run:`` could stop being exactly ``verification/shard.py "$job-index" "$job-
   total"`` (a trailing ``|| true``, an ``echo`` prefix, a job- or step-level ``if:`` that skips it
   under some condition) and still contain the same substrings a looser regex would accept. Checked
   by an exact (whitespace-stripped) match, and by rejecting any ``if:`` on the job or the step.
3. The ``quick`` job's own gating logic could stop actually depending on ``demo-fault``'s result (for
   example ``needs: [build]``), defeating the one property the item asks for by name: a failed,
   cancelled or skipped shard must still fail the required check. Checked by reading ``quick``'s
   ``needs``, its ``if: always()``, and that its step compares both dependencies' results.

Usage:
    ci_demo_coverage_check.py              # checks the real workflow and the real shard.py
    ci_demo_coverage_check.py --self-test  # proves the comparator discriminates, without CI
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "verification.yml"
DEMO_JOB = "demo-fault"
QUICK_JOB = "quick"
SHARD_SCRIPT = "verification/shard.py"
CANONICAL_RUN = 'python3 verification/shard.py "${{ strategy.job-index }}" "${{ strategy.job-total }}"'
PARTITION_TOTALS = (
    6,
    7,
)  # 6 matches the workflow's lane count; 7 catches an off-by-one that
# only shows up once total doesn't divide the gate count evenly


def _demo_step(job: dict[str, Any]) -> dict[str, Any] | None:
    steps = job.get("steps")
    if not isinstance(steps, list):
        return None
    for step in steps:
        if isinstance(step, dict) and SHARD_SCRIPT in str(step.get("run", "")):
            return step
    return None


def wiring_problems(job: Any) -> list[str]:
    """The demo-fault job's step must be exactly the canonical invocation, with no `if:` on the job
    or the step, and no continue-on-error swallowing a failure -- each independently defeats the
    complete-by-construction partition without the other two noticing."""
    if not isinstance(job, dict):
        return [f"jobs.{DEMO_JOB} is missing or not a mapping"]
    problems: list[str] = []
    if job.get("continue-on-error"):
        problems.append(
            f"jobs.{DEMO_JOB} sets continue-on-error, so a failed shard would not fail the job"
        )
    if "if" in job:
        problems.append(
            f"jobs.{DEMO_JOB} has a job-level if: ({job['if']!r}), which could skip the whole job"
        )
    step = _demo_step(job)
    if step is None:
        return problems + [f"no step in jobs.{DEMO_JOB} invokes {SHARD_SCRIPT}"]
    if step.get("continue-on-error"):
        problems.append(
            f"the {SHARD_SCRIPT} step sets continue-on-error, so a failed demo would not fail the job"
        )
    if "if" in step:
        problems.append(
            f"the {SHARD_SCRIPT} step has an if: ({step['if']!r}), which could skip it for some lanes"
        )
    run_text = str(step.get("run", "")).strip()
    if run_text != CANONICAL_RUN:
        problems.append(
            f"the {SHARD_SCRIPT} step's run: is not exactly {CANONICAL_RUN!r}, so its partition is "
            f"not guaranteed complete for whatever the matrix actually enumerates: {run_text!r}"
        )
    return problems


_ALWAYS = re.compile(r"\balways\(\)")


def quick_wiring_problems(job: Any) -> list[str]:
    """`quick` is the required check; GitHub treats a *skipped* required check as passing, so
    `quick` must be a real job whose own logic fails unless both dependencies truly succeeded."""
    if not isinstance(job, dict):
        return [f"jobs.{QUICK_JOB} is missing or not a mapping"]
    problems: list[str] = []
    needs = job.get("needs")
    needed = set(needs) if isinstance(needs, list) else set()
    for required in ("build", DEMO_JOB):
        if required not in needed:
            problems.append(
                f"jobs.{QUICK_JOB}.needs does not include {required!r}: {needs!r}"
            )
    if not _ALWAYS.search(str(job.get("if", ""))):
        problems.append(
            f"jobs.{QUICK_JOB} has no if: always() (got {job.get('if')!r}), so it could be skipped "
            "entirely if an earlier job failed -- and GitHub treats a skipped required check as passing"
        )
    raw_steps = job.get("steps")
    steps: list[Any] = raw_steps if isinstance(raw_steps, list) else []
    blob = " ".join(
        str(step.get("run", "")) for step in steps if isinstance(step, dict)
    )
    if not re.search(r'needs\.build\.result.{0,20}["\']success["\']', blob):
        problems.append(
            f"no step in jobs.{QUICK_JOB} compares needs.build.result to success"
        )
    if not re.search(r'needs\.demo-fault\.result.{0,20}["\']success["\']', blob):
        problems.append(
            f"no step in jobs.{QUICK_JOB} compares needs.demo-fault.result to success"
        )
    return problems


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
            f"shard.py --dry-run (total={total}) produced gate(s) not in `verification/run --list`: {extra}"
        )
    return problems


def partition_problems(
    gates: list[str], total: int, root: Path = REPO_ROOT
) -> list[str]:
    """Actually run ``shard.py --dry-run`` for every index of ``total`` and confirm the union,
    against the real gate list, assigns every gate to exactly one shard -- catching a truncated or
    off-by-one edit to ``partition()`` that a wiring check alone cannot see."""
    assignments: dict[str, list[int]] = {}
    for index in range(total):
        result = subprocess.run(
            ["python3", str(root / SHARD_SCRIPT), "--dry-run", str(index), str(total)],
            capture_output=True,
            text=True,
            check=True,
            cwd=root,
        )
        for gate in result.stdout.splitlines():
            if gate:
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
    problems = wiring_problems(jobs.get(DEMO_JOB)) + quick_wiring_problems(
        jobs.get(QUICK_JOB)
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
    ]
    failures = [
        name
        for name, job, should_pass in cases
        if bool(wiring_problems(job)) == should_pass
    ]
    quick_good = {
        "needs": ["build", "demo-fault"],
        "if": "always()",
        "steps": [
            {
                "run": 'if [ "${{ needs.build.result }}" != "success" ] || '
                '[ "${{ needs.demo-fault.result }}" != "success" ]; then exit 1; fi'
            }
        ],
    }
    quick_cases: list[tuple[str, Any, bool]] = [
        ("correctly wired quick job", quick_good, True),
        ("quick needs only build", {**quick_good, "needs": ["build"]}, False),
        ("quick with no if: always()", {**quick_good, "if": "success()"}, False),
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
    ]
    failures += [
        name
        for name, job, should_pass in quick_cases
        if bool(quick_wiring_problems(job)) == should_pass
    ]
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
    failures += [
        name
        for name, assignments, should_pass in partition_cases
        if bool(_coverage_problems(gates, assignments, len(gates))) == should_pass
    ]
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
