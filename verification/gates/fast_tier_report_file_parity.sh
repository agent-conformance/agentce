#!/usr/bin/env bash
# Build gate helper for VG-FAST-TIER-REPORT-FILE-PARITY (18.184v): three-engine.yml, a fast-tier workflow that runs on
# every phase push, runs the cross-engine readiness, sign and report --validate agreement checks from source, each
# --self-test then the real run, in one unconditional job of 20 minutes or less. Without it a phase push that made one
# engine disagree went green on the fast tier (quickstart.yml runs the same checks on main, pull requests and dispatch).
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$root"
env -u VIRTUAL_ENV uv run --project tools --frozen python - .github/workflows/three-engine.yml <<'PY'
import sys

import yaml

TOOLS = ("readiness", "sign", "report_validate")
JOB_KEYS = {"name", "runs-on", "timeout-minutes", "steps"}
STEP_KEYS = {"name", "uses", "with", "run"}
ROOT_KEYS = {"name", "on", "concurrency", "permissions", "jobs"}

path = sys.argv[1]
with open(path, encoding="utf-8") as handle:
    workflow = {"on" if key is True else key: value for key, value in yaml.safe_load(handle).items()}  # PyYAML reads `on:` as True
problems = []

if set(workflow) - ROOT_KEYS:
    problems.append(f"workflow root carries {sorted(set(workflow) - ROOT_KEYS)}, which can change how every run: line executes")
on = workflow.get("on") or {}
push = on.get("push") if isinstance(on, dict) else None
if not isinstance(push, dict) or "phase/**" not in (push.get("branches") or []):
    problems.append("on.push.branches does not include phase/**, so phase pushes do not run the checks")
elif set(push) - {"branches"}:
    problems.append(f"on.push carries a filter {sorted(set(push) - {'branches'})}, so some phase pushes skip the checks")

jobs = workflow.get("jobs") or {}
run_lines = {
    name: [line.strip() for step in job.get("steps") or [] for line in str(step.get("run", "")).splitlines()]
    for name, job in jobs.items()
}
owner_jobs = set()
for tool in TOOLS:
    owners = [name for name, lines in run_lines.items() if any(f"tools/{tool}_parity_check.py" in line for line in lines)]
    if len(owners) != 1:
        problems.append(f"{tool}_parity_check.py runs in {len(owners)} jobs {owners}, expected exactly one")
    owner_jobs.update(owners)
for name in sorted(owner_jobs):
    job = jobs[name]
    if set(job) - JOB_KEYS:
        problems.append(f"job {name} carries {sorted(set(job) - JOB_KEYS)}, which can skip or excuse it")
    minutes = job.get("timeout-minutes")
    if type(minutes) is not int or not 0 < minutes <= 20:
        problems.append(f"job {name} timeout-minutes is {minutes!r}, expected 1 to 20")
    for step in job.get("steps") or []:
        if set(step) - STEP_KEYS:
            problems.append(f"job {name} has a step carrying {sorted(set(step) - STEP_KEYS)}")
    for tool in TOOLS:
        command = f"uv run --project tools --frozen python tools/{tool}_parity_check.py"
        for wanted in (f"{command} --self-test", command):
            count = run_lines[name].count(wanted)
            if count != 1:
                problems.append(f"job {name} runs `{wanted}` {count} times as a whole line, expected once")

for problem in problems:
    print(f"FAIL: {path}: {problem}")
if problems:
    sys.exit(1)
print(f"OK: {path} runs the readiness, sign and report --validate agreement checks on every phase push")
PY
