#!/usr/bin/env bash
# Build gate helper for VG-ASSESS-EXIT-INSUFFICIENT-EVIDENCE (item 18.30, SPEC.md:1076 / SPEC §8.5):
# `assess` returns exit code 2 (insufficient evidence on any severity: high control) identically across
# Python, TypeScript, and Java, over four real corpus projects with known expected codes --
# credit/langgraph/insufficient-evidence (2, OVS-03 severity: high forced to insufficient_evidence, plus
# 7 genuinely non-conformant controls so exit_status carries both "findings" and
# "insufficient_evidence"), credit/langgraph/known-pass (0), credit/langgraph/known-fail (1, no
# severity-high gap), and the vendored corpus/quickstart (0, its 19 insufficient_evidence assertions are
# all severity: medium). tools/assess_exit_code_parity_check.py's own `--self-test` proves the
# comparator discriminates a one-value tamper before the real cross-engine run trusts it.
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
env -u VIRTUAL_ENV uv run --project "$root/engines/python" --frozen python "$root/tools/assess_exit_code_parity_check.py" --self-test
uv run --project "$root/corpus/generator" --frozen python "$root/corpus/generator/generate.py" --set v1 --out /tmp/vg-exit-code-corpus >/dev/null
(cd "$root/engines/typescript" && pnpm install --frozen-lockfile >/dev/null && pnpm build)
(cd "$root/engines/java" && ./gradlew --no-daemon :assemble -q)
env -u VIRTUAL_ENV uv run --project "$root/engines/python" --frozen python "$root/tools/assess_exit_code_parity_check.py"
