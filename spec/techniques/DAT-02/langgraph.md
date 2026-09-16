# DAT-02 — langgraph

**Control:** Input data quality and representativeness are measured

To satisfy DAT-02 in a langgraph deployment, instrument the agent so it records the inputs each consequential decision consumed (a `ModelCall` stream) so data provenance is auditable. Implement this with a LangGraph node/callback that exports each step through the `otel-genai` adapter; the adapter maps the framework's telemetry to the AgentCE evidence schema (SPEC §12) and the engine reads it as the control's minimum evidence (SPEC §7.6). This technique is informative and never changes an outcome (DC-10).
