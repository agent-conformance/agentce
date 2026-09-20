# CLI commands

One page per command (SPEC §8.5). Each is generated from the engine's own argument parser, so the documentation and the tool never disagree.

| Command | Purpose |
|---|---|
| [`agentce assess`](assess.md) | run a full assessment |
| [`agentce catalog`](catalog.md) | catalog tools |
| [`agentce collect`](collect.md) | plan a scheduled collection job; no source connector exists yet |
| [`agentce config`](config.md) | show engine configuration |
| [`agentce conformance`](conformance.md) | engine conformance suite |
| [`agentce diff`](diff.md) | deterministic diff of two assertion sets |
| [`agentce doctor`](doctor.md) | diagnose a project and name the exact fix (SPEC 13.4) |
| [`agentce init`](init.md) | write a starter applicability profile |
| [`agentce quickstart`](quickstart.md) | assess the bundled quickstart project |
| [`agentce readiness`](readiness.md) | compute the report-readiness verdict (SPEC 13.3.4) |
| [`agentce report`](report.md) | re-render a report, or validate one |
| [`agentce sign`](sign.md) | sign a report as claimant or assessor |
| [`agentce validate`](validate.md) | schema-validate a bundle |
| [`agentce verify`](verify.md) | integrity or signature verification |
| [`agentce version`](version.md) | print engine, spec, and no_ml information |

## Exit codes

Every command shares one scheme: `0` success with nothing needing action; `1` findings requiring action; `2` insufficient evidence on a high-severity control (`assess`); `3` an input, version, or signature-verification error. When several apply the highest is returned and the `--json` output carries them all.
