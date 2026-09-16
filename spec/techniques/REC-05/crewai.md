# REC-05 — crewai

**Control:** Every consequential decision records the authority it acted under

To satisfy REC-05 in a crewai deployment, instrument the agent so it records the acting agent, timestamp, and inputs of every consequential decision. Implement this with a CrewAI event listener that exports each task step through the `otel-genai` adapter; the adapter maps the framework's telemetry to the AgentCE evidence schema (SPEC §12) and the engine reads it as the control's minimum evidence (SPEC §7.6). This technique is informative and never changes an outcome (DC-10).
