"""Phase-3 explanation-narrative conformance check (P3.3).

Renders the explanation narratives for a frozen set of representative credit-domain decisions and
compares them byte-for-byte to the golden in ``narratives_data/golden.md``. The renderer is
deterministic and generative-free (SPEC §8.1), so a drift in the templates, the edge citations, or
the "not reconstructable from record" behaviour fails here.

Run it as::

    cd conformance && uv run python narratives.py --check
"""

from __future__ import annotations

import argparse
import difflib
import json
import sys
from pathlib import Path

from agentce import explanation
from agentce.domain import DomainBinding
from agentce.graph import build_graph

DATA = Path(__file__).resolve().parent / "narratives_data"


def render_golden() -> str:
    data = json.loads((DATA / "decisions.json").read_text(encoding="utf-8"))
    domain = DomainBinding.from_dict(data["domain"])
    store = build_graph(data["events"], domain=domain)
    try:
        narratives = explanation.render_sample(data["events"], store, domain)
    finally:
        store.close()
    return "# Golden explanation narratives (P3.3)\n\n" + "\n".join(
        n.render() for n in narratives
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Phase-3 explanation-narrative conformance check (P3.3)."
    )
    parser.add_argument(
        "--check", action="store_true", help="compare against the committed golden"
    )
    parser.add_argument(
        "--update", action="store_true", help="rewrite the golden from the renderer"
    )
    args = parser.parse_args(argv)

    rendered = render_golden()
    golden_path = DATA / "golden.md"
    if args.update:
        golden_path.write_text(rendered, encoding="utf-8")
        print("GOLDEN UPDATED")
        return 0

    golden = golden_path.read_text(encoding="utf-8")
    if rendered == golden:
        print("GOLDEN OK")
        return 0
    diff = difflib.unified_diff(
        golden.splitlines(), rendered.splitlines(), "golden.md", "rendered", lineterm=""
    )
    print("GOLDEN MISMATCH")
    print("\n".join(diff))
    return 1


if __name__ == "__main__":
    sys.exit(main())
