# CND-03 — custom-loop

**Control:** Harm signals are triaged into an incident or recorded decision

To satisfy CND-03 in a custom-loop deployment, instrument the agent so it binds task effect scopes in the domain binding so the gateway emits within-scope and within-budget determinations, `Instruction` provenance, and `Refusal` events. Implement this with the `agentce-emit` SDK (`AGENTCE_EMIT=1`) called at each decision and tool call; the adapter maps the framework's telemetry to the AgentCE evidence schema (SPEC §12) and the engine reads it as the control's minimum evidence (SPEC §7.6). This technique is informative and never changes an outcome (DC-10).
