// Real accessibility gate for the built website.
//
// Runs the axe-core rules engine (via Playwright + the version-pinned Chromium) over every built HTML
// page in BOTH themes (light and dark) with the WCAG tag set wcag2a / wcag2aa / wcag21aa / wcag22aa,
// and fails the build on any violation. It is offline and deterministic: dist/ is served from
// 127.0.0.1 (no network), the browser build is pinned by the `playwright` version in package.json, and
// pages, themes, and violations are all sorted before reporting, so the result never depends on the
// clock, locale, or filesystem order. The fast regex pass in check-site.mjs stays as a cheap pre-check;
// this is the authoritative gate.
//
// Usage:
//   node scripts/check-a11y.mjs              Scan dist/ in both themes; exit 1 on any violation.
//   node scripts/check-a11y.mjs --report     Scan dist/ but exit 0 after printing the coverage manifest
//                                            and any violations (diagnostic; does not gate the build).
//   node scripts/check-a11y.mjs --dir <path> Scan <path> instead of dist/ (used to point the gate at the
//                                            committed known-bad fixtures, proving it fails on them).
//   node scripts/check-a11y.mjs --self-test  Prove the gate can fail: run axe over the deliberately
//                                            inaccessible fixture (must report the seeded violations,
//                                            including colour contrast) and over the accessible control
//                                            (must report none). Exit 0 iff the gate discriminates; a
//                                            stubbed or toothless gate exits 1.
import { createServer } from 'node:http';
import { readFileSync, readdirSync, statSync, existsSync, mkdtempSync, copyFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve, extname, relative, sep, isAbsolute } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';
import axeModule from '@axe-core/playwright';

const AxeBuilder = axeModule.default ?? axeModule;

const scriptDir = dirname(fileURLToPath(import.meta.url));
const websiteDir = resolve(scriptDir, '..');
const distDir = resolve(websiteDir, 'dist');
const fixturesDir = resolve(websiteDir, 'tests', 'fixtures');

const THEMES = ['dark', 'light']; // sorted for deterministic output
const WCAG_TAGS = ['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa'];
const VIEWPORT = { width: 1280, height: 800 };

// The self-test's inaccessible fixture seeds these WCAG failures; the gate must report every one of
// them (proving the rules engine is real and, in particular, that colour contrast is live for #45 and
// keyboard access to scrollable code blocks is live for #43).
const REQUIRED_SELFTEST_RULES = [
  'button-name',
  'color-contrast',
  'html-has-lang',
  'image-alt',
  'scrollable-region-focusable',
];

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.mjs': 'text/javascript; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.woff2': 'font/woff2',
  '.woff': 'font/woff',
  '.ttf': 'font/ttf',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.webp': 'image/webp',
  '.ico': 'image/x-icon',
  '.xml': 'application/xml; charset=utf-8',
  '.txt': 'text/plain; charset=utf-8',
  '.map': 'application/json; charset=utf-8',
};

// Resolve a request URL to an absolute path confined to `root`, or null if it would escape.
// The containment guard (path.relative → no leading `..`, not absolute) dominates every filesystem
// call downstream, so an untrusted request path can never traverse outside the served directory.
function resolveWithin(root, urlPath) {
  let p = decodeURIComponent(urlPath.split('?')[0].split('#')[0]);
  if (p.endsWith('/')) p += 'index.html';
  const candidate = resolve(join(root, p));
  const rel = relative(root, candidate);
  if (rel.startsWith('..') || isAbsolute(rel)) return null;
  // A directory request maps to its index.html — a constant suffix that stays within `root`.
  if (existsSync(candidate) && statSync(candidate).isDirectory()) return join(candidate, 'index.html');
  return candidate;
}

function serve(rootDir) {
  const root = resolve(rootDir);
  const server = createServer((req, res) => {
    try {
      const file = resolveWithin(root, req.url);
      if (file === null) {
        res.writeHead(403);
        res.end('forbidden');
        return;
      }
      if (!existsSync(file)) {
        res.writeHead(404);
        res.end('not found');
        return;
      }
      res.writeHead(200, { 'content-type': MIME[extname(file)] || 'application/octet-stream' });
      res.end(readFileSync(file));
    } catch (err) {
      // Never reflect exception text or a stack trace into the response (avoids HTML injection and
      // information exposure); log server-side for diagnosis and return a generic message instead.
      console.error('a11y static server error:', err);
      res.writeHead(500, { 'content-type': 'text/plain; charset=utf-8' });
      res.end('internal server error');
    }
  });
  return new Promise((res) => {
    server.listen(0, '127.0.0.1', () => res({ server, port: server.address().port }));
  });
}

