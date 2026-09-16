# CND-05 — custom-loop

**Control:** No action is taken on an untrusted instruction

To satisfy CND-05 in a custom-loop deployment, instrument the agent so it binds task effect scopes in the domain binding so the gateway emits within-scope and within-budget determinations, `Instruction` provenance, and `Refusal` events. Implement this with the `agentce-emit` SDK (`AGENTCE_EMIT=1`) called at each decision and tool call; the adapter maps the framework's telemetry to the AgentCE evidence schema (SPEC §12) and the engine reads it as the control's minimum evidence (SPEC §7.6). This technique is informative and never changes an outcome (DC-10).
