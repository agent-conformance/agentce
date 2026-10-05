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
shaped, and that construction can raise even though the shape matched. This module's loader catches
that and falls back to a text rendering that reproduces what ``str(date(...))``/
``str(datetime(...))`` would have printed had construction succeeded -- ``_pythonize_timestamp``, a
straight port of ``engines/typescript/src/readiness.ts``'s ``pythonizeTimestamp``/
``engines/java/src/main/java/org/agentce/Readiness.java``'s ``pythonizeTimestamp`` -- so a
calendar-invalid date reaches ``readiness.parse_date``/``deviation_lint`` as the same shape of
string either other engine already produces, rather than a new divergence between the three (the
fix those two ports already carry; this gives Python the same one). A *valid* unquoted date is
unaffected: construction succeeds and the value stays a real ``date``/``datetime`` object, exactly
as it is today.

A fourth, broader hazard subsumes the explicit-tag cases above: an explicit tag (``!!int 0xZZ``,
``!!bool maybe``, ``!!int ''``, ``!!timestamp foo``, ...) bypasses the resolver's own shape gate
entirely, so it can reach any of PyYAML's built-in constructors with a value that constructor was
never designed to validate -- and each one leaks whatever stdlib exception its own implementation
happens to raise (confirmed live: ``ValueError``, ``KeyError``, ``IndexError``, ``AttributeError``).
That set is not enumerable in advance, so the loader does not try: its ``construct_object`` and
``construct_document`` re-raise *any* exception other than a ``yaml.YAMLError`` or a
``RecursionError`` as a ``yaml.constructor.ConstructorError`` naming the node, which
:func:`load_untrusted_yaml`'s existing ``yaml.YAMLError`` refusal then reports. The catch is scoped to
construction only, so reading the file (an ``OSError`` a caller handles itself) is unaffected.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .errors import InputError


class _PermissiveTimestampSafeLoader(yaml.SafeLoader):
    """``yaml.SafeLoader`` whose construction never leaks a non-YAML exception: a calendar-invalid
    timestamp falls back to :func:`_pythonize_timestamp`, and any other exception raised while
    constructing a value becomes a ``ConstructorError`` (the module docstring's fourth hazard)."""

    def construct_object(self, node: yaml.Node, deep: bool = False) -> Any:
        try:
            return super().construct_object(node, deep)
        except (yaml.YAMLError, RecursionError):
            raise
        except Exception as exc:
            raise _construction_error(node, exc) from exc

    def construct_document(self, node: yaml.Node) -> Any:
        try:
            return super().construct_document(node)
        except (yaml.YAMLError, RecursionError):
            raise
        except Exception as exc:
            raise _construction_error(node, exc) from exc


def _construction_error(
    node: yaml.Node, exc: Exception
) -> yaml.constructor.ConstructorError:
    shown = f" {node.value!r}" if isinstance(node, yaml.ScalarNode) else ""
    return yaml.constructor.ConstructorError(
        None,
        None,
        f"cannot construct {node.tag}{shown} ({type(exc).__name__}: {exc})",
        node.start_mark,
    )


def _construct_yaml_timestamp_permissive(
    loader: yaml.SafeLoader, node: yaml.ScalarNode
) -> Any:
    try:
        return yaml.SafeLoader.construct_yaml_timestamp(loader, node)
    except Exception:
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
