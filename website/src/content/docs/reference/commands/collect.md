---
title: agentce collect
description: run a scheduled collection job over sources with a local export, or plan one
---

Run a scheduled collection job over sources with a local export, or plan one.

```text
usage: agentce collect [-h] [--json] [--debug] [--quiet] [--config CONFIG]
                       [--out OUT] [--dry-run] [--adapters-root ADAPTERS_ROOT]

Plan or run a scheduled collection job from a config. --dry-run lists what
would be collected and resolves no credential. A real run adapts every source
that names a local export (already written by its own pipeline, e.g. an OTel
Collector's file exporter, SPEC 5.4) and records it complete; a source with no
export, or whose export cannot be adapted, is recorded incomplete, reason 'no
source connector in the reference collector', and the run exits 1. To get
evidence into a bundle today without a config, emit it with agentce-emit, or
adapt one export file directly with `agentce ingest`.

options:
  -h, --help            show this help message and exit
  --config CONFIG       the collection config file
  --out OUT             the output bundle path
  --dry-run             plan only; write nothing
  --adapters-root ADAPTERS_ROOT
                        the adapters checkout a source's `export` is resolved
                        through (default: ./adapters)

global options:
  --json                emit machine-readable JSON on stdout
  --debug               verbose logs on stderr and a stack trace on unexpected
                        errors
  --quiet               log warnings and errors only
```

Exit codes follow the [common CLI scheme](/reference/commands/#exit-codes).
