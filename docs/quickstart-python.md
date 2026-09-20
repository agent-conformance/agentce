# Quickstart — Python engine

Run a full conformance assessment with the reference (Python) engine on a fresh machine, in one
command, within five minutes (SPEC §13.4 AX-1). Everything runs offline against artifacts in this
repository.

## Prerequisites

- Python 3.12
- [`uv`](https://docs.astral.sh/uv/) — `curl -LsSf https://astral.sh/uv/install.sh | sh`

## Run it

```bash
uv run --project engines/python agentce quickstart --out ./out
```

This assesses the vendored [`corpus/quickstart`](../corpus/quickstart) project against the EU AI Act
base catalog and writes `assertions.json`, `report.md` / `report.html`, `oscal-ar.json`,
`results.sarif`, and the coverage, integrity, and quarantine detail to `./out`. The full walkthrough,
including `agentce init` for your own profile, is in the [main quickstart](quickstart.md).

## Container image

The repository builds the engine as a container image (see
[`engines/python/Dockerfile`](../engines/python/Dockerfile)); it is not published to a registry yet, so
build it from a checkout. The build is two-stage: the first stage builds the wheel and exports its
hash-pinned dependencies, and the final stage installs only those. The image contains the installed
package and its data, not the repository, and runs as an unprivileged user. Run it with no network and
a mounted output directory to keep the report:

```bash
docker build -f engines/python/Dockerfile -t agentce/agentce:dev .
mkdir -p out
docker run --rm --network none --user "$(id -u):$(id -g)" -v "$PWD/out:/out" agentce/agentce:dev quickstart --out /out
```
