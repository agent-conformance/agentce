// @ts-check
import { defineConfig } from 'astro/config';
import starlight from '@astrojs/starlight';

// The canonical origin. Every dereferenceable IRI the specification defines resolves under this host,
// so the site is built with it fixed here (used for canonical links, Open Graph, and the sitemap).
export default defineConfig({
  site: 'https://agent-conformance.org',
  integrations: [
    starlight({
      title: 'Agent Conformance',
      description:
        'A deterministic, model-free, vendor-neutral standard for assessing how AI agents behave — with reproducible evidence.',
      customCss: ['./src/styles/tokens.css'],
      social: [
        {
          icon: 'github',
          label: 'GitHub',
          href: 'https://github.com/agent-conformance/agentce',
        },
      ],
      // Documentation lives under /docs (content authored in src/content/docs/docs/**); the landing
      // page at / is a custom Astro page (src/pages/index.astro) outside the Starlight route tree.
      sidebar: [
        {
          label: 'Documentation',
          items: [
            { label: 'Getting Started', slug: 'docs/getting-started' },
            { label: 'What is agent conformance?', slug: 'docs/concepts' },
            { label: 'The Conformance Spec', slug: 'docs/specification' },
            { label: 'Running Assessments', slug: 'docs/running-assessments' },
            { label: 'CI Integration', slug: 'docs/ci-integration' },
            { label: 'Contributing', slug: 'docs/contributing' },
          ],
        },
      ],
    }),
  ],
});
