# 0010 — Public-claims register

Status: accepted
Spec refs: —

## Context

The project makes public claims — on the website, in the documentation, and in the README — about
what the engine does: that it is deterministic, that it uses no learned components, that independent
engines produce byte-identical output, and so on. A claim that outruns what the code can demonstrate
is a defect: it misleads adopters and erodes trust in a standard whose whole value is that its
results can be reproduced. There was no machine-readable link between a public claim and the check
that proves it, so nothing stopped a page from stating a capability the CI never exercised or a
number the tooling never produced.

## Decision

Keep a machine-readable register of the project's public claims at `tools/claims/register.yaml`.
Each claim records its identifier, its text, the pages that state it, a build status of `built` or
`roadmap`, and — for a `built` claim — the CI check that proves it (a workflow file, the job within
it, and a one-line description).

`tools/claims_check.py` enforces the register's integrity and runs in CI on every push and pull
request (`.github/workflows/claims.yml`):

- a claim marked `built` must name a check whose workflow file exists, defines the cited job, and
  runs on `pull_request` or `push`;
- every page a claim references must exist;
- a `roadmap` claim's text must read as forward-looking — it must carry a qualifier such as
  "planned", "will", or "not yet" — so roadmap material can never be worded as if it had shipped.

A claim becomes `built` only together with a green check; that is the only way a claim widens from
`roadmap` to `built`. The register starts with the claims that are demonstrably true today and grows
to cover every public claim as the work that makes each one true lands.

The checker ships as a small module in the `agentce-tools` package so it can be invoked as
`uv run --project tools --frozen python -m claims_check` (and `--self-test`). It parses YAML with
PyYAML's `safe_load` only, per ADR-0005, and uses no network and no learned component.

## Alternatives considered (with why not)

- **Human inspection only.** Rely on people to catch claims that outrun the code. Rejected: it does
  not scale, it is not reproducible, and it is exactly the gap that lets an unproven claim ship.
- **A single "proof" page instead of a register.** Rejected: the register can be consumed by more
  than one view (for example a proof strip on the site, or a drift check over the quickstart tally),
  and a data file with an enforced schema is auditable in a way a rendered page is not.
- **Re-run every proving check inside the register check.** Rejected: it would duplicate the CI
  suites and make the register check slow and flaky. The register check verifies the *wiring* — that
  each built claim names a real job that runs on pull requests — while the cited jobs prove the
  claims themselves. Keeping the two separate means the register check runs exactly what CI runs and
  can be reproduced locally.
- **Keep the tool standard-library-only (like the other repository checks) and invoke it by file
  path.** Rejected for this checker because it must parse YAML, for which the repository already
  standardises on PyYAML (ADR-0005); taking that dependency lets the checker share the one parser
  rather than reimplement one.

## Consequences (including determinism, portability to TypeScript/Java, performance)

- The register check is deterministic: it reads tracked files, resolves paths, and parses YAML with
  a fixed loader; it makes no network call and depends on no clock, locale, or ordering.
- It is language-neutral. The register and its checker are repository governance, not part of any
  engine; they place no requirement on the TypeScript or Java engines and share no code with them.
- Performance is negligible: the checker parses a handful of small YAML files.
- `agentce-tools` gains a runtime dependency on PyYAML and becomes an installable package exposing
  the `claims_check` module. The other repository checks remain standalone standard-library scripts
  invoked by path; making the package installable does not change how they run.
- The checker's discrimination is proven by a self-test over one good and ten bad fixtures, each
  isolating one rule; the self-test runs in CI, so a change that weakens the checker fails the build.

## Verification (the test or check that proves the decision holds)

- `uv run --project tools --frozen python -m claims_check --self-test` exits 0 and prints
  `CLAIMS SELF-TEST PASSED`: the good fixture passes and each of the ten bad fixtures is caught on
  the exact rule it targets.
- `uv run --project tools --frozen python -m claims_check` validates `tools/claims/register.yaml`
  against the repository and exits non-zero on any violation.
- `.github/workflows/claims.yml` runs both on every push and pull request.
