// Phone-width gate for the built website (USER_EXPERIENCE.md R7, R10).
//
// Opens every built HTML page at 390x844 from a 127.0.0.1 server (offline: a request to any other host
// is aborted and fails the gate) and checks:
//   - no page scrolls sideways (measured once per page, after fonts load; the theme does not change
//     layout width), naming the widest element that sticks out;
//   - axe finds no WCAG 2.2 AA violation at phone width in either theme on SNAPSHOT_PAGES, one page per
//     layout plus the longest-text locale (the every-page desktop scan stays with check-a11y.mjs);
//   - a full-page PNG of each SNAPSHOT_PAGES entry in both themes is written outside the working tree,
//     as evidence for review. The PNGs are not pixel goldens: font rasterising differs by machine.
// Every failure line starts with a stable key, then the page, the theme where it matters, and what to
// change.
//
// Usage:
//   node scripts/check-ux-pages.mjs                 Check dist/; snapshots go to a new temp directory.
//   node scripts/check-ux-pages.mjs --out <dir>     Write the snapshots to <dir>.
//   node scripts/check-ux-pages.mjs --dir <path>    Check <path> instead of dist/.
//   node scripts/check-ux-pages.mjs --self-test     Run the checks over tests/fixtures/ux-pages and show
//                                                   each failure kind is caught and the fitting page passes.
import { createServer } from 'node:http';
import { readFileSync, readdirSync, statSync, existsSync, mkdirSync, mkdtempSync, rmSync } from 'node:fs';
import { parseArgs } from 'node:util';
import { tmpdir } from 'node:os';
import { dirname, join, resolve, extname, relative, sep, isAbsolute } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';
import axeModule from '@axe-core/playwright';

const AxeBuilder = axeModule.default ?? axeModule;

const scriptDir = dirname(fileURLToPath(import.meta.url));
const websiteDir = resolve(scriptDir, '..');
const distDir = resolve(websiteDir, 'dist');
const fixturesDir = resolve(websiteDir, 'tests', 'fixtures', 'ux-pages');

const THEMES = ['dark', 'light'];
// Must equal check-a11y.mjs's WCAG_TAGS; the self-test compares the two.
const WCAG_TAGS = ['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa'];
const VIEWPORT = { width: 390, height: 844 };
const CONTEXT_OPTIONS = { reducedMotion: 'reduce', viewport: VIEWPORT, deviceScaleFactor: 1 };
const OFF_SITE = /^(?!http:\/\/127\.0\.0\.1[:/])/;
const SNAPSHOT_PAGES = [
  '/',
  '/docs/getting-started/',
  '/reference/commands/assess/',
  '/reference/ingest-support-matrix/',
  '/ru/docs/getting-started/',
];
const PARALLEL_PAGES = 6;

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
};

// A request path resolved inside `root`, or null when it would leave it.
function resolveWithin(root, urlPath) {
  let p = decodeURIComponent(urlPath.split('?')[0].split('#')[0]);
  if (p.endsWith('/')) p += 'index.html';
  const candidate = resolve(join(root, p));
  const rel = relative(root, candidate);
  if (rel.startsWith('..') || isAbsolute(rel)) return null;
  if (existsSync(candidate) && statSync(candidate).isDirectory()) return join(candidate, 'index.html');
  return candidate;
}

function serve(rootDir) {
  const root = resolve(rootDir);
  const server = createServer((req, res) => {
    try {
      const file = resolveWithin(root, req.url);
      if (file === null || !existsSync(file)) {
        res.writeHead(file === null ? 403 : 404);
        res.end();
        return;
      }
      res.writeHead(200, { 'content-type': MIME[extname(file)] || 'application/octet-stream' });
      res.end(readFileSync(file));
    } catch {
      res.writeHead(500);
      res.end();
    }
  });
  return new Promise((done) => server.listen(0, '127.0.0.1', () => done({ server, port: server.address().port })));
}

function htmlPages(dir) {
  return readdirSync(dir, { recursive: true })
    .filter((name) => name.endsWith('.html'))
    .sort()
    .map((name) => join(dir, name));
}

function urlPathFor(dir, file) {
  const rel = relative(dir, file).split(sep).join('/');
  if (rel === 'index.html') return '/';
  if (rel.endsWith('/index.html')) return '/' + rel.slice(0, -'index.html'.length);
  return '/' + rel;
}

async function open(page, url, theme) {
  await page.goto(url, { waitUntil: 'load' });
  if (theme) {
    await page.evaluate((t) => {
      document.documentElement.setAttribute('data-theme', t);
      try {
        localStorage.setItem('starlight-theme', t);
      } catch {
        /* the attribute already switched the theme */
      }
    }, theme);
  }
  await page.evaluate(() => (document.fonts ? document.fonts.ready : null)).catch(() => {});
}

