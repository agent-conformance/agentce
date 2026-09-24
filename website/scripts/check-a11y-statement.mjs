// Built-site proof that the accessibility statement, VPAT, and manual test protocol (finding: no
// accessibility statement, EN 301 549 mapping, VPAT, or manual-test protocol) are not just present as
// source files but actually ship, are reachable from the site's own navigation (not orphan routes), and
// still carry every required structural element once built.
//
// Complements the source-level structural checks (which scan committed markdown/MDX/Astro source) by
// proving the build actually turned each page into a linked, content-complete page: every page has a
// corresponding built `index.html`, its route is linked as an `href` from at least one other built page
// (the sidebar put it in the navigation), and the same structural markers the source-level checks require
// are still present in the rendered text.
//
// Usage:
//   node scripts/check-a11y-statement.mjs              Scan dist/ against the real built site.
//   node scripts/check-a11y-statement.mjs --dir <path>  Scan an alternate dist root (used by --self-test).
//   node scripts/check-a11y-statement.mjs --self-test   Prove the gate can fail: a well-formed fixture
//                                                        (all three pages built, linked, and content-
//                                                        complete) must pass; a fixture missing a built
//                                                        page, one missing a nav link, and one missing a
//                                                        required structural marker, must each fail.
import { existsSync, mkdtempSync, mkdirSync, writeFileSync, rmSync, readFileSync, readdirSync, statSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';

const scriptDir = new URL('.', import.meta.url).pathname;
const websiteDir = resolve(scriptDir, '..');
const DEFAULT_DIST = resolve(websiteDir, 'dist');

// Strip tags to plain text so the same substring/regex markers the source-level checks use still find
// their target inside rendered HTML (which wraps headings and prose in elements the source markers don't
// expect to see literally).
function textOf(html) {
  return html
    .replace(/<script[\s\S]*?<\/script>/gi, ' ')
    .replace(/<style[\s\S]*?<\/style>/gi, ' ')
    .replace(/<[^>]+>/g, ' ')
    .replace(/&amp;/g, '&')
    .replace(/&nbsp;/g, ' ')
    .replace(/\s+/g, ' ');
}

const PAGES = [
  {
    id: 'accessibility statement',
    slug: 'docs/accessibility-statement',
    markers: [
      { name: 'compliance status', re: /complian(t|ce)\s+status/i },
      { name: 'scope', re: /\bscope\b/i },
      { name: 'non-accessible content', re: /non-accessible content|content (that|which) is not accessible|not (yet |fully )?accessible content/i },
      { name: 'feedback / contact', re: /feedback|contact (us|information|form|email)|how to contact/i },
      { name: 'enforcement / preparation', re: /enforcement (procedure|body)|preparation of (this|the) (accessibility )?statement/i },
    ],
  },
  {
    id: 'VPAT',
    slug: 'docs/vpat',
    markers: [
      { name: 'VPAT', re: /\bVPAT\b/ },
      { name: 'EN 301 549', re: /en\s*301[\s-]*549/i },
      { name: 'Clause 9', re: /\bclause\s*9\b/i },
      { name: 'Clause 10', re: /\bclause\s*10\b/i },
      { name: 'Clause 11', re: /\bclause\s*11\b/i },
      { name: 'Not Evaluated', re: /not evaluated/i },
    ],
  },
  {
    id: 'manual test protocol',
    slug: 'docs/accessibility-testing-protocol',
    markers: [
      { name: 'screen reader', re: /screen\s*reader/i },
      { name: 'keyboard', re: /keyboard/i },
      { name: '200%', re: /200\s*%/ },
      { name: '400%', re: /400\s*%/ },
      { name: 'forced-colors', re: /forced[- ]colou?rs|high[- ]contrast mode/i },
    ],
  },
];

function walk(dir) {
  const out = [];
  for (const name of readdirSync(dir)) {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) out.push(...walk(full));
    else out.push(full);
  }
  return out;
}

