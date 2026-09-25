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
    ("--accent-contrast", "--accent-strong"),
    ("--text", "--bg"),
    ("--text-muted", "--bg"),
    ("--text-faint", "--bg"),
    ("--accent-strong", "--bg"),
]
TRACKED = {t for pair in PAIRS for t in pair}
THEME_SELECTORS = {
    ":root": "light",
    ":root:not([data-theme='light'])": "dark (auto)",
    ":root[data-theme='dark']": "dark (explicit)",
}
RULE = re.compile(r"([^{}]+)\{([^{}]*)\}")
DECL = re.compile(r"(--[\w-]+)\s*:\s*([^;}]+?)\s*(?:;|$)")
HEX = re.compile(r"#[0-9a-fA-F]{6}")


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


def resolve(css: str):
    """Return ({theme: {token: hex}}, [problems]) applying every theme rule in source order, so a later
    override wins exactly as it would in the browser. Comments are ignored; a tracked token declared as
    anything but a six-digit hex colour is a problem, because its contrast cannot be computed here."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    themes = {name: {} for name in THEME_SELECTORS.values()}
    problems = []
    for selector, body in RULE.findall(css):
        theme = THEME_SELECTORS.get(re.sub(r"\s+", "", selector.split(";")[-1]).replace('"', "'"))
        if theme is None:
            continue
        for token, value in DECL.findall(body):
            if token not in TRACKED:
                continue
            if HEX.fullmatch(value):
                themes[theme][token] = value
            else:
                problems.append(f"{theme}: {token} is declared as {value!r}, not a six-digit hex colour")
    return themes, problems


def failures(css: str) -> list:
    themes, bad = resolve(css)
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

    def edit(pattern: str, repl: str) -> str:
        return re.sub(pattern, repl, good, count=1, flags=re.DOTALL | re.MULTILINE)

    cases = [
        ("real tokens pass", good, 0),
        ("pre-fix light accent fails", edit(r"(^:root\s*\{.*?--accent:\s*)#[0-9a-fA-F]{6}", r"\g<1>#0d9488"), 1),
        ("dark CTA regression fails", edit(r"(^:root\[data-theme='dark'\]\s*\{.*?--accent-contrast:\s*)#[0-9a-fA-F]{6}", r"\g<1>#2dd4bf"), 1),
        ("CTA hover regression fails", edit(r"(^:root\[data-theme='dark'\]\s*\{.*?--accent-strong:\s*)#[0-9a-fA-F]{6}", r"\g<1>#0d9488"), 1),
        ("a later override block wins and fails", good + "\n:root { --accent: #0d9488; }\n", 1),
        ("a commented-out declaration is ignored", good + "\n/* :root { --accent: #ffffff; } */\n", 0),
        ("a non-hex redeclaration is refused", good + "\n:root { --accent: var(--other); }\n", 1),
        ("missing blocks fail", "", 1),
    ]
    failed = 0
    for label, css, want in cases:
        got = 1 if failures(css) else 0
        if got != want:
            print(f"self-test FAIL: {label} (expected {want}, got {got})", file=sys.stderr)
            failed += 1
    print("self-test OK" if not failed else f"self-test: {failed} failure(s)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(self_test() if "--self-test" in sys.argv else run())
