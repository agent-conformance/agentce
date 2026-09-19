# `agentce init`

Write a starter applicability profile.

```text
usage: agentce init [-h] [--json] [--debug] [--quiet] [--non-interactive]
                    [--framework FRAMEWORK] [--subject SUBJECT]
                    [--role {deployer,provider,both}] [--out OUT]

options:
  -h, --help            show this help message and exit
  --non-interactive     generate without prompting (required)
  --framework FRAMEWORK
                        the agent framework, e.g. custom-loop, langgraph
  --subject SUBJECT     the assessed subject id (default: the id
                        agentce_emit.auto() emits under)
  --role {deployer,provider,both}
                        the subject's role: deployer, provider, or both
  --out OUT             the output directory

global options:
  --json                emit machine-readable JSON on stdout
  --debug               verbose logs on stderr and a stack trace on unexpected
                        errors
  --quiet               log warnings and errors only
```

Exit codes follow the [common CLI scheme](index.md#exit-codes).
