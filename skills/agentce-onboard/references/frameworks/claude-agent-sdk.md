# claude-agent-sdk (Claude Agent SDK)

- **Model spans**: instrument `client.messages.create` → `chat` spans with `gen_ai.*`.
- **Tool spans**: `@tool` / `execute_tool` → `execute_tool`; MCP servers are tool calls; prefer the
  gateway's record where one sits in front.
- **Sessions**: the `ClaudeSDKClient` run is the `invoke_agent` span; propagate trace context across
  async continuations.
- **Emitter**: add `agentce-emit` for `Decision`, and `Notice`/`Disclosure` on user-facing outputs.
