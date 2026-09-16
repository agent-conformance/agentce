# TRN-04 — custom-loop

**Control:** Consequential decisions are timestamped for explanation

To satisfy TRN-04 in a custom-loop deployment, instrument the agent so it emits a `Notice` to affected persons and records the inputs behind each consequential decision so an explanation can be reconstructed. Implement this with the `agentce-emit` SDK (`AGENTCE_EMIT=1`) called at each decision and tool call; the adapter maps the framework's telemetry to the AgentCE evidence schema (SPEC §12) and the engine reads it as the control's minimum evidence (SPEC §7.6). This technique is informative and never changes an outcome (DC-10).
