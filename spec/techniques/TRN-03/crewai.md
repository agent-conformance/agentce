# TRN-03 — crewai

**Control:** Affected persons informed and explanation reconstructable

To satisfy TRN-03 in a crewai deployment, instrument the agent so it emits a `Notice` to affected persons and records the inputs behind each consequential decision so an explanation can be reconstructed. Implement this with a CrewAI event listener that exports each task step through the `otel-genai` adapter; the adapter maps the framework's telemetry to the AgentCE evidence schema (SPEC §12) and the engine reads it as the control's minimum evidence (SPEC §7.6). This technique is informative and never changes an outcome (DC-10).
