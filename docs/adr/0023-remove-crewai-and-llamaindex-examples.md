# 0023 — Remove the crewai and llamaindex runnable examples

Status: accepted
Spec refs: SPEC §13.4 AX-3, AX-4

## Context

`examples/crewai` and `examples/llamaindex` each resolved a dependency with open security advisories and
no published fix: `chromadb` (every release, four advisories; ADR 0018) and `nltk` (every release, one
advisory; ADR 0021). Keeping them meant allow-listing those advisories repository-wide in
`.github/workflows/dependency-review.yml`.

## Decision

1. Delete `examples/crewai` and `examples/llamaindex`, and remove the advisory allow-list from the
   dependency review workflow, so the repository carries no accepted advisory.
2. Remove the two lockfile paths from the framework-example exemption in `tools/no_ml_check.py` and
   `tools/framework_examples_check.py`, and update the example counts in the checks, the claims register
   and the docs.
3. The frameworks are not run in this repository. Covering CrewAI and LlamaIndex through OpenInference
   captures is planned (phase 20, item 20.1); until a build gate is green on such captures, no page
   claims support for either.

## Alternatives considered (with why not)

- **Keep the advisory exceptions.** Rejected: the exceptions cannot expire until upstream ships a fix.
- **Pin older frameworks.** Rejected: every release of both dependencies is in the vulnerable range.

## Consequences (including determinism, portability to TypeScript/Java, performance)

- Eight framework examples and eleven runnable examples remain. No engine, adapter or output changes;
  the engines are unaffected.

## Verification (the test or check that proves the decision holds)

- `tools/framework_examples_check.py` and `tools/examples_check.py` pass with the new counts, and no
  manifest or lockfile in the repository names `chromadb` or `nltk`.