function check(distDir) {
  const problems = [];
  if (!existsSync(distDir)) return [`${distDir} not found — run the build first (pnpm build).`];

  const htmlFiles = walk(distDir).filter((f) => f.endsWith('.html'));
  const pages = htmlFiles.map((f) => ({ file: f, text: readFileSync(f, 'utf8') }));

  for (const page of PAGES) {
    const builtPath = join(distDir, ...page.slug.split('/'), 'index.html');
    if (!existsSync(builtPath)) {
      problems.push(`${page.id}: missing built page (expected dist/${page.slug}/index.html)`);
      continue;
    }
    const href = `/${page.slug}/`;
    // A page linking only to itself is still an orphan route: nothing else in the site navigates
    // to it, so the incoming-link scan must exclude the page's own built file.
    const linked = pages.some((p) => p.file !== builtPath && p.text.includes(`href="${href}"`));
    if (!linked) problems.push(`${page.id}: no incoming href="${href}" from any OTHER built page (not in site navigation)`);

    const text = textOf(readFileSync(builtPath, 'utf8'));
    const missing = page.markers.filter((m) => !m.re.test(text)).map((m) => m.name);
    if (missing.length) problems.push(`${page.id}: built page is missing required elements: ${missing.join(', ')}`);
  }
  return problems;
}

