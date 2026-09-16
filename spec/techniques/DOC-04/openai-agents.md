# DOC-04 — openai-agents

**Control:** Configuration drift from the declared snapshot is bounded

To satisfy DOC-04 in a openai-agents deployment, instrument the agent so it declares the operating components in a `BundleLoaded` snapshot and keeps the declared documentation current. Implement this with the OpenAI Agents SDK tracing processor, mapped by the `otel-genai` adapter; the adapter maps the framework's telemetry to the AgentCE evidence schema (SPEC §12) and the engine reads it as the control's minimum evidence (SPEC §7.6). This technique is informative and never changes an outcome (DC-10).
