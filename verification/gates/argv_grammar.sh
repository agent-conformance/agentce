#!/usr/bin/env bash
# Build gate helper for VG-ARGV-GRAMMAR (18.184w): every engine reads its command line under the SPEC 8.5 grammar.
# The hand-written table spec/cli/argv-grammar.json is the source; tools/argv_grammar.py --check holds each engine's
# vendored copy and option spellings to it. The shared case table spec/cli/argv-cases.json keeps a case for every
# rule the grammar names (the registry lint fails otherwise, shown here on a copy with one rule's cases dropped), each
# engine's case-table test runs every case through its real scanner and CLI entry, and tools/argv_parity_check.py runs
# every case through the three real CLIs, with and without --json, and checks a refused run writes nothing.
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$root"

env -u VIRTUAL_ENV uv run --project tools --frozen python - <<'PY'
import importlib.machinery
import importlib.util
import json
import shutil
import sys
import tempfile
from pathlib import Path

loader = importlib.machinery.SourceFileLoader("verification_run", "verification/run")
run = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
loader.exec_module(run)
problems = run.lint_argv_cases(Path("."))
if problems:
    print("FAIL", *problems)
    sys.exit(1)
with tempfile.TemporaryDirectory() as tmp:
    for rel in (run.ARGV_GRAMMAR, run.ARGV_CASES):
        (Path(tmp) / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(rel, Path(tmp) / rel)
    cases = Path(tmp) / run.ARGV_CASES
    table = json.loads(cases.read_text(encoding="utf-8"))
    table["cases"] = [c for c in table["cases"] if c["rule"] != "double_dash_alone"]
    cases.write_text(json.dumps(table), encoding="utf-8")
    problems = run.lint_argv_cases(Path(tmp))
    if problems != ["registry.argv_rule_without_case: double_dash_alone"]:
        print("FAIL the registry lint does not name a rule left with no case:", problems)
        sys.exit(1)
print("PASS every grammar rule keeps a case, and the registry lint names one that loses its last")
PY

env -u VIRTUAL_ENV uv run --project tools --frozen python tools/argv_grammar.py --check | tail -n 3

(cd engines/python && env -u VIRTUAL_ENV uv run --frozen pytest -q -p no:cacheprovider tests/test_argv_cases.py) |
  tail -n 3
echo "PASS python case-table test"

# Install here: a gate's place in a CI shard decides which gates ran before it.
(cd engines/typescript && pnpm install --frozen-lockfile >/dev/null && node --import tsx --test src/argvCases.test.ts) |
  tail -n 8
echo "PASS typescript case-table test"

# One Gradle start for the case-table test and the runnable jar the parity check runs.
(cd engines/java && ./gradlew --no-daemon --quiet test --rerun --tests org.agentce.ArgvCasesTest :assemble)
echo "PASS java case-table test"

env -u VIRTUAL_ENV uv run --project tools --frozen python tools/argv_parity_check.py --self-test | tail -n 3
(cd engines/typescript && pnpm build >/dev/null)
env -u VIRTUAL_ENV uv run --project tools --frozen python tools/argv_parity_check.py | tail -n 5
