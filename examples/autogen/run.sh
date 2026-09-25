#!/usr/bin/env bash
# Run the autogen example, emitting an AgentCE evidence bundle to $1 (SPEC 13.4 AX-4).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
BUNDLE="${1:?usage: run.sh <bundle-dir>}"
export AGENTCE_EMIT=1
export AGENTCE_EMIT_OUT="$BUNDLE"
export AGENTCE_EMIT_SUBJECT="spiffe://corp/agents/credit-autogen"
export PYTHONPATH="$HERE/..${PYTHONPATH:+:$PYTHONPATH}"
exec uv run --project "$HERE" --quiet python "$HERE/agent.py"
