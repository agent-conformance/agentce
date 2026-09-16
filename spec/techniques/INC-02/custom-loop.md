# INC-02 — custom-loop

**Control:** Serious incidents are recorded with an accountable actor (by role)

To satisfy INC-02 in a custom-loop deployment, instrument the agent so it emits `Incident` events with the role clocks and links every adverse `Outcome` to its decision. Implement this with the `agentce-emit` SDK (`AGENTCE_EMIT=1`) called at each decision and tool call; the adapter maps the framework's telemetry to the AgentCE evidence schema (SPEC §12) and the engine reads it as the control's minimum evidence (SPEC §7.6). This technique is informative and never changes an outcome (DC-10).
