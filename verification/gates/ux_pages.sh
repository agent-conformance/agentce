#!/usr/bin/env bash
# Build gate helper for VG-UX-PAGES (USER_EXPERIENCE.md R7, R10): the site builds, and every built page
# fits a 390px phone screen with no sideways scroll, the snapshot pages pass axe (WCAG 2.2 AA) at that
# width in light and dark, and their full-page snapshots are written outside the working tree
# ($UX_PAGES_OUT when set, else a new temp directory the gate prints).
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$root/website"
pnpm install --frozen-lockfile >/dev/null
if [ -n "${CI:-}" ] && [ "$(uname -s)" = "Linux" ]; then
  pnpm exec playwright install --with-deps chromium >/dev/null
else
  pnpm exec playwright install chromium >/dev/null
fi
node scripts/check-ux-pages.mjs --self-test
pnpm build >/dev/null
node scripts/check-ux-pages.mjs ${UX_PAGES_OUT:+--out "$UX_PAGES_OUT"}
