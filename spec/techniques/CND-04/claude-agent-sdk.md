# CND-04 — claude-agent-sdk

**Control:** Consequential tool calls act on a recorded instruction

To satisfy CND-04 in a claude-agent-sdk deployment, instrument the agent so it binds task effect scopes in the domain binding so the gateway emits within-scope and within-budget determinations, `Instruction` provenance, and `Refusal` events. Implement this with a Claude Agent SDK hook that emits each step through the `otel-genai` adapter; the adapter maps the framework's telemetry to the AgentCE evidence schema (SPEC §12) and the engine reads it as the control's minimum evidence (SPEC §7.6). This technique is informative and never changes an outcome (DC-10).
