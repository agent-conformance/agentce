---
title: agentce report
description: re-render a report, or validate one
---

Re-render a report, or validate one.

```text
usage: agentce report [-h] [--json] [--debug] [--quiet] [--from FROM_]
                      [--format {md,html,oscal,sarif,public,pack}]
                      [--role {provider,deployer}] [--catalog CATALOG]
                      [--language LANGUAGE] [--out OUT] [--validate VALIDATE]

options:
  -h, --help            show this help message and exit
  --from FROM_          an assertions.json to re-render
  --format {md,html,oscal,sarif,public,pack}
                        the output format
  --role {provider,deployer}
                        evidence-pack role variant
  --catalog CATALOG     catalog labels for the public statement, comma-
                        separated
  --language LANGUAGE   message-key catalogue for md/html rendering (SPEC 9.3)
  --out OUT             write the rendering to this file
  --validate VALIDATE   validate every artifact in a report directory

global options:
  --json                emit machine-readable JSON on stdout
  --debug               verbose logs on stderr and a stack trace on unexpected
                        errors
  --quiet               log warnings and errors only
```

Exit codes follow the [common CLI scheme](/reference/commands/#exit-codes).
