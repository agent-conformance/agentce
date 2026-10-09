package org.agentce;

import com.fasterxml.jackson.databind.JsonNode;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Set;
import java.util.stream.Stream;

/**
 * Build the provenance graph from ingested events (SPEC §6.3, §7.2).
 *
 * <p>Each event becomes a typed node with deterministic IRIs, the §6.3 relations become edges, and the
 * engine materialises the glue edges the Portable Shape Profile needs ({@code agentce:chainTerminus},
 * {@code agentce:chainVerified}, {@code agentce:executesConsequential},
 * {@code agentce:oversightModalityMatchesDeclared}, {@code agentce:danglingRef},
 * {@code agentce:precededBy}). The {@code rdfs:subClassOf*} closure is materialised so class membership
 * needs no inference. This is a faithful port of the reference; it must build byte-identical graphs.
 */
public final class Graph {
    private Graph() {}

    private static final String BOOL = "xsd:boolean";
    private static final String DATETIME = "xsd:dateTime";
    private static final String INTEGER = "xsd:integer";

    /** The base class hierarchy (child -> parent), mirroring spec/vocab/agentce.ttl (SPEC §6.3). */
    private static final Map<String, String> BASE_SUBCLASS = baseSubclass();

    private static Map<String, String> baseSubclass() {
        Map<String, String> m = new LinkedHashMap<>();
        m.put("prov:SoftwareAgent", "prov:Agent");
        m.put("agentce:Agent", "prov:SoftwareAgent");
        m.put("agentce:Principal", "prov:Agent");
        m.put("agentce:HumanPrincipal", "agentce:Principal");
        m.put("agentce:ServicePrincipal", "agentce:Principal");
        m.put("agentce:Activity", "prov:Activity");
        m.put("agentce:ContextItem", "prov:Entity");
        m.put("agentce:PolicyDecision", "agentce:Activity");
        m.put("agentce:DelegationIssued", "agentce:Activity");
        m.put("agentce:Decision", "agentce:Activity");
        m.put("agentce:ConsequentialDecision", "agentce:Decision");
        m.put("agentce:Instruction", "agentce:Activity");
        m.put("agentce:Refusal", "agentce:Activity");
        m.put("agentce:ToolCall", "agentce:Activity");
        m.put("agentce:ModelCall", "agentce:Activity");
        m.put("agentce:ResourceAccess", "agentce:Activity");
        m.put("agentce:MemoryRead", "agentce:Activity");
        m.put("agentce:MemoryWrite", "agentce:Activity");
        return m;
    }

    /** refs.* keys that map directly to an edge from the referring event (SPEC §6.3). */
    private static final Map<String, String> GENERIC_REFS = genericRefs();

    private static Map<String, String> genericRefs() {
        Map<String, String> m = new LinkedHashMap<>();
        m.put("authorization", "agentce:authorizedBy");
        m.put("request", "agentce:authorizedBy");
        m.put("delegation", "agentce:delegatedVia");
        m.put("instruction", "agentce:actsOn");
        m.put("parent", "agentce:derivedFrom");
        m.put("origin", "agentce:derivedFrom");
        return m;
    }

    /** Instruction source classes an agent may act on without corroboration (SPEC §7.7, Appendix F).
     * Any other declared class is untrusted for the Conduct overlay's provenance and isolation
     * controls. */
    private static final Set<String> TRUSTED_INSTRUCTION =
            Set.of("user", "operator", "service", "agent_identified", "memory_trusted");

    /** A memory record's trust, or a read's {@code trust_min}, that ROB-02 treats as untrusted, and
     * the memory guard verdicts that mark a record untrusted whatever its trust field says (SPEC §7.4). */
    private static final Set<String> UNTRUSTED_TRUST = Set.of("untrusted", "quarantined");
    private static final Set<String> UNTRUSTED_VERDICTS = Set.of("quarantine", "block");

    /** Payload fields that carry content into their event, and those naming content their event
     * produced (SPEC §6.2); ROB-02's taint flows along both. Identity, audit and person refs carry none. */
    private static final List<String> CONTENT_IN =
            List.of("inputs", "used", "args_ref", "input_ref", "record_refs", "provenance_origin_ref");
    private static final List<String> CONTENT_OUT = List.of("output_ref", "result_ref", "record_ref", "content_ref");
    /** {@code refs} members that carry content into their event: the producing activity, the
     * instruction acted on and the instruction it was derived from. */
    private static final List<String> REFS_IN = List.of("origin", "instruction", "parent");

    public static GraphStore buildGraph(List<JsonNode> events) {
        return buildGraph(events, DomainBinding.empty(), new GraphStore(), Iri.ZERO_KEY);
    }

