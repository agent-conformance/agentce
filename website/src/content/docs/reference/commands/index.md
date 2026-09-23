---
title: CLI commands
description: One page per agentce command, generated from the engine's own argument parser.
---

One page per command (SPEC §8.5). Each is generated from the engine's own argument parser, so the documentation and the tool never disagree.

| Command | Purpose |
|---|---|
| [`agentce assess`](/reference/commands/assess/) | run a full assessment |
| [`agentce catalog`](/reference/commands/catalog/) | catalog tools |
| [`agentce collect`](/reference/commands/collect/) | run a scheduled collection job over sources with a local export, or plan one |
| [`agentce config`](/reference/commands/config/) | show engine configuration |
| [`agentce conformance`](/reference/commands/conformance/) | engine conformance suite |
| [`agentce diff`](/reference/commands/diff/) | deterministic diff of two assertion sets |
| [`agentce doctor`](/reference/commands/doctor/) | diagnose a project and name the exact fix (SPEC 13.4) |
| [`agentce ingest`](/reference/commands/ingest/) | adapt a real adapter export into an evidence bundle |
| [`agentce init`](/reference/commands/init/) | write a starter applicability profile |
| [`agentce quickstart`](/reference/commands/quickstart/) | assess the bundled quickstart project |
| [`agentce readiness`](/reference/commands/readiness/) | compute the report-readiness verdict (SPEC 13.3.4) |
| [`agentce report`](/reference/commands/report/) | re-render a report, or validate one |
| [`agentce sign`](/reference/commands/sign/) | sign a report as claimant or assessor |
| [`agentce validate`](/reference/commands/validate/) | schema-validate a bundle |
| [`agentce verify`](/reference/commands/verify/) | integrity or signature verification |
| [`agentce version`](/reference/commands/version/) | print engine, spec, and no_ml information |

## Exit codes

Every command shares one scheme: `0` success with nothing needing action; `1` findings requiring action; `2` insufficient evidence on a high-severity control (`assess`); `3` an input, version, or signature-verification error. When several apply the highest is returned and the `--json` output carries them all.
