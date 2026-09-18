# 0012 — CI quality gate: lint, type-check, tests, coverage, and an offline proof

Status: accepted
Spec refs: —

## Context

The repository conventions state that continuous integration lints, type-checks, and tests every
package with per-package coverage floors, and that a dedicated job proves an assessment runs with
networking disabled. None of that was true. Across every workflow, no job ran `ruff`, `mypy`,
`pytest`, `biome`, `tsc`, `pnpm test`, or `./gradlew check`; the workflows only built the pre-GA
distribution packages, validated YAML, and grepped for hygiene. No package declared a coverage floor. The
"offline" job opened no assessment and disabled no networking: it exported dead loopback proxies and
summed the length of a text file, so it would pass whether or not the code could run offline.

The consequence is the failure mode a gate exists to prevent: a real type regression and a
cross-engine output divergence both reached the main branch unseen, because nothing ran the checks
that would have caught them, and the offline guarantee was asserted rather than proven.

## Decision

Add `.github/workflows/tests.yml`, a per-engine quality gate that runs on every push and pull
request, with one job per engine so each language's signal is isolated and can be a required check:

- **python** — `ruff check`, `ruff format --check`, `mypy`, and `pytest` with a coverage floor,
  run through `uv run --frozen`.
- **typescript** — `biome check`, `tsc --noEmit`, and the test suite under Node's built-in test
  runner with native coverage (`--experimental-test-coverage`) and a line-coverage floor.
- **java** — `./gradlew check`, which runs the tests and a JaCoCo coverage verification.

Each package declares a **coverage floor as a ratchet**: it is set at the value the suite measures
today and may only rise toward the `>=90%` target, never fall. The Python suite measures ~91%, so
its floor is 90. The Java engine measures below the target today, so its floor is set at the measured
value and is raised, with meaningful tests, until it reaches the target before the extended
end-to-end gate. A coverage regression fails the build.

Rebuild the `ci` `no-network` job into a real offline proof: install dependencies online, then run
an actual assessment inside a network namespace (`sudo unshare -n`) that has only a down loopback and
no route off the host. The job first confirms the namespace cannot reach the network — so a leak can
never pass silently — and then runs the assessment under that isolation.

Every action is pinned by commit SHA. Registering the new jobs as required status checks is a
branch-protection change and therefore a maintainer action.

## Alternatives considered (with why not)

- **One monolithic test job.** Rejected: per-engine jobs isolate the three toolchains, give a
  distinct pass/fail signal per language, and map one-to-one onto required status checks.
- **Add a coverage tool (c8, nyc, or a new test framework) to the TypeScript engine.** Rejected:
  Node's built-in test runner already provides `--experimental-test-coverage` with per-metric
  thresholds, so the coverage floor needs no new dependency in the engine's tree.
- **Use a container with `--network none` for the offline proof.** Rejected for now in favour of a
  network namespace: `sudo unshare -n` needs no built image and no user-namespace privileges, and
  works on the CI runner today. It can be revisited once a production container image exists.
- **Enforce the `>=90%` target immediately for every package.** Rejected: a package below the target
  today would either block the gate or invite tests written only to lift a number. A ratchet at the
  measured value makes the gate honest immediately and the remaining gap explicit and tracked.

## Consequences (including determinism, portability to TypeScript/Java, performance)

- Determinism: the tests and the assessment run offline (only dependency installation uses the
  package registries); the offline job proves the assessment opens no socket. Coverage measurement is
  deterministic for a fixed suite.
- Portability: each engine's gate uses that engine's own toolchain; the jobs share no code and impose
  no cross-language coupling.
- Performance: three jobs run in parallel, each a few minutes; the offline job adds one assessment.
- The coverage ratchet can only rise. The gap between a package's current floor and the `>=90%`
  target is closed with meaningful tests before the extended end-to-end gate.
- The gate is only fully enforced once the new jobs are registered as required status checks, which
  the maintainer does on the branch-protection ruleset.

## Verification (the test or check that proves the decision holds)

- The **python** job fails on a lint, format, type, or test error, or if coverage falls below the
  floor; it passes on a clean tree at ~91% coverage.
- The **typescript** job fails on a `biome`, `tsc --noEmit`, test, or line-coverage-floor violation.
- The **java** job runs `./gradlew check`, whose `jacocoTestCoverageVerification` fails below the
  floor.
- The rebuilt `no-network` job runs an assessment under `sudo unshare -n` and fails if the namespace
  can still reach the network — a real behavioural offline proof, not a stand-in computation.
- `.github/workflows/tests.yml` and the rebuilt `no-network` job in `.github/workflows/ci.yml` run on
  every push and pull request.
