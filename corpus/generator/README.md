# Corpus generator

Implements **SPEC §11.2–11.4** (corpus structure, project anatomy and generator, realism
requirements). A seeded, deterministic program that emits the synthetic projects the engine and the
conformance suite run against. The generated output is never committed (SPEC §11.7); only this
generator is.

## Entrypoints

Two ways to run the same self-contained [`generate.py`](generate.py):

```
# The package entrypoint the phase-1 eval calls:
uv run --project corpus python -m corpus.generator --out <dir>

# The standalone script (this directory):
uv run python generate.py --set v1 --out <dir>
```

Both write `<dir>/projects/<domain>/<style>/<variant>/…` for every project plus a top-level
`<dir>/corpus-manifest.json` of the shape `{"projects":[{"id":…,"events":<int>}, …], …}` whose digest
`corpus/VERSIONS.md` pins (SPEC §11.7).

## Phase-1 set (`v1`)

The **credit decisioning** domain × six implementation styles (LangGraph, OpenAI Agents SDK, Claude
Agent SDK, Google ADK, CrewAI, a custom loop) × five variants:

| Variant | Seeds | Dominant outcome |
|---|---|---|
| `known-pass` | full enforcement-point evidence | conformant across REC/OVS/INT/INC |
| `known-fail` | missing actor, unverified delegation not reaching a human, mismatched oversight, self-reported consequential tool call, incident without actor | non-conformant with precise violations |
| `insufficient-evidence` | only self-reported streams for the consequential tool call | insufficient_evidence on the oversight control |
| `tampered` | one integrity stream edited after hashing | integrity failure detected; structural verdicts unchanged |
| `coverage-gap` | an independent system of record (reference ledger) saw more tool calls than were captured | coverage below threshold |

The style changes a bundle's surface — source systems, convention versions, agent identity — but never
the control logic, so the engine reaches the same verdict for a variant whatever the style. One project
(`credit/custom-loop/known-pass`) carries ≥ 200k benign background events to meet the realism/volume
floor (SPEC §11.4); the volume is background traffic on other agents, so the assessed subject's
verdicts are unchanged.

Each event is a CloudEvents-shaped envelope with a JSON-LD payload (SPEC §6.2); integrity hashes use
the engine's RFC 8785 canonical form (SPEC §6.7), the only thing imported from the engine.
