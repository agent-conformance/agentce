// Built-site proof that the nine declared locales (astro.config.mjs) produce real per-locale
// routing, not just configuration text: every locale has a built page with the right `<html lang>`,
// every page cross-links every other locale (plus x-default) via `<link rel="alternate" hreflang>`,
// and the sitemap lists a URL under every locale prefix. Complements the source-level check on
// astro.config.mjs (which can only see the declaration) by proving the build actually acted on it.
//
// Usage:
//   node scripts/check-i18n-site.mjs              Scan dist/; exit 1 on any missing lang/hreflang/sitemap entry.
//   node scripts/check-i18n-site.mjs --dir <path>  Scan <path> instead of dist/ (used by --self-test).
//   node scripts/check-i18n-site.mjs --self-test   Prove the gate can fail: build a tiny well-formed
//                                                   fixture tree (must pass) and a single-locale fixture
//                                                   tree with no hreflang/sitemap entries (must fail).
import { existsSync, mkdtempSync, mkdirSync, writeFileSync, rmSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const scriptDir = dirname(fileURLToPath(import.meta.url));
const websiteDir = resolve(scriptDir, '..');
const distDir = resolve(websiteDir, 'dist');

// The nine BCP-47 UI locales fixed by the maintainer decision (English is `root`, served unprefixed).
const LOCALES = [
  { prefix: '', lang: 'en' },
  { prefix: 'es', lang: 'es' },
  { prefix: 'fr', lang: 'fr' },
  { prefix: 'ja', lang: 'ja' },
  { prefix: 'zh-hans', lang: 'zh-Hans' },
  { prefix: 'pl', lang: 'pl' },
  { prefix: 'pt-br', lang: 'pt-BR' },
  { prefix: 'uk', lang: 'uk' },
  { prefix: 'ru', lang: 'ru' },
];
const SLUG = 'docs/getting-started/';

function pagePath(dir, prefix) {
  return join(dir, ...(prefix ? [prefix] : []), ...SLUG.split('/').filter(Boolean), 'index.html');
}

function urlFor(prefix) {
  return `https://agent-conformance.org/${prefix ? prefix + '/' : ''}${SLUG}`;
}

function sitemapUrls(dir) {
  const indexPath = join(dir, 'sitemap-index.xml');
  if (!existsSync(indexPath)) return new Set();
  const index = readFileSync(indexPath, 'utf8');
  const subSitemaps = [...index.matchAll(/<loc>([^<]+)<\/loc>/g)].map((m) => m[1].split('/').pop());
  const urls = new Set();
  for (const name of subSitemaps) {
    const p = join(dir, name);
    if (!existsSync(p)) continue;
    for (const m of readFileSync(p, 'utf8').matchAll(/<loc>([^<]+)<\/loc>/g)) urls.add(m[1]);
  }
  return urls;
}

function check(dir) {
  const problems = [];
  if (!existsSync(dir)) return [`${dir} not found — run the build first (pnpm build).`];

  const expectedHreflangs = new Set([...LOCALES.map((l) => l.lang), 'x-default']);

  for (const { prefix, lang } of LOCALES) {
    const file = pagePath(dir, prefix);
    if (!existsSync(file)) {
      problems.push(`${urlFor(prefix)}: not built (expected ${file})`);
      continue;
    }
    const html = readFileSync(file, 'utf8');
    const htmlTag = html.match(/<html\b[^>]*>/i)?.[0] ?? '';
    const langMatch = htmlTag.match(/\blang="([^"]+)"/i);
    if (!langMatch || langMatch[1] !== lang) {
      problems.push(`${urlFor(prefix)}: <html lang> is ${langMatch ? langMatch[1] : '(missing)'}, expected ${lang}`);
    }
    const hreflangs = new Set(
      [...html.matchAll(/<link\s+rel="alternate"\s+hreflang="([^"]+)"/gi)].map((m) => m[1])
    );
    const missing = [...expectedHreflangs].filter((h) => !hreflangs.has(h));
    if (missing.length) {
      problems.push(`${urlFor(prefix)}: missing hreflang alternate(s): ${missing.sort().join(', ')}`);
    }
  }

  const sitemapped = sitemapUrls(dir);
  if (sitemapped.size === 0) {
    problems.push('no sitemap-index.xml (or its referenced sitemap files) found under the built site');
  } else {
    for (const { prefix } of LOCALES) {
      const url = urlFor(prefix);
      if (!sitemapped.has(url)) problems.push(`sitemap: no entry for ${url}`);
    }
  }
  return problems;
}

