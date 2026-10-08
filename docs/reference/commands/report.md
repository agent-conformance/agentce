# `agentce report`

Re-render a report, or validate one.

```text
usage: agentce report [-h] [--json] [--debug] [--quiet] [--from FILE]
                      [--format {md,html,oscal,sarif,public,pack}]
                      [--role {provider,deployer}] [--catalog LABELS]
                      [--language LANG] [--out FILE] [--validate DIR]

options:
  -h, --help            show this help message and exit
  --from FILE           an assertions.json to re-render
  --format {md,html,oscal,sarif,public,pack}
                        the output format (default: md)
  --role {provider,deployer}
                        evidence-pack role variant; --format pack only
  --catalog LABELS      catalog labels for the public statement, comma-
                        separated; --format public only
  --language LANG       message-key catalogue: de or en (default: en);
                        --format md or html only (SPEC 9.3)
  --out FILE            write the rendering to this file
  --validate DIR        validate every artifact in a report directory; takes
                        no other report option

global options:
  --json                emit machine-readable JSON on stdout
  --debug               verbose logs on stderr and a stack trace on unexpected
                        errors
  --quiet               log warnings and errors only
```

Exit codes follow the [common CLI scheme](index.md#exit-codes).
