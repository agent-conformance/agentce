"""The engine's own no-learned-components self-report (SPEC §8.7, HR-1/HR-2).

``agentce version --json`` reports ``no_ml``: the engine scans its installed distributions against a
vendored copy of the denylist (``data/no-ml-denylist.txt``, kept byte-identical to
``spec/rules/no-ml-denylist.txt`` by a test) and reports ``pass`` only when none is present. The
authoritative gate over the *locked* dependency tree is ``tools/no_ml_check.py`` and the ECS
``no_ml`` result; this self-report mirrors it so a consumer of a single installed engine can check it
offline. Names are compared by PEP 503 normalised form, never as substrings, so ``linkml`` or
``pyyaml`` are unaffected.
"""

from __future__ import annotations

import re
from importlib import resources
from importlib.metadata import distributions
from typing import Any

_NORMALISE = re.compile(r"[-_.]+")


def normalise(name: str) -> str:
    """PEP 503 normalisation: lowercase with runs of ``-``, ``_``, ``.`` collapsed to one ``-``."""
    return _NORMALISE.sub("-", name.strip().lower())


def load_denylist() -> set[str]:
    """Return the normalised denylisted distribution names from the vendored copy."""
    text = (
        resources.files("agentce.data")
        .joinpath("no-ml-denylist.txt")
        .read_text(encoding="utf-8")
    )
    names: set[str] = set()
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            names.add(normalise(stripped))
    return names


def installed_distribution_names() -> set[str]:
    """Return the normalised names of every distribution installed in the current environment."""
    names: set[str] = set()
    for dist in distributions():
        raw = dist.metadata["Name"] if dist.metadata else None
        if raw:
            names.add(normalise(raw))
    return names


def evaluate() -> dict[str, Any]:
    """Return ``{"result": "pass"|"fail", "denylisted_present": [...]}`` for the current env."""
    denylist = load_denylist()
    present = sorted(denylist & installed_distribution_names())
    return {"result": "fail" if present else "pass", "denylisted_present": present}
