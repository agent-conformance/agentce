# Integrate an agent with AgentCE

How to make an AI-agent deployment emit canonical AgentCE evidence. This guide's snippets are taken
from `examples/` (SPEC §13.4 AX-7); every `examples/<style>/` is a runnable script that produces a
bundle `agentce validate` accepts with zero quarantines. Five of them really import and run their named
framework end to end; see [Run an example](#run-an-example).

## One line to set up the emitter (SPEC §13.4 AX-3)

Create the emitter with a single call. It is a no-op until you switch it on with `AGENTCE_EMIT=1`, so
it never changes what the agent does until you want evidence. That call alone hooks nothing: an agent
that calls it and nothing else emits no events. Two ways to fill that in exist today. Emit at each
chokepoint yourself (below) — every framework example under `examples/` does this, each from its own
callback or event hook that turns that framework's real payloads into `emit_*` calls. Or, for a
framework that already produces OTel-shaped spans, register `agentce_emit.instrument()`'s
`AgentCESpanProcessor` with its tracer so those spans become events through the existing `otel-genai`
mapping, without writing a chokepoint-by-chokepoint hook yourself. Fully automatic capture from the one
line alone, with no per-framework wiring at all, is on the roadmap and is not built.

```python
import agentce_emit

emitter = agentce_emit.auto()   # active when AGENTCE_EMIT=1; the bundle is flushed at process exit
```

Then emit at each chokepoint — one call per model call, tool call, decision, and so on:

```python
emitter.emit_session_start(environment="production", session_id="s1")
emitter.emit_model_call(operation="chat", provider="openai", model="gpt-4o", input_tokens=1024, output_tokens=128)
tool = emitter.emit_tool_call(name="credit.record_decision", server="mcp://credit-core", protocol="mcp",
                              args=arguments, result=result, side_effect="write", effect_class="write")
emitter.emit_decision(decision_type="dom:CreditDecision", affects_natural_person=True,
                      oversight_modality="review_before", chosen="approve",
                      refs={"executed_by": f"agentce:event/{tool}"})
emitter.emit_session_end(end_reason="completed", session_id="s1")
```

Content is referenced by hash, never captured (SPEC R12): pass the `args`/`result` and the emitter
records only their SHA-256. Agent-side emission is `self_report` (SPEC §6.4, S-2) — honest by default.
Run it:

```bash
AGENTCE_EMIT=1 AGENTCE_EMIT_OUT=evidence python your_agent.py
agentce validate --bundle evidence
```

## Run an example

Each `examples/<style>/run.sh <bundle-dir>` runs a scripted example and writes a bundle. Five of the six
are named for an implementation style and really import and run that framework end to end, offline and
keyless against a scripted deterministic model or transport, each in its own environment with the
framework's own dependencies; the events in the bundle come from that framework's real callback data.
`examples/custom-loop` has no framework to import by design and emits through `agentce_emit` the
evidence a hand-rolled agent of that style would produce; fully automatic, zero-wiring capture for any
of the styles is on the roadmap.

```bash
examples/langgraph/run.sh /tmp/bundle
agentce validate --bundle /tmp/bundle
```

`examples/run_all.py --check` runs and validates all of them at once. The styles: `langgraph`,
`openai-agents`, `claude-agent-sdk`, `google-adk`, `crewai`, `custom-loop`, plus `mcp-server` and
`a2a-mesh`.

## Prefer enforcement-point evidence

Agent-side emission is `self_report`. Where a gateway, policy engine, or identity provider sits in
front of a call, take its record through the matching adapter (`mcp-gateway`, `policy-engines`,
`identity`) as `enforcement_point` evidence, and do not also emit agent-side — the adapter's record is
the one an assessor trusts. The `agentce-onboard` skill inventories chokepoints and tells you which
have an enforcement point available.

## From a session to an assessment

1. Emit evidence (above) into a bundle, or read it from an export a source already wrote. `agentce
   ingest --adapter <name> --in <export file> --out <bundle>` adapts one file directly; `agentce
   collect` runs a scheduled job over a config's sources, reading each one's local `export` (an
   OpenTelemetry Collector's file exporter writes this shape, SPEC §5.4) and recording it `complete`.
   A source with no `export`, or whose export cannot be adapted, has no live connector: it is recorded
   `incomplete` and the run exits 1, never hidden. `--dry-run` only plans the job.
2. `agentce validate --bundle <dir>` — fix quarantines until it is clean.
3. `agentce assess --bundle <dir> --catalog eu-ai-act@2026.09 --profile agentce/applicability.yaml`.
4. Resolve any `insufficient_evidence` with the `agentce-onboard` skill's `explain_insufficient`, then
   re-emit and re-assess.

For the full declare → instrument → validate procedure, use the **agentce-onboard** skill
(`skills/agentce-onboard/`).
