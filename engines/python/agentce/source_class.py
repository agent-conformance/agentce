"""The class a control's judgment rests on, against the control's minimum (SPEC §6.4, HR-5, §7.7.5).

Every control declares ``evaluation.min_source_class``. An assertion meets it only when every applicable focus
does: :func:`judged_class` is the lowest class among the focus's own record (left out for a Decision, which is the
agent's own account by nature, §7.7.5) and the records one hop away through each predicate the control names in
``evaluation.source_class_from``. A record's class is its own ``agentcesourceclass``, except for an Instruction,
which counts as ``enforcement_point`` only when attested (:meth:`SourceClassJudge.attested`) and as
``self_report`` otherwise: an agent's own record that it was told something by the user is never taken as given.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from .store import GraphStore

#: The source classes in their SPEC §6.4 order; the rank is the order. ``any`` is not a class.
SOURCE_CLASS_RANK: dict[str, int] = {
    "self_report": 0,
    "enforcement_point": 1,
    "independent_system": 2,
}
SELF_REPORT = "self_report"
ENFORCEMENT_POINT = "enforcement_point"

#: The graph links ``evaluation.source_class_from`` may name, each one hop from the focus (SPEC §6.3), and the
#: record type each one names.
SOURCE_CLASS_FROM: dict[str, str] = {
    "actsOn": "Instruction",
    "delegatedVia": "DelegationIssued",
    "approvedBy": "ApprovalDecided",
}

#: The outcome an assertion takes when a focus's judged class is below the control's minimum (HR-5). One
#: constant, so a later reading of single-source evidence (SPEC §10.4 ``partial``) changes it in one place.
BELOW_MINIMUM_OUTCOME = "insufficient_evidence"

#: The Appendix F instruction source classes an agent may act on (the graph's trusted set, SPEC §7.7).
_TRUSTED_INSTRUCTION: frozenset[str] = frozenset(
    {"user", "operator", "service", "agent_identified", "memory_trusted"}
)


def class_rank(value: Any) -> int:
    """The rank of a record's class; a record with no class, or one outside SPEC §6.4, ranks as self_report."""
    return SOURCE_CLASS_RANK.get(value, 0) if isinstance(value, str) else 0


def _data(event: dict[str, Any]) -> dict[str, Any]:
    data = event.get("data")
    return data if isinstance(data, dict) else {}


def _refs(event: dict[str, Any]) -> dict[str, Any]:
    refs = _data(event).get("refs")
    return refs if isinstance(refs, dict) else {}


def _type(event: dict[str, Any]) -> str:
    ptype = _data(event).get("@type")
    return ptype if isinstance(ptype, str) else ""


def _principal_id(value: Any) -> str | None:
    if isinstance(value, dict) and isinstance(value.get("id"), str) and value["id"]:
        return str(value["id"])
    return None


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


#: Which witnesses may stand in for an instruction's own class at (iv): its own class only (a twin), its own
#: class or a twin (a parent: the PolicyDecision allows the action, which acts on the child only), or all.
_OWN, _TWIN, _ALL = 0, 1, 2


@dataclass(frozen=True)
class Judged:
    """A focus's judged class: its rank and the record types that set it (the lowest contributors)."""

    rank: int
    lowest: tuple[str, ...]