    public static GraphStore buildGraph(List<JsonNode> events, DomainBinding domain) {
        return buildGraph(events, domain, new GraphStore(), Iri.ZERO_KEY);
    }

    public static GraphStore buildGraph(
            List<JsonNode> events, DomainBinding domain, GraphStore store, byte[] key) {
        return new Builder(store, domain, key).build(events);
    }

    // --- helpers ---

    private static JsonNode dataOf(JsonNode event) {
        JsonNode data = event.get("data");
        return data != null && data.isObject() ? data : Json.nodes().objectNode();
    }

    private static JsonNode refsOf(JsonNode event) {
        JsonNode refs = dataOf(event).get("refs");
        return refs != null && refs.isObject() ? refs : Json.nodes().objectNode();
    }

    /** An Appendix F source class outside the trusted set (CND-05's rule, also ROB-02's). */
    private static boolean untrustedSourceClass(String sourceClass) {
        return sourceClass != null && !TRUSTED_INSTRUCTION.contains(sourceClass);
    }

    private static boolean isOneOf(JsonNode node, Set<String> values) {
        String value = str(node);
        return value != null && values.contains(value);
    }

    /** A field the record leaves out or sets to null (Python's None). */
    private static boolean isAbsent(JsonNode node) {
        return node == null || node.isNull();
    }

    private static String str(JsonNode node) {
        return node != null && node.isTextual() ? node.textValue() : null;
    }

    // DOC-01 (SPEC §7.4, Art. 11 / Annex IV): the events that declare an operating component (a
    // BundleLoaded manifest) or exercise one (a ToolCall or ModelCall) are typed with this
    // engine-materialised class, so one shape can compare the declaration with what operated.
    private static final String COMPONENT_RECORD = "agentce:ComponentRecord";
    private static final Set<String> COMPONENT_RECORD_TYPES = Set.of("BundleLoaded", "ToolCall", "ModelCall");

    /** A non-empty string, else null. */
    private static String text(JsonNode node) {
        String value = str(node);
        return value == null || value.isEmpty() ? null : value;
    }

    /**
     * Which declared component kinds a call can exercise. A tool call exercises a skill or an MCP server (by its
     * tool name or server name), a model call a model; a component with no kind may be either. Prompts, configs and
     * policies are never exercised by a call, so they take no part in DOC-01.
     */
    private static List<String> families(String kind) {
        if (kind == null) {
            return List.of("tool", "model");
        }
        return switch (kind) {
            case "skill", "mcp_server" -> List.of("tool");
            case "model" -> List.of("model");
            default -> List.of();
        };
    }

    /**
     * Declared kinds that must be seen operating (DOC-01 S2). A skill is left out: its name is not reliably a tool
     * name, so not seeing it is not evidence that it never ran.
     */
    private static String mustOperate(String kind) {
        if ("model".equals(kind)) {
            return "model";
        }
        return "mcp_server".equals(kind) ? "tool" : null;
    }

    /** One named component a BundleLoaded manifest declares; empty pins: any version matches. */
    private record Declared(String kind, String name, Set<String> pins) {}

    /** The component a ToolCall or ModelCall exercises: a tool by its tool and server names, a model by its name. */
    private record Operated(String family, List<String> names, String version) {}

    private static List<Declared> declaredComponents(JsonNode data) {
        List<Declared> out = new ArrayList<>();
        JsonNode components = data.get("components");
        if (components == null || !components.isArray()) {
            return out;
        }
        for (JsonNode component : components) {
            String bare = text(component);
            if (bare != null) {
                out.add(new Declared(null, bare, Set.of()));
            } else if (component.isObject() && text(component.get("name")) != null) {
                Set<String> pins = new LinkedHashSet<>();
                for (String key : List.of("version", "digest")) {
                    String pin = text(component.get(key));
                    if (pin != null) {
                        pins.add(pin);
                    }
                }
                out.add(new Declared(str(component.get("kind")), text(component.get("name")), pins));
            }
        }
        return out;
    }

    private static Operated operated(String ptype, JsonNode data) {
        String family = "ToolCall".equals(ptype) ? "tool" : "model";
        JsonNode ref = data.get(family);
        if (ref == null || !ref.isObject()) {
            return new Operated(family, List.of(), null);
        }
        List<String> names = new ArrayList<>();
        for (String key : "tool".equals(family) ? List.of("name", "server") : List.of("name")) {
            String name = text(ref.get(key));
            if (name != null) {
                names.add(name);
            }
        }
        return new Operated(family, names, text(ref.get("version_or_digest")));
    }

