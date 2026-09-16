"""Build the provenance graph from ingested events (SPEC §6.3, §7.2).

Events are materialised into a PROV-O profile stored in the SQLite graph store (ADR-0001): each event
becomes a typed node with deterministic IRIs, the §6.3 relations become edges, and the engine
materialises the glue edges that let the Portable Shape Profile avoid unbounded path traversal --
``agentce:chainTerminus``, ``agentce:chainVerified``, ``agentce:executesConsequential``,
``agentce:oversightModalityMatchesDeclared``, ``agentce:danglingRef``, and ``agentce:precededBy`` --
from the domain binding, the delegation events, and the references between events. The
``rdfs:subClassOf*`` closure of the class hierarchy (base vocabulary plus the domain binding) is
materialised so class membership needs no inference (SPEC §7.2). Everything is deterministic.
"""

from __future__ import annotations

from typing import Any

from .domain import DomainBinding
from .iri import ZERO_KEY, event_iri, is_event_ref, principal_iri
from .store import GraphStore

BOOL = "xsd:boolean"
DATETIME = "xsd:dateTime"
INTEGER = "xsd:integer"

#: The base class hierarchy (child -> parent), mirroring spec/vocab/agentce.ttl (SPEC §6.3).
BASE_SUBCLASS: dict[str, str] = {
    "prov:SoftwareAgent": "prov:Agent",
    "agentce:Agent": "prov:SoftwareAgent",
    "agentce:Principal": "prov:Agent",
    "agentce:HumanPrincipal": "agentce:Principal",
    "agentce:ServicePrincipal": "agentce:Principal",
    "agentce:Activity": "prov:Activity",
    "agentce:ContextItem": "prov:Entity",
    "agentce:PolicyDecision": "agentce:Activity",
    "agentce:DelegationIssued": "agentce:Activity",
    "agentce:Decision": "agentce:Activity",
    "agentce:ConsequentialDecision": "agentce:Decision",
    "agentce:Instruction": "agentce:Activity",
    "agentce:Refusal": "agentce:Activity",
    "agentce:ToolCall": "agentce:Activity",
    "agentce:ModelCall": "agentce:Activity",
    "agentce:ResourceAccess": "agentce:Activity",
    "agentce:MemoryRead": "agentce:Activity",
    "agentce:MemoryWrite": "agentce:Activity",
}

#: refs.* keys that map directly to an edge from the referring event (SPEC §6.3).
_GENERIC_REFS: dict[str, str] = {
    "authorization": "agentce:authorizedBy",
    "request": "agentce:authorizedBy",
    "delegation": "agentce:delegatedVia",
    "instruction": "agentce:actsOn",
    "parent": "agentce:derivedFrom",
    "origin": "agentce:derivedFrom",
}

#: Instruction source classes an agent may act on without corroboration (SPEC §7.7, Appendix F). Any
#: other declared class is untrusted for the Conduct overlay's provenance and isolation controls.
_TRUSTED_INSTRUCTION: frozenset[str] = frozenset(
    {"user", "operator", "service", "agent_identified", "memory_trusted"}
)


def _closure(subclass: dict[str, str], classes: set[str]) -> set[tuple[str, str]]:
    nodes = set(classes) | set(subclass) | set(subclass.values())
    pairs: set[tuple[str, str]] = {(node, node) for node in nodes}  # reflexive
    for node in nodes:
        current = node
        seen: set[str] = set()
        while current in subclass and current not in seen:
            seen.add(current)
            current = subclass[current]
            pairs.add((node, current))
    return pairs


def _principal_ref(entry: Any) -> tuple[str | None, str | None]:
    """A principal in ``acted_for`` is a bare id string or ``{id, kind}`` (SPEC §5.3)."""
    if isinstance(entry, str):
        return entry, None
    if isinstance(entry, dict) and isinstance(entry.get("id"), str):
        kind = entry.get("kind")
        return str(entry["id"]), str(kind) if isinstance(kind, str) else None
    return None, None


