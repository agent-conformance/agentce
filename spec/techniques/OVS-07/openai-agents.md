# OVS-07 — openai-agents

**Control:** Overrides and interrupts are effective and recorded

To satisfy OVS-07 in a openai-agents deployment, instrument the agent so it routes consequential tool calls through an enforcement-point gateway that emits `PolicyDecision` and `ApprovalDecided`, and declares the oversight modality per decision type. Implement this with the OpenAI Agents SDK tracing processor, mapped by the `otel-genai` adapter; the adapter maps the framework's telemetry to the AgentCE evidence schema (SPEC §12) and the engine reads it as the control's minimum evidence (SPEC §7.6). This technique is informative and never changes an outcome (DC-10).
