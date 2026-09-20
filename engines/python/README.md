# agent-conformance

Python implementation of the **Agent Conformance Engine (AgentCE)**: a deterministic, model-free engine that evaluates the evidence an AI-agent deployment produces against executable control catalogs and emits a conformance report.

The distribution provides the `agentce` command and carries the base catalog and a small simulated project, so the first run needs nothing else and works offline:

```
agentce quickstart --out ./out
```

The quickstart assesses the bundled simulated project and writes a human report (`report.md`, `report.html`), machine-readable assertions, and an evidence pack under `./out`. To assess your own agent, `agentce init` writes a starter profile, `agentce doctor` checks it, and `agentce assess` evaluates a bundle of evidence. The documentation is at https://agent-conformance.org.

- Repository: https://github.com/agent-conformance/agentce
- Website: https://agent-conformance.org

This is a pre-1.0 release: interfaces may change between minor versions. Conformance to a catalog is not a legal compliance determination.
