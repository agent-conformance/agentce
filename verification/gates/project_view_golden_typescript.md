# AgentCE project view

## Agents in this project

| Agent | Declared | Verdict | What it did | Deviations |
|---|---|---|---|---|
| [spiffe://corp/agents/project-view-fixture-a](agents/spiffe___corp_agents_project-view-fixtur-8b604750/report.md) | Declared | Incomplete — no control failed, but not every applicable control is demonstrated. (insufficient evidence 1) | agents: spiffe://corp/agents/project-view-fixture-a; actions: read 1 | none |
| [spiffe://corp/agents/project-view-fixture-b](agents/spiffe___corp_agents_project-view-fixtur-01209673/report.md) | Declared | Incomplete — no control failed, but not every applicable control is demonstrated. (insufficient evidence 1) | agents: spiffe://corp/agents/project-view-fixture-delegate; actions: 0 | none |

## Top gaps across agents

- `ModelCall (self_report)`: unlocks 1 check(s), needed by 1 more; rung 1 -- a code change for the agent team (see the agentce-get-evidence skill). Adapters that can supply this: otel-genai. Agents: spiffe://corp/agents/project-view-fixture-a, spiffe://corp/agents/project-view-fixture-b.
- `ToolCall (self_report)`: unlocks 0 check(s), needed by 1 more; rung 1 -- a code change for the agent team (see the agentce-get-evidence skill). Adapters that can supply this: mcp-gateway, otel-genai. Agents: spiffe://corp/agents/project-view-fixture-b.

## Undeclared agents

- `spiffe://corp/agents/project-view-fixture-delegate` -- [Full report](agents/spiffe___corp_agents_project-view-fixtur-eadc37a4/report.md)
