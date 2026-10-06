"""verify_census_shard_coverage_check - the CI census shards run every `agentce verify` census mutation
exactly once (18.93).

`tools/verify_parity_check.py`'s census (every mutation `verify_flow_census.generate` derives, through
all three engines) used to run whole inside quickstart's `installed-artifacts-offline` job, 15 of its 21
minutes. It now runs as a four-way `verify-census` matrix job, each job running
``verify_parity_check.py --shard "<job-index>/<job-total>"``, GitHub's own 0..job-total-1 numbering of
the jobs the matrix creates, while the artifacts job runs the named scenarios once with
``--scenarios-only``. This check guards the ways that split can silently stop running some mutations,
asserting the good shape (as `ci_demo_coverage_check` does, whose allowlist helpers it reuses) rather
than naming bad edits one at a time:

1. The census step's ``run:`` must be exactly the canonical invocation and the step may carry only
   ``name``/``run``; the job may carry only the keys it has today, ``strategy`` only ``fail-fast`` and
   ``matrix``, so a literal shard spelling, an ``if:``, ``continue-on-error`` or a ``shell:`` override
   is rejected by construction; any other line there naming the script (a full census or the
   scenarios beside the shard step) is rejected too.
2. The matrix must create exactly `CENSUS_SHARDS` jobs: with job-total wiring fewer shards would still
   cover every mutation, but each would run longer than the 8-minute budget the split exists for.
3. Outside the census job, a run line naming ``verify_parity_check.py`` may only be its ``--self-test``
   or ``--scenarios-only`` (an allowlist, so ``--shard 0/1``, a pipe or a copied shard line cannot bring
   the full census back), and the artifacts job must run both census self-tests and the named
   scenarios exactly once each.
4. The real ``--list-shard i/N`` output for every i must cover ``--list-shard 0/1`` (the full list) with
   every position exactly once and the same name per position, catching a truncated or duplicated
   shard selection that a wiring check alone cannot see. Positions, not names, are the key: two
   different census mutations share a name.
5. ``--list-shard 0/1`` with ``CI=true`` and with ``CI`` unset must print the same list (18.96), so a
   developer's local list is the one CI's shards run. The fixture report's ``assess`` once read the
   variable and added ``report.junit.xml`` under CI, eight mutations a laptop never listed.

Usage:
    verify_census_shard_coverage_check.py              # the real workflow and the real --list-shard
    verify_census_shard_coverage_check.py --self-test  # proves the comparator discriminates
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import yaml
from ci_demo_coverage_check import (
    _misjudged,
    _shape_violation_cases,
    _step_wiring_problems,
    _unknown_keys,
    workflow_level_problems,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "quickstart.yml"
CENSUS_JOB = "verify-census"
ARTIFACTS_JOB = "installed-artifacts-offline"
CENSUS_SCRIPT = "tools/verify_parity_check.py"
CENSUS_SHARDS = 4
UV_PYTHON = "uv run --project tools --frozen python"
CENSUS_CMD = f"{UV_PYTHON} {CENSUS_SCRIPT}"
CANONICAL_RUN = f'{CENSUS_CMD} --shard "${{{{ strategy.job-index }}}}/${{{{ strategy.job-total }}}}"'
SCENARIOS_RUN = f"{CENSUS_CMD} --scenarios-only"
JOB_ALLOWED_KEYS = {"name", "runs-on", "steps", "strategy", "timeout-minutes"}
STRATEGY_ALLOWED_KEYS = {"fail-fast", "matrix"}
SELF_TEST_RUNS = (
    f"{UV_PYTHON} tools/verify_flow_census.py --self-test",
    f"{CENSUS_CMD} --self-test",
)
# Outside the census job, the only lines allowed to mention the census script; any other spelling
# (no flag, `--shard 0/1`, a pipe, a copied shard line) could run the full census again.
ALLOWED_OUTSIDE_CENSUS = {SCENARIOS_RUN, SELF_TEST_RUNS[1]}

Listing = list[tuple[int, str]]


def _steps(job: dict[str, Any]) -> list[dict[str, Any]]:
    steps = job.get("steps")
    return [s for s in steps if isinstance(s, dict)] if isinstance(steps, list) else []


def matrix_size(strategy: Any) -> int | None:
    """How many jobs the matrix creates, or None for a shape this check refuses to reason about
    (include/exclude, more than one dimension, a non-list value)."""
    matrix = strategy.get("matrix") if isinstance(strategy, dict) else None
    if not isinstance(matrix, dict) or len(matrix) != 1:
        return None
    (values,) = matrix.values()
    return len(values) if isinstance(values, list) else None


def _run_lines(job: Any) -> list[str]:
    steps = _steps(job) if isinstance(job, dict) else []
    return [line.strip() for s in steps for line in str(s.get("run", "")).splitlines()]


def _stray_lines(name: str, job: Any, allowed: set[str]) -> list[str]:
    """Every run line of `job` that names the census script but is not one of `allowed`."""
    return [
        f"jobs.{name} runs {line!r}; there a line naming {CENSUS_SCRIPT} may only be one of "
        f"{sorted(allowed)}"
        for line in _run_lines(job)
        if Path(CENSUS_SCRIPT).name in line and line not in allowed
    ]


def census_job_problems(job: Any) -> list[str]:
    if not isinstance(job, dict):
        return [f"jobs.{CENSUS_JOB} is missing or not a mapping"]
    problems = _unknown_keys(job, JOB_ALLOWED_KEYS, f"jobs.{CENSUS_JOB}")
    strategy = job.get("strategy")
    if isinstance(strategy, dict):
        problems += _unknown_keys(
            strategy, STRATEGY_ALLOWED_KEYS, f"jobs.{CENSUS_JOB}.strategy"
        )
    size = matrix_size(strategy)
    if size != CENSUS_SHARDS:
        problems.append(
            f"jobs.{CENSUS_JOB}'s matrix must be one list of exactly {CENSUS_SHARDS} values "
            f"(no include/exclude); it creates {size if size is not None else 'an unknown number of'} job(s)"
        )
    problems += _stray_lines(CENSUS_JOB, job, {CANONICAL_RUN})
    census_steps = [s for s in _steps(job) if "--shard" in str(s.get("run", ""))]
    if len(census_steps) != 1:
        return problems + [
            f"jobs.{CENSUS_JOB} has {len(census_steps)} step(s) running {CENSUS_SCRIPT} --shard "
            "(expected exactly 1)"
        ]
    return problems + _step_wiring_problems(
        census_steps[0], "census shard", CANONICAL_RUN
    )


def scenario_problems(jobs: dict[str, Any]) -> list[str]:
    problems = [
        problem
        for name, job in jobs.items()
        if name != CENSUS_JOB
        for problem in _stray_lines(name, job, ALLOWED_OUTSIDE_CENSUS)
    ]
    lines = _run_lines(jobs.get(ARTIFACTS_JOB))
    for expected in (*SELF_TEST_RUNS, SCENARIOS_RUN):
        if lines.count(expected) != 1:
            problems.append(
                f"jobs.{ARTIFACTS_JOB} must run {expected!r} exactly once; "
                f"found {lines.count(expected)}"
            )
    return problems


def partition_problems(full: Listing, shards: list[Listing]) -> list[str]:
    """Every position of the full list in exactly one shard, under the same name."""
    names = dict(full)
    seen: dict[int, int] = {}
    problems = []
    for index, listing in enumerate(shards):
        for position, name in listing:
            seen[position] = seen.get(position, 0) + 1
            if names.get(position) != name:
                problems.append(
                    f"shard {index}/{len(shards)} lists position {position} as {name!r}; the full "
                    f"list has {names.get(position)!r}"
                )
    missing = [p for p in names if p not in seen]
    doubled = sorted(p for p, n in seen.items() if n > 1)
    if missing:
        problems.append(
            f"{len(missing)} of {len(names)} census mutation(s) are in no shard, "
            f"first: {missing[:5]}"
        )
    if doubled:
        problems.append(
            f"{len(doubled)} census mutation(s) are in more than one shard, first: {doubled[:5]}"
        )
    return problems


def list_shard(
    index: int, total: int, root: Path = REPO_ROOT, *, ci: bool | None = None
) -> tuple[Listing, int]:
    """The real `--list-shard index/total` output, and the census size it reports on stderr; `ci`
    sets (`True`) or removes (`False`) the `CI` variable for the run, `None` keeps the caller's."""
    env = dict(os.environ)
    if ci is not None:
        env.pop("CI", None)
        if ci:
            env["CI"] = "true"
    result = subprocess.run(
        [sys.executable, str(root / CENSUS_SCRIPT), "--list-shard", f"{index}/{total}"],
        capture_output=True,
        text=True,
        check=True,
        cwd=root,
        env=env,
    )
    listing = []
    for line in result.stdout.splitlines():
        position, _, name = line.partition("\t")
        listing.append((int(position), name))
    size = re.search(r"lists \d+ of (\d+) mutations", result.stderr)
    return listing, int(size.group(1)) if size else -1