    private static boolean truthy(JsonNode node) {
        if (node == null || node.isNull() || node.isMissingNode()) {
            return false;
        }
        if (node.isBoolean()) {
            return node.booleanValue();
        }
        if (node.isNumber()) {
            return node.asDouble() != 0.0;
        }
        if (node.isTextual()) {
            return !node.asText().isEmpty();
        }
        return true;
    }

    private static String[] principalRef(JsonNode entry) {
        if (entry == null) {
            return new String[] {null, null};
        }
        if (entry.isTextual()) {
            return new String[] {entry.textValue(), null};
        }
        if (entry.isObject() && entry.get("id") != null && entry.get("id").isTextual()) {
            JsonNode kind = entry.get("kind");
            return new String[] {entry.get("id").textValue(), kind != null && kind.isTextual() ? kind.textValue() : null};
        }
        return new String[] {null, null};
    }

    private static String principalClass(String kind) {
        if ("human".equals(kind)) {
            return "agentce:HumanPrincipal";
        }
        if ("service".equals(kind)) {
            return "agentce:ServicePrincipal";
        }
        return "agentce:Principal";
    }

    private static List<String[]> closure(Map<String, String> subclass, Set<String> classes) {
        Set<String> nodes = new LinkedHashSet<>(classes);
        for (Map.Entry<String, String> e : subclass.entrySet()) {
            nodes.add(e.getKey());
            nodes.add(e.getValue());
        }
        List<String[]> pairs = new ArrayList<>();
        for (String node : nodes) {
            pairs.add(new String[] {node, node}); // reflexive
            String current = node;
            Set<String> seen = new LinkedHashSet<>();
            while (subclass.containsKey(current) && !seen.contains(current)) {
                seen.add(current);
                current = subclass.get(current);
                pairs.add(new String[] {node, current});
            }
        }
        return pairs;
    }

    private static final class Builder {
        private final GraphStore store;
        private final DomainBinding domain;
        private final byte[] key;
        private final Set<String> eventIris = new LinkedHashSet<>();
        private final Map<String, String> decisionType = new LinkedHashMap<>();
        private final Map<String, String> decisionTime = new LinkedHashMap<>();
        private final Set<String> decisionReviewed = new LinkedHashSet<>();
        private final Map<String, String> outcomeDecision = new LinkedHashMap<>(); // Outcome IRI -> its refs.decision IRI, if any
        private final Map<String, Set<String>> decisionNotice = new LinkedHashMap<>(); // Decision IRI -> every notifying Notice IRI (order-independent)
        private final Set<String> danglingNodes = new LinkedHashSet<>(); // event IRI -> has >=1 agentce:danglingRef literal
        private final Map<String, Boolean> instructionUntrusted = new LinkedHashMap<>(); // Instruction IRI -> untrusted flag
        // DOC-01: family -> declared name -> the union of its declared pins over every BundleLoaded manifest in the
        // subject's records (empty: any version matches), so event order does not matter.
        private final Map<String, Map<String, Set<String>>> declared =
                Map.of("tool", new LinkedHashMap<>(), "model", new LinkedHashMap<>());
        private final Map<String, Set<String>> operatedNames =
                Map.of("tool", new LinkedHashSet<>(), "model", new LinkedHashSet<>());
        private boolean hasCalls;

        Builder(GraphStore store, DomainBinding domain, byte[] key) {
            this.store = store;
            this.domain = domain;
            this.key = key;
        }

        GraphStore build(List<JsonNode> events) {
            Set<String> usedClasses = new LinkedHashSet<>(BASE_SUBCLASS.keySet());
            usedClasses.add("prov:Agent");
            usedClasses.add("prov:Activity");
            usedClasses.add("prov:Entity");
            for (JsonNode event : events) {
                eventIris.add(Iri.eventIri(event.get("id").asText()));
            }
            for (JsonNode event : events) {
                usedClasses.add("agentce:" + ptype(event));
                if (COMPONENT_RECORD_TYPES.contains(ptype(event))) {
                    usedClasses.add(COMPONENT_RECORD);
                }
                mapEvent(event);
            }
            usedClasses.addAll(decisionType.values());
            Map<String, String> merged = new LinkedHashMap<>(BASE_SUBCLASS);
            merged.putAll(domain.subclasses);
            store.addSubclassClosure(closure(merged, usedClasses));
            materialise(events);
            return store;
        }

        private String ptype(JsonNode event) {
            String type = str(dataOf(event).get("@type"));
            return type != null ? type : "Activity";
        }

