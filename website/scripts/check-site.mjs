// Offline link and accessibility check over the built site (run in CI as `node scripts/check-site.mjs`).
//
// It is deterministic and needs no browser or network. For every built HTML page it checks that the
// page has exactly one <h1>, a <main> landmark, a device-width viewport, and alt text on every image,
// and that every root-relative internal link resolves to a built file or directory index. It also
// checks that a reduced-motion preference is honoured somewhere in the output. Prints "SITE CHECK OK".
import { readFileSync, readdirSync, statSync, existsSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const scriptDir = dirname(fileURLToPath(import.meta.url));
const dist = resolve(scriptDir, '..', 'dist');
if (!existsSync(dist)) {
  console.error('SITE CHECK FAILED: dist/ not found — run the build first.');
  process.exit(1);
}

const htmlFiles = [];
let reducedMotion = false;
const walk = (dir) => {
  for (const name of readdirSync(dir)) {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) walk(full);
    else {
      if (name.endsWith('.html')) htmlFiles.push(full);
      if (!reducedMotion) {
        try {
          if (readFileSync(full, 'utf8').includes('prefers-reduced-motion')) reducedMotion = true;
        } catch {
          /* skip unreadable/binary files */
        }
      }
    }
  }
};
walk(dist);

const failures = [];
const rel = (f) => f.slice(dist.length + 1);

if (!reducedMotion) failures.push('no prefers-reduced-motion rule found anywhere in dist/');

const resolves = (href) => {
  let p = href.replace(/^href="/i, '').replace(/"$/, '');
  p = p.split('#')[0].split('?')[0];
  if (p === '' || p.startsWith('//')) return true;
  if (p === '/') return existsSync(join(dist, 'index.html'));
  const r = p.replace(/^\//, '').replace(/\/$/, '');
  return existsSync(join(dist, r)) || existsSync(join(dist, r, 'index.html')) || existsSync(join(dist, `${r}.html`));
};

for (const file of htmlFiles) {
  const html = readFileSync(file, 'utf8');
  const name = rel(file);

  const h1s = (html.match(/<h1[\s>]/gi) || []).length;
  if (h1s !== 1) failures.push(`${name}: expected exactly one <h1>, found ${h1s}`);
  if (!/<main[\s>]/i.test(html)) failures.push(`${name}: missing a <main> landmark`);
  if (!/name=["']?viewport/i.test(html) || !/width=device-width/i.test(html)) {
    failures.push(`${name}: missing a device-width viewport`);
  }
  for (const tag of html.match(/<img\b[^>]*>/gi) || []) {
    if (!/\balt\s*=/i.test(tag)) failures.push(`${name}: <img> without alt text: ${tag.slice(0, 60)}`);
  }
  for (const m of html.match(/href="\/[^"]*"/gi) || []) {
    if (!resolves(m)) failures.push(`${name}: dangling internal link ${m}`);
  }
}

if (failures.length) {
  console.error('SITE CHECK FAILED:');
  for (const f of failures) console.error(`  - ${f}`);
  process.exit(1);
}
console.log(`SITE CHECK OK — ${htmlFiles.length} pages: one h1, landmarks, viewport, image alt text, and internal links all pass.`);
