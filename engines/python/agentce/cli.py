"""Minimal command-line entry point for the placeholder release."""

from __future__ import annotations

import sys

from agentce import __version__


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] in {"--version", "-V", "version"}:
        print(f"agentce {__version__}")
        return 0
    print(
        "agentce (Agent Conformance Engine) - placeholder release.\n"
        "The assessment engine is under development.\n"
        "Specification: https://github.com/agent-conformance/agentce/blob/main/docs/SPEC.md\n"
        "Usage: agentce --version"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