        private void mapEvent(JsonNode event) {
            String node = Iri.eventIri(event.get("id").asText());
            JsonNode data = dataOf(event);
            String ptype = ptype(event);
            store.addType(node, "agentce:" + ptype);
            String sourceClass = str(event.get("agentcesourceclass"));
            if (sourceClass != null) {
                store.addEdge(node, "agentce:sourceClass", "agentce:" + sourceClass);
            }
            String time = str(event.get("time"));
            if (time != null) {
                store.addLiteral(node, "prov:atTime", time, DATETIME);
            }

            JsonNode agent = data.get("agent");
            if (agent != null && agent.isObject() && agent.get("id") != null && agent.get("id").isTextual()) {
                String agentId = agent.get("id").textValue();
                store.addType(agentId, "agentce:Agent");
                store.addEdge(node, "prov:wasAssociatedWith", agentId);
                mapChain(agentId, data.get("acted_for"));
            }

            JsonNode used = data.get("used");
            if (used != null && used.isArray()) {
                for (JsonNode item : used) {
                    if (item.isTextual()) {
                        store.addType(item.textValue(), "agentce:ContextItem");
                        store.addEdge(node, "prov:used", item.textValue());
                    }
                }
            }

            JsonNode refs = refsOf(event);
            var it = refs.fields();
            while (it.hasNext()) {
                Map.Entry<String, JsonNode> entry = it.next();
                String predicate = GENERIC_REFS.get(entry.getKey());
                if (predicate != null && entry.getValue().isTextual()) {
                    store.addEdge(node, predicate, entry.getValue().textValue());
                }
            }

            mapDecisionLinks(node, ptype, event);
            mapConduct(node, ptype, data);
            mapComponents(node, ptype, data);
            dangling(node, event);

            if ("DelegationIssued".equals(ptype)) {
                mapDelegationPrincipals(data.get("chain"));
            }

            if ("Decision".equals(ptype)) {
                decisionTime.put(node, time != null ? time : "");
                String dtype = str(data.get("decision_type"));
                if (dtype != null) {
                    decisionType.put(node, dtype);
                    store.addType(node, dtype);
                }
            }
        }

        private void mapDelegationPrincipals(JsonNode chain) {
            if (chain == null || !chain.isArray()) {
                return;
            }
            for (int index = 0; index < chain.size(); index++) {
                String[] ref = principalRef(chain.get(index));
                if (ref[0] == null) {
                    continue;
                }
                String pIri = Iri.principalIri(ref[0], key);
                store.addType(pIri, principalClass(ref[1]));
                store.addLiteral(pIri, "agentce:chainIndex", Integer.toString(index), INTEGER);
            }
        }

        private void mapChain(String agentId, JsonNode actedFor) {
            if (actedFor == null || !actedFor.isArray()) {
                return;
            }
            for (int index = 0; index < actedFor.size(); index++) {
                String[] ref = principalRef(actedFor.get(index));
                if (ref[0] == null) {
                    continue;
                }
                String pIri = Iri.principalIri(ref[0], key);
                store.addType(pIri, principalClass(ref[1]));
                store.addEdge(agentId, "prov:actedOnBehalfOf", pIri);
                store.addLiteral(pIri, "agentce:chainIndex", Integer.toString(index), INTEGER);
            }
        }

        private void mapDecisionLinks(String node, String ptype, JsonNode event) {
            JsonNode refs = refsOf(event);
            String decision = str(refs.get("decision"));
            if (decision != null) {
                switch (ptype) {
                    case "ToolCall" -> store.addEdge(node, "agentce:executes", decision);
                    case "Outcome" -> {
                        store.addEdge(decision, "agentce:resultedIn", node);
                        outcomeDecision.put(node, decision);
                    }
                    case "ApprovalDecided" -> {
                        store.addEdge(decision, "agentce:reviewedBy", node);
                        decisionReviewed.add(decision);
                    }
                    case "Override" -> store.addEdge(decision, "agentce:overriddenBy", node);
                    case "Interrupt" -> store.addEdge(decision, "agentce:interruptedBy", node);
                    case "Notice" -> {
                        store.addEdge(decision, "agentce:notifiedBy", node);
                        decisionNotice.computeIfAbsent(decision, k -> new LinkedHashSet<>()).add(node);
                    }
                    default -> {
                        // other event types carry no decision link
                    }
                }
            }
            if ("Refusal".equals(ptype)) {
                String instruction = str(refs.get("instruction"));
                if (instruction != null) {
                    store.addEdge(instruction, "agentce:refusedBy", node);
                }
            }
        }

        /** Collects what the manifests declare and what the calls exercise (DOC-01); the literals come in the second pass. */
        private void mapComponents(String node, String ptype, JsonNode data) {
            if (!COMPONENT_RECORD_TYPES.contains(ptype)) {
                return;
            }
            if ("BundleLoaded".equals(ptype)) {
                for (Declared component : declaredComponents(data)) {
                    for (String family : families(component.kind())) {
                        declared.get(family).computeIfAbsent(component.name(), k -> new LinkedHashSet<>())
                                .addAll(component.pins());
                    }
                }
            } else {
                store.addType(node, COMPONENT_RECORD);
                hasCalls = true;
                Operated call = operated(ptype, data);
                operatedNames.get(call.family()).addAll(call.names());
            }
        }