class SourceClassJudge:
    """Judges foci against one subject's held records (``events_by_iri``: graph IRI -> event) and graph."""

    def __init__(
        self, store: GraphStore, events_by_iri: dict[str, dict[str, Any]]
    ) -> None:
        self.store = store
        self.events = events_by_iri
        self._instructions = [
            iri for iri, event in events_by_iri.items() if _type(event) == "Instruction"
        ]
        self._policy_decisions = [
            iri
            for iri, event in events_by_iri.items()
            if _type(event) == "PolicyDecision"
        ]

    def _own_rank(self, node: str) -> int:
        event = self.events.get(node)
        return class_rank(event.get("agentcesourceclass")) if event else 0

    def _held_instruction(self, node: Any) -> dict[str, Any] | None:
        """(i): the Instruction a ref names by its graph IRI among this subject's held records."""
        event = self.events.get(node) if isinstance(node, str) else None
        return event if event is not None and _type(event) == "Instruction" else None

    def attested(self, focus: str, instruction: str) -> bool:
        """Whether ``instruction`` is attested for ``focus`` (ruling 5: an enforcement-point ingress record): (i)
        held under the subject by graph IRI; (ii) every refs.parent held and itself attested, by its own class or
        a twin; (iii) its refs.origin, if any, an enforcement-point MemoryRead at trust_min trusted; (iv) its own
        class enforcement_point, or witnessed by an attested enforcement-point twin or by an enforcement-point
        PolicyDecision allowing the focus for the instruction's principal (corroboration replaces only (iv))."""
        return self._attested(focus, instruction, frozenset(), _ALL)

    def _attested(
        self, focus: str, node: str, visiting: frozenset[str], witnesses: int
    ) -> bool:
        event = self._held_instruction(node)
        if event is None:  # (i) held under the subject by graph IRI
            return False
        # A parent cycle: the node is already being judged further up the chain.
        if node in visiting:
            return True
        visiting = visiting | {node}
        refs = _refs(event)
        if "parent" in refs and not (  # (ii) every parent held and itself attested
            isinstance(refs["parent"], str)
            and self._attested(focus, refs["parent"], visiting, min(witnesses, _TWIN))
        ):
            return False
        if "origin" in refs and not self._trusted_origin(refs["origin"]):  # (iii)
            return False
        # (iv) by its own class, exactly enforcement_point (ruling 5); then the witnesses this hop allows.
        if event.get("agentcesourceclass") == ENFORCEMENT_POINT:
            return True
        if witnesses == _OWN:
            return False
        if self._twin_witnesses(node, event, visiting):
            return True
        return witnesses == _ALL and self._decision_witnesses(focus, event)

    def _trusted_origin(self, origin: Any) -> bool:
        """(iii): an origin is an enforcement-point MemoryRead held under the subject whose trust_min is trusted."""
        event = self.events.get(origin) if isinstance(origin, str) else None
        return (
            event is not None
            and _type(event) == "MemoryRead"
            and event.get("agentcesourceclass") == ENFORCEMENT_POINT
            and _data(event).get("trust_min") == "trusted"
        )

    def _twin_witnesses(
        self, node: str, event: dict[str, Any], visiting: frozenset[str]
    ) -> bool:
        """(iv) by an enforcement-point twin: the same content_ref, refs.task, source_class and principal, held
        under the subject, meeting (i)-(iv) by its own class, every instruction above it declaring a trusted class."""
        data, refs = _data(event), _refs(event)
        content, task = _text(data.get("content_ref")), _text(refs.get("task"))
        if content is None or task is None:
            return False
        for twin in self._instructions:
            if twin == node or twin in visiting:
                continue
            other = self.events[twin]
            other_data = _data(other)
            if (
                other.get("agentcesourceclass") == ENFORCEMENT_POINT
                and _text(other_data.get("content_ref")) == content
                and _text(_refs(other).get("task")) == task
                and other_data.get("source_class") == data.get("source_class")
                and _principal_id(other_data.get("principal"))
                == _principal_id(data.get("principal"))
                and self._attested(twin, twin, visiting, _OWN)
                and self._lineage_trusted(twin)
            ):
                return True
        return False

    def _lineage_trusted(self, node: str) -> bool:
        """Every instruction above ``node`` through refs.parent declares a trusted Appendix F class."""
        seen: set[str] = set()
        parent = _refs(self.events[node]).get("parent")
        while isinstance(parent, str) and parent not in seen:
            seen.add(parent)
            event = self._held_instruction(parent)
            if (
                event is None
                or _data(event).get("source_class") not in _TRUSTED_INSTRUCTION
            ):
                return False
            parent = _refs(event).get("parent")
        return True

    def _decision_witnesses(self, focus: str, event: dict[str, Any]) -> bool:
        """(iv) by an enforcement-point PolicyDecision allow whose refs.request is the focus and whose principal is
        the instruction's (both present)."""
        principal = _principal_id(_data(event).get("principal"))
        if principal is None:
            return False
        for decision in self._policy_decisions:
            record = self.events[decision]
            data = _data(record)
            if (
                record.get("agentcesourceclass") == ENFORCEMENT_POINT
                and data.get("decision") == "allow"
                and _refs(record).get("request") == focus
                and _principal_id(data.get("principal")) == principal
            ):
                return True
        return False

    def _attested_rank(self, focus: str, node: str, kind: str) -> tuple[int, str]:
        """A record one hop from the focus: an Instruction ranks enforcement_point only when attested; anything
        else ranks as its own class, and a ref naming no held record as self_report (``kind`` names it)."""
        event = self.events.get(node)
        if event is None:
            return 0, kind
        if _type(event) == "Instruction":
            ep = SOURCE_CLASS_RANK[ENFORCEMENT_POINT]
            return (ep if self.attested(focus, node) else 0), "Instruction"
        return self._own_rank(node), _type(event) or kind

    def judged_class(
        self, focus: str, source_class_from: Iterable[str]
    ) -> Judged | None:
        """The lowest class the judgment of ``focus`` rests on; ``None`` when nothing contributes (a Decision with
        no record through any named link: there is no record the judgment rests on to hold to a class)."""
        contributors: list[tuple[int, str]] = []
        event = self.events.get(focus)
        if event is None or _type(event) != "Decision":
            contributors.append((self._own_rank(focus), _type(event) if event else ""))
        for name in sorted(set(source_class_from) & set(SOURCE_CLASS_FROM)):
            for node in self.store.objects(focus, f"agentce:{name}"):
                contributors.append(
                    self._attested_rank(focus, node, SOURCE_CLASS_FROM[name])
                )
        if not contributors:
            return None
        rank = min(r for r, _ in contributors)
        return Judged(rank, tuple(sorted({t for r, t in contributors if r == rank})))


def below_minimum(
    judge: SourceClassJudge,
    foci: Iterable[str],
    minimum: str,
    source_class_from: Iterable[str],
) -> tuple[list[str], tuple[str, ...]]:
    """The foci whose judged class is below ``minimum``, sorted, and the record types that put them there."""
    floor = SOURCE_CLASS_RANK[minimum]
    named = list(source_class_from)
    below: list[str] = []
    kinds: set[str] = set()
    for focus in sorted(foci):
        judged = judge.judged_class(focus, named)
        if judged is not None and judged.rank < floor:
            below.append(focus)
            kinds.update(judged.lowest)
    return below, tuple(sorted(kinds))
