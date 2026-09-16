# DAT-03 — custom-loop

**Control:** Outcome parity across declared groups is within thresholds

To satisfy DAT-03 in a custom-loop deployment, instrument the agent so it records the inputs each consequential decision consumed (a `ModelCall` stream) so data provenance is auditable. Implement this with the `agentce-emit` SDK (`AGENTCE_EMIT=1`) called at each decision and tool call; the adapter maps the framework's telemetry to the AgentCE evidence schema (SPEC §12) and the engine reads it as the control's minimum evidence (SPEC §7.6). This technique is informative and never changes an outcome (DC-10).
