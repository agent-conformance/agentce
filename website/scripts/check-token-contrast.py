#!/usr/bin/env python3
"""Offline WCAG 1.4.3 contrast check over the design tokens, in both themes.

Reads website/src/styles/tokens.css (the only place the palette is declared), reads the light theme
(`:root`) and the two dark-theme blocks it ships, and requires every text/background pair below to reach 4.5:1. The
primary call-to-action pair (--accent-contrast on --accent) is the one a light-theme regression would
break first. No browser and no network: this is the fast guard beside the axe gate's self-test.

Usage (from the repository root):
  python3 website/scripts/check-token-contrast.py             Check the real tokens; exit 1 on a failure.
  python3 website/scripts/check-token-contrast.py --self-test Prove the check can fail on bad tokens.
"""

import re
import sys
from pathlib import Path

TOKENS = Path("website/src/styles/tokens.css")
MIN_RATIO = 4.5
PAIRS = [
    ("--accent-contrast", "--accent"),
    ("--accent-contrast", "--accent-strong"),
    ("--text", "--bg"),
    ("--text-muted", "--bg"),
    ("--text-faint", "--bg"),
    ("--accent-strong", "--bg"),
]
THEME_BLOCKS = {
    "light": r"^:root\s*\{(.*?)\}",
    "dark (auto)": r"^@media \(prefers-color-scheme: dark\)\s*\{\s*:root:not\(\[data-theme='light'\]\)\s*\{(.*?)\}",
    "dark (explicit)": r"^:root\[data-theme='dark'\]\s*\{(.*?)\}",
}
DECL = re.compile(r"(--[\w-]+)\s*:\s*(#[0-9a-fA-F]{6})\s*;")


def channel(v: int) -> float:
    c = v / 255
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def luminance(hexcolor: str) -> float:
    h = hexcolor.lstrip("#")
    r, g, b = (int(h[i : i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def ratio(a: str, b: str) -> float:
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def failures(css: str) -> list:
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    bad = []
    for theme, pattern in THEME_BLOCKS.items():
        block = re.search(pattern, css, flags=re.DOTALL | re.MULTILINE)
        if block is None:
            bad.append(f"{theme}: theme block not found in {TOKENS}")
            continue
        tokens = dict(DECL.findall(block.group(1)))
        for fg, bg in PAIRS:
            if fg not in tokens or bg not in tokens:
                bad.append(f"{theme}: {fg} or {bg} is not declared as a hex colour")
            elif ratio(tokens[fg], tokens[bg]) < MIN_RATIO:
                bad.append(
                    f"{theme}: {fg} {tokens[fg]} on {bg} {tokens[bg]} = {ratio(tokens[fg], tokens[bg]):.2f}:1 (< {MIN_RATIO}:1)"
                )
    return bad


def run() -> int:
    if not TOKENS.exists():
        print(f"RED: {TOKENS} not found", file=sys.stderr)
        return 1
    bad = failures(TOKENS.read_text(encoding="utf-8"))
    if bad:
        print("RED: " + "; ".join(bad), file=sys.stderr)
        return 1
    print(
        f"OK: every token pair reaches {MIN_RATIO}:1 in light, dark (auto) and dark (explicit)"
    )
    return 0


def self_test() -> int:
    good = TOKENS.read_text(encoding="utf-8") if TOKENS.exists() else ""

    def edit(pattern: str, repl: str) -> str:
        return re.sub(pattern, repl, good, count=1, flags=re.DOTALL | re.MULTILINE)

    cases = [
        ("real tokens pass", good, 0),
        (
            "pre-fix light accent fails",
            edit(r"(^:root\s*\{.*?--accent:\s*)#[0-9a-fA-F]{6}", r"\g<1>#0d9488"),
            1,
        ),
        (
            "dark CTA regression fails",
            edit(
                r"(^:root\[data-theme='dark'\]\s*\{.*?--accent-contrast:\s*)#[0-9a-fA-F]{6}",
                r"\g<1>#2dd4bf",
            ),
            1,
        ),
        (
            "dark faint text regression fails",
            edit(
                r"(^:root\[data-theme='dark'\]\s*\{.*?--text-faint:\s*)#[0-9a-fA-F]{6}",
                r"\g<1>#64748b",
            ),
            1,
        ),
        ("missing blocks fail", "", 1),
    ]
    failed = 0
    for label, css, want in cases:
        got = 1 if failures(css) else 0
        if got != want:
            print(
                f"self-test FAIL: {label} (expected {want}, got {got})", file=sys.stderr
            )
            failed += 1
    print("self-test OK" if not failed else f"self-test: {failed} failure(s)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(self_test() if "--self-test" in sys.argv else run())
