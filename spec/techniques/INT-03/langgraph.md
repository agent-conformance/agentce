# INT-03 — langgraph

**Control:** Consequential tool-call records carry no dangling references

To satisfy INT-03 in a langgraph deployment, instrument the agent so it declares the enforcement-point source class in the bundle manifest and exports each stream under a hash chain. Implement this with a LangGraph node/callback that exports each step through the `otel-genai` adapter; the adapter maps the framework's telemetry to the AgentCE evidence schema (SPEC §12) and the engine reads it as the control's minimum evidence (SPEC §7.6). This technique is informative and never changes an outcome (DC-10).
