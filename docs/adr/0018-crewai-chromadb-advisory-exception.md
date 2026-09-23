# 0018 — Allow-list four unpatched chromadb advisories for the crewai example only

Status: accepted
Spec refs: SPEC §13.4 AX-3, AX-4

## Context

`examples/crewai` (ADR 0016) makes the crewai framework example real: it imports and runs crewai
itself, offline and keyless, against a scripted deterministic model. crewai's `pyproject.toml`
declares `chromadb` as an unconditional, non-optional dependency (its default memory/embeddings
backend), so any lockfile that resolves `crewai` at all also resolves some `chromadb` release.

Every `chromadb` release published to PyPI, including the newest (1.5.9, the version this example's
`uv.lock` already resolves via `override-dependencies`), falls inside the vulnerable range of four
GitHub advisories against chromadb's server component:

| Advisory | Severity | Vulnerable range | First patched version |
|---|---|---|---|
| GHSA-f4j7-r4q5-qw2c (pre-auth code injection) | critical | `>= 1.0.0, <= 1.5.9` | none published |
| GHSA-36p7-vc44-83pf (code injection) | critical | `>= 0.4.17, <= 1.5.9` | none published |
| GHSA-2wm9-hf6c-p5cr (cross-tenant read/write/delete) | high | `>= 0.4.17, <= 1.5.9` | none published |
| GHSA-xph7-9rjv-w5fr (RBAC scope check) | high | `>= 0.5.0, <= 1.5.9` | none published |

(Checked via `gh api /advisories/<id>` against the PyPI index on 2026-09-23; each advisory's
`first_patched_version` is null, i.e. no fixed release exists yet.) The `dependency-review` required
check (`fail-on-severity: high`) therefore fails on this PR's first introduction of the crewai
example, and cannot be made to pass by picking a different `chromadb` version — none exists.

All four advisories describe attacks against chromadb's authenticated HTTP server (tenant/RBAC
authorization bypass, injection via the server's query API). `examples/crewai` never starts that
server: the example runs a single offline process against a scripted model and a local, in-process
chromadb client, with no listening port and no network access (SPEC AX-3). The vulnerable surface is
not reachable from anything this repository ships or runs.

## Decision

1. Add `allow-ghsas` to `.github/workflows/dependency-review.yml`, naming exactly the four advisories
   above, with an inline comment pointing at this ADR and at the fact that the exemption is global by
   advisory ID, not scoped by path.
2. This does not soften `fail-on-severity: high` in general: any other high-or-above advisory, on
   this or any other dependency, still fails the check. Only these four specific, currently-unpatched
   chromadb advisories are allow-listed.
3. Revisit on two triggers: (a) chromadb publishes a patched release covering all four advisories —
   drop the exemption and let `override-dependencies` pick it up; (b) a second `examples/*/uv.lock`
   starts resolving `chromadb` — re-derive reachability for that example specifically before assuming
   the same exemption still applies to it.

## Alternatives considered (with why not)

- **Pin to an older chromadb release.** Rejected: the vulnerable ranges (`>= 0.4.17`/`>= 0.5.0`/
  `>= 1.0.0`, all `<= 1.5.9`) cover essentially every chromadb release crewai's own `chromadb`
  requirement (declared without an upper bound) would resolve; there is no unaffected version to pin
  to.
- **Drop the crewai example.** Rejected: item 13.2 (ADR 0016) requires examples to really run their
  named framework; crewai is one of the five named styles (SPEC §13.4 AX-4), and chromadb is not an
  optional extra of crewai that could be dropped instead.
- **`warn-only: true` on the whole job.** Rejected: that disarms `fail-on-severity` for every
  dependency in every package, not just these four advisories — the opposite of the narrowest fix.
- **`deny-packages`/per-path scoping.** Rejected: the action has no input that allows a specific
  advisory only for a specific manifest path; `allow-ghsas` is the narrowest mechanism the action
  exposes, so the path-independence is accepted and documented rather than worked around.

## Consequences (determinism, portability, performance)

- No effect on determinism, portability, or the evaluation path: chromadb is not a dependency of any
  engine, adapter, or `tools/` package, and this ADR does not touch `tools/no_ml_check.py` (ADR 0016
  already covers why `chromadb` is permitted inside `examples/crewai/uv.lock` for that scan).
- The exemption is by GHSA ID across the whole repository, so introducing `chromadb` into a second
  lockfile (another example, or by accident into an engine tree) would not be caught by
  `dependency-review` for these four advisories specifically — every other advisory and the no-ml
  scan (which does cover engine/adapter/tools trees unconditionally) remain the backstops.

## Verification (the check that proves the decision holds)

- `.github/workflows/dependency-review.yml`'s `review` job, re-run on this PR: passes with the
  allow-list, and the job log's `Vulnerabilities` group shows only the four named GHSA IDs (verifiable
  via `gh run view <run-id> --log-failed` before the fix, `--log` after — the four advisories are the
  only ones present, so the allow-list opens exactly the four it names and nothing else).
- `docs/adr/0016-example-framework-dependency-boundary.md`'s `no_ml_check.py --self-test` is
  unaffected and continues to pass unchanged.
