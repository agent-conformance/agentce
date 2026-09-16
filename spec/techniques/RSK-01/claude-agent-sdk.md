# RSK-01 — claude-agent-sdk

**Control:** Consequential decisions record the authorisation gate they cleared

To satisfy RSK-01 in a claude-agent-sdk deployment, instrument the agent so it records the authorisation gate each consequential decision cleared and the risk-review activity that covered it. Implement this with a Claude Agent SDK hook that emits each step through the `otel-genai` adapter; the adapter maps the framework's telemetry to the AgentCE evidence schema (SPEC §12) and the engine reads it as the control's minimum evidence (SPEC §7.6). This technique is informative and never changes an outcome (DC-10).
