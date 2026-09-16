# `agentce collect`

Pull evidence from sources via adapters.

```text
usage: agentce collect [-h] [--json] [--debug] [--quiet] [--config CONFIG]
                       [--out OUT] [--dry-run]

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
