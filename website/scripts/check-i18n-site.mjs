// Built-site proof that the nine declared locales (astro.config.mjs) produce real per-locale
// routing, not just configuration text, for every page family the site actually ships — not one
// hardcoded page. Each representative page must have the right `<html lang>`, cross-link every
// other locale (plus x-default) via `<link rel="alternate" hreflang>`, and appear in the sitemap
// under every locale prefix. Complements the source-level check on astro.config.mjs (which can only
// see the declaration) by proving the build actually acted on it for docs, generated reference
// (controls, IRIs), the glossary, and the accessibility-statement/VPAT/protocol pages alike — a
// future change that excludes one page family from the localized content collection is caught here,
// not only a wholesale i18n regression (loophole L14.1).
//
// Usage:
//   node scripts/check-i18n-site.mjs              Scan dist/; exit 1 on any missing lang/hreflang/sitemap entry.
//   node scripts/check-i18n-site.mjs --dir <path>  Scan <path> instead of dist/ (used by --self-test).
//   node scripts/check-i18n-site.mjs --self-test   Prove the gate can fail two distinct ways: a
//                                                   single-locale-only fixture (must fail on every
//                                                   slug), and a fixture where only the newer page
//                                                   families (reference/accessibility) are missing
//                                                   from non-English locales while the one slug the
//                                                   original gate checked stays fully localized
//                                                   (must fail on exactly those families).
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

// One representative slug per page family Phase 14 shipped, so a family silently dropped from the
// localized content collection is caught here rather than only in the (English-only) structural
// checks each family's own contract added. Not exhaustive — one page per family is enough to prove
// the family participates in per-locale routing at all.
const SLUGS = [
  'docs/getting-started/',
  'reference/controls/rob-02/',
  'reference/iris/schema-evidence-v1/',
  'reference/glossary/',
  'docs/accessibility-statement/',
  'docs/vpat/',
  'docs/accessibility-testing-protocol/',
];

function pagePath(dir, prefix, slug) {
  return join(dir, ...(prefix ? [prefix] : []), ...slug.split('/').filter(Boolean), 'index.html');
}

function urlFor(prefix, slug) {
  return `https://agent-conformance.org/${prefix ? prefix + '/' : ''}${slug}`;
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
  const sitemapped = sitemapUrls(dir);
  if (sitemapped.size === 0) {
    problems.push('no sitemap-index.xml (or its referenced sitemap files) found under the built site');
  }

  for (const slug of SLUGS) {
    for (const { prefix, lang } of LOCALES) {
      const file = pagePath(dir, prefix, slug);
      const url = urlFor(prefix, slug);
      if (!existsSync(file)) {
        problems.push(`${url}: not built (expected ${file})`);
        continue;
      }
      const html = readFileSync(file, 'utf8');
      const htmlTag = html.match(/<html\b[^>]*>/i)?.[0] ?? '';
      const langMatch = htmlTag.match(/\blang="([^"]+)"/i);
      if (!langMatch || langMatch[1] !== lang) {
        problems.push(`${url}: <html lang> is ${langMatch ? langMatch[1] : '(missing)'}, expected ${lang}`);
      }
      const hreflangs = new Set(
        [...html.matchAll(/<link\s+rel="alternate"\s+hreflang="([^"]+)"/gi)].map((m) => m[1])
      );
      const missing = [...expectedHreflangs].filter((h) => !hreflangs.has(h));
      if (missing.length) {
        problems.push(`${url}: missing hreflang alternate(s): ${missing.sort().join(', ')}`);
      }
      if (sitemapped.size > 0 && !sitemapped.has(url)) {
        problems.push(`sitemap: no entry for ${url}`);
      }
    }
  }
  return problems;
}

function pageHtml(lang, hreflangLinks) {
  return `<!doctype html><html lang="${lang}"><head>${hreflangLinks}</head><body>ok</body></html>`;
}

function writeGoodFixture(root, { slugs = SLUGS } = {}) {
  const hreflangLinks = (slug) =>
    LOCALES.map((l) => `<link rel="alternate" hreflang="${l.lang}" href="${urlFor(l.prefix, slug)}"/>`)
      .concat(`<link rel="alternate" hreflang="x-default" href="${urlFor('', slug)}"/>`)
      .join('');
  const urls = [];
  for (const slug of slugs) {
    for (const { prefix, lang } of LOCALES) {
      const dir = join(root, ...(prefix ? [prefix] : []), ...slug.split('/').filter(Boolean));
      mkdirSync(dir, { recursive: true });
      writeFileSync(join(dir, 'index.html'), pageHtml(lang, hreflangLinks(slug)));
      urls.push(urlFor(prefix, slug));
    }
  }
  writeFileSync(
    join(root, 'sitemap-index.xml'),
    `<?xml version="1.0"?><sitemapindex><sitemap><loc>https://agent-conformance.org/sitemap-0.xml</loc></sitemap></sitemapindex>`
  );
  writeFileSync(
    join(root, 'sitemap-0.xml'),
    `<?xml version="1.0"?><urlset>${urls.map((u) => `<url><loc>${u}</loc></url>`).join('')}</urlset>`
  );
}

