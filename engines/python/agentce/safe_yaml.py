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

import unicodedata
from pathlib import Path
from typing import Any

import yaml

from .errors import InputError
from .yaml_profile import ProfileViolation, duplicate_key_line, profile_violation


_MERGE_TAG = "tag:yaml.org,2002:merge"

_SAFE_LOADER_ONLY_TAGS = frozenset(
    f"tag:yaml.org,2002:{tag}" for tag in ("binary", "set", "omap", "pairs")
)


def _merge_sources(value_node: yaml.Node) -> list[yaml.Node]:
    """The nodes a ``<<`` key's ``value_node`` brings in: the node itself, and each item of a merge list."""
    if isinstance(value_node, yaml.SequenceNode):
        return [value_node, *value_node.value]
    return [value_node]


class _PermissiveTimestampSafeLoader(yaml.SafeLoader):
    """``yaml.SafeLoader`` whose construction never leaks a non-YAML exception: a calendar-invalid
    timestamp falls back to :func:`_pythonize_timestamp`, and any other exception raised while
    constructing a value becomes a ``ConstructorError`` (the module docstring's fourth hazard).

    It has no ``!!binary``, ``!!set``, ``!!omap`` or ``!!pairs`` constructor, so a file holding one is refused by
    ``construct_undefined`` as in the TypeScript and Java readers (18.184o for catalogs, 18.184p for every file). A
    merge source is never constructed (``flatten_mapping`` merges it as a node), so the whole tree is walked first and
    each merge source's tag is checked against the same constructors."""

    yaml_constructors = {
        tag: construct
        for tag, construct in yaml.SafeLoader.yaml_constructors.items()
        if tag not in _SAFE_LOADER_ONLY_TAGS
    }

    def construct_object(self, node: yaml.Node, deep: bool = False) -> Any:
        try:
            return super().construct_object(node, deep)
        except (yaml.YAMLError, RecursionError):
            raise
        except Exception as exc:
            raise _construction_error(node, exc) from exc

    def construct_document(self, node: yaml.Node) -> Any:
        self._check_tree(node, set())
        try:
            return super().construct_document(node)
        except (yaml.YAMLError, RecursionError):
            raise
        except Exception as exc:
            raise _construction_error(node, exc) from exc

    def _check_tree(self, node: yaml.Node, visited: set[int]) -> None:
        if id(node) in visited:
            return
        visited.add(id(node))
        if isinstance(node, yaml.SequenceNode):
            for child in node.value:
                self._check_tree(child, visited)
        elif isinstance(node, yaml.MappingNode):
            for key_node, value_node in node.value:
                if key_node.tag == _MERGE_TAG:
                    for source in _merge_sources(value_node):
                        if source.tag not in self.yaml_constructors:
                            self.construct_undefined(source)
                self._check_tree(key_node, visited)
                self._check_tree(value_node, visited)


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


# Each Unicode 15.0 decimal digit (``unicodedata.decimal`` on Python 3.12, Unicode 15.0.0), as inclusive code point
# ranges. ``int()`` and ``float()`` read any decimal digit the interpreter's Unicode version knows, so ``!!int "𐵁𐵀"``
# (Garay, Unicode 16.0) is 10 on Python 3.14 and refused on 3.12; a digit outside this table is refused on every
# Python, as the TypeScript and Java readers refuse it from the same table (census odd-inputs line 101).
UNICODE_15_DIGITS: tuple[tuple[int, int], ...] = (
    (0x0030, 0x0039),
    (0x0660, 0x0669),
    (0x06F0, 0x06F9),
    (0x07C0, 0x07C9),
    (0x0966, 0x096F),
    (0x09E6, 0x09EF),
    (0x0A66, 0x0A6F),
    (0x0AE6, 0x0AEF),
    (0x0B66, 0x0B6F),
    (0x0BE6, 0x0BEF),
    (0x0C66, 0x0C6F),
    (0x0CE6, 0x0CEF),
    (0x0D66, 0x0D6F),
    (0x0DE6, 0x0DEF),
    (0x0E50, 0x0E59),
    (0x0ED0, 0x0ED9),
    (0x0F20, 0x0F29),
    (0x1040, 0x1049),
    (0x1090, 0x1099),
    (0x17E0, 0x17E9),
    (0x1810, 0x1819),
    (0x1946, 0x194F),
    (0x19D0, 0x19D9),
    (0x1A80, 0x1A89),
    (0x1A90, 0x1A99),
    (0x1B50, 0x1B59),
    (0x1BB0, 0x1BB9),
    (0x1C40, 0x1C49),
    (0x1C50, 0x1C59),
    (0xA620, 0xA629),
    (0xA8D0, 0xA8D9),
    (0xA900, 0xA909),
    (0xA9D0, 0xA9D9),
    (0xA9F0, 0xA9F9),
    (0xAA50, 0xAA59),
    (0xABF0, 0xABF9),
    (0xFF10, 0xFF19),
    (0x104A0, 0x104A9),
    (0x10D30, 0x10D39),
    (0x11066, 0x1106F),
    (0x110F0, 0x110F9),
    (0x11136, 0x1113F),
    (0x111D0, 0x111D9),
    (0x112F0, 0x112F9),
    (0x11450, 0x11459),
    (0x114D0, 0x114D9),
    (0x11650, 0x11659),
    (0x116C0, 0x116C9),
    (0x11730, 0x11739),
    (0x118E0, 0x118E9),
    (0x11950, 0x11959),
    (0x11C50, 0x11C59),
    (0x11D50, 0x11D59),
    (0x11DA0, 0x11DA9),
    (0x11F50, 0x11F59),
    (0x16A60, 0x16A69),
    (0x16AC0, 0x16AC9),
    (0x16B50, 0x16B59),
    (0x1D7CE, 0x1D7FF),
    (0x1E140, 0x1E149),
    (0x1E2F0, 0x1E2F9),
    (0x1E4F0, 0x1E4F9),
    (0x1E950, 0x1E959),
    (0x1FBF0, 0x1FBF9),
)


