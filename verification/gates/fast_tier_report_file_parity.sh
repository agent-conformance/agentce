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
    workflow = yaml.safe_load(handle)
problems = []

extra = {"on" if key is True else key for key in workflow} - ROOT_KEYS  # PyYAML reads `on:` as True
if extra:
    problems.append(f"workflow root carries {sorted(map(str, extra))}, which can change how every run: line executes")
on = workflow.get("on", workflow.get(True)) or {}
push = on.get("push") if isinstance(on, dict) else None
if not isinstance(push, dict) or "phase/**" not in (push.get("branches") or []):
    problems.append("on.push.branches does not include phase/**, so phase pushes do not run the checks")
elif set(push) - {"branches"}:
    problems.append(f"on.push carries a filter {sorted(set(push) - {'branches'})}, so some phase pushes skip the checks")

jobs = workflow.get("jobs") or {}
owners = {tool: [n for n, j in jobs.items() if f"tools/{tool}_parity_check.py" in yaml.safe_dump(j)] for tool in TOOLS}
for tool, names in owners.items():
    if len(names) != 1:
        problems.append(f"{tool}_parity_check.py is named in {len(names)} jobs {names}, expected exactly one")
names = {n for found in owners.values() for n in found}
for name in sorted(names):
    job = jobs[name]
    if set(job) - JOB_KEYS:
        problems.append(f"job {name} carries {sorted(set(job) - JOB_KEYS)}, which can skip or excuse it")
    minutes = job.get("timeout-minutes")
    if not isinstance(minutes, int) or isinstance(minutes, bool) or not 0 < minutes <= 20:
        problems.append(f"job {name} timeout-minutes is {minutes!r}, expected 1 to 20")
    lines = []
    for step in job.get("steps") or []:
        if set(step) - STEP_KEYS:
            problems.append(f"job {name} has a step carrying {sorted(set(step) - STEP_KEYS)}")
        lines += [line.strip() for line in str(step.get("run", "")).splitlines()]
    for tool in TOOLS:
        command = f"uv run --project tools --frozen python tools/{tool}_parity_check.py"
        for wanted in (f"{command} --self-test", command):
            if lines.count(wanted) != 1:
                problems.append(f"job {name} runs `{wanted}` {lines.count(wanted)} times as a whole line, expected once")

for problem in problems:
    print(f"FAIL: {path}: {problem}")
if problems:
    sys.exit(1)
print(f"OK: {path} runs the readiness, sign and report --validate agreement checks on every phase push")
PY
