# custom-loop (hand-rolled agent loop)

- **Model spans**: add the model SDK's OpenTelemetry instrumentation (or emit `ModelCall` directly via
  `agentce-emit`) around each provider call.
- **Tool spans**: emit `ToolCall` at your dispatch function; route through the gateway when one is
  configured and record its request id.
- **Credentials**: obtain tool credentials from the secret manager, never inline; the acquisition is a
  credential chokepoint.
- **Approval**: human-in-the-loop prompts are `self_report` unless the approval is recorded by an
  independent system; take those via the `oversight` adapter.
- **Emitter**: `agentce-emit` for `SessionStart/End`, `Decision`, and (Conduct) `Instruction`/`Refusal`.
