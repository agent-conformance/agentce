"""Report-rendering checks (SPEC §9.3): item 4.10 acceptance and the P4.7 gate.

Checks that ``report.html`` is self-contained, carries a strict Content-Security-Policy, meets a
deterministic WCAG 2.2 AA structural bar, and escapes every evidence-derived string; and that a
second-language rendering changes ``report.md`` but leaves ``assertions.json`` byte-identical.

``python render_check.py --renderings <dir>`` checks every committed rendering under ``<dir>`` and the
engine's own renderings, exiting 0 when all pass. ``python render_check.py --json`` prints
``{"render_check", "escaping_text", "second_language_identical"}`` for the phase-4 eval's P4.7 check.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_ENGINE = _REPO / "engines" / "python"
sys.path.insert(0, str(_ENGINE))

from agentce.assertions import Assertion, EvidencePointer, aggregate  # noqa: E402
from agentce.catalog import load_catalog  # noqa: E402
from agentce.report import render_report_html, write_report  # noqa: E402

_BASE_CATALOG = _REPO / "spec" / "catalogs" / "base" / "eu-ai-act"

_MALICIOUS_SUBJECT = "s<script>alert(1)</script>"


def _sample(subject: str = "spiffe://corp/agents/a") -> list[Assertion]:
    return [
        Assertion(
            control="REC-01",
            control_version="2026.09",
            subject=subject,
            outcome="conformant",
            rung=2,
            mode="automated",
            window=("2026-01-01T00:00:00Z", "2026-04-01T00:00:00Z"),
            population=(1, 0),
            severity="high",
            family="REC",
            evidence=[
                EvidencePointer("agentce:event/x", "sha256:ab", "enforcement_point")
            ],
        )
    ]


def _html_is_hardened(page: str) -> bool:
    """A deterministic WCAG 2.2 AA / self-containment / CSP bar (SPEC §9.3)."""
    return (
        "Content-Security-Policy" in page
        and "http://" not in page
        and "https://" not in page
        and "<script" not in page
        and "lang=" in page
        and page.count("<h1") == 1
        and "<main>" in page
    )


def _escapes_evidence_strings() -> bool:
    page = render_report_html(
        _sample(_MALICIOUS_SUBJECT), aggregate(_sample(_MALICIOUS_SUBJECT))
    )
    return "<script>alert(1)" not in page and "&lt;script&gt;" in page


def _second_language_leaves_assertions_identical() -> bool:
    assertions = _sample()
    catalog = load_catalog(_BASE_CATALOG)
    with tempfile.TemporaryDirectory() as tmp:
        en, de = Path(tmp) / "en", Path(tmp) / "de"
        for out, language in ((en, "en"), (de, "de")):
            write_report(
                out,
                assertions,
                bundle_digest="sha256:0",
                catalogs=[catalog],
                report_language=language,
            )
        assertions_identical = (en / "assertions.json").read_bytes() == (
            de / "assertions.json"
        ).read_bytes()
        report_differs = (en / "report.md").read_bytes() != (
            de / "report.md"
        ).read_bytes()
    return assertions_identical and report_differs


def _committed_renderings(renderings: Path) -> list[str]:
    problems: list[str] = []
    for page in sorted(renderings.rglob("report.html")):
        if not _html_is_hardened(page.read_text(encoding="utf-8")):
            problems.append(f"{page} is not a hardened, self-contained report")
    return problems


def _quickstart_rendering() -> bool:
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(
            ["uv", "run", "agentce", "quickstart", "--out", tmp],
            cwd=_ENGINE,
            capture_output=True,
            text=True,
            check=False,
        )
        pages = list(Path(tmp).rglob("report.html"))
        return bool(pages) and all(
            _html_is_hardened(p.read_text(encoding="utf-8")) for p in pages
        )


def evaluate(renderings: Path | None = None) -> dict[str, bool]:
    render_check = _quickstart_rendering()
    if renderings is not None and renderings.is_dir():
        render_check = render_check and not _committed_renderings(renderings)
    return {
        "render_check": render_check,
        "escaping_text": _escapes_evidence_strings(),
        "second_language_identical": _second_language_leaves_assertions_identical(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Report-rendering checks (SPEC §9.3).")
    parser.add_argument(
        "--renderings", help="a directory of committed report.html renderings to check"
    )
    parser.add_argument(
        "--json", action="store_true", help="emit machine-readable JSON"
    )
    args = parser.parse_args(argv)
    renderings = Path(args.renderings) if args.renderings else None
    result = evaluate(renderings)
    if args.json:
        print(json.dumps(result, sort_keys=True))
    elif all(result.values()):
        print("RENDER OK")
    else:
        failed = sorted(k for k, v in result.items() if not v)
        print(f"RENDER FAILED: {', '.join(failed)}")
    return 0 if all(result.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
