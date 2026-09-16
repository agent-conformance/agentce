# AgentCE documentation

The Agent Conformance Engine (AgentCE) is a deterministic, read-only, model-free engine that
evaluates the evidence an AI-agent deployment produces against executable control catalogs and emits
a conformance report. This is the pre-general-availability documentation; the published site follows
at general availability.

## Start here

- [Quickstart](quickstart.md) — run the vendored project end to end with no configuration.
- [Integrate a source](integrate.md) — emit or export the evidence the engine reads.
- [Living report example](example-report.md) — the report the quickstart project renders.

## Reference

Generated from the sources, so the documentation and the code never disagree.

- [CLI commands](reference/commands/index.md) — one page per command.
- [Source adapters](reference/adapters/index.md) — one page per adapter.
- [Control families](reference/catalog/index.md) — one page per family of the EU AI Act base catalog.

## Trust and operations

- [Verification](verification.md) — the cryptographic primitives, trust roots, and how to verify a
  signed catalog, corpus, or release offline.
- [Threat model](threat-model.md) — the threats the engine addresses and the check that proves each.
- [Error catalogue](errors.md) — every message key with its cause and exact fix.
- [Architecture decisions](adr/) — the record of every choice the specification leaves open.

## Building the documentation

`cd docs && python build.py` regenerates the reference pages and the living report example from the
sources. `python build.py --check-links` verifies the generated pages are current and that every
internal link resolves; it opens no socket.
