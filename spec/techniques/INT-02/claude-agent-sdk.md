# INT-02 — claude-agent-sdk

**Control:** Delegation chains for consequential actions are verified

To satisfy INT-02 in a claude-agent-sdk deployment, instrument the agent so it declares the enforcement-point source class in the bundle manifest and exports each stream under a hash chain. Implement this with a Claude Agent SDK hook that emits each step through the `otel-genai` adapter; the adapter maps the framework's telemetry to the AgentCE evidence schema (SPEC §12) and the engine reads it as the control's minimum evidence (SPEC §7.6). This technique is informative and never changes an outcome (DC-10).
