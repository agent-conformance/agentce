# DOC-03 — custom-loop

**Control:** Declared egress paths match the observed enforcement point

To satisfy DOC-03 in a custom-loop deployment, instrument the agent so it declares the operating components in a `BundleLoaded` snapshot and keeps the declared documentation current. Implement this with the `agentce-emit` SDK (`AGENTCE_EMIT=1`) called at each decision and tool call; the adapter maps the framework's telemetry to the AgentCE evidence schema (SPEC §12) and the engine reads it as the control's minimum evidence (SPEC §7.6). This technique is informative and never changes an outcome (DC-10).
