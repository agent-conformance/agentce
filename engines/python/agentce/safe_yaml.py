"""Hardened YAML loading for untrusted evidence-adjacent input (SPEC §8.1, §8.7).

The applicability profile, the domain binding, and the deviation register are YAML files that ride
with an evidence bundle someone else produced; they are adversarial input to the parsing layer,
independent of what the evidence itself claims about the assessed agent. ``yaml.safe_load`` already
refuses to construct a disallowed tag (a code-execution-shaped ``!!python/object/apply:``), and a
pathologically deep structure exhausts Python's recursion limit -- but left uncaught, both surface
only through the CLI's generic top-level catch-all (``internal.unexpected``), indistinguishable from
a genuinely unforeseen bug. This module gives both a deliberate, named ``input.*`` refusal instead.

A YAML-syntax-valid but calendar-invalid unquoted timestamp (``expiry: 2026-02-30``) is a third,
narrower hazard: PyYAML's own timestamp constructor always attempts ``datetime.date``/
``datetime.datetime`` construction once its resolver's regex decides a plain scalar is timestamp-
shaped, and that construction can raise ``ValueError`` even though the shape matched. This module's
loader catches exactly that case and falls back to a text rendering that reproduces what
``str(date(...))``/``str(datetime(...))`` would have printed had construction succeeded --
``_pythonize_timestamp``, a straight port of ``engines/typescript/src/readiness.ts``'s
``pythonizeTimestamp``/``engines/java/src/main/java/org/agentce/Readiness.java``'s
``pythonizeTimestamp`` -- so a calendar-invalid date reaches ``readiness.parse_date``/
``deviation_lint`` as the same shape of string either other engine already produces, rather than a
new divergence between the three (the fix those two ports already carry; this gives Python the same
one). A *valid* unquoted date is unaffected: construction succeeds and the value stays a real
``date``/``datetime`` object, exactly as it is today.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .errors import InputError


class _PermissiveTimestampSafeLoader(yaml.SafeLoader):
    """``yaml.SafeLoader`` whose timestamp construction never raises: a calendar-invalid value
    (shape-valid, like ``2026-02-30``) falls back to :func:`_pythonize_timestamp` instead of letting
    ``datetime.date``/``datetime.datetime`` construction's ``ValueError`` escape. An explicit
    ``!!timestamp`` tag on a value that is not timestamp-shaped at all (e.g. ``!!timestamp foo``)
    bypasses the resolver's own shape gate and reaches ``construct_yaml_timestamp`` with no regex
    match, which raises ``AttributeError`` (``None.groupdict()``) rather than ``ValueError`` --
    caught here too, for the same fallback."""


def _construct_yaml_timestamp_permissive(
    loader: yaml.SafeLoader, node: yaml.ScalarNode
) -> Any:
    try:
        return yaml.SafeLoader.construct_yaml_timestamp(loader, node)
    except (ValueError, AttributeError):
        return _pythonize_timestamp(node.value)


_PermissiveTimestampSafeLoader.add_constructor(
    "tag:yaml.org,2002:timestamp", _construct_yaml_timestamp_permissive
)


def _pythonize_timestamp(raw: str) -> str:
    """``str(date(...))``/``str(datetime(...))``'s own rendering, computed from PyYAML's own
    ``Constructor.timestamp_regexp`` field groups directly -- never by constructing the object itself
    (the one operation that can fail for a calendar-invalid value). Performs no range validation: an
    out-of-range field renders anyway; :func:`agentce.readiness.parse_date` is the one place both a
    quoted and a pythonized-unquoted value are range-checked, so both fail exactly the same way. A
    non-timestamp-shaped ``raw`` (should not occur -- the resolver already gated on this shape to pick
    this constructor) is returned unchanged."""
    match = yaml.SafeLoader.timestamp_regexp.match(raw)
    if match is None:
        return raw
    g = match.groupdict()
    year, month, day = int(g["year"]), int(g["month"]), int(g["day"])
    if not g["hour"]:
        return f"{year:04d}-{month:02d}-{day:02d}"
    hour, minute, second = int(g["hour"]), int(g["minute"]), int(g["second"])
    microsecond = 0
    if g["fraction"]:
        microsecond = int(g["fraction"][:6].ljust(6, "0"))
    out = f"{year:04d}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}:{second:02d}"
    if microsecond:
        out += f".{microsecond:06d}"
    if g["tz_sign"]:
        tz_hour, tz_minute = int(g["tz_hour"]), int(g["tz_minute"] or 0)
        out += f"{g['tz_sign']}{tz_hour:02d}:{tz_minute:02d}"
    elif g["tz"]:
        out += "+00:00"
    return out


def load_untrusted_yaml(path: Path, *, key: str, what: str) -> Any:
    """Load and return the YAML at ``path``, refusing hazards under the ``input.*`` ``key``."""
    try:
        text = path.read_text(encoding="utf-8")
        return yaml.load(text, Loader=_PermissiveTimestampSafeLoader)
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
    except UnicodeDecodeError as exc:
        raise InputError(
            key,
            f"{what} at {str(path)!r} is not valid UTF-8: {exc}.",
            f"save {what} as UTF-8 text.",
        ) from exc
    except (ValueError, TypeError, KeyError) as exc:
        # An explicit tag (`!!int 0xZZ`, `!!bool maybe`) bypasses the resolver's shape gate
        # entirely and can still reach its constructor with a value that construction rejects --
        # confirmed live for `!!int`/`!!float`'s ValueError and `!!bool`'s KeyError. The timestamp
        # case above is handled by the permissive loader and never reaches here.
        raise InputError(
            key,
            f"{what} at {str(path)!r} carries a YAML scalar the engine refuses to construct: {exc}.",
            f"remove the explicit YAML tag from {what}; only plain, untagged scalars are accepted.",
        ) from exc
