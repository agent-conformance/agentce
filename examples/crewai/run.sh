#!/usr/bin/env bash
# Run the crewai example, emitting an AgentCE evidence bundle to $1 (SPEC 13.4 AX-4).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
BUNDLE="${1:?usage: run.sh <bundle-dir>}"
export AGENTCE_EMIT=1
export AGENTCE_EMIT_OUT="$BUNDLE"
export AGENTCE_EMIT_SUBJECT="spiffe://corp/agents/credit-crewai"
export PYTHONPATH="$HERE/..${PYTHONPATH:+:$PYTHONPATH}"
# crewai's first-run tracing-consent prompt would otherwise block for up to 20s waiting on stdin;
# this is crewai's own documented opt-out (its error text names it), not a test-detection hook.
export CREWAI_TRACING_ENABLED=false
exec uv run --project "$HERE" --quiet python "$HERE/agent.py"
