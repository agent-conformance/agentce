# 0001 — Graph store for the reference engine

Status: accepted
Spec refs: SPEC §5.4, §8.1, §8.6; ADR-0002

## Context

The engine ingests evidence events, materialises a provenance graph (the glue relations of SPEC §6.3
plus engine-materialised edges), and evaluates Portable Shape Profile shapes over it at rung 2. It runs
as a batch job with no server (SPEC §5.4) and must meet the performance targets of SPEC §8.6 — 50M
events, 10k subjects, a 90-day window, ≤ 60 min on 16 cores with an on-disk store — while remaining
deterministic (SPEC §8.2). The store must be queryable so that shapes compile to query plans (ADR-0002)
rather than being interpreted over an in-memory object graph.

## Decision

The reference (Python) engine stores the graph in an embedded, on-disk **SQLite** database. Events and
the materialised edges are written to indexed relational tables (nodes, typed edges, literals, and the
class-hierarchy closure), and PSP shapes are compiled to SQL over those tables. The store is opened
read-mostly, written once during ingest, and queried during evaluation; all ordering in queries is
explicit (`ORDER BY`) so results do not depend on physical row order.

## Alternatives considered (with why not)

- **In-memory rdflib graph.** Does not scale to 50M events in 32 GB, and triple iteration order is not
  guaranteed, which threatens determinism. Retained only for small cross-checks and the ECS.
- **A triplestore or graph-database server** (e.g. a SPARQL endpoint or an embedded property graph).
  Contradicts the no-server batch model (SPEC §5.4), adds an operational dependency, and complicates the
  offline guarantee.
- **A bespoke on-disk index.** Reinvents transactions, indexing, and portability that SQLite already
  provides with a stable file format available in every target language.

## Consequences (including determinism, portability to TypeScript/Java, performance)

- **Determinism.** A single-writer file with explicit query ordering yields identical results across
  runs and platforms; the file format is stable and byte-defined.
- **Portability.** The TypeScript engine uses the same SQLite engine through a native binding and the
  Java engine through a JDBC driver (ADR-0008); the schema and queries are the shared artifact, so the
  three engines evaluate identical plans.
- **Performance.** Indexed joins over materialised edges meet SPEC §8.6; the store is on disk, so memory
  stays bounded. SHACL-library evaluation is reserved for graphs ≤ 10k triples (ADR-0002).

## Verification (the test or check that proves the decision holds)

The determinism CI job runs the engine on two runners of different CPU architectures and requires
byte-identical `assertions.json` (item 1.11); the performance harness (item 3.7) runs the 50M-event
scale test against the targets; the ECS cross-checks compiled-query results against a SHACL library on
the hierarchy-depth fixtures.
