"""A minimal ICU MessageFormat subset, and the loader for the vendored message catalogue.

The catalogue (authored once under ``spec/i18n/``, vendored byte-identical to ``agentce/data/i18n/``,
``tests/test_bundled_data.py`` holds the two in sync) backs both the CLI/error strings
(:mod:`agentce.error_catalogue`) and the report strings (:mod:`agentce.messages`). Most entries are
plain text or carry a single ``{var}`` interpolation slot; a handful use a real ICU
``{var, plural, one {...} other {...}}`` or ``{var, select, ...}`` construct. This module implements
just that subset -- not a general ICU MessageFormat engine -- because that is everything the
catalogue actually uses.

Nothing here ever touches a canonical, machine-readable output: the report language only ever
selects which of these strings render into ``report.md``/``report.html`` (SPEC §9.3).
"""

from __future__ import annotations

import json
import re

from . import bundled

_PLURAL_RE = re.compile(
    r"^\{(?P<var>[A-Za-z_][A-Za-z0-9_]*),\s*plural,\s*(?P<body>.*)\}$", re.DOTALL
)
_SELECT_RE = re.compile(
    r"^\{(?P<var>[A-Za-z_][A-Za-z0-9_]*),\s*select,\s*(?P<body>.*)\}$", re.DOTALL
)
_CATEGORY_RE = re.compile(r"\s*([A-Za-z0-9_=]+)\s*\{([^{}]*)\}")


def _english_plural_category(n: int) -> str:
    """CLDR English plural rule: ``one`` for exactly 1, ``other`` otherwise."""
    return "one" if n == 1 else "other"


def _parse_categories(body: str) -> dict[str, str]:
    return {match.group(1): match.group(2) for match in _CATEGORY_RE.finditer(body)}


def format_message(template: str, **args: object) -> str:
    """Render an ICU MessageFormat ``template`` against ``args``.

    Supports plain ``{var}`` interpolation and a template that is entirely one top-level
    ``{var, plural, ...}`` or ``{var, select, ...}`` construct -- the subset the catalogue uses.
    Inside a chosen plural category, ``#`` is replaced by the formatted count (ICU's own convention).
    """
    plural = _PLURAL_RE.match(template)
    if plural:
        var = plural.group("var")
        n = int(args[var])  # type: ignore[call-overload]
        categories = _parse_categories(plural.group("body"))
        exact = f"={n}"
        category = exact if exact in categories else _english_plural_category(n)
        chosen = categories.get(category, categories.get("other", ""))
        return chosen.replace("#", str(n))

    select = _SELECT_RE.match(template)
    if select:
        var = select.group("var")
        value = str(args[var])
        categories = _parse_categories(select.group("body"))
        return categories.get(value, categories.get("other", ""))

    return template.format(**args) if args else template


def load_catalog(language: str) -> dict[str, str]:
    """Load the vendored catalogue for ``language``; ``{}`` if that language has no file at all."""
    path = bundled.i18n_dir() / f"messages.{language}.json"
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {str(key): str(value) for key, value in data.items()}
