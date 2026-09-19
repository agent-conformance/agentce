import { readdirSync, statSync, readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

// Astro integration: after the static build, add tabindex="0" to every <pre> that does not already
// declare one, so horizontally scrollable code blocks are reachable and scrollable with the keyboard
// alone (WCAG 2.1.1 Keyboard; axe reports the gap as `scrollable-region-focusable`). It runs over the
// rendered HTML in astro:build:done, so it is renderer-agnostic — it covers Expressive Code, Shiki, and
// hand-written <pre> the same way — deterministic, and needs no extra dependency. The axe gate in
// scripts/check-a11y.mjs enforces that no such violation returns.
export default function focusablePre() {
  return {
    name: 'a11y-focusable-pre',
    hooks: {
      'astro:build:done': ({ dir }) => {
        const root = fileURLToPath(dir);
        const walk = (d) => {
          for (const name of readdirSync(d).sort()) {
            const full = join(d, name);
            if (statSync(full).isDirectory()) walk(full);
            else if (name.endsWith('.html')) {
              const html = readFileSync(full, 'utf8');
              const next = html.replace(/<pre(?![^>]*\btabindex=)/gi, '<pre tabindex="0"');
              if (next !== html) writeFileSync(full, next);
            }
          }
        };
        walk(root);
      },
    },
  };
}