// A single-locale fixture: only English is built, with no hreflang alternates and no sitemap — the
// state astro.config.mjs was in before C1's fix (Starlight i18n routing never turned on). Committed
// (not generated) so `--dir` can point a RED-at-base-style reproduction at it directly.
const badFixtureDir = resolve(websiteDir, 'tests', 'fixtures', 'i18n-site-bad');

// A partial fixture: `docs/getting-started/` (the one slug the original gate checked) is fully
// localized across all nine locales, but the newer reference/accessibility page families are built
// only for English — reproducing exactly the L14.1 exploit (a page family silently dropped from the
// localized content collection while the one page a narrower gate checks stays green). Committed so
// a RED-at-base-style run can point `--dir` at it directly, matching the `i18n-site-bad` precedent.
const partialFixtureDir = resolve(websiteDir, 'tests', 'fixtures', 'i18n-site-partial');

async function runGate({ dir }) {
  const problems = check(dir ? resolve(dir) : distDir);
  if (problems.length === 0) {
    console.log(`I18N SITE CHECK OK — ${LOCALES.length} locales all have correct lang, hreflang, and sitemap entries for all ${SLUGS.length} page families.`);
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
    writeGoodFixture(good);

    const goodProblems = check(good);
    const badProblems = check(badFixtureDir);
    const partialProblems = check(partialFixtureDir);

    const failures = [];
    if (goodProblems.length !== 0) failures.push(`good fixture: expected no problems, got ${JSON.stringify(goodProblems)}`);
    if (badProblems.length === 0) failures.push('bad fixture (single-locale): expected problems, got none — the gate is toothless');
    if (partialProblems.length === 0) {
      failures.push('partial fixture (newer families English-only): expected problems, got none — the gate would have missed L14.1');
    } else if (partialProblems.some((p) => p.includes('getting-started'))) {
      failures.push(`partial fixture: getting-started should be fully localized in this fixture, got ${JSON.stringify(partialProblems)}`);
    } else if (!partialProblems.some((p) => p.includes('vpat')) || !partialProblems.some((p) => p.includes('reference/controls'))) {
      failures.push(`partial fixture: expected problems naming the vpat and reference/controls families specifically, got ${JSON.stringify(partialProblems)}`);
    }

    if (failures.length) {
      console.error('SELF-TEST FAILED:');
      for (const f of failures) console.error(`  - ${f}`);
      process.exit(1);
    }
    console.log(
      `SELF-TEST OK — the gate passes a fully-localized site, reports ${badProblems.length} problem(s) on the single-locale fixture, ` +
        `and reports ${partialProblems.length} problem(s) naming only the newer page families on the partial fixture.`
    );
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
} else if (args.has('--write-fixtures')) {
  // Internal helper, not part of the CI-facing contract: (re)generates the two committed fixtures
  // below from this script's own fixture writers, so they can never drift from what `check()` and
  // `writeGoodFixture()` actually expect. Run manually after changing SLUGS/LOCALES.
  rmSync(badFixtureDir, { recursive: true, force: true });
  writeGoodFixture(badFixtureDir, { slugs: ['docs/getting-started/'] });
  // Strip hreflang/sitemap to reproduce the pre-i18n state (English only, no locale routing at all).
  rmSync(join(badFixtureDir, 'sitemap-index.xml'));
  rmSync(join(badFixtureDir, 'sitemap-0.xml'));
  for (const { prefix } of LOCALES) {
    if (!prefix) continue;
    rmSync(join(badFixtureDir, prefix), { recursive: true, force: true });
  }
  writeFileSync(
    join(badFixtureDir, 'docs', 'getting-started', 'index.html'),
    `<!doctype html><html lang="en"><head><title>i18n-site-bad fixture — English only, no locale routing</title></head><body><p>getting started</p></body></html>`
  );

  rmSync(partialFixtureDir, { recursive: true, force: true });
  writeGoodFixture(partialFixtureDir); // every family, every locale …
  for (const slug of SLUGS) {
    if (slug === 'docs/getting-started/') continue; // … except getting-started stays fully localized …
    for (const { prefix } of LOCALES) {
      if (!prefix) continue; // … every non-English locale of every OTHER family is removed.
      rmSync(join(partialFixtureDir, prefix, ...slug.split('/').filter(Boolean)), { recursive: true, force: true });
    }
  }
  console.log(`wrote fixtures: ${badFixtureDir}, ${partialFixtureDir}`);
} else {
  await runGate({ dir });
}
