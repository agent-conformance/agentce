// DOM-level proof that the landing page's theme control (BaseLayout.astro) actually changes
// `data-theme` and persists the choice — via the real `starlight-theme` localStorage key — across a
// navigation, not just that a `<select>` element is present in the markup. Complements the
// structural check on BaseLayout.astro's source (which can only see that a control exists) by
// driving it with a real browser.
//
// Usage:
//   node scripts/check-theme-persistence.mjs               Drive the built dist/ site; exit 1 if the
//                                                            control doesn't change or persist the theme.
//   node scripts/check-theme-persistence.mjs --dir <path> [--next <path>]
//                                                            Drive <path> instead of dist/ (used to point
//                                                            the gate at the committed disconnected-control
//                                                            fixture, proving it fails on it).
//   node scripts/check-theme-persistence.mjs --self-test    Prove the gate can fail: serve the committed
//                                                            good fixture (control wired, must pass) and
//                                                            the bad fixture (control present but
//                                                            disconnected, must fail).
import { createServer } from 'node:http';
import { readFileSync, existsSync, statSync } from 'node:fs';
import { dirname, join, resolve, extname, relative, isAbsolute } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const scriptDir = dirname(fileURLToPath(import.meta.url));
const websiteDir = resolve(scriptDir, '..');
const distDir = resolve(websiteDir, 'dist');
const fixturesDir = resolve(websiteDir, 'tests', 'fixtures', 'theme-control');

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.mjs': 'text/javascript; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.woff2': 'font/woff2',
};

// Confines every request to `root` (path.relative → no leading `..`, not absolute) before any
// filesystem call, the same containment guard check-a11y.mjs uses.
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
    const file = resolveWithin(root, req.url);
    if (file === null || !existsSync(file)) {
      res.writeHead(404);
      res.end('not found');
      return;
    }
    res.writeHead(200, { 'content-type': MIME[extname(file)] || 'application/octet-stream' });
    res.end(readFileSync(file));
  });
  return new Promise((res) => {
    server.listen(0, '127.0.0.1', () => res({ server, port: server.address().port }));
  });
}

// Drive one origin: load `startPath`, select each theme in turn on `select#theme-select`, assert
// `data-theme`/localStorage change and survive a reload, then navigate to `nextPath` (if given) and
// assert the theme is still applied there — the actual persistence-across-navigation proof.
async function drive(base, startPath, nextPath) {
  const browser = await chromium.launch();
  const problems = [];
  try {
    const context = await browser.newContext({ colorScheme: 'dark' });
    const page = await context.newPage();
    await page.goto(base + startPath, { waitUntil: 'load' });

    const select = page.locator('#theme-select');
    if ((await select.count()) === 0) {
      problems.push(`${startPath}: no #theme-select control found`);
      return problems;
    }

    for (const theme of ['dark', 'light']) {
      await select.selectOption(theme);
      const applied = await page.evaluate(() => document.documentElement.dataset.theme);
      const stored = await page.evaluate(() => localStorage.getItem('starlight-theme'));
      if (applied !== theme) problems.push(`${startPath}: selecting "${theme}" left data-theme="${applied}"`);
      if (stored !== theme) problems.push(`${startPath}: selecting "${theme}" left localStorage starlight-theme="${stored}"`);

      // Persists across a reload of the same page.
      await page.reload({ waitUntil: 'load' });
      const afterReload = await page.evaluate(() => document.documentElement.dataset.theme);
      if (afterReload !== theme) problems.push(`${startPath}: data-theme="${afterReload}" after reload, expected "${theme}" to persist`);

      // Persists across a navigation to a different page on the same origin.
      if (nextPath) {
        await page.goto(base + nextPath, { waitUntil: 'load' });
        const onNext = await page.evaluate(() => document.documentElement.dataset.theme);
        if (onNext !== theme) problems.push(`${nextPath}: data-theme="${onNext}" after navigating from a "${theme}" selection, expected "${theme}" to persist`);
        await page.goto(base + startPath, { waitUntil: 'load' });
      }
    }

    // "auto" clears the stored choice and falls back to the system preference (dark, per this context).
    await select.selectOption('auto');
    const storedAfterAuto = await page.evaluate(() => localStorage.getItem('starlight-theme'));
    const appliedAfterAuto = await page.evaluate(() => document.documentElement.dataset.theme);
    if (storedAfterAuto) problems.push(`${startPath}: selecting "auto" left localStorage starlight-theme="${storedAfterAuto}", expected cleared`);
    if (appliedAfterAuto !== 'dark') problems.push(`${startPath}: selecting "auto" left data-theme="${appliedAfterAuto}", expected the system preference "dark"`);

    await context.close();
  } finally {
    await browser.close();
  }
  return problems;
}

async function runGate({ dir, next } = {}) {
  const root = dir ? resolve(dir) : distDir;
  if (!existsSync(root)) {
    console.error(`I18N THEME-PERSISTENCE CHECK FAILED: ${root} not found${dir ? '' : ' — run the build first (pnpm build)'}.`);
    process.exit(1);
  }
  const { server, port } = await serve(root);
  try {
    const problems = await drive(`http://127.0.0.1:${port}`, '/', dir ? next : '/docs/getting-started/');
    if (problems.length === 0) {
      console.log('I18N THEME-PERSISTENCE CHECK OK — the landing-page control changes and persists the theme across reload and navigation.');
      process.exit(0);
    }
    console.error('I18N THEME-PERSISTENCE CHECK FAILED:');
    for (const p of problems) console.error(`  - ${p}`);
    process.exit(1);
  } finally {
    server.close();
  }
}

async function selfTest() {
  const failures = [];

  const good = await serve(join(fixturesDir, 'good'));
  try {
    const problems = await drive(`http://127.0.0.1:${good.port}`, '/', '/page2.html');
    if (problems.length !== 0) failures.push(`good fixture: expected no problems, got ${JSON.stringify(problems)}`);
  } finally {
    good.server.close();
  }

  const bad = await serve(join(fixturesDir, 'bad'));
  try {
    const problems = await drive(`http://127.0.0.1:${bad.port}`, '/', undefined);
    if (problems.length === 0) failures.push('bad fixture (disconnected control): expected problems, got none — the gate is toothless');
  } finally {
    bad.server.close();
  }

  if (failures.length) {
    console.error('SELF-TEST FAILED:');
    for (const f of failures) console.error(`  - ${f}`);
    process.exit(1);
  }
  console.log('SELF-TEST OK — the gate passes the wired control and catches the disconnected one.');
  process.exit(0);
}

const argv = process.argv.slice(2);
if (argv.includes('--self-test')) {
  await selfTest();
} else {
  const dirIdx = argv.indexOf('--dir');
  const nextIdx = argv.indexOf('--next');
  await runGate({
    dir: dirIdx >= 0 ? argv[dirIdx + 1] : undefined,
    next: nextIdx >= 0 ? argv[nextIdx + 1] : undefined,
  });
}
