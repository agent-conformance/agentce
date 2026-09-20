# Integrate an agent with AgentCE

How to make an AI-agent deployment emit canonical AgentCE evidence. This guide's snippets are taken
from `examples/` (SPEC §13.4 AX-7); every `examples/<style>/` is a runnable script that produces a
bundle `agentce validate` accepts with zero quarantines. None of them imports an agent framework: see
[Run an example](#run-an-example).

## One line to set up the emitter (SPEC §13.4 AX-3)

Create the emitter with a single call. It is a no-op until you switch it on with `AGENTCE_EMIT=1`, so
it never changes what the agent does until you want evidence. That call only returns the emitter: it
does not hook LangGraph, the OpenAI Agents SDK, CrewAI, Google ADK, or the Claude Agent SDK, so an agent
that calls it and nothing else emits no events. Capturing evidence automatically from those frameworks
is on the roadmap and is not built; today you emit at each chokepoint yourself.

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
AGENTCE_EMIT=1 AGENTCE_EMIT_OUT=agentce/bundle python your_agent.py
agentce validate --bundle agentce/bundle
```

## Run an example

Each `examples/<style>/run.sh <bundle-dir>` runs a scripted example and writes a bundle. The examples
are named for an implementation style, but none of them imports or runs that framework: each is the
same framework-free scenario, emitting through `agentce_emit` the evidence an agent of that style would
produce, and the framework-specific capture is on the roadmap. `examples/custom-loop` has no framework to
import by design.

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

1. Emit evidence (above) into a bundle, or collect it from source systems with `agentce collect`.
2. `agentce validate --bundle <dir>` — fix quarantines until it is clean.
3. `agentce assess --bundle <dir> --catalog eu-ai-act@2026.09 --profile agentce/applicability.yaml`.
4. Resolve any `insufficient_evidence` with the `agentce-onboard` skill's `explain_insufficient`, then
   re-emit and re-assess.

For the full declare → instrument → validate procedure, use the **agentce-onboard** skill
(`skills/agentce-onboard/`).