def _is_unicode_15_digit(code_point: int) -> bool:
    return any(start <= code_point <= end for start, end in UNICODE_15_DIGITS)


def _refuse_newer_digits(node: yaml.ScalarNode, what: str) -> None:
    """Refuse ``node``'s text when it holds a decimal digit Unicode 15.0 does not, as Python 3.12's ``what`` does."""
    for ch in str(node.value):
        if (
            ord(ch) > 0x7F
            and unicodedata.decimal(ch, None) is not None
            and not _is_unicode_15_digit(ord(ch))
        ):
            raise ValueError(f"could not convert {node.value!r} with {what}()")


def _construct_yaml_int(loader: yaml.SafeLoader, node: yaml.ScalarNode) -> Any:
    _refuse_newer_digits(node, "int")
    return yaml.SafeLoader.construct_yaml_int(loader, node)


def _construct_yaml_float(loader: yaml.SafeLoader, node: yaml.ScalarNode) -> Any:
    _refuse_newer_digits(node, "float")
    return yaml.SafeLoader.construct_yaml_float(loader, node)


_PermissiveTimestampSafeLoader.add_constructor(
    "tag:yaml.org,2002:int", _construct_yaml_int
)
_PermissiveTimestampSafeLoader.add_constructor(
    "tag:yaml.org,2002:float", _construct_yaml_float
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


class YamlProfileError(Exception):
    """A YAML file outside the SPEC 8.7 profile; the caller refuses it under the file role's ``input.*`` key."""

    def __init__(self, violation: ProfileViolation) -> None:
        super().__init__(violation.message())
        self.violation = violation


def _load(text: str) -> Any:
    """``text`` checked against the YAML profile, composed, checked for repeated keys, then constructed."""
    found = profile_violation(text)
    if found is not None:
        raise YamlProfileError(found)
    loader = _PermissiveTimestampSafeLoader(text)
    try:
        node = loader.get_single_node()
        line = duplicate_key_line(node)
        if line is not None:
            raise YamlProfileError(ProfileViolation("duplicate_key", line))
        return loader.construct_document(node) if node is not None else None
    finally:
        loader.dispose()


def load_catalog_yaml(text: str) -> Any:
    """A catalog file's ``text`` read as :func:`load_untrusted_yaml` reads it; its :class:`YamlProfileError`,
    ``yaml.YAMLError`` and ``RecursionError`` are the caller's to report."""
    return _load(text)


def load_untrusted_yaml(path: Path, *, key: str, what: str) -> Any:
    """Load and return the YAML at ``path`` under the SPEC 8.7 profile, refusing hazards under the ``input.*``
    ``key``."""
    try:
        text = path.read_text(encoding="utf-8")
        return _load(text)
    except YamlProfileError as exc:
        raise InputError(
            key,
            f"{what} at {str(path)!r} {exc.violation.message()}.",
            f"{exc.violation.hint}.",
        ) from exc
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
