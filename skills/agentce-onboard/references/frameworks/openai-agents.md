# openai-agents (OpenAI Agents SDK)

- **Model spans**: the OpenAI instrumentation on `client.chat.completions.create` /
  `client.responses.create` → `chat` spans with `gen_ai.*`.
- **Tool spans**: `@function_tool` dispatch → `execute_tool`; when tools are proxied through the MCP
  gateway, prefer the gateway's `enforcement_point` record and correlate by request id.
- **Sessions**: `Runner.run` is the `invoke_agent` span.
- **Emitter**: add `agentce-emit` for `Decision` on the agent transfer that produces a consequential output.
