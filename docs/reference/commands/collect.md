# `agentce collect`

Plan a scheduled collection job; no source connector exists yet.

```text
usage: agentce collect [-h] [--json] [--debug] [--quiet] [--config CONFIG]
                       [--out OUT] [--dry-run]

Plan a scheduled collection job from a config. --dry-run lists what would be
collected and resolves no credential. A real run has no source connector yet
(on the roadmap): it records every source incomplete, reason 'no source
connector in the reference collector', and exits 1. To get evidence into a
bundle today, emit it with agentce-emit.

options:
  -h, --help       show this help message and exit
  --config CONFIG  the collection config file
  --out OUT        the output bundle path
  --dry-run        plan only; write nothing

global options:
  --json           emit machine-readable JSON on stdout
  --debug          verbose logs on stderr and a stack trace on unexpected
                   errors
  --quiet          log warnings and errors only
```

Exit codes follow the [common CLI scheme](index.md#exit-codes).