function writeGoodFixture(root) {
  const hreflangLinks = LOCALES.map((l) => `<link rel="alternate" hreflang="${l.lang}" href="${urlFor(l.prefix)}"/>`)
    .concat(`<link rel="alternate" hreflang="x-default" href="${urlFor('')}"/>`)
    .join('');
  for (const { prefix, lang } of LOCALES) {
    const dir = join(root, ...(prefix ? [prefix] : []), 'docs', 'getting-started');
    mkdirSync(dir, { recursive: true });
    writeFileSync(join(dir, 'index.html'), `<!doctype html><html lang="${lang}"><head>${hreflangLinks}</head><body>ok</body></html>`);
  }
  writeFileSync(
    join(root, 'sitemap-index.xml'),
    `<?xml version="1.0"?><sitemapindex><sitemap><loc>https://agent-conformance.org/sitemap-0.xml</loc></sitemap></sitemapindex>`
  );
  const urls = LOCALES.map((l) => `<url><loc>${urlFor(l.prefix)}</loc></url>`).join('');
  writeFileSync(join(root, 'sitemap-0.xml'), `<?xml version="1.0"?><urlset>${urls}</urlset>`);
}

function writeBadFixture(root) {
  // A single-locale fixture: only English is built, with no hreflang alternates and no sitemap — the
  // state astro.config.mjs was in before C1's fix (Starlight i18n routing never turned on).
  const dir = join(root, 'docs', 'getting-started');
  mkdirSync(dir, { recursive: true });
  writeFileSync(join(dir, 'index.html'), `<!doctype html><html lang="en"><head></head><body>ok</body></html>`);
}

async function runGate({ dir }) {
  const problems = check(dir ? resolve(dir) : distDir);
  if (problems.length === 0) {
    console.log(`I18N SITE CHECK OK — ${LOCALES.length} locales all have correct lang, hreflang, and sitemap entries.`);
    process.exit(0);
  }
  console.error('I18N SITE CHECK FAILED:');
  for (const p of problems) console.error(`  - ${p}`);
  process.exit(1);
}

function selfTest() {
  const tmp = mkdtempSync(join(tmpdir(), 'agentce-i18n-site-'));
  try {
    const good = join(tmp, 'good');
    const bad = join(tmp, 'bad');
    writeGoodFixture(good);
    writeBadFixture(bad);

    const goodProblems = check(good);
    const badProblems = check(bad);

    const failures = [];
    if (goodProblems.length !== 0) failures.push(`good fixture: expected no problems, got ${JSON.stringify(goodProblems)}`);
    if (badProblems.length === 0) failures.push('bad fixture: expected problems, got none — the gate is toothless');

    if (failures.length) {
      console.error('SELF-TEST FAILED:');
      for (const f of failures) console.error(`  - ${f}`);
      process.exit(1);
    }
    console.log(`SELF-TEST OK — the gate passes a fully-localized site and reports ${badProblems.length} problem(s) on a single-locale fixture.`);
    process.exit(0);
  } finally {
    rmSync(tmp, { recursive: true, force: true });
  }
}

const argv = process.argv.slice(2);
const args = new Set(argv);
const dirIdx = argv.indexOf('--dir');
const dir = dirIdx >= 0 ? argv[dirIdx + 1] : undefined;
if (args.has('--self-test')) {
  selfTest();
} else {
  await runGate({ dir });
}
