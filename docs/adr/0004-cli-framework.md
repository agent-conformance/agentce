# 0004 — CLI framework for the reference engine

Status: accepted
Spec refs: SPEC §8.5, §8.7

## Context

Every engine exposes the same command-line interface: a fixed set of subcommands (`validate`, `verify`,
`assess`, `report`, `catalog lint`, `conformance run`, `diff`, `quickstart`, `init`, `doctor`, `config`,
`version`, `readiness`, `sign`), `--json` on every command, and the common exit-code scheme of SPEC §8.5.
The dependency tree must stay small and free of learned components (SPEC §8.7, no-ml denylist), and the
CLI must behave identically offline.

## Decision

The reference (Python) engine builds its CLI on the standard library **`argparse`**. Subcommands, the
exit-code scheme (`0/1/2/3`, highest wins), and `--json` output are implemented directly; no third-party
CLI framework is added.

## Alternatives considered (with why not)

- **click / typer.** Add dependencies and transitive packages for a command set that is fixed and small;
  the extra surface is unjustified and enlarges the dependency tree the `no_ml` check must clear.
- **A bespoke argument parser.** Reinvents parsing, help, and error handling that `argparse` provides in
  the standard library.

## Consequences (including determinism, portability to TypeScript/Java, performance)

- **Determinism.** Exit codes and JSON output are defined in code, not by a framework's conventions;
  the same invocation yields the same output and code.
- **Portability.** The command set and exit-code scheme are the shared contract; the TypeScript engine
  uses the Node standard library (`util.parseArgs`) and the Java engine a minimal parser or picocli
  (ADR-0008), all producing the same subcommands, `--json`, and codes.
- **Performance.** Negligible; startup stays fast with no framework import cost.

## Verification (the test or check that proves the decision holds)

CLI contract tests assert the exit-code scheme and the presence of `--json` on every command; the ECS
runs the same command set across engines and compares outputs and exit codes.