def _principal_class(kind: str | None) -> str:
    if kind == "human":
        return "agentce:HumanPrincipal"
    if kind == "service":
        return "agentce:ServicePrincipal"
    return "agentce:Principal"


def _data(event: dict[str, Any]) -> dict[str, Any]:
    data = event.get("data")
    return data if isinstance(data, dict) else {}


def _refs(event: dict[str, Any]) -> dict[str, Any]:
    refs = _data(event).get("refs")
    return refs if isinstance(refs, dict) else {}


class _Builder:
    def __init__(self, store: GraphStore, domain: DomainBinding, key: bytes) -> None:
        self.store = store
        self.domain = domain
        self.key = key
        self.event_iris: set[str] = set()
        self.decision_type: dict[str, str] = {}  # decision event IRI -> decision_type
        self.decision_time: dict[str, str] = {}
        self.decision_reviewed: set[str] = set()
        self.instruction_untrusted: dict[
            str, bool
        ] = {}  # Instruction IRI -> untrusted flag

    def build(self, events: list[dict[str, Any]]) -> GraphStore:
        used_classes: set[str] = set(BASE_SUBCLASS) | {
            "prov:Agent",
            "prov:Activity",
            "prov:Entity",
        }
        for event in events:
            self.event_iris.add(event_iri(str(event["id"])))
        for event in events:
            used_classes.add(f"agentce:{self._ptype(event)}")
            self._map_event(event)
        used_classes |= set(
            self.decision_type.values()
        )  # domain decision subclasses actually used
        self.store.add_subclass_closure(
            _closure({**BASE_SUBCLASS, **self.domain.subclasses}, used_classes)
        )
        self._materialise(events)
        self.store.commit()
        return self.store

    def _ptype(self, event: dict[str, Any]) -> str:
        ptype = _data(event).get("@type")
        return str(ptype) if isinstance(ptype, str) else "Activity"

    def _map_event(self, event: dict[str, Any]) -> None:
        node = event_iri(str(event["id"]))
        data = _data(event)
        ptype = self._ptype(event)
        self.store.add_type(node, f"agentce:{ptype}")
        if isinstance(event.get("agentcesourceclass"), str):
            self.store.add_edge(
                node, "agentce:sourceClass", f"agentce:{event['agentcesourceclass']}"
            )
        if isinstance(event.get("time"), str):
            self.store.add_literal(node, "prov:atTime", event["time"], DATETIME)

        agent = data.get("agent")
        if isinstance(agent, dict) and isinstance(agent.get("id"), str):
            agent_id = agent["id"]
            self.store.add_type(agent_id, "agentce:Agent")
            self.store.add_edge(node, "prov:wasAssociatedWith", agent_id)
            self._map_chain(agent_id, data.get("acted_for"))

        for item in data.get("used", []) or []:
            if isinstance(item, str):
                self.store.add_type(item, "agentce:ContextItem")
                self.store.add_edge(node, "prov:used", item)

        for key, value in _refs(event).items():
            predicate = _GENERIC_REFS.get(key)
            if predicate and isinstance(value, str):
                self.store.add_edge(node, predicate, value)

        self._map_decision_links(node, ptype, event)
        self._map_conduct(node, ptype, data)

        if ptype == "DelegationIssued":
            self._map_delegation_principals(data.get("chain"))

        if ptype == "Decision":
            self.decision_time[node] = str(event.get("time", ""))
            dtype = data.get("decision_type")
            if isinstance(dtype, str):
                self.decision_type[node] = dtype
                # The decision's domain type is a subclass of agentce:Decision, so type the node
                # with it too: controls target ConsequentialDecision via rdfs:subClassOf* (SPEC §7.2).
                self.store.add_type(node, dtype)

    def _map_delegation_principals(self, chain: object) -> None:
        """Type the principals a ``DelegationIssued.chain`` declares (SPEC §6.3: ``chain`` feeds
        ``prov:actedOnBehalfOf`` alongside ``acted_for``).

        ``acted_for`` is a list of principal-id IRIs (the JSON-LD context maps it ``@type: @id``), so
        it carries no ``kind``; the principal kinds — which is how a human overseer in the chain is
        recognised — are declared here, on the delegation's ``chain`` Principal objects. Typing them by
        pseudonymised IRI means an ``acted_for`` id that names the same principal resolves to a typed
        node (a ``HumanPrincipal`` chain terminus for the OVS family), with no unbounded traversal."""
        if not isinstance(chain, list):
            return
        for index, entry in enumerate(chain):
            pid, kind = _principal_ref(entry)
            if pid is None:
                continue
            p_iri = principal_iri(pid, self.key)
            self.store.add_type(p_iri, _principal_class(kind))
            self.store.add_literal(p_iri, "agentce:chainIndex", str(index), INTEGER)

    def _map_chain(self, agent_id: str, acted_for: object) -> None:
        if not isinstance(acted_for, list):
            return
        for index, entry in enumerate(acted_for):
            pid, kind = _principal_ref(entry)
            if pid is None:
                continue
            p_iri = principal_iri(pid, self.key)
            self.store.add_type(p_iri, _principal_class(kind))
            self.store.add_edge(agent_id, "prov:actedOnBehalfOf", p_iri)
            self.store.add_literal(p_iri, "agentce:chainIndex", str(index), INTEGER)

    def _map_decision_links(self, node: str, ptype: str, event: dict[str, Any]) -> None:
        refs = _refs(event)
        decision = refs.get("decision")
        if isinstance(decision, str):
            if ptype == "ToolCall":
                self.store.add_edge(node, "agentce:executes", decision)
            elif ptype == "Outcome":
                self.store.add_edge(decision, "agentce:resultedIn", node)
            elif ptype == "ApprovalDecided":
                self.store.add_edge(decision, "agentce:reviewedBy", node)
                self.decision_reviewed.add(decision)
            elif ptype == "Override":
                self.store.add_edge(decision, "agentce:overriddenBy", node)
            elif ptype == "Interrupt":
                self.store.add_edge(decision, "agentce:interruptedBy", node)
            elif ptype == "Notice":
                self.store.add_edge(decision, "agentce:notifiedBy", node)
        if ptype == "Refusal":
            instruction = refs.get("instruction")
            if isinstance(instruction, str):
                self.store.add_edge(instruction, "agentce:refusedBy", node)

    def _map_conduct(self, node: str, ptype: str, data: dict[str, Any]) -> None:
        """Materialise the Conduct-overlay flags (SPEC §7.7): whether an action stayed within its
        declared task scope and budget, and whether an instruction's source class is untrusted. The
        engine takes an enforcement point's scope and budget determinations from the record and
        defaults to conformant when a stream declares neither, so the flags are inert for the base
        catalog, which never reads them."""
        if ptype in ("ToolCall", "ResourceAccess"):
            within_scope = data.get("within_scope", True)
            within_budget = data.get("within_budget", True)
            self.store.add_literal(
                node, "agentce:withinScope", "true" if within_scope else "false", BOOL
            )
            self.store.add_literal(
                node, "agentce:withinBudget", "true" if within_budget else "false", BOOL
            )
        if ptype == "Instruction":
            source_class = data.get("source_class")
            untrusted = (
                isinstance(source_class, str)
                and source_class not in _TRUSTED_INSTRUCTION
            )
            self.instruction_untrusted[node] = untrusted
            self.store.add_literal(
                node,
                "agentce:instructionUntrusted",
                "true" if untrusted else "false",
                BOOL,
            )

    def _acts_on_untrusted(self, events: list[dict[str, Any]]) -> None:
        """Flag every ToolCall/Decision that acts on an untrusted instruction (SPEC §7.7, CND-05).
        Runs after every event is mapped so the acting event may precede its instruction."""
        for event in events:
            ptype = self._ptype(event)
            if ptype not in ("ToolCall", "Decision"):
                continue
            instruction = _refs(event).get("instruction")
            untrusted = isinstance(instruction, str) and self.instruction_untrusted.get(
                instruction, False
            )
            self.store.add_literal(
                event_iri(str(event["id"])),
                "agentce:actsOnUntrusted",
                "true" if untrusted else "false",
                BOOL,
            )

    # --- materialised (glue) edges (SPEC §7.2) ---

    def _materialise(self, events: list[dict[str, Any]]) -> None:
        for event in events:
            node = event_iri(str(event["id"]))
            ptype = self._ptype(event)
            data = _data(event)
            self._dangling(node, event)
            if ptype == "DelegationIssued":
                self._chain_verified(node, data)
            if ptype == "ToolCall":
                self._executes_consequential(node, _refs(event))
            if ptype == "Decision":
                self._oversight_matches(node, data)
            self._chain_terminus(node, data)
        self._acts_on_untrusted(events)
        self._preceded_by()

    def _dangling(self, node: str, event: dict[str, Any]) -> None:
        candidates: list[str] = [v for v in _refs(event).values() if isinstance(v, str)]
        candidates += [
            v for v in _data(event).get("used", []) or [] if isinstance(v, str)
        ]
        for value in candidates:
            if is_event_ref(value) and value not in self.event_iris:
                self.store.add_literal(node, "agentce:danglingRef", value)

    def _chain_verified(self, node: str, data: dict[str, Any]) -> None:
        verification = data.get("verification")
        verified = (
            isinstance(verification, dict) and verification.get("status") == "verified"
        )
        if "chain_verified" in data:
            verified = bool(data["chain_verified"])
        self.store.add_literal(
            node, "agentce:chainVerified", "true" if verified else "false", BOOL
        )

    def _executes_consequential(self, node: str, refs: dict[str, Any]) -> None:
        decision = refs.get("decision")
        consequential = False
        if isinstance(decision, str):
            consequential = (
                self.decision_type.get(decision, "") in self.domain.consequential
            )
        self.store.add_literal(
            node,
            "agentce:executesConsequential",
            "true" if consequential else "false",
            BOOL,
        )

    def _oversight_matches(self, node: str, data: dict[str, Any]) -> None:
        dtype = data.get("decision_type")
        required = (
            self.domain.required_oversight.get(str(dtype))
            if isinstance(dtype, str)
            else None
        )
        observed = data.get("oversight_modality")
        matches = required is None or observed == required
        self.store.add_literal(
            node,
            "agentce:oversightModalityMatchesDeclared",
            "true" if matches else "false",
            BOOL,
        )

    def _chain_terminus(self, node: str, data: dict[str, Any]) -> None:
        acted_for = data.get("acted_for")
        if isinstance(acted_for, list) and acted_for:
            pid, _kind = _principal_ref(acted_for[-1])
            if pid is not None:
                self.store.add_edge(
                    node, "agentce:chainTerminus", principal_iri(pid, self.key)
                )

    def _preceded_by(self) -> None:
        by_type: dict[str, list[str]] = {}
        for node, dtype in self.decision_type.items():
            by_type.setdefault(dtype, []).append(node)
        for nodes in by_type.values():
            ordered = sorted(nodes, key=lambda n: (self.decision_time.get(n, ""), n))
            for position, node in enumerate(ordered):
                for earlier in reversed(ordered[:position]):
                    if earlier in self.decision_reviewed:
                        self.store.add_edge(node, "agentce:precededBy", earlier)
                        break


def build_graph(
    events: list[dict[str, Any]],
    *,
    domain: DomainBinding | None = None,
    store: GraphStore | None = None,
    pseudonym_key: bytes = ZERO_KEY,
) -> GraphStore:
    """Materialise ``events`` into a graph store; create an in-memory store if none is given."""
    builder = _Builder(
        store or GraphStore(), domain or DomainBinding.empty(), pseudonym_key
    )
    return builder.build(events)
