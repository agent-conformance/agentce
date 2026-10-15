package org.agentce;

import static org.agentce.Graph.dataOf;
import static org.agentce.Graph.refsOf;
import static org.agentce.Graph.str;
import static org.agentce.Graph.text;

import com.fasterxml.jackson.databind.JsonNode;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Set;
import java.util.TreeSet;
import java.util.function.Supplier;

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
            Map.of(
                    "actsOn", "Instruction",
                    "delegatedVia", "DelegationIssued",
                    "approvedBy", "ApprovalDecided",
                    "releasedBy", "ApprovalDecided");

    /** The outcome an assertion takes when a focus's judged class is below the control's minimum (HR-5). One
     * constant, so a later reading of single-source evidence (SPEC §10.4 {@code partial}) changes it in one place. */
    public static final String BELOW_MINIMUM_OUTCOME = "insufficient_evidence";

    /** The declared classes SPEC §6.2.3 requires a principal for: a twin witnesses one only when both carry the same
     * id. */
    private static final Set<String> PRINCIPAL_REQUIRED = Set.of("user", "operator", "service", "agent_identified");

    /** Which witnesses may stand in for an instruction's own class at (iv): its own class only (a twin), its own
     * class or a twin (a parent: the PolicyDecision allows the action, which acts on the child only), or all. */
    private static final int OWN = 0;
    private static final int TWIN = 1;
    private static final int ALL = 2;

    /** The rank of a record's class; a record with no class, or one outside SPEC §6.4, ranks as self_report. */
    static int classRank(JsonNode value) {
        return value != null && value.isTextual() ? RANK.getOrDefault(value.textValue(), 0) : 0;
    }

    private static String type(JsonNode event) {
        String t = str(dataOf(event).get("@type"));
        return t != null ? t : "";
    }

    private static String principalId(JsonNode value) {
        return value != null && value.isObject() ? text(value.get("id")) : null;
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
        /** Each enforcement-point Instruction with a content_ref and refs.task, by (content_ref, refs.task,
         * principal id), in held order: the candidate twins of an instruction with those three. */
        private final Map<List<String>, List<String>> twins = new HashMap<>();
        /** The times of the enforcement-point PolicyDecision allows naming a principal, by (refs.request,
         * principal id). */
        private final Map<List<String>, List<JsonNode>> allows = new HashMap<>();
        /** Each focus's clock ({@link #clock}), computed once; an empty optional when it has none. */
        private final Map<String, Optional<List<String>>> clocks = new HashMap<>();

        public Judge(GraphStore store, Map<String, JsonNode> eventsByIri) {
            this.store = store;
            this.events = eventsByIri;
            for (Map.Entry<String, JsonNode> e : eventsByIri.entrySet()) {
                JsonNode event = e.getValue();
                if (!textEquals(event.get("agentcesourceclass"), ENFORCEMENT_POINT)) {
                    continue;
                }
                JsonNode data = dataOf(event);
                JsonNode refs = refsOf(event);
                String t = type(event);
                String principal = principalId(data.get("principal"));
                if ("Instruction".equals(t)) {
                    String content = text(data.get("content_ref"));
                    String task = text(refs.get("task"));
                    if (content != null && task != null) {
                        twins.computeIfAbsent(Arrays.asList(content, task, principal), k -> new ArrayList<>())
                                .add(e.getKey());
                    }
                } else if ("PolicyDecision".equals(t)
                        && textEquals(data.get("decision"), "allow")
                        && str(refs.get("request")) != null
                        && principal != null) {
                    allows.computeIfAbsent(List.of(str(refs.get("request")), principal), k -> new ArrayList<>())
                            .add(event.get("time"));
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
            JsonNode refs = refsOf(event);
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
            if (textEquals(event.get("agentcesourceclass"), ENFORCEMENT_POINT)) {
                return true;
            }
            if (witnesses == OWN) {
                return false;
            }
            if (twinWitnesses(focus, node, event, inner)) {
                return true;
            }
            return witnesses == ALL && decisionWitnesses(focus, event);
        }

        /** (iii): an origin is an enforcement-point MemoryRead held under the subject whose trust_min is trusted. */
        private boolean trustedOrigin(JsonNode origin) {
            JsonNode event = origin != null && origin.isTextual() ? events.get(origin.textValue()) : null;
            return event != null
                    && "MemoryRead".equals(type(event))
                    && textEquals(event.get("agentcesourceclass"), ENFORCEMENT_POINT)
                    && textEquals(dataOf(event).get("trust_min"), "trusted");
        }

        /** The times a witness must not follow, read only where the agent does not write them: an
         * enforcement-point (or better) focus's own time; for a Decision, self_report by nature, the time of every
         * enforcement-point ToolCall executing it. Null when there is no such clock (a self_report ToolCall, a
         * Decision no enforcement-point call executes) or one of those times is missing. */
        private List<String> clock(String focus) {
            return clocks.computeIfAbsent(focus, f -> {
                JsonNode event = events.get(f);
                List<JsonNode> anchors = new ArrayList<>();
                if (event != null && "Decision".equals(type(event))) {
                    for (String call : store.subjects("agentce:executes", f)) {
                        if (events.containsKey(call) && ownRank(call) > 0) {
                            anchors.add(events.get(call));
                        }
                    }
                } else if (event != null && ownRank(f) > 0) {
                    anchors.add(event);
                }
                List<String> times = new ArrayList<>();
                for (JsonNode anchor : anchors) {
                    JsonNode time = anchor.get("time");
                    if (time != null && time.isTextual()) {
                        times.add(time.textValue());
                    }
                }
                return anchors.isEmpty() || times.size() != anchors.size()
                        ? Optional.empty()
                        : Optional.of(times);
            }).orElse(null);
        }

        /** Whether a witness recorded at {@code witnessedAt} is at or before the focus's clock, as instants. */
        private boolean inTime(String focus, JsonNode witnessedAt) {
            List<String> clock = clock(focus);
            if (clock == null || witnessedAt == null || !witnessedAt.isTextual()) {
                return false;
            }
            for (String t : clock) {
                Integer order = Structural.compareInstants(witnessedAt.textValue(), t);
                if (order == null || order > 0) {
                    return false;
                }
            }
            return true;
        }

        /** (iv) by an enforcement-point twin: the same content_ref, refs.task, source_class and principal (present
         * on both for a class that requires one), held under the subject, recorded at or before the focus's clock,
         * meeting (i)-(iv) by its own class, every instruction above it declaring a trusted class. */
        private boolean twinWitnesses(String focus, String node, JsonNode event, Set<String> visiting) {
            JsonNode data = dataOf(event);
            String content = text(data.get("content_ref"));
            String task = text(refsOf(event).get("task"));
            String principal = principalId(data.get("principal"));
            if (content == null || task == null) {
                return false;
            }
            String declared = str(data.get("source_class"));
            if (principal == null && declared != null && PRINCIPAL_REQUIRED.contains(declared)) {
                return false;
            }
            List<String> key = Arrays.asList(content, task, principal);
            for (String twin : twins.getOrDefault(key, List.of())) {
                if (twin.equals(node) || visiting.contains(twin)) {
                    continue;
                }
                if (sameValue(dataOf(events.get(twin)).get("source_class"), data.get("source_class"))
                        && inTime(focus, events.get(twin).get("time"))
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
            JsonNode parent = refsOf(events.get(node)).get("parent");
            while (parent != null && parent.isTextual() && seen.add(parent.textValue())) {
                JsonNode event = heldInstruction(parent.textValue());
                if (event == null) {
                    return false;
                }
                String sourceClass = str(dataOf(event).get("source_class"));
                if (sourceClass == null || !Graph.TRUSTED_INSTRUCTION.contains(sourceClass)) {
                    return false;
                }
                parent = refsOf(event).get("parent");
            }
            return true;
        }

        /** (iv) by an enforcement-point PolicyDecision allow whose refs.request is the focus (for a Decision, the
         * focus or an enforcement-point ToolCall executing it) and whose principal is the instruction's (both
         * present), recorded at or before the focus's clock. */
        private boolean decisionWitnesses(String focus, JsonNode event) {
            String principal = principalId(dataOf(event).get("principal"));
            if (principal == null) {
                return false;
            }
            // A Decision, the agent's own account, is allowed through the enforcement-point calls executing it (the
            // calls its clock reads).
            List<String> named = new ArrayList<>(List.of(focus));
            JsonNode held = events.get(focus);
            if (held != null && "Decision".equals(type(held))) {
                for (String call : store.subjects("agentce:executes", focus)) {
                    if (events.containsKey(call) && ownRank(call) > 0) {
                        named.add(call);
                    }
                }
            }
            for (String request : named) {
                for (JsonNode at : allows.getOrDefault(List.of(request, principal), List.of())) {
                    if (inTime(focus, at)) {
                        return true;
                    }
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

        /** The ranks of the records one named link from {@code focus}. */
        private List<Map.Entry<Integer, String>> reached(String focus, List<String> sourceClassFrom) {
            List<Map.Entry<Integer, String>> ranks = new ArrayList<>();
            for (String name : sourceClassFrom) {
                for (String node : store.objects(focus, "agentce:" + name)) {
                    ranks.add(attestedRank(focus, node, FROM.get(name)));
                }
            }
            return ranks;
        }

        /** The lowest class the judgment of {@code focus} rests on. A Decision is self_report by nature (§7.7.5),
         * so it rests on what it reaches through the named links, or failing those on what every ToolCall
         * executing it reaches; a Decision that reaches nothing either way rests on its own record, self_report.
         * {@code sourceClassFrom} holds distinct {@link #FROM} names, sorted (as the catalog stores them). */
        public Judged judgedClass(String focus, List<String> sourceClassFrom) {
            JsonNode event = events.get(focus);
            List<Map.Entry<Integer, String>> contributors;
            if (event != null && "Decision".equals(type(event))) {
                contributors = reached(focus, sourceClassFrom);
                if (contributors.isEmpty()) {
                    for (String call : store.subjects("agentce:executes", focus)) {
                        contributors.addAll(reached(call, sourceClassFrom));
                    }
                }
                if (contributors.isEmpty()) {
                    contributors = List.of(Map.entry(0, "Decision"));
                }
            } else if (sourceClassFrom.contains("releasedBy")) {
                return releasedClass(focus, event);
            } else {
                contributors = new ArrayList<>();
                contributors.add(Map.entry(ownRank(focus), event != null ? type(event) : ""));
                contributors.addAll(reached(focus, sourceClassFrom));
            }
            return lowestOf(contributors);
        }

        /** CND-02's reading (SPEC §7.7.3, §7.7.4): the action rests on the best approval that releases its gate,
         * and on its own record only when that is below enforcement_point; an action no approval releases passed
         * only on an enforcement-point allow, which the control itself holds to enforcement_point. */
        private Judged releasedClass(String focus, JsonNode event) {
            int own = ownRank(focus);
            List<Map.Entry<Integer, String>> contributors = new ArrayList<>();
            if (own < RANK.get(ENFORCEMENT_POINT)) {
                contributors.add(Map.entry(own, event != null ? type(event) : ""));
            }
            List<Map.Entry<Integer, String>> released = reached(focus, List.of("releasedBy"));
            if (!released.isEmpty()) {
                int best = released.stream().mapToInt(Map.Entry::getKey).max().getAsInt();
                released.stream().filter(r -> r.getKey() == best).forEach(contributors::add);
            }
            if (contributors.isEmpty()) {
                return new Judged(RANK.values().stream().mapToInt(Integer::intValue).max().getAsInt(), List.of());
            }
            return lowestOf(contributors);
        }

        private static Judged lowestOf(List<Map.Entry<Integer, String>> contributors) {
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

    /** The foci whose judged class is below {@code minimum}, sorted, and the record types that put them there.
     * {@code judge} builds (or returns) the subject's judge, called only when a focus can fall below the minimum. */
    public static Below belowMinimum(
            Supplier<Judge> judge, List<String> foci, String minimum, List<String> sourceClassFrom) {
        int floor = RANK.get(minimum);
        if (floor == 0) { // nothing ranks below self_report
            return new Below(List.of(), List.of());
        }
        List<String> sorted = new ArrayList<>(foci);
        sorted.sort(Json::byteCompare);
        List<String> below = new ArrayList<>();
        Set<String> kinds = new TreeSet<>(Json::byteCompare);
        for (String focus : sorted) {
            Judged judged = judge.get().judgedClass(focus, sourceClassFrom);
            if (judged.rank() < floor) {
                below.add(focus);
                kinds.addAll(judged.lowest());
            }
        }
        return new Below(below, List.copyOf(kinds));
    }
}
