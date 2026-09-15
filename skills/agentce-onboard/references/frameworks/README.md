# Framework instrumentation notes

Per-style notes for enabling OpenTelemetry GenAI instrumentation and the AgentCE emitter, capped to the
six corpus styles (SPEC §13.3.2). Each note says where the model, tool, and agent spans come from and
where to add `agentce-emit` for the events a framework does not surface. They are guidance for Phase B
(`references/instrument.md`), not code the skill runs.

- [langgraph](langgraph.md)
- [openai-agents](openai-agents.md)
- [claude-agent-sdk](claude-agent-sdk.md)
- [google-adk](google-adk.md)
- [crewai](crewai.md)
- [custom-loop](custom-loop.md)

Where no note exists for a framework, use the generic guidance in `references/instrument.md`: enable the
OpenTelemetry GenAI instrumentation for the model SDK, propagate W3C Trace Context, and add the emitter
at the chokepoints `find_chokepoints` reports.
