"""Deterministic IRIs for graph nodes (SPEC §6.3, IR-12).

Events become ``agentce:event/<id>``. Principals are pseudonymised: ``agentce:principal/<mac>`` where
``mac`` is HMAC-SHA-256 of the principal id under a per-subject key supplied by reference
(``--pseudonym-key-file``), so principal IRIs cannot be reversed by dictionary. A run without a key
uses the documented all-zero key and reports ``pseudonymisation: none`` as a limitation. Agent ids
(SPIFFE ids or IRIs) are used verbatim as node IRIs, and ``refs.*`` values are already CURIEs.
"""

from __future__ import annotations

import hashlib
import hmac

#: The documented default pseudonymisation key (all zero); a real run supplies its own by reference.
ZERO_KEY = b"\x00" * 32


def event_iri(event_id: str) -> str:
    return f"agentce:event/{event_id}"


def principal_iri(raw_id: str, key: bytes = ZERO_KEY) -> str:
    mac = hmac.new(key, raw_id.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"agentce:principal/{mac}"


def is_event_ref(value: str) -> bool:
    """True if ``value`` is a CURIE that refers to an event node."""
    return value.startswith("agentce:event/")


def event_id_of(value: str) -> str:
    """Return the event id from an ``agentce:event/<id>`` CURIE."""
    return value.split("/", 1)[1] if is_event_ref(value) else value
