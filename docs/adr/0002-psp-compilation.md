# 0002 — Compile the Portable Shape Profile to query plans

Status: accepted
Spec refs: SPEC §7.2, §8.3, §8.6; ADR-0001

## Context

Rung-2 outcomes are structural facts: a Portable Shape Profile (PSP) shape either holds over the
materialised graph or it does not (SPEC §7.2). Two requirements pull in the same direction. First, the
result must be identical across independent engines. Second, evaluation must scale to 50M events
(SPEC §8.6). A general-purpose SHACL validator satisfies neither well: implementations differ in edge
cases, and interpreting shapes over the full population is far too slow.

## Decision

Each PSP shape is parsed to a small abstract syntax tree (the profile is a strict, bounded subset — no
SPARQL, no unbounded paths — so the AST is finite and simple) and **compiled to a query plan over the
graph store** (SQL for the reference engine, ADR-0001). A SHACL library is used only to cross-check the
compiled result on small graphs (≤ 10k triples) and inside the Engine Conformance Suite, never on the
full population. `psp_check` (spec/rules) gates every shape into the profile before compilation, so the
compiler only ever sees constructs it supports.

## Alternatives considered (with why not)

- **Interpret shapes with a SHACL library on the full graph** (e.g. pyshacl). Too slow at 50M events,
  and cross-language result divergence on profile-boundary constructs would break byte-identical
  conformance.
- **Hand-write a query per control.** Drifts from the shape that is the normative artifact, is
  unmaintainable across ~50 controls and three engines, and loses the `psp_check` guarantee.
- **A rules engine / Datalog layer.** Adds a dependency and another semantics to reconcile across
  languages for no benefit over compiled queries on the bounded profile.

## Consequences (including determinism, portability to TypeScript/Java, performance)

- **Determinism.** One compiler with a fixed lowering and explicit result ordering; the same shape
  compiles to the same plan every run. Violations map to `{focus_node, path, constraint, message_key}`
  with message text from the control file, never the validator (SPEC §7.2).
- **Portability.** Each engine lowers the same PSP AST to its store's query language (ADR-0008); the AST
  and the mapping are the shared specification, so the three engines agree.
- **Performance.** Indexed joins meet SPEC §8.6; unbounded traversal is absent by construction because
  the profile forbids `*`-paths and relies on engine-materialised edges.

## Verification (the test or check that proves the decision holds)

`psp_check` refuses any out-of-profile shape (eval P0.4). The ECS cross-checks compiled-query results
against a SHACL library on hierarchy-depth fixtures and requires agreement; the two- and three-engine
ECS runs (items 3.6, 5.2) require byte-identical outcomes on the full corpus.
