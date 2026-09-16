# ROB-01 — openai-agents

**Control:** Consequential actions traverse a trusted, corroborated source

To satisfy ROB-01 in a openai-agents deployment, instrument the agent so it routes consequential actions through the enforcement point, propagates a verified delegation chain, and keeps untrusted content out of the decision path. Implement this with the OpenAI Agents SDK tracing processor, mapped by the `otel-genai` adapter; the adapter maps the framework's telemetry to the AgentCE evidence schema (SPEC §12) and the engine reads it as the control's minimum evidence (SPEC §7.6). This technique is informative and never changes an outcome (DC-10).
