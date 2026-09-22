# @agent-conformance/cli

TypeScript/Node implementation of the **Agent Conformance Engine (AgentCE)** — a deterministic engine that evaluates the evidence an AI-agent deployment produces against executable control catalogs and emits a conformance report.

The command-line tool implements `conformance run` (the conformance suite), `assess`, `validate`, `report`, `quickstart`, and `--version`. `report --validate` (schema validation of the report artifacts) is not yet ported; use the Python engine for that one check.

- Repository: https://github.com/agent-conformance/agentce
- Website: https://agent-conformance.org

This is a pre-1.0 release: interfaces may change between minor versions. Conformance to a catalog is not a legal compliance determination.
