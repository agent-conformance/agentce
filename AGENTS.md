# AGENTS.md — engineering conventions

This file holds engineering conventions only: toolchains, commands, and the commit, branch, and
quality rules for this repository. It is not a design document. Contributors and coding agents read
it before making changes.

## Purpose

This repository builds an assessment engine and the artifacts around it: language-neutral
specification files, conforming engines in several languages, source adapters, control catalogs, a
deterministic simulated corpus and its generator, a conformance suite, agent skills, report
outputs, and the documentation an adopter needs. The engine is deterministic and read-only over its
inputs; nothing here uses a learned component to decide an outcome.

## Toolchains

- Python 3.12 with `uv` (environments and locking). Install: `curl -LsSf https://astral.sh/uv/install.sh | sh`.
- Node 22 with `pnpm` (via Corepack). Install: `corepack enable`.
- Java 21 with Gradle (from the Java engine onward). Install via your platform's JDK distribution.

## Commands

These mirror the CI workflows; keep them in sync.

- Setup (Python): `uv sync`
- Setup (Node): `pnpm install --frozen-lockfile`
- Lint: `uv run ruff check .` and `uv run ruff format --check .` (Python); `pnpm lint` (Node)
- Type-check: `uv run mypy .` (Python); `pnpm typecheck` (Node)
- Test: `uv run pytest` (Python); `pnpm test` (Node)
- Build: `uv build` (Python packages); `pnpm build` (Node packages)
- Docs: `uv run --project docs --frozen python docs/build.py --check-links` (source-generated
  reference, checked offline) and `uv run --project docs --frozen python docs/build.py --check-publish`
  (the same content published to the site); the published site itself builds with `pnpm build` in
  `website/` (see Commands above)
- Quickstart: `agentce quickstart --out ./out`
- Examples: `uv run python examples/run_all.py --check`

## Repository map

- `spec/` — language-neutral specification files (model, vocabulary, rules, catalogs, report formats, methodology).
- `engines/` — one engine per language plus its alias and emitter packages.
- `adapters/` — source adapters, each with fixtures and a support matrix.
- `corpus/` — simulated projects, the deterministic generator, small rule fixtures, and dataset pins.
- `conformance/` — the conformance suite runner and published implementation reports.
- `skills/` — the approved agent skills and their evaluation tasks.
- `examples/` — one small runnable agent per style plus a server and a mesh example.
- `docs/` — documentation, including architecture decision records.
- `governance/` — charters, versioning and support policy, security and vulnerability policy.
- `tools/` — repository-level checks (dependency denylist, license headers).
- `website/` — the public site (agent-conformance.org): the landing page, documentation, and the canonical JSON-LD context, vocabulary, and JSON Schemas served at their IRIs.

## Commit rules

- Conventional Commits: `feat(engine): …`, `fix(adapter/otel-genai): …`, `docs(adr): …`.
- Every commit carries a DCO sign-off (`git commit -s`) with the maintainer's identity; author and
  sign-off are exactly that identity and nothing else.
- One logical change per commit. Commit messages describe the change, never the tooling that
  produced it. No co-author trailers and no generated-by lines.

## Branch rules

- Work lands on the current phase branch (`phase/<n>`). `main` advances only through the one merge
  per phase after that phase's gate passes. Never push to `main` directly.

## Quality gates

- Lint and type-check are clean; tests pass with the coverage thresholds each package declares.
- Evaluator modules carry determinism tests (order, locale, and clock independence) and golden files.
- Tests run with no network; a dedicated job proves an assessment runs with networking disabled.
- No learned components in any engine or script dependency tree; a dependency-denylist job enforces it.
- Every GitHub Action is pinned by commit SHA, never by a tag.

## Where things live

- Architecture decision records live in `docs/adr/`; each records context, decision, alternatives,
  consequences, and the check that proves it holds.
- Every module has a README that cites the specification section it implements.
- Examples are the source of truth for documentation snippets; rendered outputs come from the corpus.
- Every error, warning, and skipped-evaluation reason carries a stable message key.

## What not to add

- Design documents, roadmaps, or marketing fluff. The standard's public site — its landing and
  documentation — is project infrastructure, not marketing, and lives in `website/`.
- Controls or rules that can only be satisfied by one vendor's product.
- Secrets, credentials, or personal data in code, fixtures, or logs.
- Files outside the repository map above.