        /** Materialise the Conduct-overlay instruction-trust flag (SPEC §7.7): whether an
         * instruction's declared source class is untrusted (Appendix F). Scope and budget are
         * computed from the enforcement point's records in a second pass
         * ({@code conductScopeBudget}). */
        private void mapConduct(String node, String ptype, JsonNode data) {
            if (!"Instruction".equals(ptype)) {
                return;
            }
            String sourceClass = str(data.get("source_class"));
            boolean untrusted = untrustedSourceClass(sourceClass);
            instructionUntrusted.put(node, untrusted);
            store.addLiteral(node, "agentce:instructionUntrusted", untrusted ? "true" : "false", BOOL);
        }

        /** Compute the Conduct within-scope and within-budget flags (SPEC §7.7, CND-01/CND-07) from
         * the enforcement point's records: an action is out of scope when a {@code PolicyDecision}
         * denies its request, and over budget when a {@code Refusal} with reason class
         * {@code budget_exceeded} names it; both default to conformant, so the flags are inert for a
         * bundle that records neither. */
        private void conductScopeBudget(List<JsonNode> events) {
            Set<String> denied = new LinkedHashSet<>();
            Set<String> overBudget = new LinkedHashSet<>();
            for (JsonNode event : events) {
                String ptype = ptype(event);
                JsonNode data = dataOf(event);
                JsonNode refs = refsOf(event);
                String request = str(refs.get("request"));
                if ("PolicyDecision".equals(ptype) && "deny".equals(str(data.get("decision"))) && request != null) {
                    denied.add(request);
                }
                if ("Refusal".equals(ptype)
                        && "budget_exceeded".equals(str(data.get("reason_class")))
                        && request != null) {
                    overBudget.add(request);
                }
            }
            for (JsonNode event : events) {
                String ptype = ptype(event);
                if (!"ToolCall".equals(ptype) && !"ResourceAccess".equals(ptype)) {
                    continue;
                }
                String node = Iri.eventIri(event.get("id").asText());
                store.addLiteral(node, "agentce:withinScope", denied.contains(node) ? "false" : "true", BOOL);
                store.addLiteral(
                        node, "agentce:withinBudget", overBudget.contains(node) ? "false" : "true", BOOL);
            }
        }

        /** Flag every ToolCall/Decision that acts on an instruction whose chain passes through an
         * untrusted source class (SPEC §7.7.4, CND-05). The chain is the instruction's {@code refs.parent}
         * and {@code refs.origin} lineage ({@code agentce:derivedFrom}, SPEC §6.3) for as many hops as the
         * records show. It passes through an untrusted source class at an instruction of an untrusted
         * Appendix F class, at a tool call or resource access (the content they produce is
         * {@code tool_output} or {@code retrieved}), at a parent or origin the bundle does not hold, and at
         * anything ROB-02's taint closure reached
         * ({@code tainted}: an untrusted memory read or write, or content that used one). A closure over
         * all events, so the acting event may precede its instruction and cycles end. */
        private void actsOnUntrusted(List<JsonNode> events, Set<String> tainted) {
            Set<String> untrusted = new LinkedHashSet<>(tainted);
            Map<String, Set<String>> derived = new LinkedHashMap<>();
            Set<String> held = new LinkedHashSet<>();
            for (JsonNode event : events) {
                String ptype = ptype(event);
                String node = Iri.eventIri(event.get("id").asText());
                held.add(node);
                if ("ToolCall".equals(ptype) || "ResourceAccess".equals(ptype)) {
                    untrusted.add(node);
                } else if ("Instruction".equals(ptype)) {
                    JsonNode refs = refsOf(event);
                    for (String key : List.of("parent", "origin")) {
                        flow(derived, str(refs.get(key)), node);
                    }
                }
            }
            // A lineage the bundle does not hold cannot show a trusted root, so it fails closed.
            derived.keySet().stream().filter(source -> !held.contains(source)).forEach(untrusted::add);
            reach(untrusted, derived);
            for (JsonNode event : events) {
                String ptype = ptype(event);
                if (!"ToolCall".equals(ptype) && !"Decision".equals(ptype)) {
                    continue;
                }
                String instruction = str(refsOf(event).get("instruction"));
                boolean acts = instruction != null && untrusted.contains(instruction);
                store.addLiteral(
                        Iri.eventIri(event.get("id").asText()),
                        "agentce:actsOnUntrusted",
                        acts ? "true" : "false",
                        BOOL);
            }
        }

