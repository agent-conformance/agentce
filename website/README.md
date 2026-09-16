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

Deployment is a static upload of `website/dist/`; a human performs the deploy and DNS, so the site is
built and configured here but never published from this repository.
