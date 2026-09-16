# `agentce validate`

Schema-validate a bundle.

```text
usage: agentce validate [-h] [--json] [--debug] [--quiet] [--bundle BUNDLE]
                        [--out OUT]

options:
  -h, --help       show this help message and exit
  --bundle BUNDLE  the evidence bundle directory
  --out OUT        write quarantine.jsonl to this directory

global options:
  --json           emit machine-readable JSON on stdout
  --debug          verbose logs on stderr and a stack trace on unexpected
                   errors
  --quiet          log warnings and errors only
```

Exit codes follow the [common CLI scheme](index.md#exit-codes).
