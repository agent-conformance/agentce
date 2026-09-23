# agentce-assess (composite GitHub Action)

Gate a build on an AgentCE assessment in one step: runs `agentce assess` against your evidence
bundle, uploads `results.sarif` to code scanning, and writes a job summary via
`$GITHUB_STEP_SUMMARY`. The assessment itself makes no network call; only this action's own setup
(installing `uv`) does.

## Usage

Pin the action by its full 40-character commit SHA — never a floating tag or branch, so the code
this action runs cannot change under you between reviews:

```yaml
name: assess
on: [push, pull_request]
permissions:
  contents: read
  security-events: write
jobs:
  assess:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262 # v4
      - uses: agent-conformance/agentce/.github/actions/assess@45ca972c172dc8e00a9f1f892c196b5cb884a1d2
        with:
          bundle: evidence
          profile: applicability.yaml
          domain: domain.linkml.yaml
          fail-on: 'outcome=="insufficient_evidence" and severity=="high"'
```

Look up the commit SHA you want to pin to at
`https://github.com/agent-conformance/agentce/commits/main`, then replace the example SHA above with
it. Re-pin deliberately when you want the newer behaviour; a floating tag would let it change without
your review.

## Inputs

| Input | Required | Default | Meaning |
|---|---|---|---|
| `bundle` | yes | — | the evidence bundle directory |
| `profile` | yes | — | the applicability profile file |
| `domain` | yes | — | the domain ontology binding file |
| `catalog` | no | the profile's catalogs | catalog ids, comma-separated: `<id@ver>[,<id@ver>...]` |
| `fail-on` | no | any non-conformant assertion | a `--fail-on` expression scoping which assertions fail the build |
| `out` | no | `./out` | the output directory for `assertions.json`, `results.sarif`, `report.md`, and the rest |

## Outputs

| Output | Meaning |
|---|---|
| `out` | the output directory that was written |

## What it does not do

Listing this action on the GitHub Marketplace is a separate, manual step the maintainer performs
through GitHub's own publisher flow; this action works by direct SHA-pinned reference (as above)
without any listing existing.
