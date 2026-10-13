package org.agentce;

import com.fasterxml.jackson.databind.JsonNode;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeSet;

/**
 * The class a control's judgment rests on, against the control's minimum (SPEC §6.4, HR-5, §7.7.5). A port of
 * Python's {@code agentce.source_class}.
 *
 * <p>Every control declares {@code evaluation.min_source_class}. An assertion meets it only when every applicable
 * focus does: {@link Judge#judgedClass} is the lowest class among the focus's own record (left out for a Decision,
 * which is the agent's own account by nature, §7.7.5) and the records one hop away through each predicate the
 * control names in {@code evaluation.source_class_from}. A record's class is its own {@code agentcesourceclass},
 * except for an Instruction, which counts as {@code enforcement_point} only when attested ({@link Judge#attested})
 * and as {@code self_report} otherwise: an agent's own record that it was told something by the user is never
 * taken as given.
 */
public final class SourceClass {
    private SourceClass() {}

    /** The source classes in their SPEC §6.4 order; the rank is the order. {@code any} is not a class. */
    public static final Map<String, Integer> RANK =
            Map.of("self_report", 0, "enforcement_point", 1, "independent_system", 2);

    static final String ENFORCEMENT_POINT = "enforcement_point";

    /** The graph links {@code evaluation.source_class_from} may name, each one hop from the focus (SPEC §6.3), and
     * the record type each one names. */
    public static final Map<String, String> FROM =
            Map.of("actsOn", "Instruction", "delegatedVia", "DelegationIssued", "approvedBy", "ApprovalDecided");

    /** The outcome an assertion takes when a focus's judged class is below the control's minimum (HR-5). One
     * constant, so a later reading of single-source evidence (SPEC §10.4 {@code partial}) changes it in one place. */
    public static final String BELOW_MINIMUM_OUTCOME = "insufficient_evidence";

    /** The Appendix F instruction source classes an agent may act on (the graph's trusted set, SPEC §7.7). */
    private static final Set<String> TRUSTED_INSTRUCTION =
            Set.of("user", "operator", "service", "agent_identified", "memory_trusted");

    /** Which witnesses may stand in for an instruction's own class at (iv): its own class only (a twin), its own
     * class or a twin (a parent: the PolicyDecision allows the action, which acts on the child only), or all. */
    private static final int OWN = 0;
    private static final int TWIN = 1;
    private static final int ALL = 2;

    /** The rank of a record's class; a record with no class, or one outside SPEC §6.4, ranks as self_report. */
    static int classRank(JsonNode value) {
        return value != null && value.isTextual() ? RANK.getOrDefault(value.textValue(), 0) : 0;
    }

    private static JsonNode data(JsonNode event) {
        JsonNode data = event.get("data");
        return data != null && data.isObject() ? data : Json.nodes().objectNode();
    }

    private static JsonNode refs(JsonNode event) {
        JsonNode refs = data(event).get("refs");
        return refs != null && refs.isObject() ? refs : Json.nodes().objectNode();
    }

    private static String type(JsonNode event) {
        JsonNode t = data(event).get("@type");
        return t != null && t.isTextual() ? t.textValue() : "";
    }

    /** A non-empty string, else null. */
    private static String text(JsonNode value) {
        return value != null && value.isTextual() && !value.textValue().isEmpty() ? value.textValue() : null;
    }

    private static String principalId(JsonNode value) {
        return value != null && value.isObject() ? text(value.get("id")) : null;
    }

    private static boolean isEnforcementPoint(JsonNode value) {
        return value != null && value.isTextual() && ENFORCEMENT_POINT.equals(value.textValue());
    }

    /** Python's {@code a.get(k) == b.get(k)} over two JSON values: an absent member and null are both None. */
    private static boolean sameValue(JsonNode a, JsonNode b) {
        boolean aNone = a == null || a.isNull();
        boolean bNone = b == null || b.isNull();
        return aNone || bNone ? aNone && bNone : a.equals(b);
    }

    private static boolean textEquals(JsonNode value, String expected) {
        return value != null && value.isTextual() && value.textValue().equals(expected);
    }

    /** A focus's judged class: its rank and the record types that set it (the lowest contributors). */
    public record Judged(int rank, List<String> lowest) {}

    /** The foci below the minimum, sorted, and the record types that put them there, sorted. */
    public record Below(List<String> foci, List<String> kinds) {}

