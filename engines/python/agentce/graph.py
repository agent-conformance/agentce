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

from collections import Counter
from collections.abc import Collection, Iterable
from typing import Any

from .domain import DomainBinding
from .iri import ZERO_KEY, event_iri, is_event_ref, principal_iri
from .store import GraphStore
from .structural import compare_literals

BOOL = "xsd:boolean"
#: CND-02's population (SPEC §7.7.4), materialised by :meth:`_Builder._conduct_gate`.
GATED_ACTION = "agentce:GatedAction"
#: A ModelCall in a task: consumption CND-07 judges against the task's budgets.
TASK_MODEL_CALL = "agentce:TaskModelCall"
#: An action whose task budgets the records cannot tell (CND-07's evidence shape).
BUDGET_UNJUDGED = "agentce:BudgetUnjudged"
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
#: The effect classes SPEC §7.7.4 holds to CND-02 whatever the task scope marks (ruling Q6).
_HIGH_EFFECT: frozenset[str] = frozenset(
    {"irreversible", "external_communication", "spend", "physical"}
)
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


def _before_every(time: str, actions: Iterable[str]) -> bool:
    """Whether ``time`` is at or before every action time, compared as instants (SPEC §6.7); a time that cannot be
    compared with an action's gates nothing."""
    return all(compare_literals(time, action) in (-1, 0) for action in actions)


def _after_every(time: str, actions: Collection[str]) -> bool:
    """Whether ``time`` is after every action time, compared as instants (SPEC §6.7): a decision recorded after the
    action already ran cannot withdraw the approval it ran under (18.142). Never true when no action ran."""
    return bool(actions) and all(
        compare_literals(time, action) == 1 for action in actions
    )


