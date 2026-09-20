# agentce-emit

The evidence emitter for the Agent Conformance Engine (SPEC §13.1, §13.4 AX-3). It lets an AI agent
emit canonical AgentCE evidence — CloudEvents 1.0 envelopes with JSON-LD payloads — with one line:

```python
import agentce_emit

em = agentce_emit.auto()   # active when AGENTCE_EMIT=1; the bundle is flushed at process exit
em.emit_decision(decision_type="dom:CreditDecision", affects_natural_person=True, chosen="approve")
```

`auto()` is a no-op until `AGENTCE_EMIT=1`, so wiring it in never changes behaviour until you switch
it on.
`auto()` only returns the emitter: it does not hook any agent framework, so an agent that calls it and
nothing else emits no events. Every event is an explicit `emit_*` call. Capturing evidence automatically
from LangGraph, the OpenAI Agents SDK, CrewAI, Google ADK, or the Claude Agent SDK is on the roadmap and
is not built. Every event is labelled `self_report` (agent-side emission is a self-report, SPEC §6.4);
content is referenced by SHA-256, never captured (SPEC R12); ids are deterministic; the flushed bundle
(`events/*.jsonl` + `manifest.json`) validates with zero quarantines. Standard library only — no
runtime dependencies, no network, no learned component.

See `docs/integrate.md` and the runnable `examples/` for the full set of emit calls.
