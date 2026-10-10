"""Build the provenance graph from ingested events (SPEC §6.3, §7.2).

Events are materialised into a PROV-O profile stored in the SQLite graph store (ADR-0001): each event
becomes a typed node with deterministic IRIs, the §6.3 relations become edges, and the engine
materialises the glue edges that let the Portable Shape Profile avoid unbounded path traversal --
``agentce:chainTerminus``, ``agentce:chainVerified``, ``agentce:executesConsequential``,
``agentce:oversightModalityMatchesDeclared``, ``agentce:danglingRef``, ``agentce:precededBy``,
``agentce:componentDeclared``, ``agentce:declaredComponentsObserved``, ``agentce:triggersIncident``,
``agentce:oversightCoverageComplete``, ``agentce:interventionEffective``, ``agentce:interventionByHuman``,
``agentce:incidentResponded`` and ``agentce:riskReviewed`` --
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

#: A memory record's trust, or a read's ``trust_min``, that ROB-02 treats as untrusted, and the memory
#: guard verdicts that mark a record untrusted whatever its trust field says (SPEC §7.4).
_UNTRUSTED_TRUST: frozenset[str] = frozenset({"untrusted", "quarantined"})
_UNTRUSTED_VERDICTS: frozenset[str] = frozenset({"quarantine", "block"})


#: Payload fields that carry content into their event, and those naming content their event
#: produced (SPEC §6.2); ROB-02's taint flows along both. Identity, audit and person refs carry none.
_CONTENT_IN: tuple[str, ...] = (
    "inputs",
    "used",
    "args_ref",
    "input_ref",
    "record_refs",
    "provenance_origin_ref",
)
_CONTENT_OUT: tuple[str, ...] = (
    "output_ref",
    "result_ref",
    "record_ref",
    "content_ref",
)
#: ``refs`` members that carry content into their event: the producing activity, the
#: instruction acted on and the instruction it was derived from.
_REFS_IN: tuple[str, ...] = ("origin", "instruction", "parent")

#: Interrupt effects that stop the agent, and the interrupt mechanisms SPEC §6.2 names (OVS-07).
_INTERRUPT_STOPS: frozenset[str] = frozenset({"halted", "paused"})
_INTERRUPT_MECHANISMS: frozenset[str] = frozenset(
    {"stop_button", "kill_switch", "circuit_breaker", "manual"}
)
#: The stream classes an identity-provider login record must come from (SPEC §6.2 ApprovalDecided.session_ref, §10.4).
_LOGIN_CLASSES: frozenset[str] = frozenset({"independent_system", "enforcement_point"})
#: The characters the human-actor rule trims from each end of a principal id: Unicode White_Space plus U+FEFF, written
#: out because Python's strip, JavaScript's trim and Java's strip each trim a different set (SPEC §10.4).
_ID_PAD = (
    "\t\n\x0b\x0c\r \x85\xa0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a"
    "\u2028\u2029\u202f\u205f\u3000\ufeff"
)
#: The records the SPEC §10.4 human-actor rule applies to.
_OVERSIGHT_TYPES: frozenset[str] = frozenset(
    {"ApprovalDecided", "Override", "Interrupt"}
)

#: The Incident members that record a response after detection (SPEC §6.2; ROB-07).
_INCIDENT_RESPONSES: tuple[str, ...] = (
    "causal_assessment_at",
    "provider_notified_at",
    "reported_at",
)


def _untrusted_source_class(value: Any) -> bool:
    """An Appendix F source class outside the trusted set (CND-05's rule, also ROB-02's)."""
    return isinstance(value, str) and value not in _TRUSTED_INSTRUCTION


def _is_one_of(value: Any, values: frozenset[str]) -> bool:
    return isinstance(value, str) and value in values


def _flow(edges: dict[str, set[str]], source: Any, target: Any) -> None:
    if isinstance(source, str) and isinstance(target, str):
        edges.setdefault(source, set()).add(target)


def _reach(seeds: set[str], edges: dict[str, set[str]]) -> None:
    """Grow ``seeds`` in place to everything reachable along ``edges`` (order- and cycle-free)."""
    pending = list(seeds)
    while pending:
        for target in edges.get(pending.pop(), ()):
            if target not in seeds:
                seeds.add(target)
                pending.append(target)


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


#: DOC-01 (SPEC §7.4, Art. 11 / Annex IV): the events that declare an operating component (a
#: ``BundleLoaded`` manifest) or exercise one (a ``ToolCall`` or ``ModelCall``) are typed with this
#: engine-materialised class, so one shape can compare the declaration with what operated.
COMPONENT_RECORD = "agentce:ComponentRecord"
_COMPONENT_RECORD_TYPES = frozenset({"BundleLoaded", "ToolCall", "ModelCall"})

#: Which declared component kinds a call can exercise. A tool call exercises a skill or an MCP server
#: (by its tool name or server name), a model call a model; a component with no kind may be either.
#: Prompts, configs and policies are never exercised by a call, so they take no part in DOC-01.
_FAMILIES: dict[str | None, tuple[str, ...]] = {
    "skill": ("tool",),
    "mcp_server": ("tool",),
    "model": ("model",),
    None: ("tool", "model"),
}

#: Declared kinds that must be seen operating (DOC-01 S2). A skill is left out: its name is not
#: reliably a tool name, so not seeing it is not evidence that it never ran.
_MUST_OPERATE: dict[str, str] = {"model": "model", "mcp_server": "tool"}


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _declared_components(
    data: dict[str, Any],
) -> list[tuple[str | None, str, frozenset[str]]]:
    """``(kind, name, pins)`` for each named component a ``BundleLoaded`` manifest declares, where
    ``pins`` holds its declared version and digest (empty: any version matches)."""
    components = data.get("components")
    out: list[tuple[str | None, str, frozenset[str]]] = []
    for component in components if isinstance(components, list) else []:
        if _text(component):
            out.append((None, component, frozenset()))
        elif isinstance(component, dict) and _text(component.get("name")):
            kind = component.get("kind")
            pins = (component.get("version"), component.get("digest"))
            out.append(
                (
                    kind if isinstance(kind, str) else None,
                    component["name"],
                    frozenset(filter(None, map(_text, pins))),
                )
            )
    return out


def _operated(ptype: str, data: dict[str, Any]) -> tuple[str, list[str], str | None]:
    """``(family, names, version)`` of the component a ``ToolCall`` or ``ModelCall`` exercises: a
    tool call is known by its tool name and its server name, a model call by its model name."""
    family = "tool" if ptype == "ToolCall" else "model"
    ref = data.get(family)
    if not isinstance(ref, dict):
        return family, [], None
    keys = ("name", "server") if family == "tool" else ("name",)
    names = [ref[k] for k in keys if _text(ref.get(k))]
    return family, names, _text(ref.get("version_or_digest"))


def _principal_ref(entry: Any) -> tuple[str | None, str | None]:
    """A principal in ``acted_for`` is a bare id string or ``{id, kind}`` (SPEC §5.3)."""
    if isinstance(entry, str):
        return entry, None
    if isinstance(entry, dict) and isinstance(entry.get("id"), str):
        kind = entry.get("kind")
        return str(entry["id"]), str(kind) if isinstance(kind, str) else None
    return None, None


def _rule_id(value: Any) -> str | None:
    """A principal id as the human-actor rule compares it: trimmed of ``_ID_PAD`` at both ends, ``None`` when nothing
    is left, so a padded spelling of an id is the same principal everywhere in the rule."""
    if not isinstance(value, str):
        return None
    return value.strip(_ID_PAD) or None


def _human_id(principal: Any) -> str | None:
    """The trimmed id of a human principal (SPEC §6.2 ``actor``, SessionStart ``principal``; §10.4 human-actor rule);
    ``None`` for a missing, unnamed or non-human principal."""
    if isinstance(principal, dict) and principal.get("kind") == "human":
        return _rule_id(principal.get("id"))
    return None


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
        self.outcome_decision: dict[
            str, str
        ] = {}  # Outcome IRI -> its refs.decision IRI, if any
        self.decision_notice: dict[
            str, set[str]
        ] = {}  # Decision IRI -> every notifying Notice IRI (order-independent)
        self.dangling_nodes: set[str] = (
            set()
        )  # event IRI -> has >=1 agentce:danglingRef literal
        # DOC-01: family -> declared name -> the union of its declared pins over every BundleLoaded
        # manifest in the subject's records (empty: any version matches), so event order does not
        # matter and an unpinned declaration never cancels another manifest's pin (18.37j).
        self.declared: dict[str, dict[str, set[str]]] = {"tool": {}, "model": {}}
        self.operated: dict[str, set[str]] = {"tool": set(), "model": set()}
        self.has_calls = False
        # SPEC §10.4 human-actor rule, read in the second pass so file order never matters: event IRI -> (type,
        # source class, agent id) of every event; agent id -> every principal IRI in its delegation chain (its
        # acted_for and its DelegationIssued chains); event IRI -> the principal IRIs of its own acted_for.
        # A SessionStart also records the IRI of the human principal its login names (18.140), else None.
        self.event_info: dict[str, tuple[str, str | None, str | None, str | None]] = {}
        # event IRI -> the agents it concerns: its own agent and its CloudEvents subject (the assessed agent), so an
        # event that leaves out the optional agent still concerns the subject.
        self.event_agents: dict[str, frozenset[str]] = {}
        self.agent_chain: dict[str, set[str]] = {}
        self.acted_for: dict[str, set[str]] = {}
        # OVS-08: decision IRI -> the actor IRI of every verified ApprovalDecided that reviewed it.
        self.human_reviewers: dict[str, set[str]] = {}
        # INC-03: event IRI -> the decision its refs.decision names, whatever the event type.
        self.ref_decision: dict[str, str] = {}

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
            if self._ptype(event) in _COMPONENT_RECORD_TYPES:
                used_classes.add(COMPONENT_RECORD)
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
        agent_id = (
            agent["id"]
            if isinstance(agent, dict) and isinstance(agent.get("id"), str)
            else None
        )
        source_class = event.get("agentcesourceclass")
        login_principal = (
            _human_id(data.get("principal")) if ptype == "SessionStart" else None
        )
        self.event_info[node] = (
            ptype,
            source_class if isinstance(source_class, str) else None,
            agent_id,
            None
            if login_principal is None
            else principal_iri(login_principal, self.key),
        )
        self.acted_for[node] = self._principal_iris(data.get("acted_for"))
        subject = event.get("subject")
        self.event_agents[node] = frozenset(
            a for a in (agent_id, subject) if isinstance(a, str)
        )
        for owner in self.event_agents[node]:
            chain = self.agent_chain.setdefault(owner, set())
            chain |= self.acted_for[node]
            if ptype == "DelegationIssued":
                chain |= self._principal_iris(data.get("chain"))
        if agent_id is not None:
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
        self._map_components(node, ptype, data)
        self._dangling(node, event)

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

    def _principal_iris(self, principals: object) -> set[str]:
        """The pseudonymised IRIs of an ``acted_for`` or ``chain`` list (SPEC §6.3: the delegation chain), ids trimmed
        as the human-actor rule compares them."""
        if not isinstance(principals, list):
            return set()
        ids = (_rule_id(_principal_ref(entry)[0]) for entry in principals)
        return {principal_iri(pid, self.key) for pid in ids if pid is not None}

    def _human_key(self, node: str, event: dict[str, Any]) -> str | None:
        """SPEC §10.4 human-actor rule: the actor IRI of an ApprovalDecided, Override or Interrupt whose actor is a
        human principal whose ``session_ref`` names a held identity-provider login record of that same human, and who
        is nowhere in the delegation chain of the activity; ``None`` when any part fails (fail closed).

        The login record is a held ``SessionStart`` (the only session record the event model has) from an
        independent_system or enforcement_point stream whose ``principal`` is the actor (ids trimmed of ``_ID_PAD``)
        and whose agent is none of the agents the oversight record concerns: the own agent and CloudEvents subject of
        the record and of the named decision, whose own run session is no human's login. The delegation chain is the
        named decision's and the record's own ``acted_for`` and every chain of those agents."""
        data = _data(event)
        actor = _human_id(data.get("actor"))
        login = data.get("session_ref")
        if actor is None or not isinstance(login, str) or login not in self.event_info:
            return None
        decision = _refs(event).get("decision")
        concerned = self.event_agents[node] | (
            self.event_agents.get(decision, frozenset())
            if isinstance(decision, str)
            else frozenset()
        )
        login_type, login_class, login_agent, login_principal = self.event_info[login]
        if login_type != "SessionStart" or login_class not in _LOGIN_CLASSES:
            return None
        if login_agent is None or login_agent in concerned:
            return None
        actor_iri = principal_iri(actor, self.key)
        if login_principal != actor_iri:
            return None
        chain = set(self.acted_for[node])
        if isinstance(decision, str):
            chain |= self.acted_for.get(decision, set())
        for agent_id in concerned:
            chain |= self.agent_chain.get(agent_id, set())
        return None if actor_iri in chain else actor_iri

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
            self.ref_decision[node] = decision
            if ptype == "ToolCall":
                self.store.add_edge(node, "agentce:executes", decision)
            elif ptype == "Outcome":
                self.store.add_edge(decision, "agentce:resultedIn", node)
                self.outcome_decision[node] = decision
            elif ptype == "Override":
                self.store.add_edge(decision, "agentce:overriddenBy", node)
            elif ptype == "Interrupt":
                self.store.add_edge(decision, "agentce:interruptedBy", node)
            elif ptype == "Notice":
                self.store.add_edge(decision, "agentce:notifiedBy", node)
                self.decision_notice.setdefault(decision, set()).add(node)
        if ptype == "Refusal":
            instruction = refs.get("instruction")
            if isinstance(instruction, str):
                self.store.add_edge(instruction, "agentce:refusedBy", node)

    def _map_conduct(self, node: str, ptype: str, data: dict[str, Any]) -> None:
        """Materialise the Conduct-overlay instruction-trust flag (SPEC §7.7): whether an
        instruction's declared source class is untrusted (Appendix F). Scope and budget are computed
        from the enforcement point's records in a second pass (:meth:`_conduct_scope_budget`)."""
        if ptype == "Instruction":
            source_class = data.get("source_class")
            untrusted = _untrusted_source_class(source_class)
            self.instruction_untrusted[node] = untrusted
            self.store.add_literal(
                node,
                "agentce:instructionUntrusted",
                "true" if untrusted else "false",
                BOOL,
            )

    def _map_components(self, node: str, ptype: str, data: dict[str, Any]) -> None:
        """Collect what the manifests declare and what the calls exercise (DOC-01); the two literals
        are set in the second pass, once every event is mapped."""
        if ptype not in _COMPONENT_RECORD_TYPES:
            return
        if ptype == "BundleLoaded":
            for kind, name, pins in _declared_components(data):
                for family in _FAMILIES.get(kind, ()):
                    self.declared[family].setdefault(name, set()).update(pins)
        else:
            self.store.add_type(node, COMPONENT_RECORD)
            self.has_calls = True
            family, names, _version = _operated(ptype, data)
            self.operated[family].update(names)

    def _conduct_scope_budget(self, events: list[dict[str, Any]]) -> None:
        """Compute the Conduct within-scope and within-budget flags (SPEC §7.7, CND-01/CND-07) from
        the enforcement point's records: an action is out of scope when a ``PolicyDecision`` denies
        its request, and over budget when a ``Refusal`` with reason class ``budget_exceeded`` names
        it; both default to conformant, so the flags are inert for a bundle that records neither."""
        denied: set[str] = set()
        over_budget: set[str] = set()
        for event in events:
            ptype = self._ptype(event)
            data, refs = _data(event), _refs(event)
            request = refs.get("request")
            if (
                ptype == "PolicyDecision"
                and data.get("decision") == "deny"
                and isinstance(request, str)
            ):
                denied.add(request)
            if (
                ptype == "Refusal"
                and data.get("reason_class") == "budget_exceeded"
                and isinstance(request, str)
            ):
                over_budget.add(request)
        for event in events:
            if self._ptype(event) not in ("ToolCall", "ResourceAccess"):
                continue
            node = event_iri(str(event["id"]))
            self.store.add_literal(
                node, "agentce:withinScope", "false" if node in denied else "true", BOOL
            )
            self.store.add_literal(
                node,
                "agentce:withinBudget",
                "false" if node in over_budget else "true",
                BOOL,
            )

    def _acts_on_untrusted(
        self, events: list[dict[str, Any]], tainted: set[str]
    ) -> None:
        """Flag every ToolCall/Decision that acts on an instruction whose chain passes through an
        untrusted source class (SPEC §7.7.4, CND-05). The chain is the instruction's ``refs.parent``
        and ``refs.origin`` lineage (``agentce:derivedFrom``, SPEC §6.3) for as many hops as the
        records show. It passes through an untrusted source class at an instruction of an untrusted
        Appendix F class, at a tool call or resource access (the content they produce is
        ``tool_output`` or ``retrieved``), at a parent or origin the bundle does not hold, and at
        anything ROB-02's closures reached (``tainted``: an untrusted memory read or write, content
        the memory guard never ruled on, or content that used either). An instruction whose source
        class is not a declared trusted one (none given, say) cannot show a trusted root either, so
        it fails closed. A closure over all events, so the acting event may precede its instruction
        and cycles end."""
        untrusted = set(tainted)
        derived: dict[str, set[str]] = {}
        held: set[str] = set()
        for event in events:
            ptype = self._ptype(event)
            node = event_iri(str(event["id"]))
            held.add(node)
            if ptype in ("ToolCall", "ResourceAccess"):
                untrusted.add(node)
            elif ptype == "Instruction":
                source_class = _data(event).get("source_class")
                if not _is_one_of(source_class, _TRUSTED_INSTRUCTION):
                    untrusted.add(node)
                refs = _refs(event)
                for key in ("parent", "origin"):
                    _flow(derived, refs.get(key), node)
        # A lineage the bundle does not hold cannot show a trusted root, so it fails closed.
        untrusted |= set(derived) - held
        _reach(untrusted, derived)
        for event in events:
            ptype = self._ptype(event)
            if ptype not in ("ToolCall", "Decision"):
                continue
            instruction = _refs(event).get("instruction")
            acts = isinstance(instruction, str) and instruction in untrusted
            self.store.add_literal(
                event_iri(str(event["id"])),
                "agentce:actsOnUntrusted",
                "true" if acts else "false",
                BOOL,
            )

    def _robust_to_untrusted(self, events: list[dict[str, Any]]) -> set[str]:
        """Flag whether each Decision kept untrusted content out, and whether the memory guard
        ruled on all it used (SPEC §7.4, ROB-02).

        Untrusted content starts at a MemoryWrite whose trust is untrusted or quarantined, whose
        guard verdict is quarantine or block, or which carries no trust and no verdict but an
        untrusted Appendix F ``provenance_origin_class``; a MemoryRead whose ``trust_min`` is
        untrusted or quarantined; and an instruction with an untrusted Appendix F source class
        (CND-05's rule). Content the guard never ruled on starts at a MemoryRead not reported by
        an enforcement point, a record no ruled enforcement-point write and no enforcement-point
        read with a ``trust_min`` covers, and a ref the bundle does not hold. Both flow along
        every edge that carries content into an event (``_CONTENT_IN``, ``_CONTENT_OUT``, a read
        to its consumer, a record to and from its writes) for as many hops as the records show,
        so a decision is reached through another decision, a tool call or a model call. Closures
        over all events, so order and cycles never matter. Returns both closures, which CND-05's
        instruction chain also reads (:meth:`_acts_on_untrusted`)."""
        flows: dict[str, set[str]] = {}
        tainted: set[str] = set()
        unruled: set[str] = set()
        held: set[str] = set()
        records: set[str] = set()
        guarded: set[str] = set()
        for event in events:
            ptype = self._ptype(event)
            data = _data(event)
            refs = _refs(event)
            node = event_iri(str(event["id"]))
            held.add(node)
            enforced = event.get("agentcesourceclass") == "enforcement_point"
            for key in _CONTENT_IN:
                value = data.get(key)
                for ref in value if isinstance(value, list) else [value]:
                    _flow(flows, ref, node)
            for key in _REFS_IN:
                _flow(flows, refs.get(key), node)
            for key in _CONTENT_OUT:
                _flow(flows, node, data.get(key))
                if isinstance(data.get(key), str):
                    held.add(data[key])
            _flow(flows, node, refs.get("consumer"))
            if ptype == "MemoryWrite":
                record = data.get("record_ref")
                # A record and each write of it stand for the same content.
                _flow(flows, record, node)
                trust, verdict = data.get("trust"), data.get("guard_verdict")
                unmarked = trust is None and verdict is None
                if isinstance(record, str):
                    records.add(record)
                    if enforced and not unmarked:
                        guarded.add(record)
                if (
                    _is_one_of(trust, _UNTRUSTED_TRUST)
                    or _is_one_of(verdict, _UNTRUSTED_VERDICTS)
                    or (
                        unmarked
                        and _untrusted_source_class(data.get("provenance_origin_class"))
                    )
                ):
                    tainted.add(node)
            elif ptype == "MemoryRead":
                read = data.get("record_refs")
                # A guard's read rules on its records only when it filtered them by trust.
                ruling = enforced and data.get("trust_min") is not None
                for record in read if isinstance(read, list) else []:
                    if isinstance(record, str):
                        records.add(record)
                        if ruling:
                            guarded.add(record)
                if not enforced:
                    unruled.add(node)
                if _is_one_of(data.get("trust_min"), _UNTRUSTED_TRUST):
                    tainted.add(node)
        tainted |= {
            iri for iri, untrusted in self.instruction_untrusted.items() if untrusted
        }
        unruled |= (records - guarded) | (set(flows) - held - records)
        _reach(tainted, flows)
        _reach(unruled, flows)
        for event in events:
            if self._ptype(event) == "Decision":
                node = event_iri(str(event["id"]))
                self.store.add_literal(
                    node,
                    "agentce:robustToUntrustedContent",
                    "false" if node in tainted else "true",
                    BOOL,
                )
                self.store.add_literal(
                    node,
                    "agentce:untrustedContentRuledOn",
                    "false" if node in unruled else "true",
                    BOOL,
                )
        tainted |= unruled
        return tainted

    # --- materialised (glue) edges (SPEC §7.2) ---

    def _materialise(self, events: list[dict[str, Any]]) -> None:
        by_human = self._map_oversight(events)
        incident_decisions = self._incident_decisions(events)
        policy_decisions = frozenset(
            event_iri(str(event["id"]))
            for event in events
            if self._ptype(event) == "PolicyDecision"
        )
        for event in events:
            node = event_iri(str(event["id"]))
            ptype = self._ptype(event)
            data = _data(event)
            if ptype == "DelegationIssued":
                self._chain_verified(node, data)
            if ptype == "ToolCall":
                self._executes_consequential(node, _refs(event))
            if ptype == "Decision":
                self._oversight_matches(node, data)
                self._explanation_reconstructable(node)
                self._oversight_coverage(node, data)
                self._literal(
                    node,
                    "agentce:triggersIncident",
                    incident_decisions is None or node in incident_decisions,
                )
                self._risk_reviewed(node, _refs(event), policy_decisions)
            if ptype in ("Override", "Interrupt"):
                self._intervention(node, ptype, event, by_human[node])
            if ptype == "Incident":
                self._literal(
                    node,
                    "agentce:incidentResponded",
                    _text(data.get("detected_at")) is not None
                    and any(_text(data.get(k)) for k in _INCIDENT_RESPONSES),
                )
            if ptype == "Outcome":
                self._adverse_outcome_linked(node, data)
            if ptype in ("ToolCall", "ModelCall"):
                self._component_declared(node, ptype, data)
            if ptype == "BundleLoaded":
                self._declared_components_observed(node, data)
            self._chain_terminus(node, data)
        self._acts_on_untrusted(events, self._robust_to_untrusted(events))
        self._conduct_scope_budget(events)
        self._preceded_by()

    def _map_oversight(self, events: list[dict[str, Any]]) -> dict[str, bool]:
        """Whether each ApprovalDecided, Override and Interrupt meets the SPEC §10.4 human-actor rule. An
        ApprovalDecided reviews the decision it names (``agentce:reviewedBy``; OVS-01, OVS-08, CND-02, INC-03, RSK-02)
        only when it does; one that fails it is no human's review."""
        by_human: dict[str, bool] = {}
        for event in events:
            ptype = self._ptype(event)
            if ptype not in _OVERSIGHT_TYPES:
                continue
            node = event_iri(str(event["id"]))
            key = self._human_key(node, event)
            by_human[node] = key is not None
            decision = _refs(event).get("decision")
            if (
                ptype == "ApprovalDecided"
                and key is not None
                and isinstance(decision, str)
            ):
                self.store.add_edge(decision, "agentce:reviewedBy", node)
                self.decision_reviewed.add(decision)
                self.human_reviewers.setdefault(decision, set()).add(key)
        return by_human

    def _literal(self, node: str, predicate: str, value: bool) -> None:
        self.store.add_literal(node, predicate, "true" if value else "false", BOOL)

    def _incident_decisions(self, events: list[dict[str, Any]]) -> set[str] | None:
        """INC-03: the held decisions the ``Incident`` events name in ``related_refs[]`` or
        ``refs.decision``, directly or through the ``refs.decision`` of the event named (an
        ``Outcome``, a ``ToolCall``, an ``Override``, ...). ``None`` when any name does not lead to a
        held decision: the records cannot show which decision triggered that incident, so every
        decision is held to the rule (fail closed)."""
        out: set[str] = set()
        for event in events:
            if self._ptype(event) != "Incident":
                continue
            related = _data(event).get("related_refs")
            names = list(related) if isinstance(related, list) else []
            if (decision := _refs(event).get("decision")) is not None:
                names.append(decision)
            if not names:
                return None
            for ref in names:
                if not isinstance(ref, str):
                    return None
                named = ref if ref in self.decision_time else self.ref_decision.get(ref)
                if named not in self.decision_time:
                    return None
                out.add(named)
        return out

    def _risk_reviewed(
        self, node: str, refs: dict[str, Any], policy_decisions: frozenset[str]
    ) -> None:
        """RSK-02 (Art. 9): a review of the decision, or a ``refs.authorization`` (or
        ``refs.request``) naming a ``PolicyDecision`` the bundle holds. A name that leads to no
        held policy decision gates nothing."""
        self._literal(
            node,
            "agentce:riskReviewed",
            node in self.decision_reviewed
            or any(
                _is_one_of(refs.get(k), policy_decisions)
                for k in ("authorization", "request")
            ),
        )

    def _oversight_coverage(self, node: str, data: dict[str, Any]) -> None:
        """OVS-08 (Art. 14(5)): enough distinct human reviewers -- two when the decision's observed
        or domain-declared oversight modality is ``dual_control``, otherwise one."""
        dtype = data.get("decision_type")
        declared = (
            self.domain.required_oversight.get(dtype)
            if isinstance(dtype, str)
            else None
        )
        dual = "dual_control" in (data.get("oversight_modality"), declared)
        reviewers = len(self.human_reviewers.get(node, ()))
        self._literal(
            node,
            "agentce:oversightCoverageComplete",
            reviewers >= (2 if dual else 1),
        )

    def _intervention(
        self, node: str, ptype: str, event: dict[str, Any], by_human: bool
    ) -> None:
        """OVS-07: an ``Override`` is effective when it names a held decision and records a
        replacement that differs from the original; an ``Interrupt`` when a named mechanism halted or paused the agent. Either is
        by a human when its actor meets the SPEC §10.4 human-actor rule."""
        data = _data(event)
        if ptype == "Override":
            decision = _refs(event).get("decision")
            effective = (
                isinstance(decision, str)
                and decision in self.decision_time
                and data.get("replacement") is not None
                and data.get("replacement") != data.get("original")
            )
        else:
            effective = _is_one_of(data.get("effect"), _INTERRUPT_STOPS) and _is_one_of(
                data.get("mechanism"), _INTERRUPT_MECHANISMS
            )
        self._literal(node, "agentce:interventionEffective", effective)
        self._literal(node, "agentce:interventionByHuman", by_human)

    def _dangling(self, node: str, event: dict[str, Any]) -> None:
        candidates: list[str] = [v for v in _refs(event).values() if isinstance(v, str)]
        candidates += [
            v for v in _data(event).get("used", []) or [] if isinstance(v, str)
        ]
        for value in candidates:
            if is_event_ref(value) and value not in self.event_iris:
                self.store.add_literal(node, "agentce:danglingRef", value)
                self.dangling_nodes.add(node)

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

    def _adverse_outcome_linked(self, node: str, data: dict[str, Any]) -> None:
        """INC-01 (SPEC §7.4): an adverse outcome is linked to the consequential decision it
        resulted from; a non-adverse outcome carries no such expectation and is vacuously linked.
        A ``refs.decision`` that is missing, dangling (names no ingested event), or resolves to a
        decision that is not itself a ``ConsequentialDecision`` does not count as linked -- a real
        link requires a real consequential decision, not merely the shape of one."""
        adverse = bool(data.get("adverse", False))
        decision = self.outcome_decision.get(node)
        linked = (not adverse) or (
            decision is not None
            and decision in self.event_iris
            and self.store.is_a(decision, "agentce:ConsequentialDecision")
        )
        self.store.add_literal(
            node, "agentce:adverseOutcomeLinked", "true" if linked else "false", BOOL
        )

    def _explanation_reconstructable(self, node: str) -> None:
        """TRN-03 (SPEC §7.6): affected persons are informed (``notifiedBy`` a ``Notice``) and the
        evidence chain resolves on both ends of that notification -- no ``danglingRef`` on the
        decision itself (every ``refs.*``/``used`` value it names resolves to an ingested event) AND
        no ``danglingRef`` on at least one Notice that notified it (round-2 critic finding: a Notice's
        own dangling ``refs.*`` value, e.g. a free-form ``explanation_ref``, must not be invisible just
        because `_dangling` lands the literal on the Notice node, not the Decision node) -- so the
        evidence an explanation would be built from is actually present on both legs. A decision can be
        ``notifiedBy`` more than one Notice; checking only the last one mapped made the result depend
        on event order, so this checks every notifying Notice and passes if any one of them is clean
        (a verifier-found regression, fixed this item). Scoped narrower than the Notice's own
        ``content_ref`` or the Decision's own ``rationale_claim_ref`` (SPEC model attributes, not
        ``refs.*`` edges): neither is materialised as a graph reference anywhere today, so neither can
        dangle in this model yet -- that is new graph-builder work, not a drop-in check, and is
        disclosed and out of this item's scope (see this item's contract, split to 18.37h)."""
        notices = self.decision_notice.get(node)
        reconstructable = (
            notices is not None
            and node not in self.dangling_nodes
            and any(notice not in self.dangling_nodes for notice in notices)
        )
        self.store.add_literal(
            node,
            "agentce:explanationReconstructable",
            "true" if reconstructable else "false",
            BOOL,
        )

    def _component_declared(self, node: str, ptype: str, data: dict[str, Any]) -> None:
        """DOC-01 S1: a call exercises a component some ``BundleLoaded`` manifest declares -- one of
        its names is declared for its family, and when both the call and the declaration state a
        version or digest, they agree. A call that names nothing cannot be shown declared."""
        family, names, version = _operated(ptype, data)
        known = self.declared[family]
        declared = any(
            name in known
            and (not (pins := known[name]) or version is None or version in pins)
            for name in names
        )
        self.store.add_literal(
            node, "agentce:componentDeclared", "true" if declared else "false", BOOL
        )

    def _declared_components_observed(self, node: str, data: dict[str, Any]) -> None:
        """DOC-01 S2: every model and MCP server this manifest declares is exercised by at least one
        call in the subject's records (declared but never seen is drift too). A manifest with nothing
        to compare -- no call in the records and no model or MCP server to see -- is not a component
        record, so DOC-01 never reads conformant on it alone (18.37j)."""
        must_operate = [
            (_MUST_OPERATE[kind], name)
            for kind, name, _pins in _declared_components(data)
            if kind in _MUST_OPERATE
        ]
        if not (self.has_calls or must_operate):
            return
        self.store.add_type(node, COMPONENT_RECORD)
        observed = all(name in self.operated[family] for family, name in must_operate)
        self.store.add_literal(
            node,
            "agentce:declaredComponentsObserved",
            "true" if observed else "false",
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