def full_list_problems(full: Listing, size: int) -> list[str]:
    """`--list-shard 0/1` must list positions 0..size-1, size being the generator's own count."""
    if [position for position, _ in full] != list(range(size)):
        return [
            f"--list-shard 0/1 lists {len(full)} position(s), not exactly 0..{size - 1} of the "
            f"{size} census mutations the generator reports"
        ]
    return []


def ci_env_problems(with_ci: Listing, without_ci: Listing) -> list[str]:
    """The full list must not depend on the `CI` variable: CI's shards and a local run list the same."""
    if with_ci == without_ci:
        return []
    differ = next(
        (i for i, (a, b) in enumerate(zip(with_ci, without_ci)) if a != b),
        min(len(with_ci), len(without_ci)),
    )
    return [
        f"the census list depends on the CI variable: {len(with_ci)} mutation(s) with CI=true, "
        f"{len(without_ci)} without; first difference at position {differ}"
    ]


def check_workflow(
    workflow_path: Path = WORKFLOW_PATH, root: Path = REPO_ROOT
) -> list[str]:
    try:
        doc = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        return [f"{workflow_path}: unreadable: {exc}"]
    raw_jobs = doc.get("jobs") if isinstance(doc, dict) else None
    jobs: dict[str, Any] = raw_jobs if isinstance(raw_jobs, dict) else {}
    problems = (
        workflow_level_problems(doc)
        + census_job_problems(jobs.get(CENSUS_JOB))
        + scenario_problems(jobs)
    )
    full, size = list_shard(0, 1, root)
    if not full or size < 1:
        return problems + [
            f"{CENSUS_SCRIPT} --list-shard 0/1 printed no mutations or no size"
        ]
    # Each call rebuilds the signed fixtures (about 3.5 s); they are independent, so run them at once.
    with ThreadPoolExecutor(max_workers=CENSUS_SHARDS + 2) as pool:
        with_ci, without_ci = (
            pool.submit(list_shard, 0, 1, root, ci=ci) for ci in (True, False)
        )
        shards = list(
            pool.map(
                lambda i: list_shard(i, CENSUS_SHARDS, root)[0], range(CENSUS_SHARDS)
            )
        )
        env_problems = ci_env_problems(with_ci.result()[0], without_ci.result()[0])
    return (
        problems
        + full_list_problems(full, size)
        + partition_problems(full, shards)
        + env_problems
    )


