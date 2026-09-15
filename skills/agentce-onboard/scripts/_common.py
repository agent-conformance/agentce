"""Shared plumbing for the agentce-onboard scripts (SPEC 13.3.2).

Every bundled script is a pure function of its inputs: deterministic, offline, model-free, sorted
output, stable exit codes, and JSON alongside human text (rule S-4). Before doing anything else a
script verifies the spec and CLI version pins declared in ``SKILL.md`` against the installed engine
(rule S-5); on a mismatch it stops with exit code 3 rather than proceeding with stale field names.

Exit codes (SPEC 13.3.2): 0 ok, 1 findings that require action, 2 input error, 3 version mismatch.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

OK = 0
FINDINGS = 1
INPUT_ERROR = 2
VERSION_MISMATCH = 3

_SKILL_MD = Path(__file__).resolve().parent.parent / "SKILL.md"


@dataclass
class Finding:
    """One issue a script reports: a stable code, a human message, and structured details."""

    code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, **self.details}


def read_frontmatter(path: Path = _SKILL_MD) -> dict[str, Any]:
    """Return the YAML frontmatter block of ``SKILL.md`` as a dict (empty if absent)."""
    import yaml

    text = path.read_text(encoding="utf-8")
    match = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    if not match:
        return {}
    parsed = yaml.safe_load(match.group(1))
    return parsed if isinstance(parsed, dict) else {}


def _version_tuple(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", version)) or (0,)


def _satisfies(version: str, spec: str) -> bool:
    """Tiny version-range check: comma-separated ``>=``/``>``/``<=``/``<``/``==`` clauses."""
    actual = _version_tuple(version)
    for clause in (c.strip() for c in spec.split(",") if c.strip()):
        match = re.match(r"(>=|<=|==|>|<)?\s*([0-9.]+)", clause)
        if not match:
            return False
        op, bound_raw = match.group(1) or "==", match.group(2)
        bound = _version_tuple(bound_raw)
        ok = {
            ">=": actual >= bound,
            "<=": actual <= bound,
            "==": actual == bound,
            ">": actual > bound,
            "<": actual < bound,
        }[op]
        if not ok:
            return False
    return True


def check_pins() -> Finding | None:
    """Verify the SKILL.md spec/CLI pins against the installed engine (S-5); ``None`` when they hold."""
    import agentce

    pins = read_frontmatter().get("metadata", {})
    spec_pin = str(pins.get("spec_version", ""))
    if spec_pin and spec_pin != str(agentce.SPEC_VERSION):
        return Finding(
            "skill.spec_version_mismatch",
            f"skill pins spec_version {spec_pin} but the engine is {agentce.SPEC_VERSION}",
            {"pinned": spec_pin, "engine": str(agentce.SPEC_VERSION)},
        )
    cli_pin = str(pins.get("cli_version", ""))
    if cli_pin and not _satisfies(str(agentce.__version__), cli_pin):
        return Finding(
            "skill.cli_version_mismatch",
            f"skill pins cli_version {cli_pin} but the engine is {agentce.__version__}",
            {"pinned": cli_pin, "engine": str(agentce.__version__)},
        )
    return None


def emit(payload: dict[str, Any], *, want_json: bool, human: str) -> None:
    """Print JSON (``--json``) or a human line; JSON always goes to stdout (SPEC 13.3.2)."""
    if want_json:
        print(json.dumps(payload, sort_keys=True, indent=2))
    else:
        print(human)


def run_guarded(argv: list[str], body: Any) -> int:
    """Run ``body(args)`` after the version-pin gate, mapping a pin mismatch to exit 3 (S-5)."""
    want_json = "--json" in argv
    mismatch = check_pins()
    if mismatch is not None:
        emit(
            {"status": "version_mismatch", "finding": mismatch.to_json()},
            want_json=want_json,
            human=f"VERSION MISMATCH: {mismatch.message}",
        )
        return VERSION_MISMATCH
    exit_code: int = body(argv)
    return exit_code


def arg_value(argv: list[str], flag: str) -> str | None:
    """Return the value following ``flag`` in ``argv`` (``--flag value``), or ``None``."""
    if flag in argv:
        index = argv.index(flag)
        if index + 1 < len(argv):
            return argv[index + 1]
    return None
