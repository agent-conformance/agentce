// Built-site proof that the published Reference and Explanation pages (website/src/content/docs/
// {reference,explanation}/) are not just present as source files but actually ship and are reachable
// from the built site's own navigation, not merely built as orphan routes.
//
// Complements the source-level check in docs/build.py (--check-publish, which can only see that the
// files exist and match a fresh generation) by proving the build actually turned every one of them
// into a linked page: every source page under reference/ or explanation/ has a corresponding built
// `index.html` (the site built without error), and its route is linked as an `href` from at least one
// other built page (Starlight's sidebar autogenerate put it in the navigation).
//
// Usage:
//   node scripts/check-docs-reference.mjs              Scan dist/ against the real content sources.
//   node scripts/check-docs-reference.mjs --dir <path> --content-dir <path>   Scan alternate roots (used by --self-test).
//   node scripts/check-docs-reference.mjs --self-test   Prove the gate can fail: a well-formed fixture
//                                                        (page built and linked) must pass; a fixture
//                                                        missing the built page, and one missing the nav
//                                                        link, must each fail.
import { existsSync, mkdtempSync, mkdirSync, writeFileSync, rmSync, readFileSync, readdirSync, statSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

const scriptDir = dirname(fileURLToPath(import.meta.url));
const websiteDir = resolve(scriptDir, '..');
const DEFAULT_DIST = resolve(websiteDir, 'dist');
const DEFAULT_CONTENT = resolve(websiteDir, 'src', 'content', 'docs');

function walk(dir) {
  const out = [];
  for (const name of readdirSync(dir)) {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) out.push(...walk(full));
    else out.push(full);
  }
  return out;
}

// Every published page's route slug: the path under reference/ or explanation/, minus the extension,
// lowercased (Starlight lowercases every route slug regardless of the source file's own case), with a
// bare `index` collapsing to its parent directory.
function collectSlugs(contentDir, category) {
  const root = join(contentDir, category);
  if (!existsSync(root)) return [];
  const slugs = [];
  for (const file of walk(root)) {
    if (!/\.mdx?$/.test(file)) continue;
    const rel = file.slice(root.length + 1).replace(/\.mdx?$/, '');
    const parts = rel.split(sep).filter(Boolean);
    if (parts[parts.length - 1] === 'index') parts.pop();
    const slug = [category, ...parts].join('/').toLowerCase();
    slugs.push(slug);
  }
  return slugs;
}

function check(distDir, contentDir) {
  const problems = [];
  if (!existsSync(distDir)) return [`${distDir} not found — run the build first (pnpm build).`];
  if (!existsSync(contentDir)) return [`${contentDir} not found.`];

  const slugs = [...collectSlugs(contentDir, 'reference'), ...collectSlugs(contentDir, 'explanation')];
  if (slugs.length === 0) problems.push('no reference/ or explanation/ content found to check');

  const htmlFiles = walk(distDir).filter((f) => f.endsWith('.html'));
  const htmlText = htmlFiles.map((f) => readFileSync(f, 'utf8'));

  for (const slug of slugs) {
    const builtPath = join(distDir, ...slug.split('/'), 'index.html');
    if (!existsSync(builtPath)) {
      problems.push(`missing built page for ${slug} (expected dist/${slug}/index.html)`);
      continue;
    }
    const href = `/${slug}/`;
    const linked = htmlText.some((html) => html.includes(`href="${href}"`));
    if (!linked) problems.push(`${slug} has no incoming href="${href}" from any built page (not in the site navigation)`);
  }
  return problems;
}

function selfTest() {
  const root = mkdtempSync(join(tmpdir(), 'docs-reference-check-'));
  try {
    // Good fixture: a reference page that is both built and linked from another page.
    const goodContent = join(root, 'good-content');
    const goodDist = join(root, 'good-dist');
    mkdirSync(join(goodContent, 'reference', 'widgets'), { recursive: true });
    writeFileSync(join(goodContent, 'reference', 'widgets', 'gizmo.md'), '---\ntitle: Gizmo\n---\nBody.\n');
    mkdirSync(join(goodDist, 'reference', 'widgets', 'gizmo'), { recursive: true });
    writeFileSync(join(goodDist, 'reference', 'widgets', 'gizmo', 'index.html'), '<html><body><h1>Gizmo</h1></body></html>');
    mkdirSync(join(goodDist, 'nav-page'), { recursive: true });
    writeFileSync(
      join(goodDist, 'nav-page', 'index.html'),
      '<html><body><a href="/reference/widgets/gizmo/">Gizmo</a></body></html>'
    );
    const goodProblems = check(goodDist, goodContent);
    if (goodProblems.length !== 0) {
      console.error('SELF-TEST FAILED: expected the good fixture to pass, got:', goodProblems);
      return 1;
    }

    // Bad fixture 1: the page was never built (a build error swallowed it).
    const missingBuildDist = join(root, 'missing-build-dist');
    mkdirSync(join(missingBuildDist, 'nav-page'), { recursive: true });
    writeFileSync(
      join(missingBuildDist, 'nav-page', 'index.html'),
      '<html><body><a href="/reference/widgets/gizmo/">Gizmo</a></body></html>'
    );
    const missingBuildProblems = check(missingBuildDist, goodContent);
    if (!missingBuildProblems.some((p) => p.includes('missing built page'))) {
      console.error('SELF-TEST FAILED: expected a missing-built-page failure, got:', missingBuildProblems);
      return 1;
    }

    // Bad fixture 2: the page built, but nothing links to it (an orphan route, not in the navigation).
    const orphanDist = join(root, 'orphan-dist');
    mkdirSync(join(orphanDist, 'reference', 'widgets', 'gizmo'), { recursive: true });
    writeFileSync(join(orphanDist, 'reference', 'widgets', 'gizmo', 'index.html'), '<html><body><h1>Gizmo</h1></body></html>');
    const orphanProblems = check(orphanDist, goodContent);
    if (!orphanProblems.some((p) => p.includes('no incoming href'))) {
      console.error('SELF-TEST FAILED: expected an orphan-route failure, got:', orphanProblems);
      return 1;
    }

    console.log('SELF-TEST OK — the gate passes a linked, built page and fails a missing or orphaned one.');
    return 0;
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
}

function main() {
  const args = process.argv.slice(2);
  if (args.includes('--self-test')) return selfTest();
  const dirIdx = args.indexOf('--dir');
  const contentIdx = args.indexOf('--content-dir');
  const distDir = dirIdx >= 0 ? resolve(args[dirIdx + 1]) : DEFAULT_DIST;
  const contentDir = contentIdx >= 0 ? resolve(args[contentIdx + 1]) : DEFAULT_CONTENT;
  const problems = check(distDir, contentDir);
  if (problems.length) {
    console.error('DOCS REFERENCE CHECK FAILED:');
    for (const p of problems) console.error(`  - ${p}`);
    return 1;
  }
  console.log('DOCS REFERENCE CHECK OK — every published reference/explanation page is built and linked from the site navigation.');
  return 0;
}

process.exit(main());
