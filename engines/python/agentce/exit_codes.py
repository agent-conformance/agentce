"""The common exit-code scheme for every AgentCE command (SPEC §8.5).

``0`` success with no findings requiring action; ``1`` findings requiring action (non-conformant
outcomes for ``assess``, quarantined events for ``validate``, broken streams for ``verify``); ``2``
insufficient evidence on any ``severity: high`` control (``assess`` only); ``3`` input, version, or
signature-verification error. When several apply the highest code is returned and the JSON output
carries all of them. Every code has a stable name so that automation never scrapes text.
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import IntEnum


class ExitCode(IntEnum):
    """The four exit codes, in ascending precedence (higher wins)."""

    OK = 0
    FINDINGS = 1
    INSUFFICIENT_EVIDENCE = 2
    INPUT_ERROR = 3


#: Stable, machine-readable name for each code (the JSON output carries these, never prose).
NAMES: dict[int, str] = {
    ExitCode.OK: "ok",
    ExitCode.FINDINGS: "findings",
    ExitCode.INSUFFICIENT_EVIDENCE: "insufficient_evidence",
    ExitCode.INPUT_ERROR: "input_error",
}


def name_of(code: int) -> str:
    """Return the stable name of ``code`` or raise ``ValueError`` for an unknown code."""
    try:
        return NAMES[int(code)]
    except KeyError as exc:  # pragma: no cover - guarded by callers
        raise ValueError(f"unknown exit code {code!r}") from exc


def combine(codes: Iterable[int]) -> int:
    """Return the highest applicable code; an empty set of codes means ``OK`` (SPEC §8.5)."""
    highest = int(ExitCode.OK)
    for code in codes:
        value = int(code)
        if value not in NAMES:
            raise ValueError(f"unknown exit code {code!r}")
        highest = max(highest, value)
    return highest


def applicable(codes: Iterable[int]) -> list[int]:
    """Return the sorted, de-duplicated applicable codes.

    ``OK`` is reported only when nothing else applies: a run with findings carries ``[1]``, not
    ``[0, 1]``, because "success with no findings" no longer holds once a finding is present.
    """
    unique = {int(c) for c in codes}
    for value in unique:
        if value not in NAMES:
            raise ValueError(f"unknown exit code {value!r}")
    non_ok = {c for c in unique if c != int(ExitCode.OK)}
    return sorted(non_ok) if non_ok else [int(ExitCode.OK)]