// The page's width past the viewport and the outermost element that sticks out furthest, or null when
// the page fits.
function measureOverflow() {
  const width = window.innerWidth;
  const scroll = document.documentElement.scrollWidth;
  if (scroll <= width) return null;
  const describe = (el) => {
    let s = el.tagName.toLowerCase();
    if (el.id) return `${s}#${el.id}`;
    if (el.classList.length) s += '.' + [...el.classList].slice(0, 2).join('.');
    const parent = el.parentElement;
    return parent && parent !== document.body ? `${describe(parent)} > ${s}` : s;
  };
  let widest = null;
  let widestRight = width;
  for (const el of document.body.querySelectorAll('*')) {
    const right = el.getBoundingClientRect().right;
    if (right <= widestRight) continue;
    const parentRight = el.parentElement ? el.parentElement.getBoundingClientRect().right : 0;
    if (parentRight > width && el.parentElement !== document.body) continue;
    widest = el;
    widestRight = right;
  }
  return { scroll, width, element: widest ? describe(widest) : 'html', right: Math.round(widestRight) };
}

// A phone-sized context that refuses every request to another host and reports its origin.
async function newContext(browser, theme, onOffSite) {
  const context = await browser.newContext({ colorScheme: theme, ...CONTEXT_OPTIONS });
  await context.route(OFF_SITE, (route) => {
    onOffSite(new URL(route.request().url()).origin);
    return route.abort();
  });
  return context;
}

// Run every check over `dir`. Returns { pages, findings, snapshots } with findings sorted.
export async function checkSite({ dir, snapshotPages, outDir }) {
  const files = existsSync(dir) ? htmlPages(dir) : [];
  const findings = [];
  const snapshots = [];
  if (files.length === 0) {
    findings.push({ key: 'ux.pages.none_built', page: relative(websiteDir, dir) || dir, detail: 'no HTML page found; run pnpm build first' });
    return { pages: 0, findings, snapshots };
  }
  const pageIds = files.map((f) => urlPathFor(dir, f));
  const { server, port } = await serve(dir);
  const base = `http://127.0.0.1:${port}`;
  const browser = await chromium.launch();
  try {
    // Overflow, once per page in the light theme, PARALLEL_PAGES at a time.
    const offlineByPage = new Map();
    const queue = [...pageIds];
    const worker = async () => {
      let current = null;
      const context = await newContext(browser, 'light', (origin) => {
        if (!offlineByPage.has(current)) offlineByPage.set(current, new Set());
        offlineByPage.get(current).add(origin);
      });
      const page = await context.newPage();
      while (queue.length) {
        current = queue.shift();
        await open(page, base + current, null);
        const over = await page.evaluate(measureOverflow);
        if (over) {
          findings.push({
            key: 'ux.pages.horizontal_scroll',
            page: current,
            theme: 'light',
            detail: `the page is ${over.scroll}px wide at ${over.width}px; ${over.element} reaches ${over.right}px. Let it wrap or scroll inside its own container`,
          });
        }
      }
      await context.close();
    };
    await Promise.all(Array.from({ length: Math.min(PARALLEL_PAGES, pageIds.length) }, worker));

    // Axe and snapshots at phone width, both themes, SNAPSHOT_PAGES only. Off-site requests were already
    // reported by the overflow pass, which opened every page.
    for (const pageId of snapshotPages) {
      if (!pageIds.includes(pageId)) {
        findings.push({ key: 'ux.pages.snapshot_missing', page: pageId, detail: 'this snapshot page was not built; build it or update SNAPSHOT_PAGES' });
      }
    }
    const present = snapshotPages.filter((p) => pageIds.includes(p));
    for (const theme of THEMES) {
      const context = await newContext(browser, theme, () => {});
      const page = await context.newPage();
      for (const pageId of present) {
        await open(page, base + pageId, theme);
        const results = await new AxeBuilder({ page }).withTags(WCAG_TAGS).analyze();
        for (const v of results.violations) {
          const targets = v.nodes.map((n) => (Array.isArray(n.target) ? n.target.join(' ') : String(n.target)));
          findings.push({ key: 'ux.pages.axe', page: pageId, theme, detail: `${v.id}: ${v.help} (at ${targets.slice(0, 3).join(', ')})` });
        }
        const name = (pageId.replace(/^\/|\/$/g, '').replace(/[/.]+/g, '_') || 'index') + `.${theme}.png`;
        const file = join(outDir, name);
        await page.screenshot({ path: file, fullPage: true });
        snapshots.push(file);
      }
      await context.close();
    }
    for (const [pageId, origins] of offlineByPage) {
      findings.push({ key: 'ux.pages.offline_request', page: pageId, detail: `asked for ${[...origins].sort().join(', ')}; serve every file from the site itself` });
    }
  } finally {
    await browser.close();
    server.close();
  }
  findings.sort((a, b) => a.key.localeCompare(b.key) || a.page.localeCompare(b.page) || (a.theme || '').localeCompare(b.theme || '') || a.detail.localeCompare(b.detail));
  return { pages: pageIds.length, findings, snapshots: snapshots.sort() };
}

