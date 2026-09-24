#!/usr/bin/env python3
"""Repository check: the landing page carries a clean, copy-paste-able quickstart command.

A "copy-paste quickstart" block is a ``<pre>`` whose first line -- once a leading ``$`` prompt is
stripped -- is a real ``agentce`` invocation (or its install), and that is never mixed with the
decorative terminal's simulated-run narration (checkmarks, an arrow, "Assessing"/"Assessment
complete" prose) -- so the existing illustrative transcript (which does contain a real ``agentce``
invocation, but only interleaved with fabricated output a copy would also grab) cannot itself
satisfy this check.

This file, like ``tools/release_state_check.py`` and ``website/scripts/check-install.mjs``, holds
the zero-install command patterns themselves as detection logic; ``tools/release_state_check.py``
exempts all three for the same reason (the pattern text is a checker's own detector, never a
rendered one-liner).

Usage:
    quickstart_copypaste_check.py <path/to/index.html>
"""

from __future__ import annotations

import html as html_mod
import re
import sys
from pathlib import Path

NOISE = ("assessing", "assessment complete", "✓", "→")
COMMAND_START = re.compile(
    r"^(agentce\b|pipx\s+install\s+agentce\b|pip3?\s+install\s+agentce\b|"
    r"uvx\s+agentce\b|uv\s+run\s+agentce\b|npx\s+agentce\b|npm\s+i(?:nstall)?\s+.*agentce\b)",
    re.I,
)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: quickstart_copypaste_check.py <path/to/index.html>", file=sys.stderr)
        return 3
    path = Path(argv[1])
    if not path.is_file():
        print(f"{path}: not found -- build did not produce it (setup problem)", file=sys.stderr)
        return 3
    doc = path.read_text(encoding="utf-8")

    pre_blocks = re.findall(r"<pre\b[^>]*>(.*?)</pre>", doc, re.I | re.S)

    candidates = []
    for block in pre_blocks:
        text = html_mod.unescape(re.sub(r"<[^>]+>", "", block))
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if not lines:
            continue
        first = re.sub(r"^\$\s*", "", lines[0])
        is_clean = not any(marker in text.lower() for marker in NOISE)
        is_command = bool(COMMAND_START.match(first)) and len(lines) <= 4
        candidates.append((lines[0], is_clean, is_command))
        if is_clean and is_command:
            print(f"GREEN: a copy-paste quickstart command block is present ({lines[0]!r})")
            return 0

    print(
        "RED: no <pre> block is a clean, copy-paste-able quickstart command (a runnable "
        "'agentce ...'/install invocation with no simulated-run narration mixed in):",
        file=sys.stderr,
    )
    for first_line, is_clean, is_command in candidates:
        print(f"  block starting {first_line!r}: clean={is_clean} command-like={is_command}", file=sys.stderr)
    if not candidates:
        print("  (no <pre> block found at all)", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