function htmlPages(dir) {
  const out = [];
  const walk = (d) => {
    for (const name of readdirSync(d).sort()) {
      const full = join(d, name);
      if (statSync(full).isDirectory()) walk(full);
      else if (name.endsWith('.html')) out.push(full);
    }
  };
  walk(dir);
  return out;
}

function urlPathFor(dir, file) {
  let rel = relative(dir, file).split(sep).join('/');
  if (rel === 'index.html') return '/';
  if (rel.endsWith('/index.html')) return '/' + rel.slice(0, -'index.html'.length);
  return '/' + rel;
}

async function scanPage(page, url, theme) {
  await page.goto(url, { waitUntil: 'load' });
  // Force the theme deterministically: tokens.css keys light/dark off data-theme on <html>.
  await page.evaluate((t) => {
    document.documentElement.setAttribute('data-theme', t);
    try {
      localStorage.setItem('starlight-theme', t);
    } catch {
      /* localStorage may be unavailable; the attribute already switched the theme */
    }
  }, theme);
  await page.evaluate(() => (document.fonts ? document.fonts.ready : null)).catch(() => {});
  const results = await new AxeBuilder({ page }).withTags(WCAG_TAGS).analyze();
  return results.violations;
}

function formatViolations(pageId, theme, violations) {
  const lines = [];
  for (const v of [...violations].sort((a, b) => a.id.localeCompare(b.id))) {
    const n = v.nodes.length;
    lines.push(`  ${pageId} [${theme}] ${v.id} (${v.impact || 'n/a'}; ${n} node${n === 1 ? '' : 's'}) — ${v.help}`);
    for (const node of v.nodes) {
      const target = Array.isArray(node.target) ? node.target.join(' ') : String(node.target);
      lines.push(`      at ${target}`);
    }
  }
  return lines;
}

// Scan every HTML page of `dir` in both themes. Returns { pages, scans, findings } where findings is a
// sorted array of { pageId, theme, violations }.
async function scanDir(dir) {
  const files = htmlPages(dir);
  const { server, port } = await serve(dir);
  const base = `http://127.0.0.1:${port}`;
  const browser = await chromium.launch();
  const findings = [];
  try {
    for (const theme of THEMES) {
      const context = await browser.newContext({
        colorScheme: theme,
        reducedMotion: 'reduce',
        viewport: VIEWPORT,
      });
      const page = await context.newPage();
      for (const file of files) {
        const pageId = urlPathFor(dir, file);
        const violations = await scanPage(page, base + pageId, theme);
        findings.push({ pageId, theme, violations });
      }
      await context.close();
    }
  } finally {
    await browser.close();
    server.close();
  }
  findings.sort((a, b) => a.pageId.localeCompare(b.pageId) || a.theme.localeCompare(b.theme));
  return { pages: files.length, scans: files.length * THEMES.length, findings };
}

async function scanSingle(dir, fileName, theme) {
  const { server, port } = await serve(dir);
  const browser = await chromium.launch();
  try {
    const context = await browser.newContext({ colorScheme: theme, reducedMotion: 'reduce', viewport: VIEWPORT });
    const page = await context.newPage();
    const violations = await scanPage(page, `http://127.0.0.1:${port}/${fileName}`, theme);
    await context.close();
    return violations;
  } finally {
    await browser.close();
    server.close();
  }
}

async function runGate({ report, dir }) {
  const root = dir ? resolve(dir) : distDir;
  if (!existsSync(root)) {
    console.error(`A11Y CHECK FAILED: ${relative(websiteDir, root) || root} not found — run the build first (pnpm build).`);
    process.exit(1);
  }
  const { pages, scans, findings } = await scanDir(root);
  const { total, withViolations, exitCode } = verdict(findings, report);
  console.log(`A11Y coverage: ${pages} pages × ${THEMES.length} themes = ${scans} axe scans (tags: ${WCAG_TAGS.join(', ')}).`);
  if (total === 0) {
    console.log('A11Y CHECK OK — axe reported no WCAG 2.2 AA violations in either theme.');
    process.exit(0);
  }
  console.error(`A11Y ${report ? 'report' : 'CHECK FAILED'}: ${total} violation instance(s) across ${withViolations.length} page/theme(s):`);
  for (const f of withViolations) for (const line of formatViolations(f.pageId, f.theme, f.violations)) console.error(line);
  process.exit(exitCode);
}

// The enforce path's decision: any violation fails the build unless --report was asked for.
function verdict(findings, report) {
  const withViolations = findings.filter((f) => f.violations.length > 0);
  const total = withViolations.reduce((n, f) => n + f.violations.length, 0);
  return { total, withViolations, exitCode: total > 0 && !report ? 1 : 0 };
}

