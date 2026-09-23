---
title: agentce ingest
description: adapt a real adapter export into an evidence bundle
---

Adapt a real adapter export into an evidence bundle.

```text
usage: agentce ingest [-h] [--json] [--debug] [--quiet] [--in IN_PATH]
                      [--out OUT] [--adapter ADAPTER]
                      [--adapters-root ADAPTERS_ROOT] [--subject SUBJECT]
                      [--source-class SOURCE_CLASS] [--source SOURCE]
                      [--engine ENGINE]

Turn an already-exported adapter payload (e.g. an OTLP/JSON trace export, or
one an OTel Collector's file exporter wrote, SPEC 5.4) into an evidence bundle
`agentce validate` accepts -- no live collect connector needed.

options:
  -h, --help            show this help message and exit
  --in IN_PATH          the adapter export file
  --out OUT             the output bundle directory
  --adapter ADAPTER     the adapter to use, e.g. otel-genai
  --adapters-root ADAPTERS_ROOT
                        the adapters checkout (default: ./adapters)
  --subject SUBJECT     the assessed subject id (default:
                        agentce:subject/local)
  --source-class SOURCE_CLASS
                        self_report | enforcement_point | independent_system
                        (default: self_report)
  --source SOURCE       override the per-event source URI
  --engine ENGINE       the underlying engine, for adapters that need one
                        (e.g. policy-engines)

global options:
  --json                emit machine-readable JSON on stdout
  --debug               verbose logs on stderr and a stack trace on unexpected
                        errors
  --quiet               log warnings and errors only
```

Exit codes follow the [common CLI scheme](/reference/commands/#exit-codes).
