"""Errors that name the fix (SPEC §13.4 AX-6).

Every error the engine raises carries a stable ``key`` (so automation and the generated error
catalogue can key on it, never on prose), a one-sentence ``cause``, and a ``fix`` (a command to run,
a file to edit, or a technique to apply). The CLI renders these deterministically and, unless
``--debug`` is set, never lets a stack trace reach the user.
"""

from __future__ import annotations

from dataclasses import dataclass

from .exit_codes import ExitCode


@dataclass
class AgentceError(Exception):
    """A user-facing error with a stable message key, a cause, and a fix."""

    key: str
    cause: str
    fix: str = ""
    exit_code: int = int(ExitCode.INPUT_ERROR)

    def __post_init__(self) -> None:
        super().__init__(f"{self.key}: {self.cause}")

    def to_dict(self) -> dict[str, str | int]:
        """Return the machine-readable form carried in ``--json`` output."""
        return {
            "key": self.key,
            "cause": self.cause,
            "fix": self.fix,
            "exit_code": int(self.exit_code),
        }


class InputError(AgentceError):
    """A missing, unreadable, or malformed input (exit code 3)."""

    def __init__(self, key: str, cause: str, fix: str = "") -> None:
        super().__init__(
            key=key, cause=cause, fix=fix, exit_code=int(ExitCode.INPUT_ERROR)
        )
