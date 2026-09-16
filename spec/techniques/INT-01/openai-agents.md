# INT-01 — openai-agents

**Control:** Consequential tool calls are captured by an enforcement point

To satisfy INT-01 in a openai-agents deployment, instrument the agent so it declares the enforcement-point source class in the bundle manifest and exports each stream under a hash chain. Implement this with the OpenAI Agents SDK tracing processor, mapped by the `otel-genai` adapter; the adapter maps the framework's telemetry to the AgentCE evidence schema (SPEC §12) and the engine reads it as the control's minimum evidence (SPEC §7.6). This technique is informative and never changes an outcome (DC-10).
