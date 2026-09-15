"""The result of running one command, and its deterministic JSON envelope.

A command handler returns a :class:`CommandResult`. It accumulates the exit codes that apply (the
scheme in :mod:`agentce.exit_codes`), a machine-readable ``data`` payload, and human-readable lines.
The CLI renders it as the ``--json`` envelope or as text, and derives the process exit code from it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .exit_codes import ExitCode, applicable, combine, name_of

#: Top-level keys the envelope always sets; a command's ``data`` must not rely on carrying them.
RESERVED_KEYS = ("command", "exit_code", "exit_codes", "exit_status")


@dataclass
class CommandResult:
    """The outcome of a single command invocation."""

    command: str
    codes: set[int] = field(default_factory=set)
    data: dict[str, Any] = field(default_factory=dict)
    human_lines: list[str] = field(default_factory=list)

    def add_code(self, code: int) -> None:
        """Record that ``code`` applies to this result."""
        int_code = int(code)
        if int_code not in {int(c) for c in ExitCode}:
            raise ValueError(f"unknown exit code {code!r}")
        self.codes.add(int_code)

    def note(self, line: str) -> None:
        """Append a human-readable line."""
        self.human_lines.append(line)

    @property
    def applicable_codes(self) -> list[int]:
        return applicable(self.codes)

    @property
    def exit_code(self) -> int:
        return combine(self.codes)

    def envelope(self) -> dict[str, Any]:
        """Return the deterministic ``--json`` object for this result.

        The command's ``data`` is emitted at the top level (so automation reads, e.g.,
        ``.no_ml`` or ``.values`` directly), and the reserved exit-code keys are layered on top.
        """
        out: dict[str, Any] = dict(self.data)
        out["command"] = self.command
        out["exit_code"] = self.exit_code
        out["exit_codes"] = self.applicable_codes
        out["exit_status"] = [name_of(c) for c in self.applicable_codes]
        return out
