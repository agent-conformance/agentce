#!/usr/bin/env python3
"""spdx_check - verify the repository's licensing baseline (SPDX Apache-2.0).

Checks that the project licence is present and consistent, and that no tracked file carries an SPDX
licence identifier that conflicts with Apache-2.0 (for example a copyleft file vendored by accident):

  * LICENSE exists and is the Apache License 2.0;
  * NOTICE exists and names Apache-2.0;
  * no tracked file declares an `SPDX-License-Identifier` for a copyleft licence (GPL/AGPL/LGPL) or any
    licence other than Apache-2.0.

Prints "SPDX OK" on success. Uses only the standard library and git; no network, no learned component.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

SPDX_RE = re.compile(r"SPDX-License-Identifier:\s*([A-Za-z0-9.\-+ ()]+)")
ALLOWED = {"Apache-2.0"}
# Substrings that mark a licence identifier as incompatible with an Apache-2.0 distribution.
COPYLEFT = ("GPL", "AGPL", "LGPL", "MPL", "EUPL", "CDDL")
# Binary or vendored trees whose contents are not our source headers.
SKIP_PREFIXES = ("LICENSE", "NOTICE")


def fail(msg: str) -> int:
    print(f"SPDX FAILED: {msg}")
    return 1


def tracked_files() -> list[str]:
    out = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files"], capture_output=True, text=True, check=True
    ).stdout
    return [line for line in out.splitlines() if line]


def main() -> int:
    license_path = ROOT / "LICENSE"
    if not license_path.exists():
        return fail("LICENSE is missing")
    license_text = license_path.read_text(encoding="utf-8", errors="replace")
    if "Apache License" not in license_text or "2.0" not in license_text:
        return fail("LICENSE is not the Apache License 2.0")

    notice_path = ROOT / "NOTICE"
    if not notice_path.exists():
        return fail("NOTICE is missing")
    if "Apache-2.0" not in notice_path.read_text(encoding="utf-8", errors="replace"):
        return fail("NOTICE does not name Apache-2.0")

    conflicts: list[str] = []
    for rel in tracked_files():
        if rel.startswith(SKIP_PREFIXES):
            continue
        path = ROOT / rel
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue  # binary or unreadable: no SPDX header to check
        for match in SPDX_RE.finditer(text):
            ident = match.group(1).strip()
            if any(c in ident.upper() for c in COPYLEFT) or ident not in ALLOWED:
                conflicts.append(f"{rel}: SPDX-License-Identifier {ident}")

    if conflicts:
        print("SPDX FAILED: conflicting licence identifiers:")
        for c in conflicts:
            print(f"  {c}")
        return 1

    print("SPDX OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
