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
`auto()` alone hooks nothing: called with no further code, an agent emits no events. Two ways to fill
that in exist today. Write an explicit `emit_*` call at each chokepoint (model call, tool call,
decision) — `examples/langgraph`, `examples/openai-agents`, `examples/crewai`, `examples/google-adk`,
and `examples/claude-agent-sdk` each do exactly this, from a callback or event hook that turns that
real framework's own payloads into `emit_*` calls. Or, for a framework that already produces OTel-shaped
spans, register `agentce_emit.instrument()`'s `AgentCESpanProcessor` with its tracer and let the
existing `otel-genai` mapping turn those spans into events directly, without writing a
chokepoint-by-chokepoint hook yourself. Fully automatic capture from one line, with no per-framework
wiring at all, is on the roadmap and is not built. Every event is labelled `self_report` (agent-side emission is a self-report, SPEC §6.4);
content is referenced by SHA-256, never captured (SPEC R12); ids are deterministic; the flushed bundle
(`events/*.jsonl` + `manifest.json`) validates with zero quarantines. Standard library only — no
runtime dependencies, no network, no learned component.

See `docs/integrate.md` and the runnable `examples/` for the full set of emit calls.
