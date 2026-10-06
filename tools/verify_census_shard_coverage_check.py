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
   developer's local list is the one CI's shards run.
6. Every shard splits one list (18.97): the ``census-list`` job publishes ``--list-sha256`` as its
   ``sha256`` output, ``verify-census`` needs it and passes it as ``--expect-list-sha256``, the real
   ``--list-sha256`` equals the sha256 of the real full list, and a real shard given that digest with
   one hex digit flipped refuses before any engine runs.
7. The census is recognised by the module stem ``verify_parity`` anywhere in a run line, so a full
   census spelled ``python -m verify_parity_check`` or ``cd tools && python verify_parity_check.py``
   is caught, and the workflow's ``on:`` must be exactly ``EXPECTED_TRIGGERS``: a ``paths-ignore``,
   ``paths`` or branch filter, or a dropped trigger, would stop the census running on some changes.

Usage:
    verify_census_shard_coverage_check.py              # the real workflow and the real --list-shard
    verify_census_shard_coverage_check.py --self-test  # proves the comparator discriminates
"""

from __future__ import annotations

import argparse
import hashlib
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
LIST_JOB = "census-list"
LIST_JOB_NAME = "verify census list"
ARTIFACTS_JOB = "installed-artifacts-offline"
CENSUS_SCRIPT = "tools/verify_parity_check.py"
# Any run line naming this stem reaches the census script, whatever the spelling (`.py`, `-m`, `cd tools`).
CENSUS_STEM = "verify_parity"
CENSUS_SHARDS = 4
UV_PYTHON = "uv run --project tools --frozen python"
CENSUS_CMD = f"{UV_PYTHON} {CENSUS_SCRIPT}"
CANONICAL_RUN = (
    f'{CENSUS_CMD} --shard "${{{{ strategy.job-index }}}}/${{{{ strategy.job-total }}}}" '
    f'--expect-list-sha256 "${{{{ needs.{LIST_JOB}.outputs.sha256 }}}}"'
)
SCENARIOS_RUN = f"{CENSUS_CMD} --scenarios-only"
LIST_LINE = f"digest=$({CENSUS_CMD} --list-sha256)"
LIST_RUN = f'{LIST_LINE}\necho "sha256=$digest" >> "$GITHUB_OUTPUT"'
LIST_OUTPUTS = {"sha256": "${{ steps.list.outputs.sha256 }}"}
LIST_JOB_ALLOWED_KEYS = {"name", "runs-on", "steps", "outputs", "timeout-minutes"}
LIST_STEP_ALLOWED_KEYS = {"name", "id", "run"}
JOB_ALLOWED_KEYS = {"name", "needs", "runs-on", "steps", "strategy", "timeout-minutes"}
STRATEGY_ALLOWED_KEYS = {"fail-fast", "matrix"}
SELF_TEST_RUNS = (
    f"{UV_PYTHON} tools/verify_flow_census.py --self-test",
    f"{CENSUS_CMD} --self-test",
)
# Outside the census job, the only lines allowed to mention the census script; any other spelling
# (no flag, `--shard 0/1`, a pipe, a copied shard line) could run the full census again.
ALLOWED_OUTSIDE_CENSUS = {SCENARIOS_RUN, SELF_TEST_RUNS[1], LIST_LINE}
# The workflow's whole `on:`, compared exactly: any filter or dropped trigger is rejected by construction.
EXPECTED_TRIGGERS: dict[str, Any] = {
    "push": {"branches": ["main", "phase/**"]},
    "pull_request": None,
    "workflow_dispatch": None,
}
REFUSAL_TIMEOUT_S = 60

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
        f"jobs.{name} runs {line!r}; there a line naming {CENSUS_STEM} may only be one of "
        f"{sorted(allowed)}"
        for line in _run_lines(job)
        if CENSUS_STEM in line and line not in allowed
    ]


def trigger_problems(doc: Any) -> list[str]:
    """`on:` must be exactly `EXPECTED_TRIGGERS` (PyYAML reads the bare key `on` as True)."""
    triggers = doc.get("on", doc.get(True)) if isinstance(doc, dict) else None
    if triggers == EXPECTED_TRIGGERS:
        return []
    return [
        f"the workflow's on: is {triggers!r}, not exactly {EXPECTED_TRIGGERS!r}; a paths, "
        "paths-ignore or branch filter, or a dropped trigger, stops the census running on some "
        "changes (update EXPECTED_TRIGGERS here if a trigger change is deliberate)"
    ]


def census_list_job_problems(job: Any) -> list[str]:
    """The job publishing the reference digest: only the allowed keys, its name, its one output, and
    exactly one step naming the census script, which is the canonical `--list-sha256` step."""
    label = f"jobs.{LIST_JOB}"
    if not isinstance(job, dict):
        return [f"{label} is missing or not a mapping"]
    problems = _unknown_keys(job, LIST_JOB_ALLOWED_KEYS, label)
    if job.get("name") != LIST_JOB_NAME:
        problems.append(f"{label}'s name must be {LIST_JOB_NAME!r}")
    if job.get("outputs") != LIST_OUTPUTS:
        problems.append(f"{label}'s outputs must be exactly {LIST_OUTPUTS!r}")
    steps = [s for s in _steps(job) if CENSUS_STEM in str(s.get("run", ""))]
    if len(steps) != 1:
        return problems + [
            f"{label} has {len(steps)} step(s) naming {CENSUS_STEM} (expected exactly 1)"
        ]
    step = steps[0]
    problems += _unknown_keys(step, LIST_STEP_ALLOWED_KEYS, f"the {LIST_JOB} step")
    if step.get("id") != "list":
        problems.append(f"the {LIST_JOB} step's id must be 'list'")
    if str(step.get("run", "")).strip() != LIST_RUN:
        problems.append(
            f"the {LIST_JOB} step's run: is not exactly {LIST_RUN!r}: "
            f"{str(step.get('run', '')).strip()!r}"
        )
    return problems


def census_job_problems(job: Any) -> list[str]:
    if not isinstance(job, dict):
        return [f"jobs.{CENSUS_JOB} is missing or not a mapping"]
    problems = _unknown_keys(job, JOB_ALLOWED_KEYS, f"jobs.{CENSUS_JOB}")
    if job.get("needs") != LIST_JOB:
        problems.append(
            f"jobs.{CENSUS_JOB} must have needs: {LIST_JOB}, the job whose digest each shard compares"
        )
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


def listing_sha256(listing: Listing) -> str:
    """The sha256 of a listing as `--list-shard` prints it, computed here and not by the script."""
    lines = "".join(f"{position}\t{name}\n" for position, name in listing)
    return hashlib.sha256(lines.encode("utf-8")).hexdigest()


def flip_last_digit(digest: str) -> str:
    return digest[:-1] + ("1" if digest[-1] == "0" else "0")


def list_sha256(root: Path = REPO_ROOT) -> str:
    """The real `--list-sha256` output (the reference CI's census-list job publishes)."""
    result = subprocess.run(
        [sys.executable, str(root / CENSUS_SCRIPT), "--list-sha256"],
        capture_output=True,
        text=True,
        check=True,
        cwd=root,
    )
    return result.stdout.strip()


def reference_problems(reference: str, full: Listing) -> list[str]:
    """`--list-sha256` must hash the list the shards split, the full `--list-shard 0/1` listing."""
    own = listing_sha256(full)
    if reference == own:
        return []
    return [
        f"--list-sha256 prints {reference!r}, but the full census list hashes to {own}: the "
        "reference digest is not the list the shards split"
    ]


def refusal_problems(
    returncode: int | None, output: str, own: str, wrong: str
) -> list[str]:
    """A shard handed `wrong` (its own digest `own` with one hex digit flipped) must exit 1 with a
    MISMATCH line naming both, and run no mutation; `returncode` None means it was still running."""
    if returncode is None:
        return [
            f"--shard 0/4 --expect-list-sha256 <wrong digest> was still running after "
            f"{REFUSAL_TIMEOUT_S} s: the shard did not refuse before running the engines"
        ]
    problems = []
    if returncode != 1:
        problems.append(f"a shard given a wrong digest exited {returncode}, not 1")
    if not any(
        "MISMATCH" in line and own in line and wrong in line
        for line in output.splitlines()
    ):
        problems.append(
            "a shard given a wrong digest printed no MISMATCH line naming both its own and the "
            "expected digest"
        )
    if "census shard 0/4: ran" in output:
        problems.append("a shard given a wrong digest ran mutations anyway")
    return problems


def probe_refusal(own: str, root: Path = REPO_ROOT) -> list[str]:
    """Run a real shard with a digest one hex digit off; it must refuse before any engine runs."""
    wrong = flip_last_digit(own)
    try:
        result = subprocess.run(
            [sys.executable, str(root / CENSUS_SCRIPT), "--shard", "0/4"]
            + ["--expect-list-sha256", wrong],
            capture_output=True,
            text=True,
            cwd=root,
            timeout=REFUSAL_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return refusal_problems(None, "", own, wrong)
    return refusal_problems(
        result.returncode, result.stdout + result.stderr, own, wrong
    )


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
        + trigger_problems(doc)
        + census_list_job_problems(jobs.get(LIST_JOB))
        + census_job_problems(jobs.get(CENSUS_JOB))
        + scenario_problems(jobs)
    )
    # Each call rebuilds the signed fixtures (about 3.5 s); they are independent, so run them at once.
    with ThreadPoolExecutor(max_workers=CENSUS_SHARDS + 3) as pool:
        without_ci, with_ci = (
            pool.submit(list_shard, 0, 1, root, ci=ci) for ci in (False, True)
        )
        reference = pool.submit(list_sha256, root)
        shards = list(
            pool.map(
                lambda i: list_shard(i, CENSUS_SHARDS, root)[0], range(CENSUS_SHARDS)
            )
        )
        full, size = without_ci.result()
        if not full or size < 1:
            return problems + [
                f"{CENSUS_SCRIPT} --list-shard 0/1 printed no mutations or no size"
            ]
        env_problems = ci_env_problems(with_ci.result()[0], full)
        digest_problems = reference_problems(reference.result(), full)
    return (
        problems
        + full_list_problems(full, size)
        + partition_problems(full, shards)
        + env_problems
        + digest_problems
        + probe_refusal(listing_sha256(full), root)
    )


def self_test() -> int:
    good_step = {"name": "census", "run": CANONICAL_RUN}
    good_strategy: dict[str, Any] = {
        "fail-fast": False,
        "matrix": {"shard": [0, 1, 2, 3]},
    }
    good_job: dict[str, Any] = {
        "runs-on": "ubuntu-latest",
        "needs": LIST_JOB,
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
        (
            "step without the expected digest",
            {
                **good_job,
                "steps": [{"run": CANONICAL_RUN.split(" --expect-list-sha256")[0]}],
            },
            False,
        ),
        (
            "job without needs",
            {k: v for k, v in good_job.items() if k != "needs"},
            False,
        ),
        ("job needing another job", {**good_job, "needs": "python"}, False),
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
            "full census spelled python -m",
            artifacts_plus("cd tools && uv run --frozen python -m verify_parity_check"),
            False,
        ),
        (
            "full census run from inside tools/",
            artifacts_plus("cd tools && python verify_parity_check.py"),
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
    good_list_step = {"name": "list", "id": "list", "run": LIST_RUN}
    good_list_job: dict[str, Any] = {
        "name": LIST_JOB_NAME,
        "runs-on": "ubuntu-latest",
        "outputs": LIST_OUTPUTS,
        "steps": [{"run": "uv sync"}, good_list_step],
    }

    def list_job_with(**step: Any) -> dict[str, Any]:
        return {**good_list_job, "steps": [{**good_list_step, **step}]}

    list_job_cases: list[tuple[str, Any, bool]] = [
        ("canonical census-list job", good_list_job, True),
        ("census-list job missing", None, False),
        ("census-list job renamed", {**good_list_job, "name": "list"}, False),
        (
            "census-list output renamed",
            {**good_list_job, "outputs": {"digest": "x"}},
            False,
        ),
        ("census-list job with an if:", {**good_list_job, "if": "false"}, False),
        ("list step without its id", list_job_with(id="other"), False),
        (
            "list step with continue-on-error",
            list_job_with(**{"continue-on-error": True}),
            False,
        ),
        (
            "list step writing a literal digest",
            list_job_with(run='echo "sha256=0" >> x'),
            False,
        ),
        (
            "list step piped",
            list_job_with(
                run=LIST_RUN.replace("--list-sha256)", "--list-sha256 | head -c 8)")
            ),
            False,
        ),
        (
            "two list steps",
            {**good_list_job, "steps": [good_list_step, good_list_step]},
            False,
        ),
    ]
    trigger_cases: list[tuple[str, Any, bool]] = [
        ("the expected triggers", {True: EXPECTED_TRIGGERS}, True),
        ("the expected triggers under a quoted on:", {"on": EXPECTED_TRIGGERS}, True),
        (
            "paths-ignore on push",
            {
                True: {
                    **EXPECTED_TRIGGERS,
                    "push": {
                        "branches": ["main", "phase/**"],
                        "paths-ignore": ["docs/**"],
                    },
                }
            },
            False,
        ),
        (
            "paths on pull_request",
            {True: {**EXPECTED_TRIGGERS, "pull_request": {"paths": ["engines/**"]}}},
            False,
        ),
        (
            "pull_request dropped",
            {True: {k: v for k, v in EXPECTED_TRIGGERS.items() if k != "pull_request"}},
            False,
        ),
        ("only workflow_dispatch", {True: {"workflow_dispatch": None}}, False),
        ("no on: at all", {}, False),
    ]
    reference_cases: list[tuple[str, Any, bool]] = [
        ("reference is the full list's digest", listing_sha256(full), True),
        ("reference of a shorter list", listing_sha256(full[:-1]), False),
        ("reference one digit off", flip_last_digit(listing_sha256(full)), False),
    ]
    own = listing_sha256(full)
    wrong = flip_last_digit(own)
    refusal_cases: list[tuple[str, Any, bool]] = [
        (
            "refused before any engine",
            (
                1,
                f"MISMATCH: census list sha256 {own} differs from the expected {wrong}\n",
            ),
            True,
        ),
        ("still running at the timeout", (None, ""), False),
        (
            "exit 0 after running",
            (0, f"census shard 0/4: ran 3 of 5\nMATCH {own} {wrong}"),
            False,
        ),
        (
            "refused naming only the expected digest",
            (1, f"MISMATCH: expected {wrong}"),
            False,
        ),
        (
            "refused after running mutations",
            (1, f"census shard 0/4: ran 3 of 5\nMISMATCH: {own} {wrong}"),
            False,
        ),
        ("crashed (exit 2)", (2, f"MISMATCH: {own} {wrong}"), False),
    ]
    judged: list[tuple[list[tuple[str, Any, bool]], Any]] = [
        (list_job_cases, census_list_job_problems),
        (trigger_cases, trigger_problems),
        (reference_cases, lambda reference: reference_problems(reference, full)),
        (refusal_cases, lambda args: refusal_problems(args[0], args[1], own, wrong)),
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
        f"jobs.{ARTIFACTS_JOB}, the shards cover every census mutation exactly once, the list is "
        f"the same with and without CI, every shard compares its list with jobs.{LIST_JOB}'s "
        "digest and refuses a wrong one before any engine runs, and the triggers are unfiltered"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
