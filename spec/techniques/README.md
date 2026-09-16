# Techniques library (SPEC §7.6)

`<control-id>/<framework>.md` documents at least one implementation pattern per control per supported
framework that is known to produce the control's minimum evidence — or an explicit "not applicable"
note for a manual control, which has no code technique. Techniques are informative: changing a
technique never changes an outcome (DC-10). The instrumentation prompt (§13) is generated from this
library.

Supported frameworks: `langgraph`, `openai-agents`, `claude-agent-sdk`, `google-adk`, `crewai`,
`custom-loop`.

`check_techniques.py` (run `uv run python check_techniques.py`) verifies that every control in the
base catalog, the overlays, and the organisation template carries a technique for every framework;
it prints `TECHNIQUES OK` and exits 0 when the library is complete.