    /** Judges foci against one subject's held records ({@code eventsByIri}: graph IRI -> event) and graph. */
    public static final class Judge {
        private final GraphStore store;
        private final Map<String, JsonNode> events;
        private final List<String> instructions = new ArrayList<>();
        private final List<String> policyDecisions = new ArrayList<>();

        public Judge(GraphStore store, Map<String, JsonNode> eventsByIri) {
            this.store = store;
            this.events = eventsByIri;
            for (Map.Entry<String, JsonNode> e : eventsByIri.entrySet()) {
                String t = type(e.getValue());
                if ("Instruction".equals(t)) {
                    instructions.add(e.getKey());
                } else if ("PolicyDecision".equals(t)) {
                    policyDecisions.add(e.getKey());
                }
            }
        }

        private int ownRank(String node) {
            JsonNode event = events.get(node);
            return event != null ? classRank(event.get("agentcesourceclass")) : 0;
        }

        /** (i): the Instruction a ref names by its graph IRI among this subject's held records. */
        private JsonNode heldInstruction(String node) {
            JsonNode event = node != null ? events.get(node) : null;
            return event != null && "Instruction".equals(type(event)) ? event : null;
        }

        /** Whether {@code instruction} is attested for {@code focus} (ruling 5: an enforcement-point ingress
         * record): (i) held under the subject by graph IRI; (ii) every refs.parent held and itself attested, by its
         * own class or a twin; (iii) its refs.origin, if any, an enforcement-point MemoryRead at trust_min trusted;
         * (iv) its own class enforcement_point, or witnessed by an attested enforcement-point twin or by an
         * enforcement-point PolicyDecision allowing the focus for the instruction's principal (corroboration
         * replaces only (iv)). */
        public boolean attested(String focus, String instruction) {
            return attested(focus, instruction, Set.of(), ALL);
        }

        private boolean attested(String focus, String node, Set<String> visiting, int witnesses) {
            JsonNode event = heldInstruction(node);
            if (event == null) { // (i) held under the subject by graph IRI
                return false;
            }
            // A parent cycle: the node is already being judged further up the chain.
            if (visiting.contains(node)) {
                return true;
            }
            Set<String> inner = new HashSet<>(visiting);
            inner.add(node);
            JsonNode refs = refs(event);
            if (refs.has("parent")) { // (ii) every parent held and itself attested
                JsonNode parent = refs.get("parent");
                if (!(parent.isTextual() && attested(focus, parent.textValue(), inner, Math.min(witnesses, TWIN)))) {
                    return false;
                }
            }
            if (refs.has("origin") && !trustedOrigin(refs.get("origin"))) { // (iii)
                return false;
            }
            // (iv) by its own class, exactly enforcement_point (ruling 5); then the witnesses this hop allows.
            if (isEnforcementPoint(event.get("agentcesourceclass"))) {
                return true;
            }
            if (witnesses == OWN) {
                return false;
            }
            if (twinWitnesses(node, event, inner)) {
                return true;
            }
            return witnesses == ALL && decisionWitnesses(focus, event);
        }

        /** (iii): an origin is an enforcement-point MemoryRead held under the subject whose trust_min is trusted. */
        private boolean trustedOrigin(JsonNode origin) {
            JsonNode event = origin != null && origin.isTextual() ? events.get(origin.textValue()) : null;
            return event != null
                    && "MemoryRead".equals(type(event))
                    && isEnforcementPoint(event.get("agentcesourceclass"))
                    && textEquals(data(event).get("trust_min"), "trusted");
        }

        /** (iv) by an enforcement-point twin: the same content_ref, refs.task, source_class and principal, held
         * under the subject, meeting (i)-(iv) by its own class, every instruction above it declaring a trusted
         * class. */
        private boolean twinWitnesses(String node, JsonNode event, Set<String> visiting) {
            JsonNode data = data(event);
            String content = text(data.get("content_ref"));
            String task = text(refs(event).get("task"));
            if (content == null || task == null) {
                return false;
            }
            for (String twin : instructions) {
                if (twin.equals(node) || visiting.contains(twin)) {
                    continue;
                }
                JsonNode other = events.get(twin);
                JsonNode otherData = data(other);
                if (isEnforcementPoint(other.get("agentcesourceclass"))
                        && content.equals(text(otherData.get("content_ref")))
                        && task.equals(text(refs(other).get("task")))
                        && sameValue(otherData.get("source_class"), data.get("source_class"))
                        && java.util.Objects.equals(
                                principalId(otherData.get("principal")), principalId(data.get("principal")))
                        && attested(twin, twin, visiting, OWN)
                        && lineageTrusted(twin)) {
                    return true;
                }
            }
            return false;
        }

