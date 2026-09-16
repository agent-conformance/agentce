# `agentce conformance`

Engine conformance suite.

```text
usage: agentce conformance [-h] [--json] [--debug] [--quiet] <action> ...

positional arguments:
  <action>
    run       run the ECS and emit an implementation report

options:
  -h, --help  show this help message and exit

global options:
  --json      emit machine-readable JSON on stdout
  --debug     verbose logs on stderr and a stack trace on unexpected errors
  --quiet     log warnings and errors only
```

Exit codes follow the [common CLI scheme](index.md#exit-codes).
