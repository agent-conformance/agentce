# DOC-03 — claude-agent-sdk

**Control:** Declared egress paths match the observed enforcement point

To satisfy DOC-03 in a claude-agent-sdk deployment, instrument the agent so it declares the operating components in a `BundleLoaded` snapshot and keeps the declared documentation current. Implement this with a Claude Agent SDK hook that emits each step through the `otel-genai` adapter; the adapter maps the framework's telemetry to the AgentCE evidence schema (SPEC §12) and the engine reads it as the control's minimum evidence (SPEC §7.6). This technique is informative and never changes an outcome (DC-10).
