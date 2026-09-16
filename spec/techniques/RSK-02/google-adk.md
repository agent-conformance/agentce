# RSK-02 — google-adk

**Control:** Consequential decisions are subject to a risk-review activity

To satisfy RSK-02 in a google-adk deployment, instrument the agent so it records the authorisation gate each consequential decision cleared and the risk-review activity that covered it. Implement this with a Google ADK callback that exports each step through the `otel-genai` adapter; the adapter maps the framework's telemetry to the AgentCE evidence schema (SPEC §12) and the engine reads it as the control's minimum evidence (SPEC §7.6). This technique is informative and never changes an outcome (DC-10).
