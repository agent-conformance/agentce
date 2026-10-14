"""The class a control's judgment rests on, against the control's minimum (SPEC §6.4, HR-5, §7.7.5).

Every control declares ``evaluation.min_source_class``. An assertion meets it only when every applicable focus
does: :func:`judged_class` is the lowest class among the focus's own record (left out for a Decision, which is the
agent's own account by nature, §7.7.5) and the records one hop away through each predicate the control names in
``evaluation.source_class_from``; a Decision that reaches none is judged through the ToolCalls that execute it, and
one that reaches nothing that way either is ``self_report``. A record's class is its own ``agentcesourceclass``,
except for an Instruction, which counts as ``enforcement_point`` only when attested (:meth:`SourceClassJudge.attested`) and as
``self_report`` otherwise: an agent's own record that it was told something by the user is never taken as given.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from .graph import _TRUSTED_INSTRUCTION, _data, _refs, _text
from .statistical import event_type as _type
from .store import GraphStore
from .structural import compare_instants

#: The source classes in their SPEC §6.4 order; the rank is the order. ``any`` is not a class.
SOURCE_CLASS_RANK: dict[str, int] = {
    "self_report": 0,
    "enforcement_point": 1,
    "independent_system": 2,
}
ENFORCEMENT_POINT = "enforcement_point"

#: The graph links ``evaluation.source_class_from`` may name, each one hop from the focus (SPEC §6.3), and the
#: record type each one names.
SOURCE_CLASS_FROM: dict[str, str] = {
    "actsOn": "Instruction",
    "delegatedVia": "DelegationIssued",
    "approvedBy": "ApprovalDecided",
    "releasedBy": "ApprovalDecided",
}

#: The outcome an assertion takes when a focus's judged class is below the control's minimum (HR-5). One
#: constant, so a later reading of single-source evidence (SPEC §10.4 ``partial``) changes it in one place.
BELOW_MINIMUM_OUTCOME = "insufficient_evidence"


def class_rank(value: Any) -> int:
    """The rank of a record's class; a record with no class, or one outside SPEC §6.4, ranks as self_report."""
    return SOURCE_CLASS_RANK.get(value, 0) if isinstance(value, str) else 0


def _principal_id(value: Any) -> str | None:
    if isinstance(value, dict) and isinstance(value.get("id"), str) and value["id"]:
        return str(value["id"])
    return None


