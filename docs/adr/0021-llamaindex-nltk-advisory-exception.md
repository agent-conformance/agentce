# 0021 — Allow-list one unpatched nltk advisory for the llamaindex example only

Status: accepted
Spec refs: SPEC §13.4 AX-3, AX-4

## Context

`examples/llamaindex` (ADR 0016, item 16.3) makes the llamaindex framework example real: it imports
and runs `llama-index-core` itself, offline and keyless, against a scripted deterministic model.
`llama-index-core` transitively resolves `nltk` (used for its default sentence splitter and stopword
list, via `nltk.tokenize.sent_tokenize`/`wordpunct_tokenize`/`PunktSentenceTokenizer` and
`nltk.corpus.stopwords` — confirmed by grepping the installed package's own source, `utils.py`,
`node_parser/text/sentence.py`, and `indices/keyword_table/utils.py`).

The newest `nltk` release this example's `uv.lock` resolves (3.10.3) falls inside the vulnerable range
of one GitHub advisory:

| Advisory | Severity | Vulnerable range | First patched version |
|---|---|---|---|
| GHSA-8mgp-746c-j5xp (model-artifact APIs bypass pathsec) | high | `<= 3.10.3` | none published |

(Checked via `gh api /advisories/GHSA-8mgp-746c-j5xp` against the PyPI index on 2026-09-25; the
advisory's `first_patched_version` is null, i.e. no fixed release exists yet.) The `dependency-review`
required check (`fail-on-severity: high`) therefore fails on this PR's first introduction of the
llamaindex example, and cannot be made to pass by picking a different `nltk` version — none exists.

The advisory names six specific model-persistence APIs as vulnerable: `TransitionParser.train`,
`TransitionParser.parse`, `AveragedPerceptron.save`, `AveragedPerceptron.load`,
`PerceptronTagger.save_to_json`, and `save_maxent_params` — all part of nltk's POS-tagging/dependency-
parsing model import/export path, which accepts a caller-controlled path and can be tricked into
reading or writing outside an intended sandbox root. Neither `llama-index-core`'s own use of `nltk`
(sentence tokenization and a static stopword corpus, both read-only, path-fixed lookups via
`nltk.data.find`/`nltk.data.path`) nor `examples/llamaindex/agent.py` calls any of the six named APIs,
confirmed by grepping the installed package tree and the example's own source. The vulnerable surface
is not reachable from anything this repository ships or runs.

## Decision

1. Add `allow-ghsas` to `.github/workflows/dependency-review.yml`, naming the advisory above alongside
   the existing crewai/chromadb exemptions (ADR 0018), with an inline comment pointing at this ADR.
2. This does not soften `fail-on-severity: high` in general: any other high-or-above advisory, on this
   or any other dependency, still fails the check. Only this one, currently-unpatched nltk advisory is
   allow-listed.
3. Revisit on two triggers: (a) nltk publishes a patched release covering this advisory — drop the
   exemption; (b) a second `examples/*/uv.lock` starts resolving `nltk` — re-derive reachability for
   that example specifically before assuming the same exemption still applies to it (in particular,
   confirm it does not call the six named model-persistence APIs on a caller-controlled path).

## Alternatives considered (with why not)

- **Pin to an older nltk release.** Rejected: the vulnerable range (`<= 3.10.3`) covers every nltk
  release currently published; there is no unaffected version to pin to.
- **Drop the llamaindex example.** Rejected: item 16.3 requires the five new framework examples to
  really run their named framework (SPEC §13.4 AX-3/AX-4, mirroring item 13.2/ADR 0016), and
  `llama-index-core` is not usable without its own default sentence-splitting dependency.
- **`warn-only: true` on the whole job.** Rejected: that disarms `fail-on-severity` for every
  dependency in every package, not just this one advisory — the opposite of the narrowest fix.
- **Vendor or monkey-patch nltk to remove the vulnerable APIs.** Rejected: it would no longer be the
  real `nltk` release `llama-index-core` actually depends on, adding an ongoing fork-maintenance
  burden to close a surface this example never touches.

## Consequences (determinism, portability, performance)

- No effect on determinism, portability, or the evaluation path: `nltk` is not a dependency of any
  engine, adapter, or `tools/` package. `nltk` is already exempted from `tools/no_ml_check.py`'s scan
  for this same lockfile path under `FRAMEWORK_EXAMPLE_LOCKS` (ADR 0016) for an unrelated reason (the
  no-ml denylist, not a security advisory); this ADR does not change that exemption.
- The exemption is by GHSA ID across the whole repository, so introducing `nltk` into a second
  lockfile would not be caught by `dependency-review` for this advisory specifically — every other
  advisory and the no-ml scan (which does cover engine/adapter/tools trees unconditionally) remain the
  backstops.

## Verification (the check that proves the decision holds)

- `.github/workflows/dependency-review.yml`'s `review` job, re-run on this PR: passes with the
  allow-list, and the job log's `Vulnerabilities` group is empty (verifiable via
  `gh run view <run-id> --log` — no advisory lines remain).
- `docs/adr/0016-example-framework-dependency-boundary.md`'s `no_ml_check.py --self-test` is
  unaffected and continues to pass unchanged.
