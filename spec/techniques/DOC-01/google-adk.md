# DOC-01 — google-adk

**Control:** Operating components match the declared documentation

To satisfy DOC-01 in a google-adk deployment, instrument the agent so it declares the operating components in a `BundleLoaded` snapshot and keeps the declared documentation current. Implement this with a Google ADK callback that exports each step through the `otel-genai` adapter; the adapter maps the framework's telemetry to the AgentCE evidence schema (SPEC §12) and the engine reads it as the control's minimum evidence (SPEC §7.6). This technique is informative and never changes an outcome (DC-10).
