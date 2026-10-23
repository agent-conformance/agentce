---
title: Command line
description: 'Spec refs: SPEC §8.5 (command-line grammar)'
---

Spec refs: SPEC §8.5 (command-line grammar)

AgentCE reads every command line under one grammar, the same in the Python, TypeScript and Java
engines. The grammar is a table, [`spec/cli/argv-grammar.json`](https://github.com/agent-conformance/agentce/blob/main/spec/cli/argv-grammar.json): for
each command, the exact spelling of each option, whether it takes a value, whether it may be given
more than once, and how many positionals the command takes. Each engine reads that table, so the same
command line gives the same exit code, message key and first message line whichever engine runs it.

## What a command line may hold

- The global flags `--json`, `--debug` and `--quiet`, before or after the command word.
- `-h`/`--help` or `-V`/`--version` with no command.
- The command word, and for `conformance`, `catalog` and `config` the action word after it.
- Options written `--name value` or `--name=value`. After `=`, the whole rest of the argument is the
  value, spaces and a leading `-` included: `--catalog="/path/with a space"` reads the path.
- A flag that takes no value, written alone: `--dry-run`.
- `--`, after which every argument is a positional, even one that starts with `-`.
- `-h` or `--help` anywhere after the command, which prints that command's usage and exits `0`.

## What the engines refuse

Each of these exits `3` with the command's keyed error, and the message names the argument:

| Rule | Example | Write it as |
|---|---|---|
| `abbreviation`, `exact_spelling` | `--gap gaps.md`, `--GAPS gaps.md` | the full spelling: `--gaps gaps.md` |
| `short_cluster` | `-jq` | one flag per argument |
| `repeated_option` | `--gaps a.md --gaps b.md` | the option once; only `--catalog-dir` may repeat, and every value is kept in order |
| `needs_value` | `--gaps` at the end, or before `--` or another option | `--gaps gaps.md` |
| `value_on_flag` | `--dry-run=yes` | `--dry-run` |
| `dash_token_not_option` | `--gaps -1`, a lone `-` | `--gaps=-1`; a negative number is written the same way (`--limit=-1`), though no current option takes a number |
| `extra_positional` | `readiness a b` | one report directory |
| `double_dash_alone` | `readiness out --` | drop the `--`, or put the positionals after it |

`--help` in the place of an option's value is not a request for help. The engines refuse
`verify --report --help` because `--report` has no value, and `verify --catalog=--help` reads
`--help` as the path.

The [error catalogue](/explanation/errors/) lists the keys: `input.<command>_unrecognized_flag` for each
command (and `input.assess_flag_needs_value`, `input.from_missing` and `input.validate_missing` for
an option given without its value), and `input.unknown_command` before the command word.

## Tests

One shared case table, [`spec/cli/argv-cases.json`](https://github.com/agent-conformance/agentce/blob/main/spec/cli/argv-cases.json), holds at least one
case per rule. Each engine's tests read it, and `tools/argv_parity_check.py` runs every case through
the three command-line tools and compares their exit codes, keys and messages.
