# The ACF conformance matrix and scorecard

An internal QA and credibility mechanism, not a spec vocabulary module (see `matrix.schema.json`'s own
header): AgentCE scores its own domain coverage against the Agent Conformance Framework (ACF), a
seven-dimension reference model (coverage, rigor, interoperability, lifecycle, operability,
assurance-tool integrity, governance) with a 0-3 maturity scale (0 Absent, 1 Asserted, 2 Partial, 3
Complete) that scores an unverifiable claim *below* an honest partial. This directory and
`conformance/acf_score.py` are how AgentCE dog-foods that model against itself: assessed the way
AgentCE assesses agents — deterministic, offline, evidence-based, honest about gaps.

## Files

- `matrix.yaml` — one entry per named ACF capability: `{id, dimension, capability, statement,
  level_target, test: {cmd, fixture, expect}, evidence_ref}`. `statement` is the natural-language
  definition of what "level 3 (Complete)" means for a user; `test` is the executable proof, reusing an
  existing conformance-suite/tools check wherever one already exists rather than inventing a second,
  competing proof.
- `matrix.schema.json` — the JSON Schema `matrix.yaml` validates against.
- `fixtures-golden.json` — the committed `{exit, expect_ok}` outcome each tested entry's `test.cmd`
  reproduced the last time it was recorded; `acf_matrix_check.py --check-fixtures` re-runs every
  entry's command twice and rejects a mismatch, a missing fixture, a golden that bakes in a failure, or
  a command that does not reproduce identically twice in a row.
- `scorecard.json` — the committed scorecard `conformance/acf_score.py` publishes: one record per
  capability (`score`, `level_label`, `evidence_ref`, the live `outcome` that produced the score), a
  per-dimension summary, and an overall summary. This is the score-only-rises ratchet's baseline: a
  fresh run whose score for any capability falls below this file's refuses to publish (exit 1) instead
  of silently regressing.

## The scoring rule (`acf_score.py`)

- No `test` -> capped at Asserted (1), regardless of `level_target`: an unverifiable claim never scores
  above an honest "asserted."
- `test` present and fails -> Absent (0): a capability whose own proof does not pass today is not real
  today.
- `test` present and passes -> `level_target`, unless `level_target` is 3 (Complete) and `evidence_ref`
  does not resolve to a real, on-disk path, in which case the score is capped at Partial (2). A
  Complete score always carries a resolvable evidence pointer — no exceptions.

## Running it

```
cd conformance
uv run --frozen python acf_matrix_check.py --self-test       # matrix schema/structure discriminate
uv run --frozen python acf_matrix_check.py --check-fixtures  # every tested entry reproduces its golden outcome
uv run --frozen python acf_score.py --self-test               # scoring rule, determinism, ratchet discriminate
uv run --frozen python acf_score.py                            # emit the scorecard; ratchet-gate against scorecard.json
uv run --frozen python acf_score.py --write                    # after a genuine improvement: overwrite scorecard.json
```

All four are wired into `.github/workflows/ci.yml`'s `domain-depth` job and run on every pull request
and push.
