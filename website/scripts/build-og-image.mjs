// Render the social-preview (Open Graph / Twitter) card into the built site.
//
// Runs after `astro build`, alongside build-iris.mjs. The card reuses the site's own favicon mark
// (the "certification-neutral mark" Appendix A4 calls for) rather than an abstract illustration, and
// the same headline the hero uses, so the card a reader sees before they ever click through is
// consistent with the page it links to. Rendered deterministically from the committed SVG with sharp
// (already a website dependency) — no external image service, no network.
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import sharp from 'sharp';

const scriptDir = dirname(fileURLToPath(import.meta.url));
const websiteDir = resolve(scriptDir, '..');
const dist = join(websiteDir, 'dist');

const WIDTH = 1200;
const HEIGHT = 630;
const MARK_SIZE = 168;

const favicon = readFileSync(join(websiteDir, 'public', 'favicon.svg'), 'utf8');
// Pull the mark's own path geometry out of favicon.svg so the OG card draws the identical glyph
// rather than a second, hand-maintained copy of it.
const markPath = favicon.match(/<path[^>]*d="([^"]+)"[^>]*>/)[1];

const svg = `
<svg xmlns="http://www.w3.org/2000/svg" width="${WIDTH}" height="${HEIGHT}" viewBox="0 0 ${WIDTH} ${HEIGHT}">
  <rect width="${WIDTH}" height="${HEIGHT}" fill="#0b1120" />
  <g transform="translate(96 96)">
    <rect width="${MARK_SIZE}" height="${MARK_SIZE}" rx="${MARK_SIZE * 0.22}" fill="#0d9488" />
    <g transform="scale(${MARK_SIZE / 32})">
      <path d="${markPath}" fill="none" stroke="#ffffff" stroke-width="3.2" stroke-linecap="round" stroke-linejoin="round" />
    </g>
  </g>
  <text x="96" y="336" font-family="Space Grotesk, ui-sans-serif, sans-serif" font-size="64" font-weight="700" fill="#e2e8f0">Agent Conformance</text>
  <text x="96" y="404" font-family="Inter, ui-sans-serif, sans-serif" font-size="34" fill="#94a3b8">Assess any AI agent against the same standard.</text>
  <text x="96" y="546" font-family="Inter, ui-sans-serif, sans-serif" font-size="26" fill="#5eead4">agent-conformance.org</text>
</svg>
`.trim();

mkdirSync(dist, { recursive: true });
const outPath = join(dist, 'og-card.png');
await sharp(Buffer.from(svg)).png().toFile(outPath);

console.log(`build-og-image: wrote ${outPath} (${WIDTH}x${HEIGHT})`);
