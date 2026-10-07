#!/usr/bin/env bash
# Build gate helper for VG-OTEL-GENAI-PARITY (item 18.29): the otel-genai adapter's translation
# layer (bytes -> canonical AgentCE events) is byte-identical across Python, TypeScript, and Java
# over all 9 vendor fixtures (adapters/otel-genai/fixtures/) plus all 21 hostile vectors
# (spec/model/test-vectors/otel-genai-hostile/) -- 30 vectors in total, each read fresh into a
# temporary directory holding only its own input.json/adapt.json (tools/otel_genai_adapter_check.py's
# `--self-test` proves the comparator itself discriminates the truncation/enum-filter fault class
# before the real cross-engine run trusts it; that run gives each TypeScript and Java run 30 s per
# vector and fails naming the vector and the engine past it).
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
env -u VIRTUAL_ENV uv run --project "$root/engines/python" --frozen python "$root/tools/otel_genai_adapter_check.py" --self-test
(cd "$root/engines/typescript" && pnpm install --frozen-lockfile >/dev/null && pnpm build)
(cd "$root/engines/java" && ./gradlew --no-daemon :assemble -q)
env -u VIRTUAL_ENV uv run --project "$root/engines/python" --frozen python "$root/tools/otel_genai_adapter_check.py"
