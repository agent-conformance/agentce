// Resolver check for the canonical IRIs (run as `pnpm run check:iri`, after a build).
//
// Prints "IRI RESOLUTION OK" and exits 0 iff both hold:
//   (a) every entry in iri-manifest.json is served in website/dist/ at its path, byte-identical to its
//       spec/ source (no drift), and
//   (b) every dereferenceable agent-conformance.org IRI referenced across spec/ appears in the
//       manifest — so a newly added schema $id cannot ship unresolved. IRIs that spec/ uses as
//       identifiers rather than documents (a provenance source, a UUID namespace) are classified
//       explicitly below, so a genuinely new IRI still fails the check until it is handled.
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const scriptDir = dirname(fileURLToPath(import.meta.url));
const websiteDir = resolve(scriptDir, '..');
const repoRoot = resolve(scriptDir, '..', '..');
const dist = join(websiteDir, 'dist');
const ORIGIN = 'https://agent-conformance.org';

// Canonical IRIs that spec/ references as identifiers, not as served documents.
const KNOWN_NON_RESOURCE = new Map([
  ['/catalogs/base/eu-ai-act', 'catalog provenance source identifier (catalog.yaml provenance.source)'],
  ['/catalogs/base/nist-ai-rmf', 'catalog provenance source identifier (catalog.yaml provenance.source)'],
  ['/oscal/component-definition', 'UUID5 namespace for OSCAL component identifiers'],
]);

const manifest = JSON.parse(readFileSync(join(websiteDir, 'iri-manifest.json'), 'utf8'));
const manifestPaths = new Set(manifest.map((e) => e.path));
const failures = [];

// (a) Every served IRI is byte-identical to its source.
for (const { path, source } of manifest) {
  const served = join(dist, path.replace(/^\//, ''));
  let a;
  let b;
  try {
    a = readFileSync(served);
  } catch {
    failures.push(`not served: ${path} (expected ${served} — run the build first)`);
    continue;
  }
  try {
    b = readFileSync(join(repoRoot, source));
  } catch {
    failures.push(`missing source: ${source}`);
    continue;
  }
  if (!a.equals(b)) failures.push(`drift: ${path} differs from ${source}`);
}

// (b) Every canonical IRI referenced in spec/ is resolved or classified.
const files = [];
const skip = new Set(['.venv', '.git', 'node_modules', '__pycache__']);
const walk = (dir) => {
  for (const name of readdirSync(dir)) {
    if (skip.has(name)) continue;
    const full = join(dir, name);
    if (statSync(full).isDirectory()) walk(full);
    else files.push(full);
  }
};
walk(join(repoRoot, 'spec'));

const referenced = new Set();
const re = /https:\/\/agent-conformance\.org(\/[^\s"'`<>)\]}\\,#]*)/g;
for (const f of files) {
  let text;
  try {
    text = readFileSync(f, 'utf8');
  } catch {
    continue;
  }
  for (const m of text.matchAll(re)) {
    let p = m[1];
    if (p.length > 1) p = p.replace(/[.,;:]+$/, '');
    referenced.add(p);
  }
}

for (const p of [...referenced].sort()) {
  if (manifestPaths.has(p) || KNOWN_NON_RESOURCE.has(p)) continue;
  failures.push(`unresolved IRI referenced in spec/: ${ORIGIN}${p} — add it to iri-manifest.json or classify it in check-iris.mjs`);
}

if (failures.length) {
  console.error('IRI RESOLUTION FAILED:');
  for (const f of failures) console.error(`  - ${f}`);
  process.exit(1);
}
console.log(
  `IRI RESOLUTION OK — ${manifest.length} canonical IRIs served byte-identically; ` +
    `${referenced.size} referenced IRIs all resolved or classified.`,
);
