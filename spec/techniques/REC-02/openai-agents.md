# REC-02 — openai-agents

**Control:** Every consequential decision is timestamped

To satisfy REC-02 in a openai-agents deployment, instrument the agent so it records the acting agent, timestamp, and inputs of every consequential decision. Implement this with the OpenAI Agents SDK tracing processor, mapped by the `otel-genai` adapter; the adapter maps the framework's telemetry to the AgentCE evidence schema (SPEC §12) and the engine reads it as the control's minimum evidence (SPEC §7.6). This technique is informative and never changes an outcome (DC-10).
