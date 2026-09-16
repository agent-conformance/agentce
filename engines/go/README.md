# agentce-go — DEFERRED

Status: **DEFERRED**. A Go engine is named in the multi-language strategy (SPEC §5.3, "engines/go
(later)") but is not built in this cycle, and nothing in the repository depends on it. This file
records the decision so the deferral is explicit rather than an omission.

## Why deferred

- **The cross-language contract is already demonstrated.** Determinism across implementations is
  proven by three conforming engines — Python (reference), TypeScript, and Java — whose outputs are
  byte-identical over the full corpus after RFC 8785 canonicalisation (SPEC §5.3, §11.5). A fourth
  engine adds maintenance surface without adding a new conformance signal at this stage.
- **The specification schedules Go for later.** SPEC §5.3 lists the Go engine as a later engine, and
  the typed Go bindings generated from the evidence model (SPEC §5.3) already give a Go adopter the
  data types without a full engine.
- **Read-only, model-free parity is the bar.** A Go engine would have to implement the Engine
  Conformance Suite path and pass the same byte-identity gate as the other engines; that work is
  scheduled against real Go-adopter demand rather than done speculatively.

## What deferral means

- No Go sources, module, or build live here; the Engine Conformance Suite and CI do not include a Go
  engine, and no goal or gate depends on one.
- The module identity is reserved by the specification's identifier conventions
  (`github.com/agent-conformance/agentce`, binary `agentce`), so a future Go engine has a fixed home.

## Picking it up

When a Go engine is built, it implements SPEC §5.3 (identical results) and §11.5 (Engine Conformance
Suite), joins the multi-engine byte-identity job, and publishes an implementation report — the same
acceptance every other engine meets. Until then, this deferral stands.
