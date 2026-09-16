# DAT-03 — openai-agents

**Control:** Outcome parity across declared groups is within thresholds

To satisfy DAT-03 in a openai-agents deployment, instrument the agent so it records the inputs each consequential decision consumed (a `ModelCall` stream) so data provenance is auditable. Implement this with the OpenAI Agents SDK tracing processor, mapped by the `otel-genai` adapter; the adapter maps the framework's telemetry to the AgentCE evidence schema (SPEC §12) and the engine reads it as the control's minimum evidence (SPEC §7.6). This technique is informative and never changes an outcome (DC-10).
