"""Verify the techniques library covers every control for every supported framework (SPEC §7.6).

Every control in the base catalog, the overlays, and the organisation template MUST carry at least
one technique per supported implementation style, or an explicit "not applicable" note (a manual
control has no code technique). This is item 4.4's acceptance: it prints ``TECHNIQUES OK`` and exits
0 when the library is complete, otherwise it lists the gaps and exits 1.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parents[1]

#: The six implementation styles the corpus and examples cover (SPEC §11.2, §13.2).
STYLES = (
    "langgraph",
    "openai-agents",
    "claude-agent-sdk",
    "google-adk",
    "crewai",
    "custom-loop",
)

_CATALOG_GLOBS = (
    "spec/catalogs/base/eu-ai-act/controls/*.yaml",
    "spec/catalogs/overlays/*/controls/*.yaml",
    "spec/catalogs/org-template/controls/*.yaml",
)


def control_ids() -> list[str]:
    ids: list[str] = []
    for pattern in _CATALOG_GLOBS:
        for path in sorted(_REPO.glob(pattern)):
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("id"), str):
                ids.append(data["id"])
    return sorted(set(ids))


def gaps() -> list[str]:
    problems: list[str] = []
    for cid in control_ids():
        for style in STYLES:
            path = _HERE / cid / f"{style}.md"
            if not path.is_file():
                problems.append(f"{cid}: missing technique for style {style}")
            elif not path.read_text(encoding="utf-8").strip():
                problems.append(f"{cid}: empty technique for style {style}")
    return problems


def main() -> int:
    ids = control_ids()
    if not ids:
        print("no controls found; is the catalog present?", file=sys.stderr)
        return 1
    problems = gaps()
    if problems:
        for problem in problems:
            print(problem, file=sys.stderr)
        print(f"TECHNIQUES INCOMPLETE: {len(problems)} gap(s)", file=sys.stderr)
        return 1
    print(f"TECHNIQUES OK: {len(ids)} controls × {len(STYLES)} styles")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
