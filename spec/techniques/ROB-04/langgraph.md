# ROB-04 — langgraph

**Control:** Consequential tool calls run over a verified delegation chain

To satisfy ROB-04 in a langgraph deployment, instrument the agent so it routes consequential actions through the enforcement point, propagates a verified delegation chain, and keeps untrusted content out of the decision path. Implement this with a LangGraph node/callback that exports each step through the `otel-genai` adapter; the adapter maps the framework's telemetry to the AgentCE evidence schema (SPEC §12) and the engine reads it as the control's minimum evidence (SPEC §7.6). This technique is informative and never changes an outcome (DC-10).