        /** Every instruction above {@code node} through refs.parent declares a trusted Appendix F class. */
        private boolean lineageTrusted(String node) {
            Set<String> seen = new HashSet<>();
            JsonNode parent = refs(events.get(node)).get("parent");
            while (parent != null && parent.isTextual() && seen.add(parent.textValue())) {
                JsonNode event = heldInstruction(parent.textValue());
                if (event == null) {
                    return false;
                }
                JsonNode sourceClass = data(event).get("source_class");
                if (!(sourceClass != null && sourceClass.isTextual()
                        && TRUSTED_INSTRUCTION.contains(sourceClass.textValue()))) {
                    return false;
                }
                parent = refs(event).get("parent");
            }
            return true;
        }

        /** (iv) by an enforcement-point PolicyDecision allow whose refs.request is the focus and whose principal
         * is the instruction's (both present). */
        private boolean decisionWitnesses(String focus, JsonNode event) {
            String principal = principalId(data(event).get("principal"));
            if (principal == null) {
                return false;
            }
            for (String decision : policyDecisions) {
                JsonNode record = events.get(decision);
                JsonNode data = data(record);
                if (isEnforcementPoint(record.get("agentcesourceclass"))
                        && textEquals(data.get("decision"), "allow")
                        && textEquals(refs(record).get("request"), focus)
                        && principal.equals(principalId(data.get("principal")))) {
                    return true;
                }
            }
            return false;
        }

        /** A record one hop from the focus: an Instruction ranks enforcement_point only when attested; anything
         * else ranks as its own class, and a ref naming no held record as self_report ({@code kind} names it). */
        private Map.Entry<Integer, String> attestedRank(String focus, String node, String kind) {
            JsonNode event = events.get(node);
            if (event == null) {
                return Map.entry(0, kind);
            }
            String t = type(event);
            if ("Instruction".equals(t)) {
                return Map.entry(attested(focus, node) ? RANK.get(ENFORCEMENT_POINT) : 0, "Instruction");
            }
            return Map.entry(ownRank(node), t.isEmpty() ? kind : t);
        }

        /** The lowest class the judgment of {@code focus} rests on; null when nothing contributes (a Decision with
         * no record through any named link: there is no record the judgment rests on to hold to a class). */
        public Judged judgedClass(String focus, List<String> sourceClassFrom) {
            List<Map.Entry<Integer, String>> contributors = new ArrayList<>();
            JsonNode event = events.get(focus);
            if (event == null || !"Decision".equals(type(event))) {
                contributors.add(Map.entry(ownRank(focus), event != null ? type(event) : ""));
            }
            Set<String> names = new TreeSet<>(Json::byteCompare);
            for (String name : sourceClassFrom) {
                if (FROM.containsKey(name)) {
                    names.add(name);
                }
            }
            for (String name : names) {
                for (String node : store.objects(focus, "agentce:" + name)) {
                    contributors.add(attestedRank(focus, node, FROM.get(name)));
                }
            }
            if (contributors.isEmpty()) {
                return null;
            }
            int rank = contributors.stream().mapToInt(Map.Entry::getKey).min().getAsInt();
            Set<String> lowest = new TreeSet<>(Json::byteCompare);
            for (Map.Entry<Integer, String> c : contributors) {
                if (c.getKey() == rank) {
                    lowest.add(c.getValue());
                }
            }
            return new Judged(rank, List.copyOf(lowest));
        }
    }

    /** The foci whose judged class is below {@code minimum}, sorted, and the record types that put them there. */
    public static Below belowMinimum(Judge judge, List<String> foci, String minimum, List<String> sourceClassFrom) {
        int floor = RANK.get(minimum);
        List<String> sorted = new ArrayList<>(foci);
        sorted.sort(Json::byteCompare);
        List<String> below = new ArrayList<>();
        Set<String> kinds = new TreeSet<>(Json::byteCompare);
        for (String focus : sorted) {
            Judged judged = judge.judgedClass(focus, sourceClassFrom);
            if (judged != null && judged.rank() < floor) {
                below.add(focus);
                kinds.addAll(judged.lowest());
            }
        }
        return new Below(below, List.copyOf(kinds));
    }
}
