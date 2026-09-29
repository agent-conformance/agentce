# langgraph

- **Model spans** come from the LangChain OpenTelemetry instrumentation on the chat model node; enable
  it so `chat` spans carry `gen_ai.*` at the pinned convention.
- **Tool spans**: LangGraph `ToolNode` / tool functions — wrap dispatch so `execute_tool` spans exist;
  route through the gateway where one is configured and record its request id.
- **Sessions**: the graph invocation is the `invoke_agent` span; carry the task id as `agentcetask`.
- **Memory**: `MemorySaver`/checkpointer reads and writes → `MemoryRead`/`MemoryWrite`.
- **Emitter**: add `agentce-emit` for `Decision` at consequential nodes and `Instruction`/`Refusal`
  when the Conduct overlay is targeted; agent-side emission is `self_report`.
