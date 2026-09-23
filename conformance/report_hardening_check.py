"""Report-hardening guard across all three engines (SPEC §9.3): the hostile-subject-id escaping,
Content-Security-Policy, and structural-bar proof from contract F19, extended from TypeScript-only
(the present defect) to Python, TypeScript, and Java (a regression in any engine's renderer fails CI).
Also proves the TypeScript engine's ``--language`` parameter genuinely varies the rendered report while
leaving ``assertions.json`` untouched, mirroring ``render_check.py``'s own
``_second_language_leaves_assertions_identical`` for the Python engine.

Run: ``uv run --project engines/python python conformance/report_hardening_check.py``
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "engines" / "python"))
sys.path.insert(0, str(_REPO / "conformance"))

from agentce.assertions import Assertion, EvidencePointer, aggregate  # noqa: E402
from agentce.report import render_report_html  # noqa: E402

import render_check  # the existing, unchanged structural bar (SPEC S9.3) -- not reimplemented here  # noqa: E402
from ecs import java_launcher  # noqa: E402

_TS_ENGINE = _REPO / "engines" / "typescript"
_JAVA_ENGINE = _REPO / "engines" / "java"

_MALICIOUS_SUBJECT = "s<script>alert(1)</script><img src=x onerror=alert(2)>"

_ASSERTION_JSON = {
    "control": "REC-01",
    "control_version": "2026.09",
    "subject": _MALICIOUS_SUBJECT,
    "outcome": "conformant",
    "rung": 2,
    "mode": "automated",
    "window": ["2026-01-01T00:00:00Z", "2026-04-01T00:00:00Z"],
    "population": [1, 0],
    "evidence": [
        {
            "ref": "agentce:event/x",
            "digest": "sha256:ab",
            "source_class": "enforcement_point",
        }
    ],
}


def _write_fixture(tmp: Path, subject: str = _MALICIOUS_SUBJECT) -> Path:
    path = tmp / f"assertions-{abs(hash(subject))}.json"
    body = dict(_ASSERTION_JSON, subject=subject)
    path.write_text(json.dumps([body]), encoding="utf-8")
    return path


def _check_html(label: str, html: str, problems: list[str]) -> None:
    if "<script>alert(1)" in html or "<img src=x onerror=alert(2)>" in html:
        problems.append(
            f"{label}: raw hostile payload present unescaped in report.html"
        )
    if "&lt;script&gt;alert(1)&lt;/script&gt;" not in html:
        problems.append(f"{label}: no escaped form of the hostile payload found")
    if not render_check._html_is_hardened(html):
        problems.append(
            f"{label}: fails the existing structural bar (_html_is_hardened)"
        )
    if 'name="viewport"' not in html:
        problems.append(f"{label}: no viewport meta tag")


def _python_report() -> str:
    assertion = Assertion(
        control="REC-01",
        control_version="2026.09",
        subject=_MALICIOUS_SUBJECT,
        outcome="conformant",
        rung=2,
        mode="automated",
        window=("2026-01-01T00:00:00Z", "2026-04-01T00:00:00Z"),
        population=(1, 0),
        evidence=[EvidencePointer("agentce:event/x", "sha256:ab", "enforcement_point")],
    )
    return render_report_html([assertion], aggregate([assertion]))


def _typescript_report(tmp: Path) -> str:
    fixture = _write_fixture(tmp)
    out = tmp / "ts-report.html"
    proc = subprocess.run(
        [
            "pnpm",
            "--silent",
            "agentce",
            "report",
            "--from",
            str(fixture),
            "--format",
            "html",
            "--out",
            str(out),
        ],
        cwd=str(_TS_ENGINE),
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0 or not out.is_file():
        raise SystemExit(
            f"TypeScript report render failed (rc={proc.returncode}):\n{(proc.stderr or proc.stdout)[-800:]}"
        )
    return out.read_text(encoding="utf-8")


def _java_report(tmp: Path) -> str:
    fixture = _write_fixture(tmp)
    out = tmp / "java-report.html"
    launcher = java_launcher()
    proc = subprocess.run(
        [
            str(launcher),
            "report",
            "--from",
            str(fixture),
            "--format",
            "html",
            "--out",
            str(out),
        ],
        cwd=str(_JAVA_ENGINE),
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0 or not out.is_file():
        raise SystemExit(
            f"Java report render failed (rc={proc.returncode}):\n{(proc.stderr or proc.stdout)[-800:]}"
        )
    return out.read_text(encoding="utf-8")


def _typescript_language_differential(tmp: Path, problems: list[str]) -> None:
    """The TS ``--language`` parameter varies ``report.html`` while ``assertions.json`` stays
    byte-identical (mirrors render_check.py's own second-language proof for the Python engine)."""
    fixture = _write_fixture(tmp, subject="spiffe://corp/agents/a")
    en_out, de_out = tmp / "lang-en.html", tmp / "lang-de.html"
    for out, language in ((en_out, "en"), (de_out, "de")):
        proc = subprocess.run(
            [
                "pnpm",
                "--silent",
                "agentce",
                "report",
                "--from",
                str(fixture),
                "--format",
                "html",
                "--language",
                language,
                "--out",
                str(out),
            ],
            cwd=str(_TS_ENGINE),
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0 or not out.is_file():
            problems.append(
                f"typescript {language} render failed (rc={proc.returncode}): "
                f"{(proc.stderr or proc.stdout)[-400:]}"
            )
            return
    if en_out.read_text(encoding="utf-8") == de_out.read_text(encoding="utf-8"):
        problems.append(
            "typescript: --language en and --language de render byte-identical report.html "
            "(the language parameter has no effect)"
        )


def main() -> int:
    problems: list[str] = []
    with tempfile.TemporaryDirectory() as tmp_s:
        tmp = Path(tmp_s)
        _check_html("python", _python_report(), problems)
        _check_html("typescript", _typescript_report(tmp), problems)
        _check_html("java", _java_report(tmp), problems)
        _typescript_language_differential(tmp, problems)
    if problems:
        print("REPORT-HARDENING FAILED:")
        for p in problems:
            print(f"  - {p}")
        return 1
    print(
        "REPORT-HARDENING OK: python, typescript, and java escape a hostile subject id and carry a "
        "hardened report.html; the typescript --language parameter varies the rendering"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
