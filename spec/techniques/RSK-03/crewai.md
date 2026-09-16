# RSK-03 — crewai

**Control:** Residual-risk indicators are measured within thresholds

To satisfy RSK-03 in a crewai deployment, instrument the agent so it records the authorisation gate each consequential decision cleared and the risk-review activity that covered it. Implement this with a CrewAI event listener that exports each task step through the `otel-genai` adapter; the adapter maps the framework's telemetry to the AgentCE evidence schema (SPEC §12) and the engine reads it as the control's minimum evidence (SPEC §7.6). This technique is informative and never changes an outcome (DC-10).
