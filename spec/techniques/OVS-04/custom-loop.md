# OVS-04 — custom-loop

**Control:** Consequential tool calls run over a verified delegation chain

To satisfy OVS-04 in a custom-loop deployment, instrument the agent so it routes consequential tool calls through an enforcement-point gateway that emits `PolicyDecision` and `ApprovalDecided`, and declares the oversight modality per decision type. Implement this with the `agentce-emit` SDK (`AGENTCE_EMIT=1`) called at each decision and tool call; the adapter maps the framework's telemetry to the AgentCE evidence schema (SPEC §12) and the engine reads it as the control's minimum evidence (SPEC §7.6). This technique is informative and never changes an outcome (DC-10).
