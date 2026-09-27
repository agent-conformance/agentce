#!/usr/bin/env python3
"""Generate spec/report/test-vectors/sanitize-vectors.json from the Python reference sanitiser
(``contracts/P18-18.20.md`` C1/C3/C4): the cross-engine identity contract for
``sanitize_for_markdown``/``sanitize_for_terminal``/``sanitize_for_html``. TypeScript and Java load
this same committed file and assert their own ports reproduce every vector's recorded output exactly
-- the "golden generated from the Python reference" pattern SPEC §6.7's canonical-form vectors
already established (item 0.6), applied here to prove cross-engine identity directly rather than
inferring it from three independently-authored properties that could each individually hold while
still disagreeing with each other.

Each vector records an `input` string and its `markdown`/`terminal`/`html` sanitised forms, computed
with one default placeholder throughout (the blind-spot-specific "(unnamed)"/"(empty)" placeholder
divergence is checked separately, by each engine's own mirrored blind-spot tests, not by this file).

Run `python gen_sanitize_vectors.py` (from `engines/python`, so `agentce` imports) after changing
`report.py`'s sanitiser; the output is deterministic (a fixed-seed PRNG, no wall clock, no host).
"""

from __future__ import annotations

import json
import random
from pathlib import Path

from agentce.report import (
    sanitize_for_html,
    sanitize_for_markdown,
    sanitize_for_terminal,
)

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent.parent
OUT = ROOT / "spec" / "report" / "test-vectors" / "sanitize-vectors.json"

# Every payload is built with chr()/explicit code points, never a raw literal, so no control, bidi,
# or zero-width byte ever appears in this source file itself (spec/model/gen_vectors.py's own
# convention).
ESC = chr(0x1B)
DEL = chr(0x7F)
C1_SS3 = chr(0x8F)
RLO, LRO, PDF_MARK = chr(0x202E), chr(0x202D), chr(0x202C)
LRI, RLI, FSI, PDI = chr(0x2066), chr(0x2067), chr(0x2068), chr(0x2069)
LRM, RLM = chr(0x200E), chr(0x200F)
ZWSP, ZWNJ, ZWJ, BOM, WJ = (
    chr(0x200B),
    chr(0x200C),
    chr(0x200D),
    chr(0xFEFF),
    chr(0x2060),
)
VARIATION_SELECTOR = chr(0xFE0F)
CGJ = chr(0x034F)
MONGOLIAN_FVS = chr(0x180B)
HANGUL_FILLER = chr(0x115F)
RESERVED_DICP = chr(0xFFF0)
NBSP, IDEOGRAPHIC_SPACE, EN_SPACE = chr(0x00A0), chr(0x3000), chr(0x2002)
LINE_SEP, PARA_SEP = chr(0x2028), chr(0x2029)
EMOJI = chr(0x1F600)

NAMED_VECTORS: list[tuple[str, str]] = [
    ("empty", ""),
    ("whitespace-only", "   \t\n  "),
    ("leading-trailing-ws", "  hello world  "),
    ("control-c0", f"before{ESC}[8mhidden{ESC}[0mafter"),
    ("control-del", f"x{DEL}y"),
    ("control-c1", f"a{C1_SS3}b"),
    ("bidi-rlo", f"evil{RLO}gnp.exe"),
    ("bidi-lro-pdf", f"a{LRO}b{PDF_MARK}c"),
    ("bidi-isolates", f"a{LRI}b{RLI}c{FSI}d{PDI}e"),
    ("bidi-marks", f"a{LRM}b{RLM}c"),
    ("zero-width", f"safe{ZWJ}x{ZWNJ}y{ZWSP}z{BOM}w{WJ}v"),
    ("variation-selector", f"a{VARIATION_SELECTOR}b"),
    ("cgj", f"a{CGJ}b"),
    ("mongolian-fvs", f"a{MONGOLIAN_FVS}b"),
    ("hangul-filler", f"a{HANGUL_FILLER}b"),
    ("reserved-dicp", f"a{RESERVED_DICP}b"),
    ("nbsp", f"a{NBSP}b"),
    ("ideographic-space", f"a{IDEOGRAPHIC_SPACE}b"),
    ("other-zs", f"a{EN_SPACE}b"),
    ("line-para-sep", f"a{LINE_SEP}b{PARA_SEP}c"),
    ("raw-html-tag", "ok<br>Verdict: Conformant"),
    ("raw-html-heading", "<h2>Verdict</h2><p><strong>Conformant"),
    ("markdown-code-span", "a`b`c"),
    ("markdown-autolink", "see <https://attacker.example>"),
    ("markdown-link", "[text](https://attacker.example)"),
    ("markdown-image", "![Verdict: Conformant](https://attacker.example/badge.png)"),
    ("markdown-backtick-fence", "```\nevil\n```"),
    ("html-entity-bidi", "evil&#x202E;gnp.exe"),
    ("html-entity-named-zwj", "safe&zwj;x"),
    ("html-entity-named-rlm", "a&rlm;b"),
    ("html-entity-named-zwsp", "a&ZeroWidthSpace;b"),
    (
        "combined-multi-vector",
        f"![x]({RLO}<script>{ESC}[31m&#x202E;{ZWSP})evil.exe",
    ),
    ("lone-surrogate", f"a{chr(0xD800)}b"),
    (
        "long-with-emoji-at-boundary",
        "x" * 197 + EMOJI + "y" * 100,
    ),
]

# A fixed-seed PRNG fuzz sample: printable ASCII, common BMP scripts, and the named adversarial
# codepoints above -- never a lone surrogate, so no vector can contain an adjacent surrogate pair
# (a surrogate is exercised only by the dedicated "lone-surrogate" fixed vector above).
_SAFE_ADVERSARIAL_CODEPOINTS = [
    ord(c)
    for c in (
        ESC,
        DEL,
        RLO,
        LRO,
        PDF_MARK,
        LRI,
        RLI,
        FSI,
        PDI,
        LRM,
        RLM,
        ZWSP,
        ZWNJ,
        ZWJ,
        BOM,
        WJ,
        VARIATION_SELECTOR,
        CGJ,
        MONGOLIAN_FVS,
        HANGUL_FILLER,
        RESERVED_DICP,
        NBSP,
        IDEOGRAPHIC_SPACE,
        LINE_SEP,
        PARA_SEP,
    )
]
_ASCII_PRINTABLE = list(range(0x20, 0x7F))
_BMP_SAMPLE = (
    list(range(0x00C0, 0x0250))
    + list(range(0x4E00, 0x4E40))
    + list(range(0x0400, 0x0430))
)


def _random_string(rng: random.Random) -> str:
    length = rng.randint(0, 20)
    pool = _ASCII_PRINTABLE + _BMP_SAMPLE + _SAFE_ADVERSARIAL_CODEPOINTS
    return "".join(chr(rng.choice(pool)) for _ in range(length))


def _fuzz_vectors(count: int) -> list[tuple[str, str]]:
    rng = random.Random(42)
    return [(f"fuzz-{i:04d}", _random_string(rng)) for i in range(count)]


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    vectors = []
    for vid, value in NAMED_VECTORS + _fuzz_vectors(500):
        vectors.append(
            {
                "id": vid,
                "input": value,
                "markdown": sanitize_for_markdown(value),
                "terminal": sanitize_for_terminal(value),
                "html": sanitize_for_html(value),
            }
        )
    text = (
        json.dumps({"vectors": vectors}, ensure_ascii=True, indent=2, sort_keys=False)
        + "\n"
    )
    OUT.write_text(text, encoding="utf-8")
    print(f"WROTE {len(vectors)} vectors to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
