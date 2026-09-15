"""Extract the populated support-matrix members of a canonical event (SPEC 12.3).

A *member* is a dotted path to a populated evidence field in an event payload: a scalar or list field
is named directly (``operation``, ``input_ref``, ``count``), and a nested object field is named one
level deep (``model.provider``, ``tool.name``, ``usage.input_tokens``, ``refs.authorization``). The
envelope, the JSON-LD framing (``@context``/``@type``), the integrity block, and the opaque ``ext``
bag are not evidence members and are excluded. ``check_support_matrix`` compares the members the
adapter actually populates, across its fixtures, against those the support matrix declares.
"""

from __future__ import annotations

from typing import Any

#: Payload keys that carry framing or metadata rather than evidence, excluded from the member set.
_NON_MEMBERS = frozenset({"@context", "@type", "integrity", "ext"})


def members_of(event: dict[str, Any]) -> set[str]:
    """Return the set of populated support-matrix member paths in one canonical event."""
    data = event.get("data")
    if not isinstance(data, dict):
        return set()
    members: set[str] = set()
    for key, value in data.items():
        if key in _NON_MEMBERS:
            continue
        if isinstance(value, dict):
            for sub_key in value:
                members.add(f"{key}.{sub_key}")
        else:
            members.add(key)
    return members
