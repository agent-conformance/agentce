# INC-03 — openai-agents

**Control:** Incident-triggering decisions carry an oversight review

To satisfy INC-03 in a openai-agents deployment, instrument the agent so it emits `Incident` events with the role clocks and links every adverse `Outcome` to its decision. Implement this with the OpenAI Agents SDK tracing processor, mapped by the `otel-genai` adapter; the adapter maps the framework's telemetry to the AgentCE evidence schema (SPEC §12) and the engine reads it as the control's minimum evidence (SPEC §7.6). This technique is informative and never changes an outcome (DC-10).
