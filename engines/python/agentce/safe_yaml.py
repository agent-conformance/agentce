"""Hardened YAML loading for untrusted evidence-adjacent input (SPEC §8.1, §8.7).

The applicability profile and the domain binding are YAML files that ride with an evidence bundle
someone else produced; they are adversarial input to the parsing layer, independent of what the
evidence itself claims about the assessed agent. ``yaml.safe_load`` already refuses to construct a
disallowed tag (a code-execution-shaped ``!!python/object/apply:``), and a pathologically deep
structure exhausts Python's recursion limit -- but left uncaught, both surface only through the CLI's
generic top-level catch-all (``internal.unexpected``), indistinguishable from a genuinely unforeseen
bug. This module gives both a deliberate, named ``input.*`` refusal instead.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .errors import InputError


def load_untrusted_yaml(path: Path, *, key: str, what: str) -> Any:
    """Load and return the YAML at ``path``, refusing hazards under the ``input.*`` ``key``."""
    text = path.read_text(encoding="utf-8")
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise InputError(
            key,
            f"{what} at {str(path)!r} carries a YAML construct the engine refuses to load: {exc}.",
            f"remove custom tags and aliases from {what}; only plain YAML scalars, mappings, and "
            "sequences are accepted.",
        ) from exc
    except RecursionError as exc:
        raise InputError(
            key,
            f"{what} at {str(path)!r} is nested too deeply to parse safely.",
            f"flatten {what}'s structure; it exceeds the engine's safe nesting depth.",
        ) from exc