const line = (f) => `${f.key} ${f.page}${f.theme ? ` [${f.theme}]` : ''}: ${f.detail}`;

async function runGate({ dir, out }) {
  const outDir = out ? resolve(out) : mkdtempSync(join(tmpdir(), 'ux-pages-'));
  mkdirSync(outDir, { recursive: true });
  const { pages, findings, snapshots } = await checkSite({ dir, snapshotPages: SNAPSHOT_PAGES, outDir });
  console.log(`UX PAGES: ${pages} pages at ${VIEWPORT.width}x${VIEWPORT.height}; axe (${WCAG_TAGS.join(', ')}) and snapshots over ${SNAPSHOT_PAGES.length} pages x ${THEMES.length} themes.`);
  console.log(`UX PAGES: ${snapshots.length} snapshots in ${outDir}`);
  if (findings.length === 0) {
    console.log('UX PAGES OK: every page fits a phone screen, and the snapshot pages pass axe in both themes.');
    return 0;
  }
  console.error(`UX PAGES FAILED: ${findings.length} finding(s):`);
  for (const f of findings) console.error(`  ${line(f)}`);
  return 1;
}

async function selfTest() {
  const problems = [];
  const a11ySource = readFileSync(join(scriptDir, 'check-a11y.mjs'), 'utf8');
  const m = a11ySource.match(/^const WCAG_TAGS = \[(.*)\];$/m);
  const theirs = m ? [...m[1].matchAll(/'([^']+)'/g)].map((x) => x[1]) : null;
  if (JSON.stringify(theirs) !== JSON.stringify(WCAG_TAGS)) problems.push(`WCAG_TAGS ${JSON.stringify(WCAG_TAGS)} differ from check-a11y.mjs's ${JSON.stringify(theirs)}`);

  const outDir = mkdtempSync(join(tmpdir(), 'ux-pages-selftest-'));
  try {
    const snapshotPages = ['/dark-contrast.html', '/fits.html', '/missing.html'];
    const { pages, findings, snapshots } = await checkSite({ dir: fixturesDir, snapshotPages, outDir });
    for (const f of findings) console.log(`SELF-TEST: ${line(f)}`);
    const has = (key, page, theme, text = '') => findings.some((f) => f.key === key && f.page === page && (theme === undefined || f.theme === theme) && f.detail.includes(text));
    if (pages !== 4) problems.push(`walked ${pages} fixture pages, expected 4`);
    if (!has('ux.pages.horizontal_scroll', '/wide.html', 'light', 'table#wide-table')) problems.push('the 600px table did not fail ux.pages.horizontal_scroll naming table#wide-table');
    if (!has('ux.pages.axe', '/dark-contrast.html', 'dark', 'color-contrast')) problems.push('the dark-only contrast failure was not reported in the dark theme');
    if (has('ux.pages.axe', '/dark-contrast.html', 'light')) problems.push('the dark-only contrast page failed axe in the light theme');
    if (!has('ux.pages.snapshot_missing', '/missing.html')) problems.push('a snapshot page that was not built did not fail ux.pages.snapshot_missing');
    if (!has('ux.pages.offline_request', '/offline.html', undefined, 'fonts.example.invalid')) problems.push('a request to another host did not fail ux.pages.offline_request');
    if (findings.some((f) => f.page === '/fits.html')) problems.push('the page that fits a phone (its wide table scrolls in its own container) was reported');
    if (snapshots.length !== 4) problems.push(`wrote ${snapshots.length} snapshots, expected 4 (two pages x two themes)`);
    const empty = mkdtempSync(join(tmpdir(), 'ux-pages-empty-'));
    const none = await checkSite({ dir: empty, snapshotPages, outDir });
    rmSync(empty, { recursive: true, force: true });
    if (!none.findings.some((f) => f.key === 'ux.pages.none_built')) problems.push('an empty site did not fail ux.pages.none_built');
  } finally {
    rmSync(outDir, { recursive: true, force: true });
  }
  if (problems.length) {
    console.error('SELF-TEST FAILED:');
    for (const p of problems) console.error(`  - ${p}`);
    return 1;
  }
  console.log('SELF-TEST OK: overflow, dark-only contrast, a missing snapshot page, an off-site request and an empty site each fail; the fitting page passes.');
  return 0;
}

let args;
try {
  ({ values: args } = parseArgs({ options: { 'self-test': { type: 'boolean' }, dir: { type: 'string' }, out: { type: 'string' } }, strict: true }));
  for (const name of ['dir', 'out']) if (args[name] === '') throw new Error(`Option '--${name}' is empty`);
  if (args['self-test'] && (args.dir !== undefined || args.out !== undefined)) throw new Error('--self-test takes no other option');
} catch (err) {
  console.error(`ux.pages.usage: ${err.message}; use --self-test, --dir <path> or --out <dir>`);
  process.exit(2);
}
process.exit(args['self-test'] ? await selfTest() : await runGate({ dir: args.dir ? resolve(args.dir) : distDir, out: args.out }));