def self_test() -> int:
    good_step = {"name": "census", "run": CANONICAL_RUN}
    good_strategy: dict[str, Any] = {
        "fail-fast": False,
        "matrix": {"shard": [0, 1, 2, 3]},
    }
    good_job: dict[str, Any] = {
        "runs-on": "ubuntu-latest",
        "strategy": good_strategy,
        "steps": [{"run": "pnpm build"}, good_step],
    }
    job_cases: list[tuple[str, Any, bool]] = [
        ("correctly wired census job", good_job, True),
        ("job missing", None, False),
        (
            "step keyed off the matrix value",
            {
                **good_job,
                "steps": [
                    {"run": CANONICAL_RUN.replace("strategy.job-index", "matrix.shard")}
                ],
            },
            False,
        ),
        (
            "step with a literal shard total",
            {
                **good_job,
                "steps": [
                    {"run": CANONICAL_RUN.replace("${{ strategy.job-total }}", "4")}
                ],
            },
            False,
        ),
        (
            "step with continue-on-error",
            {**good_job, "steps": [{**good_step, "continue-on-error": True}]},
            False,
        ),
        (
            "step with an if:",
            {**good_job, "steps": [{**good_step, "if": "github.event_name == 'push'"}]},
            False,
        ),
        (
            "step with a trailing || true",
            {**good_job, "steps": [{"run": CANONICAL_RUN + " || true"}]},
            False,
        ),
        ("job with continue-on-error", {**good_job, "continue-on-error": True}, False),
        ("job with an if:", {**good_job, "if": "false"}, False),
        (
            "matrix of 3",
            {**good_job, "strategy": {"matrix": {"shard": [0, 1, 2]}}},
            False,
        ),
        (
            "matrix of 5",
            {**good_job, "strategy": {"matrix": {"shard": [0, 1, 2, 3, 4]}}},
            False,
        ),
        (
            "matrix with include",
            {
                **good_job,
                "strategy": {
                    "matrix": {"shard": [0, 1, 2, 3], "include": [{"shard": 4}]}
                },
            },
            False,
        ),
        (
            "strategy with max-parallel",
            {**good_job, "strategy": {**good_strategy, "max-parallel": 1}},
            False,
        ),
        ("no census step", {**good_job, "steps": [{"run": "pnpm build"}]}, False),
        ("two census steps", {**good_job, "steps": [good_step, good_step]}, False),
        *_shape_violation_cases("census", good_step, good_job),
        (
            "full census added beside the shard step",
            {
                **good_job,
                "steps": [
                    good_step,
                    {"run": CENSUS_CMD},
                ],
            },
            False,
        ),
        (
            "scenarios added to every shard",
            {**good_job, "steps": [good_step, {"run": SCENARIOS_RUN}]},
            False,
        ),
    ]
    artifacts = {"steps": [{"run": "\n".join((*SELF_TEST_RUNS, SCENARIOS_RUN))}]}

    def artifacts_plus(line: str) -> dict[str, Any]:
        return {ARTIFACTS_JOB: {"steps": [*artifacts["steps"], {"run": line}]}}

    scenario_cases: list[tuple[str, Any, bool]] = [
        (
            "scenarios once, census sharded",
            {ARTIFACTS_JOB: artifacts, CENSUS_JOB: good_job},
            True,
        ),
        (
            "bare full census left in the artifacts job",
            artifacts_plus(CENSUS_CMD),
            False,
        ),
        (
            "whole census as one shard",
            artifacts_plus(f"{CENSUS_CMD} --shard 0/1"),
            False,
        ),
        (
            "full census piped to tee",
            artifacts_plus(f"{CENSUS_CMD} 2>&1 | tee x.log"),
            False,
        ),
        ("full census then echo", artifacts_plus(f"{CENSUS_CMD} && echo done"), False),
        (
            "canonical shard line copied outside the matrix",
            artifacts_plus(CANONICAL_RUN),
            False,
        ),
        (
            "full census in a third job",
            {**artifacts_plus("true"), "other": {"steps": [{"run": CENSUS_CMD}]}},
            False,
        ),
        (
            "verify_flow_census self-test dropped",
            {
                ARTIFACTS_JOB: {
                    "steps": [{"run": f"{SELF_TEST_RUNS[1]}\n{SCENARIOS_RUN}"}]
                }
            },
            False,
        ),
        (
            "verify_parity_check self-test dropped",
            {
                ARTIFACTS_JOB: {
                    "steps": [{"run": f"{SELF_TEST_RUNS[0]}\n{SCENARIOS_RUN}"}]
                }
            },
            False,
        ),
        (
            "scenarios never run",
            {ARTIFACTS_JOB: {"steps": [{"run": "\n".join(SELF_TEST_RUNS)}]}},
            False,
        ),
        (
            "scenarios run twice",
            artifacts_plus(SCENARIOS_RUN),
            False,
        ),
    ]
    full = [(0, "a"), (1, "b"), (2, "b"), (3, "c"), (4, "d")]
    partition_cases: list[tuple[str, Any, bool]] = [
        (
            "exact partition (a shared name counted per position)",
            [full[0::2], full[1::2]],
            True,
        ),
        ("truncated shard", [full[0::2][:1], full[1::2]], False),
        ("duplicated position", [full[0::2], full[1::2] + [full[0]]], False),
        ("renamed position", [full[0::2], [(1, "b"), (3, "zz")]], False),
        ("an empty shard set", [], False),
    ]
    full_cases: list[tuple[str, Any, bool]] = [
        ("full list 0..4 of 5", (full, 5), True),
        ("full list truncated by the selection it checks", (full[:3], 5), False),
        ("full list with a gap", ([full[0], full[2]], 2), False),
    ]
    env_cases: list[tuple[str, Any, bool]] = [
        ("same list with and without CI", (full, list(full)), True),
        ("an extra mutation under CI", (full + [(5, "junit")], full), False),
        ("a renamed mutation under CI", ([*full[:4], (4, "zz")], full), False),
    ]
    judged: list[tuple[list[tuple[str, Any, bool]], Any]] = [
        (job_cases, census_job_problems),
        (scenario_cases, scenario_problems),
        (partition_cases, lambda shards: partition_problems(full, shards)),
        (full_cases, lambda args: full_list_problems(*args)),
        (env_cases, lambda args: ci_env_problems(*args)),
    ]
    misjudged = [name for cases, check in judged for name in _misjudged(cases, check)]
    for name in misjudged:
        print(f"SELF-TEST FAIL: misjudged {name!r}", file=sys.stderr)
    if misjudged:
        return 1
    total = sum(len(cases) for cases, _ in judged)
    print(f"self-test PASS: {total} cases judged correctly")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        return self_test()
    problems = check_workflow()
    if problems:
        print(
            "FAIL: the CI census shards do not run every census mutation exactly once:"
        )
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print(
        f"PASS: jobs.{CENSUS_JOB} runs {CENSUS_SHARDS} shards keyed off "
        "strategy.job-index/strategy.job-total, the named scenarios run once in "
        f"jobs.{ARTIFACTS_JOB}, the shards cover every census mutation exactly once, and the "
        "list is the same with and without CI"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