function selfTest() {
  const root = mkdtempSync(join(tmpdir(), 'a11y-statement-check-'));
  try {
    const navHtml =
      '<html><body>' +
      PAGES.map((p) => `<a href="/${p.slug}/">${p.id}</a>`).join('') +
      '</body></html>';

    const goodBody = {
      'docs/accessibility-statement': '<h1>Accessibility Statement</h1><p>Compliance status: partial. Scope: this site. Non-accessible content: none known. Feedback and contact: email us. Enforcement procedure and preparation of this statement: see above.</p>',
      'docs/vpat': '<h1>VPAT</h1><p>EN 301 549 Clause 9 Clause 10 Clause 11. Every row: Not Evaluated.</p>',
      'docs/accessibility-testing-protocol': '<h1>Accessibility Test Protocol</h1><p>Screen reader. Keyboard. 200% and 400% zoom. Forced-colors mode.</p>',
    };

    // Good fixture: every page built, linked from nav, and content-complete.
    const goodDist = join(root, 'good-dist');
    for (const [slug, body] of Object.entries(goodBody)) {
      mkdirSync(join(goodDist, ...slug.split('/')), { recursive: true });
      writeFileSync(join(goodDist, ...slug.split('/'), 'index.html'), `<html><body>${body}</body></html>`);
    }
    mkdirSync(join(goodDist, 'nav-page'), { recursive: true });
    writeFileSync(join(goodDist, 'nav-page', 'index.html'), navHtml);
    const goodProblems = check(goodDist);
    if (goodProblems.length !== 0) {
      console.error('SELF-TEST FAILED: expected the good fixture to pass, got:', goodProblems);
      return 1;
    }

    // Bad fixture 1: the VPAT page was never built.
    const missingBuildDist = join(root, 'missing-build-dist');
    mkdirSync(join(missingBuildDist, 'docs', 'accessibility-statement'), { recursive: true });
    writeFileSync(join(missingBuildDist, 'docs', 'accessibility-statement', 'index.html'), `<html><body>${goodBody['docs/accessibility-statement']}</body></html>`);
    mkdirSync(join(missingBuildDist, 'docs', 'accessibility-testing-protocol'), { recursive: true });
    writeFileSync(join(missingBuildDist, 'docs', 'accessibility-testing-protocol', 'index.html'), `<html><body>${goodBody['docs/accessibility-testing-protocol']}</body></html>`);
    mkdirSync(join(missingBuildDist, 'nav-page'), { recursive: true });
    writeFileSync(join(missingBuildDist, 'nav-page', 'index.html'), navHtml);
    const missingBuildProblems = check(missingBuildDist);
    if (!missingBuildProblems.some((p) => p.includes('missing built page'))) {
      console.error('SELF-TEST FAILED: expected a missing-built-page failure, got:', missingBuildProblems);
      return 1;
    }

    // Bad fixture 2: all three pages built, but nothing links to the protocol page (an orphan route).
    const orphanDist = join(root, 'orphan-dist');
    for (const [slug, body] of Object.entries(goodBody)) {
      mkdirSync(join(orphanDist, ...slug.split('/')), { recursive: true });
      writeFileSync(join(orphanDist, ...slug.split('/'), 'index.html'), `<html><body>${body}</body></html>`);
    }
    mkdirSync(join(orphanDist, 'nav-page'), { recursive: true });
    writeFileSync(
      join(orphanDist, 'nav-page', 'index.html'),
      '<html><body><a href="/docs/accessibility-statement/">a</a><a href="/docs/vpat/">b</a></body></html>'
    );
    const orphanProblems = check(orphanDist);
    if (!orphanProblems.some((p) => p.includes('not in site navigation'))) {
      console.error('SELF-TEST FAILED: expected an orphan-route failure, got:', orphanProblems);
      return 1;
    }

    // Bad fixture 3: every page built and linked, but the VPAT page is missing its EN 301 549 mapping
    // (a regression that quietly drops a required element without deleting the page).
    const thinDist = join(root, 'thin-dist');
    mkdirSync(join(thinDist, 'docs', 'accessibility-statement'), { recursive: true });
    writeFileSync(join(thinDist, 'docs', 'accessibility-statement', 'index.html'), `<html><body>${goodBody['docs/accessibility-statement']}</body></html>`);
    mkdirSync(join(thinDist, 'docs', 'vpat'), { recursive: true });
    writeFileSync(join(thinDist, 'docs', 'vpat', 'index.html'), '<html><body><h1>VPAT</h1><p>A table of criteria.</p></body></html>');
    mkdirSync(join(thinDist, 'docs', 'accessibility-testing-protocol'), { recursive: true });
    writeFileSync(join(thinDist, 'docs', 'accessibility-testing-protocol', 'index.html'), `<html><body>${goodBody['docs/accessibility-testing-protocol']}</body></html>`);
    mkdirSync(join(thinDist, 'nav-page'), { recursive: true });
    writeFileSync(join(thinDist, 'nav-page', 'index.html'), navHtml);
    const thinProblems = check(thinDist);
    if (!thinProblems.some((p) => p.includes('missing required elements'))) {
      console.error('SELF-TEST FAILED: expected a missing-required-elements failure, got:', thinProblems);
      return 1;
    }

    // Bad fixture 4: every page built and content-complete, but the accessibility-statement page's
    // only incoming link to itself is its own self-referential nav — a page cannot satisfy its own
    // orphan-route test by linking to itself.
    const selfLinkDist = join(root, 'self-link-dist');
    for (const [slug, body] of Object.entries(goodBody)) {
      const selfHref = `/${slug}/`;
      const extra = slug === 'docs/accessibility-statement' ? `<a href="${selfHref}">self</a>` : '';
      mkdirSync(join(selfLinkDist, ...slug.split('/')), { recursive: true });
      writeFileSync(join(selfLinkDist, ...slug.split('/'), 'index.html'), `<html><body>${body}${extra}</body></html>`);
    }
    mkdirSync(join(selfLinkDist, 'nav-page'), { recursive: true });
    writeFileSync(
      join(selfLinkDist, 'nav-page', 'index.html'),
      '<html><body><a href="/docs/vpat/">b</a><a href="/docs/accessibility-testing-protocol/">c</a></body></html>'
    );
    const selfLinkProblems = check(selfLinkDist);
    if (!selfLinkProblems.some((p) => p.includes('not in site navigation'))) {
      console.error('SELF-TEST FAILED: expected a self-link-is-not-navigation failure, got:', selfLinkProblems);
      return 1;
    }

    console.log('SELF-TEST OK — the gate passes a linked, complete, built set of pages and fails a missing, orphaned, thinned, or self-linked-only one.');
    return 0;
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
}

function main() {
  const args = process.argv.slice(2);
  if (args.includes('--self-test')) return selfTest();
  const dirIdx = args.indexOf('--dir');
  const distDir = dirIdx >= 0 ? resolve(args[dirIdx + 1]) : DEFAULT_DIST;
  const problems = check(distDir);
  if (problems.length) {
    console.error('A11Y-STATEMENT CHECK FAILED:');
    for (const p of problems) console.error(`  - ${p}`);
    return 1;
  }
  console.log('A11Y-STATEMENT CHECK OK — the accessibility statement, VPAT, and manual test protocol are built, linked from site navigation, and content-complete.');
  return 0;
}

process.exit(main());
