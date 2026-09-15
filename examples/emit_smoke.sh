#!/usr/bin/env bash
# One-line-emitter smoke test (SPEC 13.4 AX-3): run an example so AGENTCE_EMIT=1 emits at least the
# minimum REC/OVS/INT/INC evidence (a Decision and a ToolCall) into the bundle at $1.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
exec "$HERE/langgraph/run.sh" "${1:?usage: emit_smoke.sh <bundle-dir>}"
