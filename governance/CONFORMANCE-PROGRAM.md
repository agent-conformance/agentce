# Conformance program

What a conformance claim is, how an implementation is listed, and how a fork relates to the project
(SPEC §14.5). A conformance claim is a checkable statement backed by a verified report, not a
self-assertion. The code is Apache-2.0 and the design is public; the program governs the things a
derivative cannot copy by editing files — the registry of verified reports, the canonical namespaces,
the signed yardstick, and neutral governance.

## What a conformance claim is (CP-1)

An implementation is listed as a **conforming engine or adapter** only when all of the following hold:

- its implementation report (SPEC §11.5) shows `claim: full` and `no_ml: pass`;
- the report was produced by the official Engine Conformance Suite runner at a tagged release;
- it was assessed against a **signed** golden revision; and
- it is accepted into [`conformance/reports/`](../conformance/reports/) by pull request through the
  `registry_check` workflow.

The report names the corpus revision, the catalog version, and the engine package digest. **The
listing is the only form of conformance claim the project recognises**, and every consumer-facing
document points readers to it. A listing lapses when the catalog version its report names leaves
support (see [SUPPORT.md](SUPPORT.md)).

## The implementation-report registry

- Reports live in `conformance/reports/` and are added by pull request.
- `registry_check` accepts a report only if the ECS runner release, the golden revision, and the
  engine package digest it names verify against their signatures. A report that does not verify is
  `claim: unverified` and is **not listed**.
- `registry_check --self-test` proves the check itself: it accepts a well-formed report and refuses one
  whose golden digest does not verify.

## Namespaces (CP-2)

The vocabulary IRIs, the JSON-LD context, the CloudEvents `type` prefix, the `agentce*` extension
attributes, and the `AGENTCE_*` variables denote the semantics of this specification. A fork that
changes rule semantics, the outcome vocabulary, the trust classes, the canonical form, or the numerics
**must not** emit or consume these namespaces; it must use its own, so no consumer mistakes its outputs
for those of a listed implementation. A redistribution that keeps the semantics — repackaging,
translations, org overlays, private catalogs — keeps the namespaces.

## Attribution (CP-3)

The [`NOTICE`](../NOTICE) file names the project and the authors of the catalogs, corpus, and
techniques; Apache-2.0 §4(d) obliges every derivative to preserve it. Catalog files carry SPDX headers
and a `provenance` block (`source`, `version`, `digest`); `agentce catalog lint --require-provenance`
recomputes the digest over the catalog's rules and enforces the block (SPEC §14.5 CP-3), so a rebranded
catalog either shows its origin or fails validation.

## Yardstick integrity (CP-4)

The corpus, golden, and probe datasets are signed by the release identity. Implementation reports
verify those signatures; a report assessed against an unsigned or re-signed dataset is
`claim: unverified` and is not listed.

## Neutral home (CP-5)

Once two engines are listed, the glue vocabulary, the report and claim formats, and the Engine
Conformance Suite are offered to an open standards or foundation home. Neutral, multi-party governance
is the property a single-vendor derivative cannot match.

## Extension points, not forks (CP-6)

Every adapter, evaluator, catalog family, output format, and skill script sits behind a documented,
versioned interface, and the loophole review (SPEC §14.2) records every plausible "simple extension"
with a closure — so the cheapest way to add a capability is a pull request to this repository rather
than a divergent fork.

## Licensing

Apache-2.0 for everything executable, catalogs included, so enterprises and vendors embed and extend
with no obligation beyond attribution; CC-BY-4.0 for the specification, documentation, and generated
datasets. There is no CLA at launch — DCO sign-off only.
