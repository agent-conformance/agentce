# website — agent-conformance.org

The public website for the Agent Conformance standard: the landing page, the documentation, and the
canonical artifacts served at their IRIs. It is built with [Astro](https://astro.build) and
[Starlight](https://starlight.astro.build) and deploys as a static site to `website/dist/`.

This module implements the public site described in the project specification. Beyond the landing page
and docs, it serves — byte-identical to their sources under `spec/` — the JSON-LD context, the
vocabulary, and every JSON Schema `$id`, each at its canonical `https://agent-conformance.org` path, so
that every dereferenceable IRI the specification defines resolves. The mapping from canonical path to
`spec/` source lives in `iri-manifest.json`, and `pnpm run check:iri` verifies that every referenced
IRI is served and unchanged.

## Layout

- `src/pages/` — the landing page and other custom pages, built on `src/layouts/BaseLayout.astro`.
- `src/content/docs/` — the Starlight documentation (served under `/docs`).
- `src/styles/tokens.css` — the single design-token file (the accent palette, light and dark).
- `public/` — static assets copied verbatim into the build.
- `iri-manifest.json` — canonical path → `spec/` source → content type (the resolver's source of truth).

## Commands

The website is a pnpm workspace member (registered in the repository-root `pnpm-workspace.yaml`) and
uses Node 22 with pnpm via Corepack.

```
pnpm install --frozen-lockfile   # install dependencies
pnpm --filter @agent-conformance/website build    # build the static site to dist/
pnpm --filter @agent-conformance/website dev      # run the dev server
pnpm --filter @agent-conformance/website preview  # preview the built site
```

From inside `website/`, the same scripts run as `pnpm build`, `pnpm dev`, and `pnpm preview`.

The quickstart result on the landing page, the Getting Started page, `docs/quickstart.md`, and
`corpus/quickstart/README.md` is generated from one real `agentce quickstart` run, never typed. After a
change to the engine, the corpus, or the report that alters that run, regenerate every surface with
`node scripts/quickstart-hero.mjs --write`; the `website` workflow runs `pnpm check:hero` (which needs
`uv`) and fails when a committed surface no longer equals a fresh run or claims that every control is
conformant.

The Install page (`src/content/docs/docs/install.mdx`) renders its per-ecosystem tabs from
`src/data/release-state.json` through `src/lib/install.mjs`: a tab shows a channel's zero-install
one-liner only when the maintainer has marked that channel published, and the command that works from a
checkout today otherwise. The `website` workflow runs `node scripts/check-install.mjs --self-test` and
`node scripts/check-install.mjs --require-built`, which fail when a tab leaks an unpublished one-liner,
when a published channel names no pinned artifact, or when the gate stops flipping.

Deployment is a static upload of `website/dist/`; a human performs the deploy and DNS, so the site is
built and configured here but never published from this repository.
