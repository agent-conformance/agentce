# INT-02 — custom-loop

**Control:** Delegation chains for consequential actions are verified

To satisfy INT-02 in a custom-loop deployment, instrument the agent so it declares the enforcement-point source class in the bundle manifest and exports each stream under a hash chain. Implement this with the `agentce-emit` SDK (`AGENTCE_EMIT=1`) called at each decision and tool call; the adapter maps the framework's telemetry to the AgentCE evidence schema (SPEC §12) and the engine reads it as the control's minimum evidence (SPEC §7.6). This technique is informative and never changes an outcome (DC-10).
