# google-adk (Google Agent Development Kit)

- **Model spans**: instrument `generate_content` → `chat` spans with `gen_ai.*`.
- **Tool spans**: ADK `FunctionTool` dispatch → `execute_tool`.
- **Authorization**: where OPA (or another policy engine) is evaluated server-side, take its decision
  log via the `policy-engines` adapter as `enforcement_point`; do not re-emit it agent-side.
- **Emitter**: add `agentce-emit` for `Decision` at the classifier/recommender.
