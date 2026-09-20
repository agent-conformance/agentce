// The Install section is gated by the release-state switch, and this check proves the gate.
//
//   node scripts/check-install.mjs                 parity over the committed switch and the Install section's
//                                                  sources, and over the built site when dist/ exists
//   node scripts/check-install.mjs --require-built  the same, and fail when the site has not been built
//   node scripts/check-install.mjs --dist <dir>     check the built pages under <dir> instead of dist/
//                                                  (how the committed known-bad build is shown to be caught)
//   node scripts/check-install.mjs --self-test      prove the gate is real logic and the checks can fail:
//                                                  the resolver flips both ways, and planted bad tabs, bad
//                                                  data, and bad sources are each rejected
//
// It needs no browser and no network, and reads only committed files and the built HTML.
import { existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { TABS, installTabs, resolveTab } from '../src/lib/install.mjs';

const siteRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const readState = () => JSON.parse(readFileSync(join(siteRoot, 'src/data/release-state.json'), 'utf8'));

// Tokens that mark a zero-install one-liner in any text, whatever the channel.
const ZERO_INSTALL = [
  /\b(uvx|pipx|npx)\b/i,
  /\buv\s+tool\s+(install|run)\b/i,
  /\bdocker\s+(run|pull)\b[^\n]*\bghcr\.io\b/i,
  /\bbrew\s+(install|tap)\b/i,
];
const WORKING_TODAY = 'uv run --project engines/python';
const UNFINISHED = /<[^>]+>|\btodo\b|\btbd\b|\blatest\b|\b0\.0\.0\b|\bx\.y\.z\b|placeholder|your-/i;
const DIGEST = /@sha256:[0-9a-f]{64}\b/;
const EXPECTED_IDS = TABS.map((tab) => tab.id);

const zeroInstallTokens = (state, text) => {
  const rxs = [...ZERO_INSTALL];
  for (const entry of Object.values(state.channels)) {
    for (const pattern of entry.patterns) rxs.push(new RegExp(pattern, 'i'));
  }
  return rxs.filter((rx) => rx.test(text)).map((rx) => rx.source);
};

const pinned = (state, command) => command.includes(state.version) || DIGEST.test(command);

// The switch data: a published channel names a concrete, pinned artifact for every gated tab it serves.
export function dataViolations(state) {
  const problems = [];
  for (const tab of TABS) {
    const entry = state.channels[tab.channel];
    if (!entry) {
      problems.push(`${tab.id}: channel ${tab.channel} is missing from the release state`);
      continue;
    }
    if (tab.key === null) {
      if (typeof entry.today !== 'string' || !entry.today.startsWith(WORKING_TODAY)) {
        problems.push(`${tab.id}: the working-today command must be a \`${WORKING_TODAY}\` invocation`);
      }
      continue;
    }
    if (typeof entry.today !== 'string' || entry.today === '') {
      problems.push(`${tab.id}: channel ${tab.channel} has no working-today command to show while unpublished`);
    }
    if (entry.published !== true) continue;
    const oneLiner = entry[tab.key];
    if (typeof oneLiner !== 'string' || oneLiner === '') {
      problems.push(`${tab.id}: channel ${tab.channel} is published but has no ${tab.key}`);
    } else if (UNFINISHED.test(oneLiner)) {
      problems.push(`${tab.id}: published one-liner is unfinished: ${oneLiner}`);
    } else if (!pinned(state, oneLiner)) {
      problems.push(`${tab.id}: published one-liner pins neither version ${state.version} nor an image digest: ${oneLiner}`);
    }
    if (typeof entry.artifact !== 'string' || entry.artifact === '' || UNFINISHED.test(entry.artifact)) {
      problems.push(`${tab.id}: published channel ${tab.channel} names no concrete artifact`);
    }
  }
  return problems;
}

// The tabs a page shows (from the resolver or from built HTML) against what the switch demands.
export function tabViolations(state, shown) {
  const problems = [];
  const ids = shown.map((tab) => tab.id);
  if (ids.join(',') !== EXPECTED_IDS.join(',')) {
    problems.push(`tabs are ${ids.join(', ') || 'absent'}, expected ${EXPECTED_IDS.join(', ')}`);
    return problems;
  }
  for (const tab of shown) {
    const want = resolveTab(state, TABS.find((t) => t.id === tab.id));
    const label = `tab ${tab.id}`;
    if (tab.published !== want.published) {
      problems.push(`${label}: shown as ${tab.published ? 'published' : 'unpublished'} but the switch says ${want.published ? 'published' : 'unpublished'}`);
    }
    if (tab.command !== want.command) {
      problems.push(`${label}: shows \`${tab.command}\`, the switch demands \`${want.command}\``);
    }
    if (!want.published) {
      const leaked = zeroInstallTokens(state, tab.command);
      if (leaked.length) problems.push(`${label}: an unpublished channel shows a zero-install one-liner (${leaked[0]})`);
    } else if (!pinned(state, tab.command) || UNFINISHED.test(tab.command)) {
      problems.push(`${label}: a published channel shows a command with no concrete pinned artifact`);
    }
  }
  return problems;
}

// The Install section's sources: rendered from the switch, never hand-typed. `files` maps a path
// relative to the site root to its text (null when absent).
export function sourceViolations(state, files) {
  const problems = [];
  const need = (path) => {
    if (typeof files[path] !== 'string') problems.push(`${path}: missing`);
    return files[path] ?? '';
  };
  const page = need('src/content/docs/docs/install.mdx');
  const component = need('src/components/InstallTabs.astro');
  const lib = need('src/lib/install.mjs');
  const config = need('astro.config.mjs');
  if (page && !/import\s+InstallTabs\s+from\s+['"][^'"]*components\/InstallTabs\.astro['"]/.test(page)) {
    problems.push('install.mdx does not import the InstallTabs component');
  }
  if (page && !/<InstallTabs\b/.test(page)) problems.push('install.mdx does not render <InstallTabs />');
  if (component && !/from\s+['"][^'"]*data\/release-state\.json['"]/.test(component)) {
    problems.push('InstallTabs.astro does not read the release-state data');
  }
  if (component && !/\binstallTabs\(/.test(component)) problems.push('InstallTabs.astro does not render from installTabs()');
  if (!/slug:\s*['"]docs\/install['"]/.test(config)) problems.push('astro.config.mjs has no sidebar entry for docs/install');
  for (const [path, text] of [['src/content/docs/docs/install.mdx', page], ['src/components/InstallTabs.astro', component]]) {
    text.split('\n').forEach((line, index) => {
      const hit = zeroInstallTokens(state, line);
      if (hit.length) problems.push(`${path}:${index + 1}: a hand-typed one-liner (${hit[0]}); tabs must render from the switch`);
    });
  }
  if (lib) {
    for (const [index, tab] of TABS.entries()) {
      if (!lib.includes(`id: '${tab.id}'`)) problems.push(`src/lib/install.mjs: tab ${tab.id} (${index}) is not declared`);
    }
  }
  return problems;
}

// One pass, so an escaped ampersand is never decoded a second time (`&amp;lt;` stays `&lt;`).
const ENTITIES = new Map([
  ['&#x26;', '&'], ['&amp;', '&'],
  ['&#x3c;', '<'], ['&lt;', '<'],
  ['&#x3e;', '>'], ['&gt;', '>'],
  ['&quot;', '"'], ['&#x22;', '"'],
  ['&#39;', "'"], ['&#x27;', "'"],
]);
const decode = (text) => text.replace(/&(?:#x26|amp|#x3c|lt|#x3e|gt|quot|#x22|#39|#x27);/gi, (entity) => ENTITIES.get(entity.toLowerCase()));

// The tabs in a built page: every <pre data-install-tab=… data-install-state=…><code>…</code></pre>.
export function tabsFromHtml(html) {
  const shown = [];
  const rx = /<pre\b([^>]*\bdata-install-tab="([^"]*)"[^>]*)>\s*<code>([\s\S]*?)<\/code>\s*<\/pre>/g;
  for (const match of html.matchAll(rx)) {
    const stateAttr = /\bdata-install-state="([^"]*)"/.exec(match[1]);
    shown.push({
      id: match[2],
      published: stateAttr ? stateAttr[1] === 'published' : null,
      command: decode(match[3]),
    });
  }
  return shown;
}

const htmlFiles = (dir) => {
  const out = [];
  for (const name of readdirSync(dir).sort()) {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) out.push(...htmlFiles(full));
    else if (name.endsWith('.html')) out.push(full);
  }
  return out;
};

// The built site: the Install page shows exactly what the switch renders, and no built page shows the
// one-liner of an unpublished channel.
export function builtViolations(state, dist) {
  const problems = [];
  const page = join(dist, 'docs/install/index.html');
  if (!existsSync(page)) return [`dist/docs/install/index.html: the Install page was not built`];
  problems.push(...tabViolations(state, tabsFromHtml(readFileSync(page, 'utf8'))).map((p) => `install page: ${p}`));
  for (const file of htmlFiles(dist)) {
    // Inline spans (syntax highlighting splits one command into many) join; every other tag breaks a line.
    const text = decode(readFileSync(file, 'utf8').replace(/<\/?span\b[^>]*>/g, '').replace(/<[^>]*>/g, '\n'));
    text.split('\n').forEach((line) => {
      const unpublished = Object.entries(state.channels).filter(([, entry]) => !entry.published);
      for (const [name, entry] of unpublished) {
        if (entry.patterns.some((pattern) => new RegExp(pattern, 'i').test(line))) {
          problems.push(`${file.slice(dist.length + 1)}: shows a ${name} one-liner but the channel is not published: ${line.trim().slice(0, 80)}`);
        }
      }
    });
  }
  return problems;
}

const committedSources = () => {
  const files = {};
  for (const path of [
    'src/content/docs/docs/install.mdx',
    'src/components/InstallTabs.astro',
    'src/lib/install.mjs',
    'astro.config.mjs',
  ]) {
    const full = join(siteRoot, path);
    files[path] = existsSync(full) ? readFileSync(full, 'utf8') : null;
  }
  return files;
};

// --- self-test -----------------------------------------------------------------------------------

const withPublished = (state, channels) => {
  const next = JSON.parse(JSON.stringify(state));
  for (const [name, entry] of Object.entries(next.channels)) entry.published = channels.includes(name);
  return next;
};

// What a resolver must do for the gate to be real logic: flip both ways, per channel.
function resolverProblems(resolveFn, base) {
  const problems = [];
  const none = withPublished(base, []);
  for (const tab of TABS) {
    const got = resolveFn(none, tab);
    if (got.published !== false || got.command !== none.channels[tab.channel].today) {
      problems.push(`unpublished ${tab.id} does not resolve to the working-today command`);
    }
    if (zeroInstallTokens(none, got.command).length) problems.push(`unpublished ${tab.id} shows a zero-install token`);
  }
  if (!resolveFn(none, TABS[0]).command.startsWith(WORKING_TODAY)) problems.push('the uv tab is not the working-today command');
  for (const channel of ['pypi', 'npm', 'container-registry']) {
    const on = withPublished(base, [channel]);
    for (const tab of TABS) {
      const got = resolveFn(on, tab);
      const gated = tab.channel === channel && tab.key !== null;
      const wantCommand = gated ? on.channels[channel][tab.key] : on.channels[tab.channel].today;
      if (got.published !== gated || got.command !== wantCommand) {
        problems.push(`publishing ${channel} ${gated ? 'does not flip' : 'wrongly flips'} tab ${tab.id}`);
      }
    }
  }
  return problems;
}

export function selfTest(base) {
  const failures = [];
  const expect = (cond, message) => {
    if (!cond) failures.push(message);
  };
  const rejects = (problems, needle, message) =>
    expect(problems.some((p) => p.includes(needle)), `${message}: expected a violation containing \`${needle}\`, got [${problems.join(' | ')}]`);

  // 0. A built page is read as it was written: an escaped ampersand is decoded once, never twice.
  const escaped = '<pre data-install-tab="t" data-install-state="published"><code>a &amp;lt; b &amp;&amp; c</code></pre>';
  expect(tabsFromHtml(escaped)[0]?.command === 'a &lt; b && c', 'the built-page reader decodes an escaped ampersand more than once');

  // 1. The gate is real logic: the resolver flips both ways, and constant resolvers cannot pass.
  expect(resolverProblems(resolveTab, base).length === 0, `the resolver fails its own contract: ${resolverProblems(resolveTab, base).join(' | ')}`);
  const mutants = {
    'always the working command': (state, tab) => ({ ...resolveTab(withPublished(state, []), tab) }),
    'always the one-liner': (state, tab) => ({ ...resolveTab(withPublished(state, Object.keys(state.channels)), tab) }),
    'ignores which channel gates the tab': (state, tab) => {
      const any = Object.values(state.channels).some((entry) => entry.published);
      return resolveTab(withPublished(state, any ? Object.keys(state.channels) : []), tab);
    },
  };
  for (const [name, mutant] of Object.entries(mutants)) {
    expect(resolverProblems(mutant, base).length > 0, `the self-test does not catch a resolver that is ${name}`);
  }

  // 2. Good states pass, in both releases.
  const none = withPublished(base, []);
  const all = withPublished(base, ['pypi', 'npm', 'container-registry']);
  expect(tabViolations(none, installTabs(none)).length === 0, 'the unpublished tabs are rejected');
  expect(tabViolations(all, installTabs(all)).length === 0, `the published tabs are rejected: ${tabViolations(all, installTabs(all)).join(' | ')}`);
  expect(dataViolations(all).length === 0, `the committed data shape is rejected when published: ${dataViolations(all).join(' | ')}`);

  // 3. Bad tabs are rejected for the rule they break.
  const leak = installTabs(none).map((tab) => (tab.id === 'uvx' ? { ...tab, command: base.channels.pypi.one_liner } : tab));
  rejects(tabViolations(none, leak), 'unpublished channel shows a zero-install one-liner', 'an unpublished tab that shows the uvx one-liner');
  const stale = installTabs(all).map((tab) => (tab.id === 'npx' ? { ...tab, command: base.channels.npm.today, published: false } : tab));
  rejects(tabViolations(all, stale), 'the switch says published', 'a published channel that still shows the checkout command');
  const unpinned = withPublished(base, ['pypi']);
  unpinned.channels.pypi.one_liner = 'uvx --from agent-conformance agentce quickstart --out ./out';
  rejects(tabViolations(unpinned, installTabs(unpinned)), 'no concrete pinned artifact', 'a published one-liner with no pinned version');
  rejects(tabViolations(none, installTabs(none).slice(0, 4)), 'tabs are', 'a page with a missing tab');
  rejects(tabViolations(none, [...installTabs(none)].reverse()), 'tabs are', 'a page with the tabs out of order');

  // 4. Bad data is rejected: a published channel must name a concrete, pinned artifact.
  const zeroVersion = withPublished(base, ['pypi']);
  zeroVersion.channels.pypi.one_liner = 'uvx --from agent-conformance==0.0.0 agentce quickstart --out ./out';
  rejects(dataViolations(zeroVersion), 'unfinished', 'a published one-liner with a zero version');
  const missing = withPublished(base, ['npm']);
  missing.channels.npm.one_liner = null;
  rejects(dataViolations(missing), 'has no one_liner', 'a published channel with no one-liner');
  const nameless = withPublished(base, ['container-registry']);
  nameless.channels['container-registry'].artifact = '';
  rejects(dataViolations(nameless), 'names no concrete artifact', 'a published channel with no artifact');
  const noToday = withPublished(base, []);
  noToday.channels.npm.today = null;
  rejects(dataViolations(noToday), 'no working-today command', 'an unpublished channel with no working command');

  // 5. Sources: hand-typed one-liners, a missing component, and a missing sidebar entry are rejected.
  const good = {
    'src/content/docs/docs/install.mdx': "import InstallTabs from '../../../components/InstallTabs.astro';\n\n<InstallTabs />\n",
    'src/components/InstallTabs.astro': "import state from '../data/release-state.json';\nimport { installTabs } from '../lib/install.mjs';\nconst tabs = installTabs(state);\n",
    'src/lib/install.mjs': TABS.map((tab) => `id: '${tab.id}'`).join('\n'),
    'astro.config.mjs': "{ label: 'Install', slug: 'docs/install' }",
  };
  expect(sourceViolations(none, good).length === 0, `good sources are rejected: ${sourceViolations(none, good).join(' | ')}`);
  rejects(
    sourceViolations(none, { ...good, 'src/content/docs/docs/install.mdx': `${good['src/content/docs/docs/install.mdx']}\nuvx --from agent-conformance agentce quickstart\n` }),
    'hand-typed one-liner',
    'a hand-typed uvx line in the page',
  );
  rejects(sourceViolations(none, { ...good, 'src/components/InstallTabs.astro': 'const tabs = [];\n' }), 'release-state data', 'a component that ignores the switch');
  rejects(sourceViolations(none, { ...good, 'astro.config.mjs': '' }), 'sidebar entry', 'a page missing from the sidebar');
  rejects(sourceViolations(none, { ...good, 'src/content/docs/docs/install.mdx': null }), 'install.mdx: missing', 'a missing Install page');

  // 6. Built HTML: what the page renders is compared with the switch, and a leak is caught.
  const renderHtml = (tabs) =>
    tabs
      .map((tab) => `<pre data-install-tab="${tab.id}" data-install-state="${tab.published ? 'published' : 'unpublished'}" tabindex="0"><code>${tab.command.replace(/&/g, '&#x26;')}</code></pre>`)
      .join('\n');
  expect(tabViolations(none, tabsFromHtml(renderHtml(installTabs(none)))).length === 0, 'rendered unpublished HTML is rejected');
  expect(tabViolations(all, tabsFromHtml(renderHtml(installTabs(all)))).length === 0, 'rendered published HTML is rejected');
  rejects(tabViolations(none, tabsFromHtml(renderHtml(installTabs(all)))), 'the switch says unpublished', 'published tabs rendered over an unpublished switch');
  rejects(tabViolations(all, tabsFromHtml(renderHtml(installTabs(none)))), 'the switch says published', 'unpublished tabs rendered over a published switch');
  rejects(tabViolations(none, tabsFromHtml('<pre><code>uvx x</code></pre>')), 'tabs are absent', 'a page whose tabs are not marked as switch-driven');

  // 7. A committed known-bad build (one tab shows a published one-liner over an unpublished switch)
  // is rejected by the same check that guards the real build.
  const leaky = builtViolations(none, join(siteRoot, 'tests/fixtures/install-leaky-dist'));
  rejects(leaky, 'tab uvx: shown as published but the switch says unpublished', 'the committed leaky build');
  rejects(leaky, 'shows a pypi one-liner but the channel is not published', 'the committed leaky build');

  // 8. A leak in a syntax-highlighted code block on another page (one command split across spans) is caught.
  const scratch = mkdtempSync(join(tmpdir(), 'install-check-'));
  try {
    mkdirSync(join(scratch, 'docs/install'), { recursive: true });
    writeFileSync(join(scratch, 'docs/install/index.html'), renderHtml(installTabs(none)));
    writeFileSync(
      join(scratch, 'other.html'),
      '<pre><code><span class="x">uvx</span> <span>--from</span> <span>agent-conformance==0.1.0</span> <span>agentce</span></code></pre>',
    );
    rejects(builtViolations(none, scratch), 'other.html: shows a pypi one-liner', 'a highlighted one-liner on another page');
  } finally {
    rmSync(scratch, { recursive: true, force: true });
  }

  return failures;
}

// --- entry point ---------------------------------------------------------------------------------

const args = process.argv.slice(2);
const state = readState();

if (args.includes('--self-test')) {
  const failures = selfTest(state);
  if (failures.length) {
    console.error('INSTALL CHECK SELF-TEST FAILED:');
    for (const failure of failures) console.error(`  - ${failure}`);
    process.exit(1);
  }
  console.log('INSTALL CHECK SELF-TEST OK - the resolver flips both ways and every planted fault is rejected.');
  process.exit(0);
}

const problems = [...dataViolations(state), ...tabViolations(state, installTabs(state)), ...sourceViolations(state, committedSources())];
const distArg = args.indexOf('--dist');
const dist = distArg >= 0 ? resolve(args[distArg + 1]) : join(siteRoot, 'dist');
if (existsSync(dist)) problems.push(...builtViolations(state, dist));
else if (args.includes('--require-built')) problems.push('dist/ not found - run the build first');

if (problems.length) {
  console.error('INSTALL CHECK FAILED:');
  for (const problem of problems) console.error(`  - ${problem}`);
  process.exit(1);
}
const published = Object.entries(state.channels).filter(([, entry]) => entry.published).map(([name]) => name);
console.log(`INSTALL CHECK OK - ${TABS.length} tabs render from the switch; published channels: ${published.join(', ') || 'none'}.`);
