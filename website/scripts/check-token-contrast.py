#!/usr/bin/env python3
"""Offline WCAG 1.4.3 contrast check over the design tokens, in both themes.

Reads website/src/styles/tokens.css (the only place the palette is declared), resolves the light theme
(`:root`) and both dark-theme blocks, and requires every text/background pair below to reach 4.5:1. The
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
    ("--text", "--bg"),
    ("--text-muted", "--bg"),
    ("--text-faint", "--bg"),
    ("--accent-strong", "--bg"),
]
LIGHT_BLOCK = re.compile(r"^:root\s*\{(.*?)^\}", re.S | re.M)
DARK_BLOCKS = {
    "dark (auto)": re.compile(r":root:not\(\[data-theme='light'\]\)\s*\{(.*?)\}", re.S),
    "dark (explicit)": re.compile(r"^:root\[data-theme='dark'\]\s*\{(.*?)^\}", re.S | re.M),
}


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


def declarations(block: str) -> dict:
    return dict(re.findall(r"(--[\w-]+)\s*:\s*(#[0-9a-fA-F]{6})\s*;", block))


def failures(css: str) -> list:
    themes = {}
    m = LIGHT_BLOCK.search(css)
    themes["light"] = declarations(m.group(1)) if m else {}
    for name, rx in DARK_BLOCKS.items():
        m = rx.search(css)
        themes[name] = declarations(m.group(1)) if m else {}
    bad = []
    for theme, tokens in themes.items():
        for fg, bg in PAIRS:
            if fg not in tokens or bg not in tokens:
                bad.append(f"{theme}: {fg} or {bg} is not declared as a hex colour")
            elif ratio(tokens[fg], tokens[bg]) < MIN_RATIO:
                bad.append(f"{theme}: {fg} {tokens[fg]} on {bg} {tokens[bg]} = {ratio(tokens[fg], tokens[bg]):.2f}:1 (< {MIN_RATIO}:1)")
    return bad


def run() -> int:
    if not TOKENS.exists():
        print(f"RED: {TOKENS} not found", file=sys.stderr)
        return 1
    bad = failures(TOKENS.read_text(encoding="utf-8"))
    if bad:
        print("RED: " + "; ".join(bad), file=sys.stderr)
        return 1
    print(f"OK: every token pair reaches {MIN_RATIO}:1 in light, dark (auto) and dark (explicit)")
    return 0


def self_test() -> int:
    good = TOKENS.read_text(encoding="utf-8") if TOKENS.exists() else ""
    failed = 0
    cases = [("real tokens pass", good, 0)]
    # The pre-fix light accent (3.75:1 under white text) must be refused, in the light theme only.
    cases.append(("low-contrast light CTA fails", re.sub(r"(^:root\s*\{.*?--accent:\s*)#[0-9a-fA-F]{6}", r"\g<1>#0d9488", good, count=1, flags=re.S | re.M), 1))
    # A dark-theme regression must be refused in the explicit block too.
    cases.append(("low-contrast dark CTA fails", re.sub(r"(^:root\[data-theme='dark'\]\s*\{.*?--accent-contrast:\s*)#[0-9a-fA-F]{6}", r"\g<1>#2dd4bf", good, count=1, flags=re.S | re.M), 1))
    cases.append(("missing block fails", "", 1))
    for label, css, want in cases:
        got = 1 if failures(css) else 0
        if got != want:
            print(f"self-test FAIL: {label} (expected {want}, got {got})", file=sys.stderr)
            failed += 1
    print("self-test OK" if not failed else f"self-test: {failed} failure(s)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(self_test() if "--self-test" in sys.argv else run())
