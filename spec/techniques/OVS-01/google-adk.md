# OVS-01 — google-adk

**Control:** Every consequential decision is reviewed by an oversight activity

To satisfy OVS-01 in a google-adk deployment, instrument the agent so it routes consequential tool calls through an enforcement-point gateway that emits `PolicyDecision` and `ApprovalDecided`, and declares the oversight modality per decision type. Implement this with a Google ADK callback that exports each step through the `otel-genai` adapter; the adapter maps the framework's telemetry to the AgentCE evidence schema (SPEC §12) and the engine reads it as the control's minimum evidence (SPEC §7.6). This technique is informative and never changes an outcome (DC-10).