// Drive the real enforce path (scanDir + verdict) over a two-page directory holding one seeded-bad and
// one clean page, so a neutered scan or a swapped fixture cannot pass the self-test.
async function enforceProbe(bad, good) {
  const tmp = mkdtempSync(join(tmpdir(), 'a11y-probe-'));
  try {
    copyFileSync(bad, join(tmp, 'bad.html'));
    copyFileSync(good, join(tmp, 'good.html'));
    const { pages, scans, findings } = await scanDir(tmp);
    const problems = [];
    if (pages !== 2 || scans !== 2 * THEMES.length) problems.push(`the probe covered ${pages} page(s) / ${scans} scan(s), expected 2 / ${2 * THEMES.length}`);
    for (const theme of THEMES) {
      const b = findings.find((f) => f.pageId === '/bad.html' && f.theme === theme);
      const g = findings.find((f) => f.pageId === '/good.html' && f.theme === theme);
      if (!b || b.violations.length === 0) problems.push(`the seeded-bad page reported no violations in the ${theme} theme`);
      if (!g || g.violations.length !== 0) problems.push(`the clean page reported violations in the ${theme} theme`);
    }
    if (verdict(findings, false).exitCode !== 1) problems.push('the enforce path did not fail on a page with violations');
    if (verdict(findings, true).exitCode !== 0) problems.push('--report did not exit 0 on a page with violations');
    if (verdict(findings.filter((f) => f.pageId === '/good.html'), false).exitCode !== 0) problems.push('the enforce path failed on a clean page');
    return { pages, scans, problems };
  } finally {
    rmSync(tmp, { recursive: true, force: true });
  }
}

async function selfTest() {
  const bad = resolve(fixturesDir, 'inaccessible.html');
  const good = resolve(fixturesDir, 'accessible.html');
  if (!existsSync(bad) || !existsSync(good)) {
    console.error(`SELF-TEST FAILED: fixtures missing under ${relative(websiteDir, fixturesDir)}/`);
    process.exit(1);
  }
  const badViol = await scanSingle(fixturesDir, 'inaccessible.html', 'light');
  const goodViol = await scanSingle(fixturesDir, 'accessible.html', 'light');
  const badIds = [...new Set(badViol.map((v) => v.id))].sort();
  const missing = REQUIRED_SELFTEST_RULES.filter((r) => !badIds.includes(r));

  console.log(`SELF-TEST: inaccessible fixture → ${badViol.length} violation type(s): ${badIds.join(', ') || '(none)'}`);
  console.log(`SELF-TEST: accessible fixture   → ${goodViol.length} violation type(s): ${goodViol.map((v) => v.id).sort().join(', ') || '(none)'}`);

  const problems = [];
  for (const r of REQUIRED_SELFTEST_RULES) console.log(`SELF-TEST: required rule ${r} ${badIds.includes(r) ? 'reported' : 'MISSING'} on the inaccessible fixture`);
  const probe = await enforceProbe(bad, good);
  console.log(`SELF-TEST: enforce path probe → ${probe.pages} pages × ${THEMES.length} themes = ${probe.scans} axe scans`);
  problems.push(...probe.problems);
  if (badViol.length === 0) problems.push('the inaccessible fixture produced no axe violations — the gate is toothless');
  if (missing.length) problems.push(`the inaccessible fixture did not trigger required rule(s): ${missing.join(', ')}`);
  if (goodViol.length !== 0) problems.push(`the accessible control fixture produced ${goodViol.length} violation(s) — the gate reports false positives`);

  if (problems.length) {
    console.error('SELF-TEST FAILED:');
    for (const p of problems) console.error(`  - ${p}`);
    process.exit(1);
  }
  console.log(`SELF-TEST OK — the axe gate reports the seeded violations (incl. colour contrast) and none on the clean control, so it can fail.`);
  process.exit(0);
}

const argv = process.argv.slice(2);
const args = new Set(argv);
const dirIdx = argv.indexOf('--dir');
const dir = dirIdx >= 0 ? argv[dirIdx + 1] : undefined;
// The maintainer runs accessibility testing manually, downstream of this build. When
// AGENTCE_SKIP_A11Y is set (to anything other than "0"), skip the full-page scan — the slow
// gate — and exit 0. The --self-test path is never skipped, so the gate's teeth-proof still
// runs. Unset the variable (or set it to 0) to run the real scan, e.g. for the maintainer's
// own downstream run.
const skipA11y = process.env.AGENTCE_SKIP_A11Y && process.env.AGENTCE_SKIP_A11Y !== '0';
if (skipA11y && !args.has('--self-test')) {
  console.log('check-a11y: SKIPPED via AGENTCE_SKIP_A11Y (accessibility is verified manually, downstream).');
  process.exit(0);
}
if (args.has('--self-test')) {
  await selfTest();
} else {
  await runGate({ report: args.has('--report'), dir });
}