        /** Flag whether each Decision kept untrusted content out, and whether the memory guard
         * ruled on all it used (SPEC §7.4, ROB-02).
         *
         * <p>Untrusted content starts at a MemoryWrite whose trust is untrusted or quarantined, whose
         * guard verdict is quarantine or block, or which carries no trust and no verdict but an
         * untrusted Appendix F {@code provenance_origin_class}; a MemoryRead whose {@code trust_min}
         * is untrusted or quarantined; and an instruction with an untrusted Appendix F source class
         * (CND-05's rule). Content the guard never ruled on starts at a MemoryRead not reported by an
         * enforcement point, a record no ruled enforcement-point write and no enforcement-point read
         * with a {@code trust_min} covers, and a ref the bundle does not hold. Both flow along every
         * edge that carries content into an event (CONTENT_IN, CONTENT_OUT, a read to its consumer, a
         * record to and from its writes) for as many hops as the records show. Closures over all
         * events, so order and cycles never matter. Returns the tainted closure, which CND-05's
         * instruction chain also reads ({@link #actsOnUntrusted}). */
        private Set<String> robustToUntrusted(List<JsonNode> events) {
            Map<String, Set<String>> flows = new LinkedHashMap<>();
            Set<String> tainted = new LinkedHashSet<>();
            Set<String> unruled = new LinkedHashSet<>();
            Set<String> held = new LinkedHashSet<>();
            Set<String> records = new LinkedHashSet<>();
            Set<String> guarded = new LinkedHashSet<>();
            for (JsonNode event : events) {
                String ptype = ptype(event);
                JsonNode data = dataOf(event);
                JsonNode refs = refsOf(event);
                String node = Iri.eventIri(event.get("id").asText());
                held.add(node);
                boolean enforced = "enforcement_point".equals(str(event.get("agentcesourceclass")));
                for (String key : CONTENT_IN) {
                    JsonNode value = data.get(key);
                    if (value != null && value.isArray()) {
                        value.forEach(ref -> flow(flows, str(ref), node));
                    } else {
                        flow(flows, str(value), node);
                    }
                }
                for (String key : REFS_IN) {
                    flow(flows, str(refs.get(key)), node);
                }
                for (String key : CONTENT_OUT) {
                    String produced = str(data.get(key));
                    flow(flows, node, produced);
                    if (produced != null) {
                        held.add(produced);
                    }
                }
                flow(flows, node, str(refs.get("consumer")));
                if ("MemoryWrite".equals(ptype)) {
                    String record = str(data.get("record_ref"));
                    // A record and each write of it stand for the same content.
                    flow(flows, record, node);
                    boolean unmarked = isAbsent(data.get("trust")) && isAbsent(data.get("guard_verdict"));
                    if (record != null) {
                        records.add(record);
                        if (enforced && !unmarked) {
                            guarded.add(record);
                        }
                    }
                    String origin = str(data.get("provenance_origin_class"));
                    if (isOneOf(data.get("trust"), UNTRUSTED_TRUST)
                            || isOneOf(data.get("guard_verdict"), UNTRUSTED_VERDICTS)
                            || (unmarked && untrustedSourceClass(origin))) {
                        tainted.add(node);
                    }
                } else if ("MemoryRead".equals(ptype)) {
                    JsonNode read = data.get("record_refs");
                    // A guard's read rules on its records only when it filtered them by trust.
                    boolean ruling = enforced && !isAbsent(data.get("trust_min"));
                    if (read != null && read.isArray()) {
                        for (JsonNode ref : read) {
                            String record = str(ref);
                            if (record != null) {
                                records.add(record);
                                if (ruling) {
                                    guarded.add(record);
                                }
                            }
                        }
                    }
                    if (!enforced) {
                        unruled.add(node);
                    }
                    if (isOneOf(data.get("trust_min"), UNTRUSTED_TRUST)) {
                        tainted.add(node);
                    }
                }
            }
            instructionUntrusted.forEach((iri, untrusted) -> {
                if (untrusted) {
                    tainted.add(iri);
                }
            });
            for (String record : records) {
                if (!guarded.contains(record)) {
                    unruled.add(record);
                }
            }
            // Every source of a flow is a ref some event names.
            for (String ref : flows.keySet()) {
                if (!held.contains(ref) && !records.contains(ref)) {
                    unruled.add(ref);
                }
            }
            reach(tainted, flows);
            reach(unruled, flows);
            for (JsonNode event : events) {
                if ("Decision".equals(ptype(event))) {
                    String node = Iri.eventIri(event.get("id").asText());
                    store.addLiteral(
                            node, "agentce:robustToUntrustedContent", tainted.contains(node) ? "false" : "true", BOOL);
                    store.addLiteral(
                            node, "agentce:untrustedContentRuledOn", unruled.contains(node) ? "false" : "true", BOOL);
                }
            }
            return tainted;
        }

