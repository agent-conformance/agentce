# 0022 — Pause the full-page accessibility scan in CI; verify manually, downstream

Status: accepted
Spec refs: —

## Context

`website/scripts/check-a11y.mjs` runs axe-core, in both the light and dark themes, over every built
page of the site. As the site's page count grew — the generated per-control reference pages, the
translated locale trees, and the per-page doc-test surface all multiply the built page count — the
full scan's wall-clock cost grew with it, to the point of dominating the website workflow's total CI
time. That cost is now large enough to slow every pull request that touches the site, whether or not
it touches anything accessibility-related.

`check-a11y.mjs` already draws a line between two things: a fast **self-test** (a small, fixed set of
good and deliberately-bad fixture pages that proves the gate still fires on a real violation) and the
**full scan** (every built page, both themes). Only the full scan carries the wall-clock cost; the
self-test is cheap and does not grow with the site.

## Decision

1. The full-page scan is paused in continuous integration (`AGENTCE_SKIP_A11Y=1` on the website
   workflow's `a11y` job): `check-a11y.mjs` prints a skipped notice and exits 0 for the full scan under
   that variable. The self-test is never skipped by this variable and still runs on every change,
   so the gate's own teeth — its ability to fail on a real violation — stay proven in CI even while the
   full scan does not run there.
2. Accessibility conformance is verified manually, downstream of this repository's automation, per the
   [accessibility statement](../../website/src/content/docs/docs/accessibility-statement.md), the
   [VPAT](../../website/src/content/docs/docs/vpat.md), and the
   [manual test protocol](../../website/src/content/docs/docs/accessibility-testing-protocol.md). Those
   three pages, and the `a11y.statement-vpat` claims-register entry, say so in their own words rather
   than claiming an automated per-page scan runs in CI that does not.
3. This is reversible: unsetting `AGENTCE_SKIP_A11Y` (or setting it to `"0"`) resumes the full scan in
   CI with no code change. The machinery, its fixtures, and its self-test stay in the tree, present and
   honest, rather than removed.
4. No accessibility check anywhere is deleted, no axe rule is disabled, and no WCAG tag is narrowed —
   only the full-site sweep's CI cadence changes, from every pull request to a manual, downstream cadence.

## Alternatives considered (with why not)

- **Shard or parallelize the full scan across jobs.** Reduces wall-clock but not total cost, and adds
  ongoing maintenance (shard count tracking page-count growth) for a problem the manual/downstream
  cadence already solves without new machinery.
- **Sample a fixed subset of pages in CI.** Rejected for now: choosing a subset that fairly represents
  the whole site, and being confident that subset's cost is worth paying on every pull request, is a
  scope decision about how much CI coverage is enough — better made deliberately than defaulted into
  silently by whichever pages happen to be picked.
- **Leave the full scan wired into CI and accept the slower workflow.** Rejected: a check that makes
  every pull request slower, including ones that never touch accessibility-relevant code, invites
  exactly the kind of pressure that leads to a gate being weakened under time pressure rather than
  paused deliberately and documented.
- **Delete the full-scan machinery entirely.** Rejected: the scanner, its fixtures, and its self-test
  are real, working infrastructure; removing them would make resuming automated coverage a rebuild
  instead of a one-line revert.

## Consequences (determinism, portability, performance)

- No effect on determinism or portability: `check-a11y.mjs`'s behaviour for a given `AGENTCE_SKIP_A11Y`
  value is unchanged; the variable's own value is the only new input, and it is fixed in the workflow
  file, not derived from the environment at run time.
- CI time for the website workflow drops by the full scan's wall-clock cost on every pull request.
- Every public claim about automated accessibility coverage running "in CI" or "on every change" is
  worded to match this: the self-test does, the full-site sweep currently does not, and conformance is
  a manual, downstream responsibility until the sweep resumes or the manual protocol completes.
- Revisit when either resuming the full scan's CI cost becomes acceptable again, or a specific sampled
  page set is deliberately chosen and documented as an interim, cheaper CI signal.
