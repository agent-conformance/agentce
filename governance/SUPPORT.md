# Support and deprecation

How long each artifact is supported and how a deprecation is announced (SPEC §14.2). Predictable
windows let an adopter plan an upgrade rather than discover one.

## Support windows

| Artifact | Supported for |
|---|---|
| Catalog version | 18 months after its successor publishes: its fixtures are maintained and the Engine Conformance Suite runs it in CI. |
| Engine major version | Overlaps with the next major version for 12 months, so an adopter has a full year to migrate. |

A conforming-implementation listing lapses when the catalog version its report names leaves support
(SPEC §14.5 CP-1).

## Deprecation

- A deprecation is announced **one minor version ahead** in the changelog.
- The CLI prints the deprecation on **every run** while the deprecated artifact is still in use, so a
  pipeline surfaces it without anyone reading release notes.
- Nothing is removed inside a support window; removal happens only in the major version that follows
  the announced window.

## Getting help

- Bugs, questions, and feature requests: open an issue in the repository.
- A suspected security issue: do **not** open a public issue — follow the
  [security process](SECURITY-PROCESS.md).
- A conformance claim or a report for the registry: see the [conformance program](CONFORMANCE-PROGRAM.md).