        private static void flow(Map<String, Set<String>> flows, String source, String target) {
            if (source != null && target != null) {
                flows.computeIfAbsent(source, k -> new LinkedHashSet<>()).add(target);
            }
        }

        /** Grows {@code seeds} in place to everything reachable along {@code edges} (order- and cycle-free). */
        private static void reach(Set<String> seeds, Map<String, Set<String>> edges) {
            List<String> pending = new ArrayList<>(seeds);
            while (!pending.isEmpty()) {
                for (String target : edges.getOrDefault(pending.remove(pending.size() - 1), Set.of())) {
                    if (seeds.add(target)) {
                        pending.add(target);
                    }
                }
            }
        }

        private void materialise(List<JsonNode> events) {
            for (JsonNode event : events) {
                String node = Iri.eventIri(event.get("id").asText());
                String ptype = ptype(event);
                JsonNode data = dataOf(event);
                if ("DelegationIssued".equals(ptype)) {
                    chainVerified(node, data);
                }
                if ("ToolCall".equals(ptype)) {
                    executesConsequential(node, refsOf(event));
                }
                if ("Decision".equals(ptype)) {
                    oversightMatches(node, data);
                    explanationReconstructable(node);
                }
                if ("Outcome".equals(ptype)) {
                    adverseOutcomeLinked(node, data);
                }
                if ("ToolCall".equals(ptype) || "ModelCall".equals(ptype)) {
                    componentDeclared(node, ptype, data);
                }
                if ("BundleLoaded".equals(ptype)) {
                    declaredComponentsObserved(node, data);
                }
                chainTerminus(node, data);
            }
            actsOnUntrusted(events, robustToUntrusted(events));
            conductScopeBudget(events);
            precededBy();
        }

        private void dangling(String node, JsonNode event) {
            List<String> candidates = new ArrayList<>();
            var it = refsOf(event).fields();
            while (it.hasNext()) {
                JsonNode value = it.next().getValue();
                if (value.isTextual()) {
                    candidates.add(value.textValue());
                }
            }
            JsonNode used = dataOf(event).get("used");
            if (used != null && used.isArray()) {
                for (JsonNode value : used) {
                    if (value.isTextual()) {
                        candidates.add(value.textValue());
                    }
                }
            }
            for (String value : candidates) {
                if (Iri.isEventRef(value) && !eventIris.contains(value)) {
                    store.addLiteral(node, "agentce:danglingRef", value);
                    danglingNodes.add(node);
                }
            }
        }

        /** INC-01 (SPEC §7.4): an adverse outcome is linked to the consequential decision it
         * resulted from; a non-adverse outcome carries no such expectation and is vacuously linked.
         * A refs.decision that is missing, dangling (names no ingested event), or resolves to a
         * decision that is not itself a ConsequentialDecision does not count as linked -- a real
         * link requires a real consequential decision, not merely the shape of one. */
        private void adverseOutcomeLinked(String node, JsonNode data) {
            boolean adverse = truthy(data.get("adverse"));
            String decision = outcomeDecision.get(node);
            boolean linked = !adverse
                    || (decision != null
                            && eventIris.contains(decision)
                            && store.isA(decision, "agentce:ConsequentialDecision"));
            store.addLiteral(node, "agentce:adverseOutcomeLinked", linked ? "true" : "false", BOOL);
        }

        /** TRN-03 (SPEC §7.6): affected persons are informed (notifiedBy a Notice) and the evidence
         * chain resolves on both ends of that notification -- no danglingRef on the decision itself
         * (every ref or "used" value it names resolves to an ingested event) AND no danglingRef on
         * the Notice that notified it (round-2 critic finding: a Notice's own dangling ref, e.g. a
         * free-form explanation_ref, must not be invisible just because dangling() lands the literal
         * on the Notice node, not the Decision node) -- so the evidence an explanation would be built
         * from is actually present on both legs. A decision can be notifiedBy more than one Notice;
         * checking only the last one mapped made the result depend on event order, so this checks
         * every notifying Notice and passes if any one of them is clean (a verifier-found regression,
         * fixed this item). Scoped narrower than the Notice's own content_ref or the Decision's own
         * rationale_claim_ref (SPEC model attributes, not refs edges): neither is materialised as a
         * graph reference anywhere today, so neither can dangle in this model yet -- disclosed, out
         * of this item's scope (see this item's contract, split to 18.37h). */
        private void explanationReconstructable(String node) {
            Set<String> notices = decisionNotice.get(node);
            boolean reconstructable =
                    notices != null
                            && !notices.isEmpty()
                            && !danglingNodes.contains(node)
                            && notices.stream().anyMatch(notice -> !danglingNodes.contains(notice));
            store.addLiteral(
                    node, "agentce:explanationReconstructable", reconstructable ? "true" : "false", BOOL);
        }