#: The declared classes SPEC §6.2.3 requires a principal for: a twin witnesses one only when both carry the same id.
_PRINCIPAL_REQUIRED = frozenset({"user", "operator", "service", "agent_identified"})

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
        #: Each enforcement-point Instruction with a content_ref and refs.task, by (content_ref, refs.task,
        #: principal id), in held order: the candidate twins of an instruction with those three.
        self._twins: dict[tuple[str, str, str | None], list[str]] = {}
        #: The times of the enforcement-point PolicyDecision allows naming a principal, by (refs.request, principal id).
        self._allows: dict[tuple[str, str], list[Any]] = {}
        #: Each focus's clock (:meth:`_clock`), computed once.
        self._clocks: dict[str, tuple[str, ...] | None] = {}
        for iri, event in events_by_iri.items():
            if event.get("agentcesourceclass") != ENFORCEMENT_POINT:
                continue
            data, refs = _data(event), _refs(event)
            ptype = _type(event)
            principal = _principal_id(data.get("principal"))
            if ptype == "Instruction":
                content, task = _text(data.get("content_ref")), _text(refs.get("task"))
                if content is not None and task is not None:
                    self._twins.setdefault((content, task, principal), []).append(iri)
            elif (
                ptype == "PolicyDecision"
                and data.get("decision") == "allow"
                and isinstance(refs.get("request"), str)
                and principal is not None
            ):
                self._allows.setdefault((refs["request"], principal), []).append(
                    event.get("time")
                )

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
        if self._twin_witnesses(focus, node, event, visiting):
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

    def _clock(self, focus: str) -> tuple[str, ...] | None:
        """The times a witness must not follow, read only where the agent does not write them: an
        enforcement-point (or better) focus's own time; for a Decision, self_report by nature, the time of every
        enforcement-point ToolCall executing it. None when there is no such clock (a self_report ToolCall, a
        Decision no enforcement-point call executes) or one of those times is missing."""
        if focus not in self._clocks:
            event = self.events.get(focus)
            if event is not None and _type(event) == "Decision":
                anchors = [
                    self.events[call]
                    for call in self.store.subjects("agentce:executes", focus)
                    if call in self.events and self._own_rank(call) > 0
                ]
            else:
                anchors = (
                    [event] if event is not None and self._own_rank(focus) > 0 else []
                )
            times = tuple(t for a in anchors if isinstance(t := a.get("time"), str))
            ok = bool(anchors) and len(times) == len(anchors)
            self._clocks[focus] = times if ok else None
        return self._clocks[focus]

    def _in_time(self, focus: str, witnessed_at: Any) -> bool:
        """Whether a witness recorded at ``witnessed_at`` is at or before the focus's clock, as instants."""
        clock = self._clock(focus)
        return (
            clock is not None
            and isinstance(witnessed_at, str)
            and all(compare_instants(witnessed_at, t) in (-1, 0) for t in clock)
        )

    def _twin_witnesses(
        self, focus: str, node: str, event: dict[str, Any], visiting: frozenset[str]
    ) -> bool:
        """(iv) by an enforcement-point twin: the same content_ref, refs.task, source_class and principal (present on
        both for a class that requires one), held under the subject, recorded at or before the focus's clock, meeting
        (i)-(iv) by its own class, every instruction above it declaring a trusted class."""
        data = _data(event)
        content, task = _text(data.get("content_ref")), _text(_refs(event).get("task"))
        principal = _principal_id(data.get("principal"))
        if content is None or task is None:
            return False
        if principal is None and data.get("source_class") in _PRINCIPAL_REQUIRED:
            return False
        for twin in self._twins.get((content, task, principal), ()):
            if twin == node or twin in visiting:
                continue
            if (
                _data(self.events[twin]).get("source_class") == data.get("source_class")
                and self._in_time(focus, self.events[twin].get("time"))
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
        the instruction's (both present), recorded at or before the focus's clock."""
        principal = _principal_id(_data(event).get("principal"))
        return principal is not None and any(
            self._in_time(focus, at) for at in self._allows.get((focus, principal), ())
        )

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

    def _reached(
        self, focus: str, source_class_from: Sequence[str]
    ) -> list[tuple[int, str]]:
        """The ranks of the records one named link from ``focus``."""
        return [
            self._attested_rank(focus, node, SOURCE_CLASS_FROM[name])
            for name in source_class_from
            for node in self.store.objects(focus, f"agentce:{name}")
        ]

    def judged_class(self, focus: str, source_class_from: Sequence[str]) -> Judged:
        """The lowest class the judgment of ``focus`` rests on. A Decision is self_report by nature (§7.7.5), so it
        rests on what it reaches through the named links, or failing those on what every ToolCall executing it
        reaches; a Decision that reaches nothing either way rests on its own record, self_report.
        ``source_class_from`` holds distinct :data:`SOURCE_CLASS_FROM` names, sorted (as the catalog stores them)."""
        event = self.events.get(focus)
        if event is not None and _type(event) == "Decision":
            contributors = self._reached(focus, source_class_from) or [
                reached
                for call in self.store.subjects("agentce:executes", focus)
                for reached in self._reached(call, source_class_from)
            ]
            if not contributors:
                contributors = [(0, "Decision")]
        elif "releasedBy" in source_class_from:
            return self._released_class(focus, event)
        else:
            contributors = [(self._own_rank(focus), _type(event) if event else "")]
            contributors += self._reached(focus, source_class_from)
        rank = min(r for r, _ in contributors)
        return Judged(rank, tuple(sorted({t for r, t in contributors if r == rank})))

    def _released_class(self, focus: str, event: dict[str, Any] | None) -> Judged:
        """CND-02's reading (SPEC §7.7.3, §7.7.4): the minimum governs the approvals that release an action's gate, so
        the action rests on the best of them (one approval meeting the minimum releases it beside a weaker one), and
        on its own record only when that is below enforcement_point. An action whose gate no approval releases passes
        only on an enforcement-point allow, which the control itself holds to enforcement_point: it is not below."""
        own = self._own_rank(focus)
        contributors = (
            [(own, _type(event) if event else "")]
            if own < SOURCE_CLASS_RANK[ENFORCEMENT_POINT]
            else []
        )
        released = self._reached(focus, ("releasedBy",))
        if released:
            best = max(r for r, _ in released)
            contributors += [(r, t) for r, t in released if r == best]
        if not contributors:
            return Judged(max(SOURCE_CLASS_RANK.values()), ())
        rank = min(r for r, _ in contributors)
        return Judged(rank, tuple(sorted({t for r, t in contributors if r == rank})))


def below_minimum(
    judge: Callable[[], SourceClassJudge],
    foci: Iterable[str],
    minimum: str,
    source_class_from: list[str],
) -> tuple[list[str], tuple[str, ...]]:
    """The foci whose judged class is below ``minimum``, sorted, and the record types that put them there.
    ``judge`` builds (or returns) the subject's judge, called only when a focus can fall below the minimum."""
    floor = SOURCE_CLASS_RANK[minimum]
    below: list[str] = []
    kinds: set[str] = set()
    if floor == 0:  # nothing ranks below self_report
        return below, ()
    for focus in sorted(foci):
        judged = judge().judged_class(focus, source_class_from)
        if judged.rank < floor:
            below.append(focus)
            kinds.update(judged.lowest)
    return below, tuple(sorted(kinds))
