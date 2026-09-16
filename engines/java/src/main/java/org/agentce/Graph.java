package org.agentce;

import com.fasterxml.jackson.databind.JsonNode;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Set;

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

    private static String str(JsonNode node) {
        return node != null && node.isTextual() ? node.textValue() : null;
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
                    case "Outcome" -> store.addEdge(decision, "agentce:resultedIn", node);
                    case "ApprovalDecided" -> {
                        store.addEdge(decision, "agentce:reviewedBy", node);
                        decisionReviewed.add(decision);
                    }
                    case "Override" -> store.addEdge(decision, "agentce:overriddenBy", node);
                    case "Interrupt" -> store.addEdge(decision, "agentce:interruptedBy", node);
                    case "Notice" -> store.addEdge(decision, "agentce:notifiedBy", node);
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

        private void materialise(List<JsonNode> events) {
            for (JsonNode event : events) {
                String node = Iri.eventIri(event.get("id").asText());
                String ptype = ptype(event);
                JsonNode data = dataOf(event);
                dangling(node, event);
                if ("DelegationIssued".equals(ptype)) {
                    chainVerified(node, data);
                }
                if ("ToolCall".equals(ptype)) {
                    executesConsequential(node, refsOf(event));
                }
                if ("Decision".equals(ptype)) {
                    oversightMatches(node, data);
                }
                chainTerminus(node, data);
            }
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
                }
            }
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
