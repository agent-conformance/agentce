// Generate dist/llms.txt: an H1 title and a set of markdown links to the site's documentation
// entry points and every canonical agent-conformance.org IRI (website/iri-manifest.json), so a
// model reading the site finds the full set of machine-readable resources from one file, per the
// emerging llms.txt convention.
//
// Runs after `astro build` and after build-iris.mjs (so every canonical path already exists in
// dist/, though this script only needs the manifest, not the copied files themselves).
import { readFileSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const scriptDir = dirname(fileURLToPath(import.meta.url));
const websiteDir = resolve(scriptDir, '..');
const dist = join(websiteDir, 'dist');
const SITE = 'https://agent-conformance.org';

const manifest = JSON.parse(readFileSync(join(websiteDir, 'iri-manifest.json'), 'utf8'));

const lines = [
  '# Agent Conformance (AgentCE)',
  '',
  '> A deterministic, model-free, vendor-neutral standard for assessing how AI agents behave, ' +
    'with reproducible evidence.',
  '',
  '## Documentation',
  '',
  `- [Reference](${SITE}/reference/): generated pages for every CLI command, adapter, control, ` +
    'evidence-model class, report schema, and canonical IRI.',
  `- [Explanation](${SITE}/explanation/): the threat model, the verification procedure, and the ` +
    'message-key catalogue.',
  `- [Controls](${SITE}/reference/controls/): every control across the base catalog and its overlays.`,
  `- [Glossary](${SITE}/reference/glossary/): canonical AgentCE terminology.`,
  '',
  '## Canonical IRIs',
  '',
  ...manifest.map((entry) => `- [${entry.path}](${SITE}${entry.path})`),
  '',
];

writeFileSync(join(dist, 'llms.txt'), lines.join('\n') + '\n');
console.log(`build-llms: wrote dist/llms.txt with ${manifest.length + 4} links.`);
