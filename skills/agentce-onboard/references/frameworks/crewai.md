# crewai

- **Model spans**: instrument the underlying model client → `chat` spans with `gen_ai.*`.
- **Tool spans**: `@tool`-decorated tools → `execute_tool`.
- **Memory/retrieval**: CrewAI memory and RAG reads/writes → `MemoryRead`/`MemoryWrite` and
  `ResourceAccess`.
- **Sessions**: a `Crew.kickoff` is the `invoke_agent`/`invoke_workflow` span.
- **Emitter**: add `agentce-emit` for `Decision` on the task that yields a consequential output.