def _whole(value: Any) -> int | None:
    """A non-negative whole number (``5`` or ``5.0``, as a JSON or YAML reader in any engine may give it), else None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value < 0 or (isinstance(value, float) and not value.is_integer()):
        return None
    return int(value)


class _TaskUsage:
    """What each task's records show it consumed (SPEC §7.7.3 budget row): the input and output tokens of
    its ModelCalls with a readable usage, whether one of them carried none, its spend calls, and whether
    it holds a record that may have spent an amount no record carries: a ToolCall with no effect class, or a
    ModelCall (a provider's usage export bills it; SPEC §7.7.3 budget row)."""

    def __init__(self) -> None:
        self.tokens: dict[str, int] = {}
        self.tokens_unread: set[str] = set()
        self.spend_calls: dict[str, int] = {}
        self.spend_unknown: set[str] = set()

    def add(self, ptype: str, data: dict[str, Any], tasks: set[str]) -> None:
        for task in tasks:
            if ptype == "ToolCall":
                effect = data.get("effect_class")
                if effect == "spend":
                    self.spend_calls[task] = self.spend_calls.get(task, 0) + 1
                elif not isinstance(effect, str):
                    self.spend_unknown.add(task)
                continue
            self.spend_unknown.add(task)
            usage = data.get("usage")
            counts = [
                _whole(usage.get(k)) if isinstance(usage, dict) else None
                for k in ("input_tokens", "output_tokens")
            ]
            read = [n for n in counts if n is not None]
            if len(read) == len(counts):
                self.tokens[task] = self.tokens.get(task, 0) + sum(read)
            else:
                self.tokens_unread.add(task)

    def verdict(
        self, task: str, dimension: str, limit: int | float | None
    ) -> bool | None:
        """True within the limit, False over it, None when the records cannot tell."""
        if dimension == "tokens":
            cap = _whole(limit)
            if cap is None:
                return None
            if self.tokens.get(task, 0) > cap:
                return False
            # no readable usage for the task, or a model call without one: the records cannot tell
            return (
                None if task in self.tokens_unread or task not in self.tokens else True
            )
        if dimension == "spend":
            if limit is None:
                return None
            if self.spend_calls.get(task):
                return False if limit == 0 else None
            return None if task in self.spend_unknown else True
        return None


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
        # Instructions that declare no source class: ROB-02 taint seeds of their own (18.127).
        self.instruction_unclassed: set[str] = set()
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
        # SPEC §10.4 human-actor rule, read in the second pass so file order never matters: event IRI -> (type, source
        # class, agent id, the IRI of the human principal it names) of every event, the last read from any type
        # (SessionStart, PolicyDecision and Instruction carry one) so only the type check keeps a login a session
        # (18.140); agent id -> every principal IRI in its delegation chain (its acted_for and its DelegationIssued
        # chains); event IRI -> the principal IRIs of its own acted_for.
        self.event_info: dict[str, tuple[str, str | None, str | None, str | None]] = {}
        # event IRI -> the agents it concerns: its own agent and its CloudEvents subject (the assessed agent), so an
        # event that leaves out the optional agent still concerns the subject.
        self.event_agents: dict[str, frozenset[str]] = {}
        self.agent_chain: dict[str, set[str]] = {}
        self.acted_for: dict[str, set[str]] = {}
        # agent id -> how many held events concern it, built on first use by an oversight record whose refs.decision
        # names no held event (18.158).
        self._agent_events: Counter[str] | None = None
        # OVS-08: decision IRI -> the actor IRI of every verified ApprovalDecided whose outcome is approve.
        self.human_approvers: dict[str, set[str]] = {}
        # INC-03: event IRI -> the decision its refs.decision names, whatever the event type.
        self.ref_decision: dict[str, str] = {}

    def build(self, events: list[dict[str, Any]]) -> GraphStore:
        used_classes: set[str] = set(BASE_SUBCLASS) | {
            "prov:Agent",
            "prov:Activity",
            "prov:Entity",
            GATED_ACTION,
            TASK_MODEL_CALL,
            BUDGET_UNJUDGED,
        }
        for event in events:
            node = event_iri(str(event["id"]))
            self.event_iris.add(node)
            # The held decisions, known before the first pass so a refs.decision read before its Decision still
            # resolves to it (18.156).
            if self._ptype(event) == "Decision":
                self.decision_time[node] = str(event.get("time", ""))
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
        login_principal = _human_id(data.get("principal"))
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

    def _verified_actor(self, node: str, event: dict[str, Any]) -> str | None:
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
        # Every name the ref may mean an event by, the held decision among them: a name that leads to another held
        # event, or to none, still counts that event's agents, so resolving never loosens the rule (fail closed).
        refs = _refs(event)
        named = self._ref_names(refs.get("decision"))
        # A present refs.decision that names no held event (null or empty included) may mean any of them, so the
        # record concerns every held event but its own login (18.158).
        unresolved = "decision" in refs and not any(
            name in self.event_info for name in named
        )
        concerned = self.event_agents[node].union(
            *(self.event_agents.get(name, frozenset()) for name in named)
        )
        login_type, login_class, login_agent, login_principal = self.event_info[login]
        if login_type != "SessionStart" or login_class not in _LOGIN_CLASSES:
            return None
        if login_agent is None or login_agent in concerned:
            return None
        # The login itself is one event concerning its agent; any other makes it that agent's session (fail closed).
        if unresolved and self._agent_event_counts()[login_agent] > 1:
            return None
        actor_iri = principal_iri(actor, self.key)
        if login_principal != actor_iri:
            return None
        chain = set(self.acted_for[node])
        for name in named:
            chain |= self.acted_for.get(name, set())
        for agent_id in concerned:
            chain |= self.agent_chain.get(agent_id, set())
        return None if actor_iri in chain else actor_iri

    def _agent_event_counts(self) -> Counter[str]:
        """How many held events concern each agent (their own agent or CloudEvents subject; 18.158)."""
        if self._agent_events is None:
            self._agent_events = Counter(
                agent for agents in self.event_agents.values() for agent in agents
            )
        return self._agent_events

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
        """The SPEC §6.3 glue edges of a refs.decision, read through ``_held_decision`` as approvedBy reads it; a ref
        naming no held decision keeps its text (18.156)."""
        refs = _refs(event)
        decision = refs.get("decision")
        if isinstance(decision, str):
            decision = self._held_decision(decision) or decision
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
            if not isinstance(source_class, str):
                # No class at all (or not a string): ROB-02 reads it as untrusted content (18.127).
                self.instruction_unclassed.add(node)
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
        """Compute the Conduct within-scope and within-budget flags (SPEC §7.7, CND-01/CND-07): an
        action is out of scope when a ``PolicyDecision`` denies its request, or when it is a ToolCall
        in a task whose declared effect scope does not allow its tool and effect class
        (:meth:`_within_task_scope`); it is over budget when a ``Refusal`` with reason class
        ``budget_exceeded`` names it, or when its task's recorded usage exceeds a budget a scope of the
        task declares (:meth:`_task_budget`). An action in no task and never denied reads within scope."""
        denied: set[str] = set()
        over_budget: set[str] = set()
        usage = _TaskUsage()
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
            if ptype in ("ModelCall", "ToolCall"):
                usage.add(ptype, data, self._action_tasks(event))
        for event in events:
            ptype = self._ptype(event)
            if ptype not in ("ToolCall", "ResourceAccess", "ModelCall"):
                continue
            node = event_iri(str(event["id"]))
            tasks = self._action_tasks(event)
            if ptype == "ModelCall":
                if not tasks:
                    continue
                # A model call in a task is consumption CND-07 judges (SPEC §7.7.3 ModelCall.usage).
                self.store.add_type(node, TASK_MODEL_CALL)
            else:
                in_scope = node not in denied and (
                    ptype != "ToolCall" or self._within_task_scope(event)
                )
                self.store.add_literal(
                    node, "agentce:withinScope", "true" if in_scope else "false", BOOL
                )
            within, judged = self._task_budget(tasks, usage)
            self.store.add_literal(
                node,
                "agentce:withinBudget",
                "true" if within and node not in over_budget else "false",
                BOOL,
            )
            if not judged:
                self.store.add_type(node, BUDGET_UNJUDGED)
                self.store.add_literal(node, "agentce:budgetJudged", "false", BOOL)

    def _task_budget(self, tasks: set[str], usage: _TaskUsage) -> tuple[bool, bool]:
        """Whether an action's tasks stayed within every budget their scopes declare (SPEC §7.7.4 CND-07,
        §7.7.6 budgets), and whether the records can tell. Tokens are the input and output tokens of the
        task's ModelCalls; spend is told only against a zero budget (a spend call exceeds it), since no
        record carries an amount; no record carries compute time. A task held to no scope while the
        binding declares scopes cannot be told; a binding declaring none sets no budget."""
        within, judged = True, True
        for task in sorted(tasks):
            scopes = self.domain.scopes_for(task)
            if not scopes:
                judged = judged and not (
                    self.domain.task_scopes or self.domain.task_scopes_malformed
                )
                continue
            for scope in scopes:
                for dimension, limit in scope.budgets:
                    verdict = usage.verdict(task, dimension, limit)
                    within = within and verdict is not False
                    judged = judged and verdict is not None
        return within, judged

    def _within_task_scope(self, event: dict[str, Any]) -> bool:
        """Whether a ToolCall stays within the declared effect scope of every task it names, by
        ``refs.task`` or the ``agentcetask`` attribute (SPEC §7.7.4 CND-01, §7.7.6): each scope
        the domain binding holds the task to (:meth:`DomainBinding.scopes_for`) allows its tool and
        its effect class. A task held to no scope, or a call with no effect class, is out of scope."""
        data = _data(event)
        tool = data.get("tool")
        name = tool.get("name") if isinstance(tool, dict) else None
        effect = data.get("effect_class")
        for task in self._action_tasks(event):
            scopes = self.domain.scopes_for(task)
            if not scopes or not all(s.allows(name, effect) for s in scopes):
                return False
        return True

    def _action_tasks(self, event: dict[str, Any]) -> set[str]:
        """The tasks an action names, by ``refs.task`` or the ``agentcetask`` attribute."""
        return {
            t
            for t in (_refs(event).get("task"), event.get("agentcetask"))
            if isinstance(t, str)
        }

    def _conduct_gate(self, events: list[dict[str, Any]]) -> None:
        """Materialise CND-02's population and gate (SPEC §7.7.4, rulings Q5 to Q10). An action (a ToolCall, or a
        ResourceAccess carrying an effect class) is a ``GatedAction`` when a scope of a task it names marks its effect
        class ``requires_approval``, or when its class is high-effect (:data:`_HIGH_EFFECT`); a high-effect action in
        no task, or in a task held to no scope, is held to the approval path (fails closed). The approval path needs an
        enforcement-point PolicyDecision naming the action (its ``refs.request`` or the action's
        ``refs.authorization``) that allows it or asks for approval, and an approval of the held decision the action's
        ``refs.decision`` names (``agentce:approvedBy``, already held to SPEC §6.3 and §10.4); any other high-effect
        action needs an enforcement-point allow, or a ``require_approval`` such an approval released (Q10). A deny
        naming the action closes its gate (Q9). The approvals a gate rests on are linked ``agentce:releasedBy``, which
        CND-02's minimum source class reads. A ToolCall with no effect class, and a consequential decision no ToolCall
        executes, are in the population but unjudged (``agentce:gateJudged`` false; Q7, Q8)."""
        policies = {
            event_iri(str(e["id"])): e
            for e in events
            if self._ptype(e) == "PolicyDecision"
        }
        named: dict[str, list[dict[str, Any]]] = {}
        for policy in policies.values():
            request = _refs(policy).get("request")
            if isinstance(request, str):
                named.setdefault(request, []).append(policy)
        executed: set[str] = set()
        for event in events:
            ptype = self._ptype(event)
            if ptype not in ("ToolCall", "ResourceAccess"):
                continue
            node = event_iri(str(event["id"]))
            refs = _refs(event)
            decision = self._held_decision(refs.get("decision"))
            if ptype == "ToolCall" and decision is not None:
                executed.add(decision)
            effect = _data(event).get("effect_class")
            if not isinstance(effect, str):
                if ptype == "ToolCall":
                    self._gate(node, met=True, judged=False)
                continue
            scopes = [self.domain.scopes_for(t) for t in self._action_tasks(event)]
            approval = any(effect in s.requires_approval for ss in scopes for s in ss)
            if effect in _HIGH_EFFECT and (not scopes or not all(scopes)):
                approval = True
            if not approval and effect not in _HIGH_EFFECT:
                continue
            gating = list(named.get(node, ()))
            authorization = refs.get("authorization")
            if isinstance(authorization, str) and authorization in policies:
                gating.append(policies[authorization])
            rulings = {
                _data(p).get("decision")
                for p in gating
                if p.get("agentcesourceclass") in _LOGIN_CLASSES
            }
            denied = any(_data(p).get("decision") == "deny" for p in gating)
            approvals = (
                sorted(self.store.objects(decision, "agentce:approvedBy"))
                if decision is not None
                else []
            )
            if approval:
                released = bool(rulings & {"allow", "require_approval"})
            else:
                released = "allow" not in rulings and "require_approval" in rulings
            if released or approval:
                for approved in approvals:
                    self.store.add_edge(node, "agentce:releasedBy", approved)
            met = not denied and (
                ("allow" in rulings and not approval) or (released and bool(approvals))
            )
            self._gate(node, met=met, judged=True)
            if not met and decision in self.store.human_actor_failed:
                # An approval that fails SPEC §10.4 left the gate shut: the deviation register cannot waive it.
                self.store.human_actor_failed.add(node)
        for decision in sorted(self.decision_time):
            if decision not in executed and self.store.is_a(
                decision, "agentce:ConsequentialDecision"
            ):
                self._gate(decision, met=True, judged=False)

    def _gate(self, node: str, *, met: bool, judged: bool) -> None:
        self.store.add_type(node, GATED_ACTION)
        self._literal(node, "agentce:gateMet", met)
        self._literal(node, "agentce:gateJudged", judged)

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
        it fails closed. So does an action whose ``refs.instruction`` names no held Instruction
        (nothing the bundle holds, or an event or record of another type: SPEC §6.2.3 types the ref
        Ref→Instruction). A closure over all events, so the acting event may precede its instruction
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
            acts = isinstance(instruction, str) and (
                instruction in untrusted
                or instruction not in self.instruction_untrusted
            )
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
        (CND-05's rule), or with none (ROB-02 only). Content the guard never ruled on starts at a MemoryRead not reported by
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
        fed = {target for targets in flows.values() for target in targets}
        _reach(tainted, flows)
        _reach(unruled, flows)
        # An instruction that declares no source class cannot show its content came from a trusted source, so
        # ROB-02 reads it as untrusted content (18.127); CND-05's own chain rule already fails it, so it stays out
        # of the closures returned to that rule.
        unclassed = set(self.instruction_unclassed)
        _reach(unclassed, flows)
        decisions = [e for e in events if self._ptype(e) == "Decision"]
        # A decision nothing recorded flows into used nothing the guard could have ruled on, so ROB-02 cannot
        # judge it (ruling 15): it reads untrustedContentRuledOn false itself, and taints nothing downstream.
        unfed = {event_iri(str(e["id"])) for e in decisions} - fed
        self._robust_literals(decisions, tainted | unclassed, unruled | unfed)
        tainted |= unruled
        return tainted

    def _robust_literals(
        self, decisions: list[dict[str, Any]], tainted: set[str], unruled: set[str]
    ) -> None:
        """ROB-02's two literals on each of ``decisions`` (see :meth:`_robust_to_untrusted`)."""
        for event in decisions:
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
        self._conduct_gate(events)
        self._preceded_by()

    @staticmethod
    def _ref_names(ref: Any) -> tuple[str, ...]:
        """The names a ``refs.decision`` may mean an event by, in the order they are tried: as written, trimmed of
        ``_ID_PAD``, and ``agentce:event/<trimmed ref>`` (a bare id); none when the ref is no string."""
        if not isinstance(ref, str):
            return ()
        trimmed = _rule_id(ref) or ""
        return (ref, trimmed, event_iri(trimmed))

    def _held_decision(self, ref: Any) -> str | None:
        """The held decision a ``refs.decision`` names (SPEC §6.3 approvedBy): its IRI, else the ref trimmed of
        ``_ID_PAD`` when that is a held decision's IRI, else ``agentce:event/<trimmed ref>`` when that is (a bare id);
        ``None`` when it names no held decision. Exact match first, so no name resolves to another held decision."""
        for name in self._ref_names(ref):
            if name in self.decision_time:
                return name
        return None

    def _map_oversight(self, events: list[dict[str, Any]]) -> dict[str, bool]:
        """Whether each ApprovalDecided, Override and Interrupt meets the SPEC §10.4 human-actor rule. An
        ApprovalDecided reviews the decision it names (``agentce:reviewedBy``; OVS-01, INC-03, RSK-02) only when it
        does, whatever its outcome; it also approves it (``agentce:approvedBy``; CND-02, OVS-08) only when its outcome
        is approve, it is recorded at or before every ToolCall that executes the decision (18.141) and every Outcome
        that names it (18.144: the decision had already taken effect), and the same human
        recorded no other decision on it at or after it, or at a time that cannot be placed against it, unless that
        decision came after every such ToolCall (18.142: each human's most recent decision governs). One that fails
        the rule is no human's review. Every refs.decision is read through ``_held_decision`` (18.148), and a ToolCall
        or Outcome naming no held decision (a null refs.decision included, 18.157) bounds every decision's approvals."""
        by_human: dict[str, bool] = {}
        action_times: dict[str, list[str]] = {}
        outcome_times: dict[str, list[str]] = {}
        # The time of every ToolCall and Outcome whose refs.decision names no held decision: it may be any decision's
        # effect, so it bounds every decision's approvals (fails closed, 18.148), though it withdraws none.
        unresolved_times: list[str] = []
        decided: dict[tuple[str, str], list[tuple[str, str, bool]]] = {}
        for event in events:
            ptype = self._ptype(event)
            refs = _refs(event)
            # No refs.decision key is no claim; a present null names no held decision (18.157).
            if ptype not in ("ToolCall", "Outcome") or "decision" not in refs:
                continue
            ref = refs["decision"]
            time = str(event.get("time", ""))
            decision = self._held_decision(ref)
            if decision is None:
                unresolved_times.append(time)
                continue
            times = action_times if ptype == "ToolCall" else outcome_times
            times.setdefault(decision, []).append(time)
        for event in events:
            ptype = self._ptype(event)
            if ptype not in _OVERSIGHT_TYPES:
                continue
            node = event_iri(str(event["id"]))
            key = self._verified_actor(node, event)
            by_human[node] = key is not None
            ref = _refs(event).get("decision")
            held = self._held_decision(ref) if ptype == "ApprovalDecided" else None
            if key is None:
                # What the deviation register cannot waive (SPEC §10.4): the failing record, and the held decision
                # a failing approval names.
                self.store.human_actor_failed.add(node)
                if held:
                    self.store.human_actor_failed.add(held)
            if ptype != "ApprovalDecided" or not isinstance(ref, str):
                continue
            # A name that leads to no held decision keeps its own key: it reviews and approves no held decision.
            decision = held or ref
            data = _data(event)
            approve = data.get("outcome") == "approve"
            actor = _human_id(data.get("actor"))
            who = key
            if who is None and actor is not None and not approve:
                # A human's own no withdraws that human's approve even when it fails §10.4 (fail closed, 18.142).
                who = principal_iri(actor, self.key)
            if who is None:
                continue
            if key is not None:
                self.store.add_edge(decision, "agentce:reviewedBy", node)
                self.decision_reviewed.add(decision)
            decided.setdefault((decision, who), []).append(
                (node, str(event.get("time", "")), approve)
            )
        for (decision, key), records in decided.items():
            actions = action_times.get(decision, ())
            outcomes = outcome_times.get(decision, ())
            withdrawals = [
                time
                for _, time, approve in records
                if not approve and not _after_every(time, actions)
            ]
            for node, time, approve in records:
                if (
                    approve
                    and _before_every(time, actions)
                    and _before_every(time, outcomes)
                    and _before_every(time, unresolved_times)
                    and all(compare_literals(time, w) == 1 for w in withdrawals)
                ):
                    self.store.add_edge(decision, "agentce:approvedBy", node)
                    self.human_approvers.setdefault(decision, set()).add(key)
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
        """OVS-08 (Art. 14(5)): enough distinct human approvers -- two when the decision's observed
        or domain-declared oversight modality is ``dual_control``, otherwise one."""
        dtype = data.get("decision_type")
        declared = (
            self.domain.required_oversight.get(dtype)
            if isinstance(dtype, str)
            else None
        )
        dual = "dual_control" in (data.get("oversight_modality"), declared)
        approvers = len(self.human_approvers.get(node, ()))
        self._literal(
            node,
            "agentce:oversightCoverageComplete",
            approvers >= (2 if dual else 1),
        )

    def _intervention(
        self, node: str, ptype: str, event: dict[str, Any], by_human: bool
    ) -> None:
        """OVS-07: an ``Override`` is effective when it names a held decision and records a
        replacement that differs from the original; an ``Interrupt`` when a named mechanism halted or paused the agent. Either is
        by a human when its actor meets the SPEC §10.4 human-actor rule."""
        data = _data(event)
        if ptype == "Override":
            effective = (
                self._held_decision(_refs(event).get("decision")) is not None
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
        decision = self._held_decision(refs.get("decision"))
        consequential = (
            decision is not None
            and self.decision_type.get(decision, "") in self.domain.consequential
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