        /**
         * DOC-01 S1: a call exercises a component some BundleLoaded manifest declares -- one of its names is declared
         * for its family, and when both the call and the declaration state a version or digest, they agree. A call
         * that names nothing cannot be shown declared.
         */
        private void componentDeclared(String node, String ptype, JsonNode data) {
            Operated call = operated(ptype, data);
            Map<String, Set<String>> known = declared.get(call.family());
            boolean isDeclared = call.names().stream().anyMatch(name -> known.containsKey(name)
                    && (known.get(name).isEmpty() || call.version() == null || known.get(name).contains(call.version())));
            store.addLiteral(node, "agentce:componentDeclared", isDeclared ? "true" : "false", BOOL);
        }

        /**
         * DOC-01 S2: every model and MCP server this manifest declares is exercised by at least one call. A manifest
         * with nothing to compare -- no call in the records and no model or MCP server to see -- is not a component
         * record, so DOC-01 never reads conformant on it alone (18.37j).
         */
        private void declaredComponentsObserved(String node, JsonNode data) {
            List<Map.Entry<String, String>> mustSee = declaredComponents(data).stream()
                    .flatMap(component -> {
                        String family = mustOperate(component.kind());
                        return family == null ? Stream.<Map.Entry<String, String>>empty()
                                : Stream.of(Map.entry(family, component.name()));
                    }).toList();
            if (!hasCalls && mustSee.isEmpty()) {
                return;
            }
            store.addType(node, COMPONENT_RECORD);
            boolean observed = mustSee.stream()
                    .allMatch(entry -> operatedNames.get(entry.getKey()).contains(entry.getValue()));
            store.addLiteral(node, "agentce:declaredComponentsObserved", observed ? "true" : "false", BOOL);
        }

        private void chainVerified(String node, JsonNode data) {
            JsonNode verification = data.get("verification");
            boolean verified = verification != null
                    && verification.isObject()
                    && "verified".equals(str(verification.get("status")));
            if (data.has("chain_verified")) {
                verified = truthy(data.get("chain_verified"));
            }
            store.addLiteral(node, "agentce:chainVerified", verified ? "true" : "false", BOOL);
        }

        private void executesConsequential(String node, JsonNode refs) {
            String decision = str(refs.get("decision"));
            boolean consequential = false;
            if (decision != null) {
                consequential = domain.consequential.contains(decisionType.getOrDefault(decision, ""));
            }
            store.addLiteral(node, "agentce:executesConsequential", consequential ? "true" : "false", BOOL);
        }

        private void oversightMatches(String node, JsonNode data) {
            String dtype = str(data.get("decision_type"));
            String required = dtype != null ? domain.requiredOversight.get(dtype) : null;
            String observed = str(data.get("oversight_modality"));
            boolean matches = required == null || Objects.equals(observed, required);
            store.addLiteral(node, "agentce:oversightModalityMatchesDeclared", matches ? "true" : "false", BOOL);
        }

        private void chainTerminus(String node, JsonNode data) {
            JsonNode actedFor = data.get("acted_for");
            if (actedFor != null && actedFor.isArray() && !actedFor.isEmpty()) {
                String[] ref = principalRef(actedFor.get(actedFor.size() - 1));
                if (ref[0] != null) {
                    store.addEdge(node, "agentce:chainTerminus", Iri.principalIri(ref[0], key));
                }
            }
        }

        private void precededBy() {
            Map<String, List<String>> byType = new LinkedHashMap<>();
            for (Map.Entry<String, String> e : decisionType.entrySet()) {
                byType.computeIfAbsent(e.getValue(), k -> new ArrayList<>()).add(e.getKey());
            }
            for (List<String> nodes : byType.values()) {
                List<String> ordered = new ArrayList<>(nodes);
                ordered.sort((a, b) -> {
                    int c = Json.byteCompare(decisionTime.getOrDefault(a, ""), decisionTime.getOrDefault(b, ""));
                    return c != 0 ? c : Json.byteCompare(a, b);
                });
                for (int position = 0; position < ordered.size(); position++) {
                    for (int i = position - 1; i >= 0; i--) {
                        String earlier = ordered.get(i);
                        if (decisionReviewed.contains(earlier)) {
                            store.addEdge(ordered.get(position), "agentce:precededBy", earlier);
                            break;
                        }
                    }
                }
            }
        }
    }
}
