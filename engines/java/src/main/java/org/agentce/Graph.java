package org.agentce;

import com.fasterxml.jackson.databind.JsonNode;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Deque;
import java.util.HashMap;
import java.util.HashSet;
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
 * {@code agentce:precededBy}, {@code agentce:componentDeclared}, {@code agentce:declaredComponentsObserved},
 * {@code agentce:triggersIncident}, {@code agentce:oversightCoverageComplete},
 * {@code agentce:interventionEffective}, {@code agentce:interventionByHuman},
 * {@code agentce:incidentResponded}, {@code agentce:riskReviewed}, {@code agentce:securityRefsResolve},
 * {@code agentce:linkedRecordsResolve} and the decision-record literals DOC-02, TRN-04, OVS-05, RSK-01, REC-05,
 * TRN-01 and TRN-05 read). The {@code rdfs:subClassOf*} closure is materialised so class membership
 * needs no inference. This is a faithful port of the reference; it must build byte-identical graphs.
 */
public final class Graph {
    private Graph() {}

    private static final String BOOL = "xsd:boolean";
    /** CND-02's population (SPEC §7.7.4), materialised by {@code conductGate}. */
    static final String GATED_ACTION = "agentce:GatedAction";
    /** CND-04's population (SPEC §7.7.4), materialised by {@code conductProvenance}. */
    static final String CONSEQUENTIAL_ACTION = "agentce:ConsequentialAction";
    /** A ModelCall in a task: consumption CND-07 judges against the task's budgets. */
    static final String TASK_MODEL_CALL = "agentce:TaskModelCall";
    /** An action whose task budgets the records cannot tell (CND-07's evidence shape). */
    static final String BUDGET_UNJUDGED = "agentce:BudgetUnjudged";
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
    static final Set<String> TRUSTED_INSTRUCTION =
            Set.of("user", "operator", "service", "agent_identified", "memory_trusted");

    /** The Appendix F classes that name an authorised principal: CND-04's chain must end at one (memory_trusted does
     * not, ruling 12 A of 2026-10-12). */
    private static final Set<String> AUTHORISED_INSTRUCTION =
            Set.of("user", "operator", "service", "agent_identified");

    /** The own classes whose record of an instruction's principal is read as recorded (SPEC §6.4 order, at or above
     * enforcement_point); below them a principal's role needs an enforcement-point witness (CND-04). */
    private static final Set<String> AT_OR_ABOVE_EP = Set.of("enforcement_point", "independent_system");

    /** The ToolCall references ROB-03 resolves, each with the event type the SPEC gives its target (§6.2). */
    private static final List<Map.Entry<String, String>> SECURITY_REFS = List.of(
            Map.entry("authorization", "PolicyDecision"),
            Map.entry("delegation", "DelegationIssued"),
            Map.entry("instruction", "Instruction"));

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

    /** Interrupt effects that stop the agent, and the interrupt mechanisms SPEC §6.2 names (OVS-07). */
    private static final Set<String> INTERRUPT_STOPS = Set.of("halted", "paused");
    private static final Set<String> INTERRUPT_MECHANISMS =
            Set.of("stop_button", "kill_switch", "circuit_breaker", "manual");
    /** The stream classes an identity-provider login record must come from (SPEC §6.2 ApprovalDecided.session_ref, §10.4). */
    private static final Set<String> LOGIN_CLASSES = Set.of("independent_system", "enforcement_point");
    /** The effect classes SPEC §7.7.4 holds to CND-02 whatever the task scope marks (ruling Q6). */
    private static final Set<String> HIGH_EFFECT =
            Set.of("irreversible", "external_communication", "spend", "physical");
    /** The records the SPEC §10.4 human-actor rule applies to. */
    private static final Set<String> OVERSIGHT_TYPES = Set.of("ApprovalDecided", "Override", "Interrupt");
    /** The Incident members that record a response after detection (SPEC §6.2; ROB-07). */
    private static final List<String> INCIDENT_RESPONSES =
            List.of("causal_assessment_at", "provider_notified_at", "reported_at");

    public static GraphStore buildGraph(List<JsonNode> events) {
        return buildGraph(events, DomainBinding.empty(), new GraphStore(), Iri.ZERO_KEY);
    }

    public static GraphStore buildGraph(List<JsonNode> events, DomainBinding domain) {
        return buildGraph(events, domain, new GraphStore(), Iri.ZERO_KEY);
    }

    public static GraphStore buildGraph(
            List<JsonNode> events, DomainBinding domain, GraphStore store, byte[] key) {
        return buildGraph(events, domain, store, key, Set.of());
    }

    public static GraphStore buildGraph(
            List<JsonNode> events, DomainBinding domain, GraphStore store, byte[] key, Set<String> foreignUntrusted) {
        return new Builder(store, domain, key, foreignUntrusted, List.of()).build(events);
    }

    /** {@code foreignUntrusted} names the records another subject's write marked untrusted or rewrote with no ruling
     * ({@link #flaggedRecords}); {@code foreignUnreadable} gives the record_ref and store of each unreadable MemoryWrite another subject holds
     * (18.172). */
    public static GraphStore buildGraph(
            List<JsonNode> events,
            DomainBinding domain,
            Set<String> foreignUntrusted,
            List<Ingest.UnreadableWrite> foreignUnreadable) {
        return new Builder(new GraphStore(), domain, Iri.ZERO_KEY, foreignUntrusted, foreignUnreadable).build(events);
    }

    /** A MemoryWrite that marks its record untrusted or writes with no guard ruling (anything but an
     * enforcement-point write with a trust or a verdict), with its subject and data. */
    private record FlaggedWrite(String subject, JsonNode data) {}

    private static List<FlaggedWrite> flaggedWrites(Iterable<JsonNode> events) {
        List<FlaggedWrite> writes = new ArrayList<>();
        for (JsonNode event : events) {
            JsonNode data = dataOf(event);
            if ("MemoryWrite".equals(str(data.get("@type"))) && (flagsUntrusted(data) || !ruledWrite(event, data))) {
                writes.add(new FlaggedWrite(Objects.toString(str(event.get("subject")), ""), data));
            }
        }
        return writes;
    }

    /** Each record a MemoryWrite in {@code events} marks untrusted or writes with no guard ruling, with the subjects
     * whose records hold such a write. A subject's graph reads a record another subject flagged or rewrote unruled as
     * one its guard never ruled on (18.120). */
    public static Map<String, Set<String>> flaggedRecords(Iterable<JsonNode> events) {
        Map<String, Set<String>> flagged = new LinkedHashMap<>();
        for (FlaggedWrite write : flaggedWrites(events)) {
            String record = text(write.data().get("record_ref"));
            if (record != null) {
                flagged.computeIfAbsent(record, r -> new LinkedHashSet<>()).add(write.subject());
            }
        }
        return flagged;
    }

    /** Each such write whose {@code record_ref} cannot be read (absent, null or empty), under any subject, as its
     * store: it counts against every record of its store, or every record, as an unreadable write does (SPEC §7.4,
     * 18.202). */
    public static List<Ingest.UnreadableWrite> unkeyedFlags(Iterable<JsonNode> events) {
        List<Ingest.UnreadableWrite> unkeyed = new ArrayList<>();
        for (FlaggedWrite write : flaggedWrites(events)) {
            if (text(write.data().get("record_ref")) == null) {
                unkeyed.add(new Ingest.UnreadableWrite(null, text(write.data().get("store"))));
            }
        }
        return unkeyed;
    }

    // --- helpers ---

    /** A write a guard ruled on: an enforcement-point MemoryWrite with a trust or a verdict (18.120). */
    private static boolean ruledWrite(JsonNode event, JsonNode data) {
        return "enforcement_point".equals(str(event.get("agentcesourceclass")))
                && (!isAbsent(data.get("trust")) || !isAbsent(data.get("guard_verdict")));
    }

    static JsonNode dataOf(JsonNode event) {
        JsonNode data = event.get("data");
        return data != null && data.isObject() ? data : Json.nodes().objectNode();
    }

    static JsonNode refsOf(JsonNode event) {
        JsonNode refs = dataOf(event).get("refs");
        return refs != null && refs.isObject() ? refs : Json.nodes().objectNode();
    }

    /** The names an event's {@code refs.*} and {@code used[]} carry: what danglingRef and linkedRecordsResolve both read. */
    private static List<String> refCandidates(JsonNode event) {
        List<String> names = new ArrayList<>();
        refsOf(event).elements().forEachRemaining(v -> {
            if (v.isTextual()) {
                names.add(v.textValue());
            }
        });
        JsonNode used = dataOf(event).get("used");
        if (used != null && used.isArray()) {
            for (JsonNode v : used) {
                if (v.isTextual()) {
                    names.add(v.textValue());
                }
            }
        }
        return names;
    }

    /** An Appendix F source class outside the trusted set (CND-05's rule, also ROB-02's). */
    private static boolean untrustedSourceClass(String sourceClass) {
        return sourceClass != null && !TRUSTED_INSTRUCTION.contains(sourceClass);
    }

    /** A MemoryWrite that marks its record untrusted: trust untrusted or quarantined, a quarantine or block verdict, or
     * no trust and no verdict but an untrusted Appendix F {@code provenance_origin_class} (SPEC §7.4, ROB-02). */
    private static boolean flagsUntrusted(JsonNode data) {
        return isOneOf(data.get("trust"), UNTRUSTED_TRUST)
                || isOneOf(data.get("guard_verdict"), UNTRUSTED_VERDICTS)
                || (isAbsent(data.get("trust"))
                        && isAbsent(data.get("guard_verdict"))
                        && untrustedSourceClass(str(data.get("provenance_origin_class"))));
    }

    private static boolean isOneOf(JsonNode node, Set<String> values) {
        String value = str(node);
        return value != null && values.contains(value);
    }

    /** A field the record leaves out or sets to null (Python's None). */
    private static boolean isAbsent(JsonNode node) {
        return node == null || node.isNull();
    }

    /** Python's {@code ==} over two JSON values: absent and null are both None, numbers compare by value. */
    private static boolean pyEquals(JsonNode a, JsonNode b) {
        if (isAbsent(a) || isAbsent(b)) {
            return isAbsent(a) && isAbsent(b);
        }
        if (a.isNumber() && b.isNumber()) {
            return a.decimalValue().compareTo(b.decimalValue()) == 0;
        }
        return a.equals(b);
    }

    /** A principal's id as CND-04 reads it: Python's strip of a non-empty string id, else empty. */
    private static String principalKey(JsonNode principal) {
        String id = principal != null && principal.isObject() ? text(principal.get("id")) : null;
        return id == null ? "" : Conformance.pyStrip(id);
    }

    /** What each task's records show it consumed (SPEC §7.7.3 budget row); the Python engine's
     * {@code _TaskUsage} documents the reading. */
    static final class TaskUsage {
        final Map<String, Long> tokens = new LinkedHashMap<>();
        final Set<String> tokensUnread = new LinkedHashSet<>();
        final Set<String> spendTasks = new LinkedHashSet<>();
        final Set<String> spendUnknown = new LinkedHashSet<>();

        void add(String ptype, JsonNode data, Set<String> tasks) {
            if ("ToolCall".equals(ptype)) {
                JsonNode effect = data.get("effect_class");
                if (effect != null && effect.isTextual() && "spend".equals(effect.asText())) {
                    spendTasks.addAll(tasks);
                } else if (effect == null || !effect.isTextual()) {
                    spendUnknown.addAll(tasks);
                }
                return;
            }
            spendUnknown.addAll(tasks);
            JsonNode usage = data.get("usage");
            boolean object = usage != null && usage.isObject();
            Long in = object ? DomainBinding.wholeNumber(usage.get("input_tokens")) : null;
            Long out = object ? DomainBinding.wholeNumber(usage.get("output_tokens")) : null;
            for (String task : tasks) {
                if (in != null && out != null) {
                    tokens.merge(task, in + out, (a, b) -> Math.min(a + b, DomainBinding.TOKEN_CAP));
                } else {
                    tokensUnread.add(task);
                }
            }
        }

        /** TRUE within the limit, FALSE over it, null when the records cannot tell. */
        Boolean verdict(String task, String dimension, Double limit) {
            if ("tokens".equals(dimension)) {
                if (limit == null) {
                    return null;
                }
                if (tokens.getOrDefault(task, 0L) > limit) {
                    return false;
                }
                // no readable usage for the task, or a model call without one: the records cannot tell
                return tokensUnread.contains(task) || !tokens.containsKey(task) ? null : Boolean.TRUE;
            }
            if ("spend".equals(dimension)) {
                if (limit == null) {
                    return null;
                }
                if (spendTasks.contains(task)) {
                    return limit == 0 ? Boolean.FALSE : null;
                }
                return spendUnknown.contains(task) ? null : Boolean.TRUE;
            }
            return null;
        }
    }

    static String str(JsonNode node) {
        return node != null && node.isTextual() ? node.textValue() : null;
    }

    private static String timeOf(JsonNode event) {
        return Objects.toString(str(event.get("time")), "");
    }

    /** Whether {@code time} is at or before every action time, compared as instants (SPEC §6.7); a time that cannot be
     * compared with an action's gates nothing. */
    private static boolean beforeEvery(String time, List<String> actions) {
        return actions.stream().allMatch(action -> {
            Integer order = Structural.compareLiterals(time, action);
            return order != null && order <= 0;
        });
    }

    /** Whether {@code time} is after every action time, compared as instants (SPEC §6.7): a decision recorded after
     * the action already ran cannot withdraw the approval it ran under (18.142). Never true when no action ran. */
    private static boolean afterEvery(String time, List<String> actions) {
        return !actions.isEmpty() && actions.stream().allMatch(action -> {
            Integer order = Structural.compareLiterals(time, action);
            return order != null && order > 0;
        });
    }

    /** One human's decision on a decision: the ApprovalDecided node, its time and whether its outcome is approve. */
    private record Decided(String node, String time, boolean approve) {}

    // DOC-01 (SPEC §7.4, Art. 11 / Annex IV): the events that declare an operating component (a
    // BundleLoaded manifest) or exercise one (a ToolCall or ModelCall) are typed with this
    // engine-materialised class, so one shape can compare the declaration with what operated.
    private static final String COMPONENT_RECORD = "agentce:ComponentRecord";
    private static final Set<String> COMPONENT_RECORD_TYPES = Set.of("BundleLoaded", "ToolCall", "ModelCall");

    /** The SPEC Digest form (§6.2.3). */
    private static final java.util.regex.Pattern DIGEST = java.util.regex.Pattern.compile("sha256:[0-9a-f]{64}");

    /** The held PolicyDecisions, Notices and Disclosures a decision's record literals read (18.37f). */
    private static final class Gates {
        final Map<String, JsonNode> policies = new LinkedHashMap<>();
        // decision IRI -> the held PolicyDecisions whose refs.request names it, by graph IRI only (SPEC §6.3).
        final Map<String, List<String>> requested = new HashMap<>();
        final Map<String, JsonNode> notices = new HashMap<>();
        final List<JsonNode> disclosures = new ArrayList<>();

        Gates(List<JsonNode> events, java.util.function.Function<JsonNode, String> ptype) {
            for (JsonNode event : events) {
                String node = Iri.eventIri(event.get("id").asText());
                String kind = ptype.apply(event);
                if ("PolicyDecision".equals(kind)) {
                    policies.put(node, dataOf(event));
                    String request = str(refsOf(event).get("request"));
                    if (request != null) {
                        requested.computeIfAbsent(request, k -> new ArrayList<>()).add(node);
                    }
                } else if ("Notice".equals(kind)) {
                    notices.put(node, dataOf(event));
                } else if ("Disclosure".equals(kind)) {
                    disclosures.add(dataOf(event));
                }
            }
        }

        /** The held PolicyDecisions that gate a decision: the one its {@code refs.authorization} names and every one
         * whose {@code refs.request} names it. Null when there is none, or when {@code refs.authorization} is present
         * and is not the graph IRI of a held PolicyDecision (a bare id, an unheld name, another type: approvals ruling
         * Q1 B). */
        List<JsonNode> linked(String node, JsonNode data) {
            JsonNode refs = data.get("refs");
            Set<String> names = new LinkedHashSet<>(requested.getOrDefault(node, List.of()));
            if (refs != null && refs.isObject() && refs.has("authorization")) {
                String authorization = str(refs.get("authorization"));
                if (authorization == null || !policies.containsKey(authorization)) {
                    return null;
                }
                names.add(authorization);
            }
            List<JsonNode> linked = new ArrayList<>();
            for (String name : names) {
                linked.add(policies.get(name));
            }
            return linked.isEmpty() ? null : linked;
        }
    }

    /** A PolicyDecision names the exact policy it applied: a policy id, a version and a SPEC Digest (REC-05). */
    private static boolean namesPolicy(JsonNode pd) {
        String digest = str(pd.get("policy_digest"));
        return text(pd.get("policy_id")) != null
                && text(pd.get("policy_version")) != null
                && digest != null
                && DIGEST.matcher(digest).matches();
    }

    /** DOC-02: the decision records what it chose and, when it lists its options, one of them (SPEC §6.2.3). */
    private static boolean documented(JsonNode data) {
        String chosen = text(data.get("chosen"));
        JsonNode options = data.get("options");
        if (chosen == null) {
            return false;
        }
        if (options == null || !options.isArray() || options.isEmpty()) {
            return true;
        }
        for (JsonNode option : options) {
            if (option.isObject() && chosen.equals(str(option.get("id")))) {
                return true;
            }
        }
        return false;
    }

    /** A non-empty string, else null. */
    static String text(JsonNode node) {
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

    /** The characters the human-actor rule trims from each end of a principal id: Unicode White_Space plus U+FEFF,
     * written out because Python's strip, JavaScript's trim and Java's strip each trim a different set (SPEC §10.4). */
    private static final String ID_PAD =
            "\t\n\u000b\u000c\r \u0085\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a"
                    + "\u2028\u2029\u202f\u205f\u3000\ufeff";

    /** A principal id as the human-actor rule compares it: trimmed of {@link #ID_PAD} at both ends, null when nothing
     * is left, so a padded spelling of an id is the same principal everywhere in the rule. */
    private static String ruleId(String value) {
        if (value == null) {
            return null;
        }
        int start = 0;
        int end = value.length();
        while (start < end && ID_PAD.indexOf(value.charAt(start)) >= 0) {
            start++;
        }
        while (end > start && ID_PAD.indexOf(value.charAt(end - 1)) >= 0) {
            end--;
        }
        return end > start ? value.substring(start, end) : null;
    }

    /** The trimmed id of a human principal (SPEC §6.2 {@code actor}, SessionStart {@code principal}; §10.4
     * human-actor rule); null for a missing, unnamed or non-human principal. */
    private static String humanId(JsonNode principal) {
        if (principal != null && principal.isObject() && "human".equals(str(principal.get("kind")))) {
            return ruleId(str(principal.get("id")));
        }
        return null;
    }

    /** What the human-actor rule reads of every event: its type, source class and agent id, and the IRI of the human
     * principal it names, else null, read from any type (SessionStart, PolicyDecision and Instruction carry one) so
     * only the type check keeps a login a session (18.140). */
    private record EventInfo(String type, String sourceClass, String agent, String loginPrincipal) {}

    /** JSON value equality as the reference's {@code ==} reads it: numbers (and booleans, as 1/0)
     * compare by value, so {@code 1} equals {@code 1.0}; an absent field equals null. */
    private static boolean jsonEquals(JsonNode a, JsonNode b) {
        JsonNode left = a == null ? Json.nodes().nullNode() : a;
        JsonNode right = b == null ? Json.nodes().nullNode() : b;
        return left.equals((x, y) -> {
            java.math.BigDecimal nx = numeric(x);
            java.math.BigDecimal ny = numeric(y);
            if (nx != null && ny != null) {
                return nx.compareTo(ny);
            }
            return x.equals(y) ? 0 : 1;
        }, right);
    }

    private static java.math.BigDecimal numeric(JsonNode node) {
        if (node.isBoolean()) {
            return node.booleanValue() ? java.math.BigDecimal.ONE : java.math.BigDecimal.ZERO;
        }
        return node.isNumber() ? node.decimalValue() : null;
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
        // Instructions that declare no source class: ROB-02 taint seeds of their own (18.127).
        private final Set<String> instructionUnclassed = new LinkedHashSet<>();
        // DOC-01: family -> declared name -> the union of its declared pins over every BundleLoaded manifest in the
        // subject's records (empty: any version matches), so event order does not matter.
        private final Map<String, Map<String, Set<String>>> declared =
                Map.of("tool", new LinkedHashMap<>(), "model", new LinkedHashMap<>());
        private final Map<String, Set<String>> operatedNames =
                Map.of("tool", new LinkedHashSet<>(), "model", new LinkedHashSet<>());
        private boolean hasCalls;
        // SPEC §10.4 human-actor rule, read in the second pass so file order never matters: event IRI -> type, source
        // class and agent id of every event; agent id -> every principal IRI in its delegation chain (its acted_for
        // and its DelegationIssued chains); event IRI -> the principal IRIs of its own acted_for.
        private final Map<String, EventInfo> eventInfo = new LinkedHashMap<>();
        private final Map<String, Set<String>> agentChain = new LinkedHashMap<>();
        // event IRI -> the agents it concerns: its own agent and its CloudEvents subject (the assessed agent), so an
        // event that leaves out the optional agent still concerns the subject.
        private final Map<String, Set<String>> eventAgents = new LinkedHashMap<>();
        private final Map<String, Set<String>> actedFor = new LinkedHashMap<>();
        // agent id -> how many held events concern it, built on first use by an oversight record whose refs.decision
        // names no held event (18.158).
        private Map<String, Integer> agentEvents;
        // OVS-08: decision IRI -> the actor IRI of every verified ApprovalDecided whose outcome is approve.
        private final Map<String, Set<String>> humanApprovers = new LinkedHashMap<>();
        // INC-03: event IRI -> the decision its refs.decision names, whatever the event type.
        private final Map<String, String> refDecision = new LinkedHashMap<>();

        // Records another subject's MemoryWrite marked untrusted or rewrote with no ruling: never ruled on in this
        // subject's graph (18.120).
        private final Set<String> foreignUntrusted;
        // The record_ref and store of each MemoryWrite another subject holds that ingest could not read (18.172).
        private final List<Ingest.UnreadableWrite> foreignUnreadable;

        Builder(
                GraphStore store,
                DomainBinding domain,
                byte[] key,
                Set<String> foreignUntrusted,
                List<Ingest.UnreadableWrite> foreignUnreadable) {
            this.store = store;
            this.domain = domain;
            this.key = key;
            this.foreignUntrusted = foreignUntrusted;
            this.foreignUnreadable = foreignUnreadable;
        }

        GraphStore build(List<JsonNode> events) {
            Set<String> usedClasses = new LinkedHashSet<>(BASE_SUBCLASS.keySet());
            usedClasses.add("prov:Agent");
            usedClasses.add("prov:Activity");
            usedClasses.add("prov:Entity");
            usedClasses.add(GATED_ACTION);
            usedClasses.add(CONSEQUENTIAL_ACTION);
            usedClasses.add(TASK_MODEL_CALL);
            usedClasses.add(BUDGET_UNJUDGED);
            for (JsonNode event : events) {
                String node = Iri.eventIri(event.get("id").asText());
                eventIris.add(node);
                // The held decisions, known before the first pass so a refs.decision read before its Decision still
                // resolves to it (18.156).
                if ("Decision".equals(ptype(event))) {
                    String time = str(event.get("time"));
                    decisionTime.put(node, time != null ? time : "");
                }
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
            String agentId = agent != null && agent.isObject() && agent.get("id") != null && agent.get("id").isTextual()
                    ? agent.get("id").textValue()
                    : null;
            String loginPrincipal = humanId(data.get("principal"));
            eventInfo.put(node, new EventInfo(
                    ptype, sourceClass, agentId, loginPrincipal == null ? null : Iri.principalIri(loginPrincipal, key)));
            Set<String> ownActedFor = principalIris(data.get("acted_for"));
            actedFor.put(node, ownActedFor);
            Set<String> owners = new LinkedHashSet<>();
            Stream.of(agentId, str(event.get("subject"))).filter(Objects::nonNull).forEach(owners::add);
            eventAgents.put(node, owners);
            for (String owner : owners) {
                Set<String> chain = agentChain.computeIfAbsent(owner, k -> new LinkedHashSet<>());
                chain.addAll(ownActedFor);
                if ("DelegationIssued".equals(ptype)) {
                    chain.addAll(principalIris(data.get("chain")));
                }
            }
            if (agentId != null) {
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

        /** The pseudonymised IRIs of an {@code acted_for} or {@code chain} list (SPEC §6.3: the delegation chain). */
        private Set<String> principalIris(JsonNode principals) {
            Set<String> out = new LinkedHashSet<>();
            if (principals != null && principals.isArray()) {
                for (JsonNode entry : principals) {
                    String pid = ruleId(principalRef(entry)[0]);
                    if (pid != null) {
                        out.add(Iri.principalIri(pid, key));
                    }
                }
            }
            return out;
        }

        /** SPEC §10.4 human-actor rule: the actor IRI of an ApprovalDecided, Override or Interrupt whose actor is a
         * human principal whose {@code session_ref} names a held identity-provider login record of that same human, and
         * who is nowhere in the delegation chain of the activity; null when any part fails (fail closed).
         *
         * <p>The login record is a held {@code SessionStart} (the only session record the event model has) from an
         * independent_system or enforcement_point stream whose {@code principal} is the actor (ids trimmed of
         * {@link #ID_PAD}) and whose agent is none of the agents the oversight record concerns: the own agent and
         * CloudEvents subject of the record and of the named decision, whose own run session is no human's login. The
         * delegation chain is the named decision's and the record's own {@code acted_for} and every chain of those
         * agents. */
        private String verifiedActor(String node, JsonNode event) {
            JsonNode data = dataOf(event);
            String actor = humanId(data.get("actor"));
            String login = str(data.get("session_ref"));
            EventInfo loginInfo = login != null ? eventInfo.get(login) : null;
            if (actor == null || loginInfo == null) {
                return null;
            }
            // Every name the ref may mean an event by, the held decision among them: a name that leads to another
            // held event, or to none, still counts that event's agents, so resolving never loosens the rule (fail closed).
            JsonNode ref = refsOf(event).get("decision");
            List<String> named = refNames(ref);
            // A present refs.decision that names no held event (null or empty included) may mean any of them, so the
            // record concerns every held event but its own login (18.158).
            boolean unresolved = ref != null && named.stream().noneMatch(eventInfo::containsKey);
            Set<String> concerned = new LinkedHashSet<>(eventAgents.get(node));
            for (String name : named) {
                concerned.addAll(eventAgents.getOrDefault(name, Set.of()));
            }
            if (!"SessionStart".equals(loginInfo.type())
                    || loginInfo.sourceClass() == null
                    || !LOGIN_CLASSES.contains(loginInfo.sourceClass())) {
                return null;
            }
            if (loginInfo.agent() == null || concerned.contains(loginInfo.agent())) {
                return null;
            }
            // The login itself is one event concerning its agent; any other makes it that agent's session (fail closed).
            if (unresolved && agentEventCounts().getOrDefault(loginInfo.agent(), 0) > 1) {
                return null;
            }
            String actorIri = Iri.principalIri(actor, key);
            if (!actorIri.equals(loginInfo.loginPrincipal())) {
                return null;
            }
            Set<String> chain = new LinkedHashSet<>(actedFor.get(node));
            for (String name : named) {
                chain.addAll(actedFor.getOrDefault(name, Set.of()));
            }
            for (String agentId : concerned) {
                chain.addAll(agentChain.getOrDefault(agentId, Set.of()));
            }
            return chain.contains(actorIri) ? null : actorIri;
        }

        /** How many held events concern each agent (their own agent or CloudEvents subject; 18.158). */
        private Map<String, Integer> agentEventCounts() {
            if (agentEvents == null) {
                agentEvents = new LinkedHashMap<>();
                for (Set<String> agents : eventAgents.values()) {
                    for (String agent : agents) {
                        agentEvents.merge(agent, 1, Integer::sum);
                    }
                }
            }
            return agentEvents;
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

        /** The SPEC §6.3 glue edges of a refs.decision, read through {@link #heldDecision} as approvedBy reads it; a
         * ref naming no held decision keeps its text (18.156). */
        private void mapDecisionLinks(String node, String ptype, JsonNode event) {
            JsonNode refs = refsOf(event);
            String ref = str(refs.get("decision"));
            if (ref != null) {
                String decision = Objects.requireNonNullElse(heldDecision(refs.get("decision")), ref);
                refDecision.put(node, decision);
                switch (ptype) {
                    case "ToolCall" -> store.addEdge(node, "agentce:executes", decision);
                    case "Outcome" -> {
                        store.addEdge(decision, "agentce:resultedIn", node);
                        outcomeDecision.put(node, decision);
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
            if (sourceClass == null) {
                // No class at all (or not a string): ROB-02 reads it as untrusted content (18.127).
                instructionUnclassed.add(node);
            }
            store.addLiteral(node, "agentce:instructionUntrusted", untrusted ? "true" : "false", BOOL);
        }

        /** Whether a ToolCall stays within the declared effect scope of every task it names, by
         * {@code refs.task} or the {@code agentcetask} attribute (SPEC §7.7.4 CND-01, §7.7.6): each
         * scope the domain binding holds the task to ({@link DomainBinding#scopesFor}) allows its tool
         * and its effect class. A task held to no scope, or a call with no effect class, is out of
         * scope. */
        private boolean withinTaskScope(JsonNode event) {
            JsonNode data = dataOf(event);
            JsonNode tool = data.get("tool");
            String name = tool != null && tool.isObject() ? str(tool.get("name")) : null;
            String effectClass = str(data.get("effect_class"));
            for (String task : actionTasks(event)) {
                List<DomainBinding.TaskScope> scopes = domain.scopesFor(task);
                if (scopes.isEmpty() || !scopes.stream().allMatch(s -> s.allows(name, effectClass))) {
                    return false;
                }
            }
            return true;
        }

        /** The tasks an action names, by {@code refs.task} or the {@code agentcetask} attribute. */
        private Set<String> actionTasks(JsonNode event) {
            Set<String> tasks = new LinkedHashSet<>();
            for (JsonNode task : new JsonNode[] {refsOf(event).get("task"), event.get("agentcetask")}) {
                if (task != null && task.isTextual()) {
                    tasks.add(task.asText());
                }
            }
            return tasks;
        }

        /** Materialise CND-02's population and gate (SPEC §7.7.4, rulings Q5 to Q10); the Python engine's
         * {@code _conduct_gate} documents the reading. An action whose effect class a scope of its task marks
         * {@code requires_approval}, or whose class is high-effect, is a {@code GatedAction}; a high-effect action
         * in no task, or in a task held to no scope, is held to the approval path. The approval path needs an
         * enforcement-point PolicyDecision naming it that allows it or asks for approval and an approval of the
         * held decision it executes; any other high-effect action needs an enforcement-point allow, or a
         * require_approval such an approval released. A deny naming it closes the gate. A ToolCall with no
         * effect class, and a consequential decision no ToolCall executes, are unjudged. */
        private void conductGate(List<JsonNode> events) {
            Map<String, JsonNode> policies = new LinkedHashMap<>();
            for (JsonNode e : events) {
                if ("PolicyDecision".equals(ptype(e))) {
                    policies.put(Iri.eventIri(e.get("id").asText()), e);
                }
            }
            Map<String, List<JsonNode>> named = new LinkedHashMap<>();
            for (JsonNode policy : policies.values()) {
                JsonNode request = refsOf(policy).get("request");
                if (request != null && request.isTextual()) {
                    named.computeIfAbsent(request.asText(), k -> new ArrayList<>()).add(policy);
                }
            }
            Set<String> executed = new LinkedHashSet<>();
            for (JsonNode event : events) {
                String ptype = ptype(event);
                if (!"ToolCall".equals(ptype) && !"ResourceAccess".equals(ptype)) {
                    continue;
                }
                String node = Iri.eventIri(event.get("id").asText());
                JsonNode refs = refsOf(event);
                String decision = heldDecision(refs.get("decision"));
                if ("ToolCall".equals(ptype) && decision != null) {
                    executed.add(decision);
                }
                JsonNode effectNode = dataOf(event).get("effect_class");
                if (effectNode == null || !effectNode.isTextual()) {
                    if ("ToolCall".equals(ptype)) {
                        gate(node, true, false);
                    }
                    continue;
                }
                String effect = effectNode.asText();
                List<List<DomainBinding.TaskScope>> scopes = new ArrayList<>();
                for (String task : actionTasks(event)) {
                    scopes.add(domain.scopesFor(task));
                }
                boolean approval = scopes.stream()
                        .anyMatch(ss -> ss.stream().anyMatch(sc -> sc.requiresApproval().contains(effect)));
                boolean high = HIGH_EFFECT.contains(effect);
                if (high && (scopes.isEmpty() || scopes.stream().anyMatch(List::isEmpty))) {
                    approval = true;
                }
                if (!approval && !high) {
                    continue;
                }
                List<JsonNode> gating = new ArrayList<>(named.getOrDefault(node, List.of()));
                JsonNode authorization = refs.get("authorization");
                if (authorization != null && authorization.isTextual() && policies.containsKey(authorization.asText())) {
                    gating.add(policies.get(authorization.asText()));
                }
                Set<String> rulings = new LinkedHashSet<>();
                boolean denied = false;
                for (JsonNode p : gating) {
                    JsonNode decided = dataOf(p).get("decision");
                    String ruling = decided != null && decided.isTextual() ? decided.asText() : null;
                    JsonNode sourceClass = p.get("agentcesourceclass");
                    if (sourceClass != null && sourceClass.isTextual() && LOGIN_CLASSES.contains(sourceClass.asText())) {
                        rulings.add(ruling);
                    }
                    denied |= "deny".equals(ruling);
                }
                List<String> approvals = decision != null ? store.objects(decision, "agentce:approvedBy") : List.of();
                boolean released = approval
                        ? rulings.contains("allow") || rulings.contains("require_approval")
                        : !rulings.contains("allow") && rulings.contains("require_approval");
                if (released || approval) {
                    for (String approved : approvals) {
                        store.addEdge(node, "agentce:releasedBy", approved);
                    }
                }
                boolean met = !denied
                        && ((rulings.contains("allow") && !approval) || (released && !approvals.isEmpty()));
                gate(node, met, true);
                if (!met && decision != null && store.humanActorFailed.contains(decision)) {
                    // An approval that fails SPEC §10.4 left the gate shut: the deviation register cannot waive it.
                    store.humanActorFailed.add(node);
                }
            }
            List<String> decisions = new ArrayList<>(decisionTime.keySet());
            decisions.sort(Json::byteCompare);
            for (String decision : decisions) {
                if (!executed.contains(decision) && store.isA(decision, "agentce:ConsequentialDecision")) {
                    gate(decision, true, false);
                }
            }
        }

        /** Materialise CND-04's population and judgment (SPEC §7.7.4; rulings 9 to 14 of 2026-10-12); the Python
         * engine's {@code _conduct_provenance} documents the reading. A ToolCall that executes a held consequential
         * decision, and every consequential Decision, is a {@code ConsequentialAction}. It meets the control when its
         * instruction chain declares only authorised classes with a principal and no untrusted origin, its last
         * principal's role is authorised for the task, and no delegation it rests on is held unverified. */
        private void conductProvenance(List<JsonNode> events) {
            Map<String, JsonNode> byIri = new LinkedHashMap<>();
            for (JsonNode e : events) {
                byIri.put(Iri.eventIri(e.get("id").asText()), e);
            }
            List<String> nodes = new ArrayList<>(byIri.keySet());
            nodes.sort(Json::byteCompare);
            Map<String, List<JsonNode>> executing = new LinkedHashMap<>();
            List<String> foci = new ArrayList<>();
            for (String node : nodes) {
                JsonNode event = byIri.get(node);
                String ptype = ptype(event);
                if ("ToolCall".equals(ptype)) {
                    String decision = heldDecision(refsOf(event).get("decision"));
                    if (decision != null) {
                        executing.computeIfAbsent(decision, k -> new ArrayList<>()).add(event);
                        if (domain.consequential.contains(decisionType.getOrDefault(decision, ""))) {
                            foci.add(node);
                        }
                    }
                } else if ("Decision".equals(ptype) && store.isA(node, "agentce:ConsequentialDecision")) {
                    foci.add(node);
                }
            }
            for (String node : foci) {
                JsonNode event = byIri.get(node);
                Chain chain = instructionChain(byIri, refsOf(event).get("instruction"));
                boolean met = chain.met();
                boolean judged = chain.judged();
                List<JsonNode> calls = "Decision".equals(ptype(event))
                        ? executing.getOrDefault(node, List.of())
                        : List.of(event);
                if (chain.terminus() != null) {
                    boolean[] role = roleAuthorised(byIri, node, event, calls, chain.terminus());
                    met = met && role[0];
                    judged = judged && role[1];
                }
                List<JsonNode> delegations = new ArrayList<>();
                for (JsonNode call : calls) {
                    JsonNode ref = refsOf(call).get("delegation");
                    if (!isAbsent(ref)) {
                        delegations.add(ref);
                    }
                }
                for (JsonNode ref : delegations) {
                    JsonNode held = ref.isTextual() ? byIri.get(ref.textValue()) : null;
                    if (held == null || !"DelegationIssued".equals(ptype(held))) {
                        judged = false;
                    } else if (!store.literalValues(ref.textValue(), "agentce:chainVerified").contains("true")) {
                        met = false;
                    }
                }
                if (delegations.isEmpty()) {
                    judged = false;
                }
                // A definite failure beats a reading the records cannot finish (critic r2 F3).
                judged = judged || !met;
                store.addType(node, CONSEQUENTIAL_ACTION);
                literal(node, "agentce:provenanceMet", met);
                literal(node, "agentce:provenanceJudged", judged);
            }
        }

        /** CND-04's reading of an instruction chain: whether it meets and can be judged, and its last Instruction
         * (null when it has none to read: a parent cycle, an unheld parent, a non-string parent). */
        private record Chain(boolean met, boolean judged, JsonNode terminus) {}

        /** CND-04's instruction chain from {@code instruction} (see {@link #conductProvenance}); the Python engine's
         * {@code _instruction_chain} documents the reading. A parent cycle never terminates, so it is unjudged and
         * has no last Instruction. */
        private Chain instructionChain(Map<String, JsonNode> byIri, JsonNode instruction) {
            JsonNode start = instruction != null && instruction.isTextual() ? byIri.get(instruction.textValue()) : null;
            if (start == null || !"Instruction".equals(ptype(start))) {
                return new Chain(false, true, null);
            }
            boolean met = true;
            boolean judged = true;
            Set<String> seen = new HashSet<>();
            JsonNode node = instruction;
            JsonNode terminus = null;
            while (node != null && node.isTextual()) {
                String iri = node.textValue();
                if (!seen.add(iri)) {
                    judged = false;
                    terminus = null;
                    break;
                }
                JsonNode event = byIri.get(iri);
                if (event == null || !"Instruction".equals(ptype(event))) {
                    judged = false;
                    terminus = null;
                    break;
                }
                terminus = event;
                JsonNode data = dataOf(event);
                JsonNode refs = refsOf(event);
                if (!isOneOf(data.get("source_class"), AUTHORISED_INSTRUCTION)
                        || principalKey(data.get("principal")).isEmpty()) {
                    met = false;
                }
                JsonNode origin = refs.get("origin");
                if (!isAbsent(origin)) {
                    JsonNode held = origin.isTextual() ? byIri.get(origin.textValue()) : null;
                    String otype = held != null ? ptype(held) : null;
                    JsonNode trust = held != null && "MemoryRead".equals(otype) ? dataOf(held).get("trust_min") : null;
                    if ("ToolCall".equals(otype) || "ResourceAccess".equals(otype)
                            || ("MemoryRead".equals(otype) && isOneOf(trust, UNTRUSTED_TRUST))) {
                        met = false;
                    } else if (!"MemoryRead".equals(otype) || !"trusted".equals(str(trust))) {
                        judged = false;
                    }
                }
                node = refs.get("parent");
                if (!isAbsent(node) && !node.isTextual()) {
                    judged = false;
                    terminus = null;
                }
            }
            return new Chain(met, judged, terminus);
        }

/** CND-06's population and judgment (SPEC §7.7.4; rulings 16 A, 17 B, 18 B), as graph.py
         * {@code _conduct_refusals}: an Instruction needs a Refusal when its source_class is absent, not a string or
         * untrusted; one declaring an authorised class is in scope, unjudged, when the records do not show it (no
         * principal id where §6.2.3 requires one, or not attested for every held call or Decision acting on it, or for
         * itself when nothing acts on it). An enforcement-point (or better) deny needs a Refusal of the instruction its
         * request acts on; it is unjudged when that cannot be shown. */
        private void conductRefusals(List<JsonNode> events) {
            Map<String, JsonNode> byIri = new LinkedHashMap<>();
            for (JsonNode e : events) {
                byIri.put(Iri.eventIri(e.get("id").asText()), e);
            }
            Map<String, List<String>> acting = new LinkedHashMap<>();
            for (Map.Entry<String, JsonNode> entry : byIri.entrySet()) {
                JsonNode instruction = refsOf(entry.getValue()).get("instruction");
                String ptype = ptype(entry.getValue());
                if (("ToolCall".equals(ptype) || "Decision".equals(ptype)) && instruction != null
                        && instruction.isTextual()) {
                    acting.computeIfAbsent(instruction.asText(), k -> new ArrayList<>()).add(entry.getKey());
                }
            }
            List<String> nodes = new ArrayList<>(byIri.keySet());
            nodes.sort(Json::byteCompare);
            SourceClass.Judge judge = null;
            for (String node : nodes) {
                JsonNode event = byIri.get(node);
                String ptype = ptype(event);
                JsonNode data = dataOf(event);
                if ("Instruction".equals(ptype)) {
                    JsonNode sourceClass = data.get("source_class");
                    if (!isOneOf(sourceClass, TRUSTED_INSTRUCTION)) {
                        markRefusal(node, refused(node), true);
                        continue;
                    }
                    if (judge == null) {
                        judge = new SourceClass.Judge(store, byIri);
                    }
                    boolean shown = !isOneOf(sourceClass, AUTHORISED_INSTRUCTION)
                            || !principalKey(data.get("principal")).isEmpty();
                    for (String focus : acting.getOrDefault(node, List.of(node))) {
                        shown = shown && judge.attested(focus, node);
                    }
                    if (!shown) {
                        markRefusal(node, true, refused(node));
                    }
                } else if ("PolicyDecision".equals(ptype)
                        && isOneOf(event.get("agentcesourceclass"), AT_OR_ABOVE_EP)
                        && "deny".equals(str(data.get("decision")))) {
                    String instruction = deniedInstruction(byIri, refsOf(event));
                    if (instruction == null) {
                        markRefusal(node, true, false);
                    } else if (!instruction.isEmpty()) {
                        markRefusal(node, refused(instruction), true);
                    }
                }
            }
        }

        private boolean refused(String instruction) {
            return !store.objects(instruction, "agentce:refusedBy").isEmpty();
        }

        private void markRefusal(String node, boolean met, boolean shown) {
            literal(node, "agentce:refusalInScope", true);
            literal(node, "agentce:refusalMet", met);
            literal(node, "agentce:refusalNeedShown", shown);
        }

        /** The instruction a deny's request acts on: its refs.instruction, or that of the held Decision its
         * refs.decision names; "" when the request is an enforcement-point record naming none (outside the deny
         * clause); null when the records cannot show it (graph.py {@code _denied_instruction}). */
        private String deniedInstruction(Map<String, JsonNode> byIri, JsonNode refs) {
            JsonNode request = refs.get("request");
            JsonNode event = request != null && request.isTextual() ? byIri.get(request.asText()) : null;
            if (event == null) {
                return null;
            }
            JsonNode instruction = refsOf(event).get("instruction");
            if (instruction != null && instruction.isTextual()) {
                return instruction.asText();
            }
            JsonNode decision = refsOf(event).get("decision");
            if (!isAbsent(decision)) {
                String heldIri = heldDecision(decision);
                JsonNode held = heldIri == null ? null : byIri.get(heldIri);
                if (held == null) {
                    return null;
                }
                JsonNode named = refsOf(held).get("instruction");
                if (named != null && named.isTextual()) {
                    return named.asText();
                }
            }
            return isOneOf(event.get("agentcesourceclass"), AT_OR_ABOVE_EP) ? "" : null;
        }

        /** {met, judged}: whether the chain's last principal holds a role every scope of the action's task authorises
         * (SPEC §7.7.4 "for the task type", §7.7.6 {@code authorised_principal_roles}); the Python engine's
         * {@code _role_authorised} documents the reading. Below enforcement_point the role is the agent's word, so
         * each enforcement-point witness of the same principal must record the same role ({@link #witnessRoles}). */
        private boolean[] roleAuthorised(
                Map<String, JsonNode> byIri, String node, JsonNode event, List<JsonNode> calls, JsonNode terminus) {
            Set<String> tasks = actionTasks(event);
            if (tasks.isEmpty() && "Decision".equals(ptype(event))) {
                for (JsonNode call : calls) {
                    tasks.addAll(actionTasks(call));
                }
            }
            List<String> ordered = new ArrayList<>(tasks);
            ordered.sort(Json::byteCompare);
            List<List<DomainBinding.TaskScope>> lists = new ArrayList<>();
            for (String task : ordered) {
                lists.add(domain.scopesFor(task));
            }
            if (lists.isEmpty()) {
                lists.add(domain.scopesFor(""));
            }
            List<DomainBinding.TaskScope> scopes = new ArrayList<>();
            lists.forEach(scopes::addAll);
            if (lists.stream().anyMatch(List::isEmpty)
                    || scopes.stream().anyMatch(sc -> sc.authorisedPrincipalRoles() == null)) {
                return new boolean[] {true, false};
            }
            JsonNode principal = dataOf(terminus).get("principal");
            if (principalKey(principal).isEmpty()) {
                return new boolean[] {true, true}; // no principal already fails the chain (met false)
            }
            String declared = str(principal.get("role"));
            String role = declared == null ? "" : Conformance.pyStrip(declared);
            if (role.isEmpty()) {
                return new boolean[] {true, false};
            }
            boolean met = scopes.stream().allMatch(sc -> sc.authorisedPrincipalRoles().contains(role));
            boolean judged = true;
            if (!isOneOf(terminus.get("agentcesourceclass"), AT_OR_ABOVE_EP)) {
                for (String witnessed : witnessRoles(byIri, node, calls, terminus)) {
                    if (witnessed == null) {
                        judged = false;
                    } else if (!witnessed.equals(role)) {
                        met = false;
                    }
                }
            }
            return new boolean[] {met, judged};
        }

        /** The role (trimmed, null when absent or blank) each enforcement-point witness records for the terminus's
         * principal: a PolicyDecision allow whose refs.request is the action or a call executing it, and a twin
         * Instruction of the same content_ref, refs.task, source_class and principal id (SPEC §7.7.5). */
        private List<String> witnessRoles(
                Map<String, JsonNode> byIri, String node, List<JsonNode> calls, JsonNode terminus) {
            JsonNode data = dataOf(terminus);
            JsonNode refs = refsOf(terminus);
            String pid = principalKey(data.get("principal"));
            Set<String> requests = new HashSet<>();
            requests.add(node);
            for (JsonNode call : calls) {
                requests.add(Iri.eventIri(call.get("id").asText()));
            }
            List<String> roles = new ArrayList<>();
            for (JsonNode other : byIri.values()) {
                if (other == terminus || !"enforcement_point".equals(str(other.get("agentcesourceclass")))) {
                    continue;
                }
                JsonNode odata = dataOf(other);
                JsonNode orefs = refsOf(other);
                JsonNode witness = odata.get("principal");
                if (witness == null || !witness.isObject() || !principalKey(witness).equals(pid)) {
                    continue;
                }
                String ptype = ptype(other);
                String request = str(orefs.get("request"));
                boolean allow = "PolicyDecision".equals(ptype)
                        && "allow".equals(str(odata.get("decision")))
                        && request != null
                        && requests.contains(request);
                boolean twin = "Instruction".equals(ptype)
                        && !isAbsent(data.get("content_ref"))
                        && !isAbsent(refs.get("task"))
                        && pyEquals(odata.get("content_ref"), data.get("content_ref"))
                        && pyEquals(orefs.get("task"), refs.get("task"))
                        && pyEquals(odata.get("source_class"), data.get("source_class"));
                if (!allow && !twin) {
                    continue;
                }
                String role = str(witness.get("role"));
                String trimmed = role == null ? "" : Conformance.pyStrip(role);
                roles.add(trimmed.isEmpty() ? null : trimmed);
            }
            return roles;
        }

        private void gate(String node, boolean met, boolean judged) {
            store.addType(node, GATED_ACTION);
            literal(node, "agentce:gateMet", met);
            literal(node, "agentce:gateJudged", judged);
        }

        /** Compute the Conduct within-scope and within-budget flags (SPEC §7.7, CND-01/CND-07): an
         * action is out of scope when a {@code PolicyDecision} denies its request, or when it is a
         * ToolCall in a task whose declared effect scope does not allow its tool and effect class
         * ({@code withinTaskScope}); it is over budget when a {@code Refusal} with reason class
         * {@code budget_exceeded} names it, or when its task's recorded usage exceeds a budget a scope
         * of the task declares ({@code taskBudget}). An action in no task and never denied reads within
         * scope. */
        private void conductScopeBudget(List<JsonNode> events) {
            Set<String> denied = new LinkedHashSet<>();
            Set<String> overBudget = new LinkedHashSet<>();
            TaskUsage usage = new TaskUsage();
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
                if ("ModelCall".equals(ptype) || "ToolCall".equals(ptype)) {
                    usage.add(ptype, data, actionTasks(event));
                }
            }
            for (JsonNode event : events) {
                String ptype = ptype(event);
                if (!"ToolCall".equals(ptype) && !"ResourceAccess".equals(ptype) && !"ModelCall".equals(ptype)) {
                    continue;
                }
                String node = Iri.eventIri(event.get("id").asText());
                Set<String> tasks = actionTasks(event);
                if ("ModelCall".equals(ptype)) {
                    if (tasks.isEmpty()) {
                        continue;
                    }
                    // A model call in a task is consumption CND-07 judges (SPEC §7.7.3 ModelCall.usage).
                    store.addType(node, TASK_MODEL_CALL);
                } else {
                    boolean inScope =
                            !denied.contains(node) && (!"ToolCall".equals(ptype) || withinTaskScope(event));
                    literal(node, "agentce:withinScope", inScope);
                }
                boolean[] budget = taskBudget(tasks, usage);
                literal(node, "agentce:withinBudget", budget[0] && !overBudget.contains(node));
                if (!budget[1]) {
                    store.addType(node, BUDGET_UNJUDGED);
                    literal(node, "agentce:budgetJudged", false);
                }
            }
        }

        /** Whether an action's tasks stayed within every budget their scopes declare (SPEC §7.7.4 CND-07,
         * §7.7.6 budgets), and whether the records can tell, as {within, judged}; the Python engine's
         * {@code _task_budget} documents the reading. */
        private boolean[] taskBudget(Set<String> tasks, TaskUsage usage) {
            boolean within = true;
            boolean judged = true;
            for (String task : tasks) {
                List<DomainBinding.TaskScope> scopes = domain.scopesFor(task);
                if (scopes.isEmpty()) {
                    judged = judged && domain.taskScopes.isEmpty() && !domain.taskScopesMalformed;
                    continue;
                }
                for (DomainBinding.TaskScope scope : scopes) {
                    for (DomainBinding.Budget budget : scope.budgets()) {
                        Boolean verdict = usage.verdict(task, budget.dimension(), budget.limit());
                        within = within && !Boolean.FALSE.equals(verdict);
                        judged = judged && verdict != null;
                    }
                }
            }
            return new boolean[] {within, judged};
        }

        /** Flag every ToolCall/Decision that acts on an instruction whose chain passes through an
         * untrusted source class (SPEC §7.7.4, CND-05). The chain is the instruction's {@code refs.parent}
         * and {@code refs.origin} lineage ({@code agentce:derivedFrom}, SPEC §6.3) for as many hops as the
         * records show. It passes through an untrusted source class at an instruction of an untrusted
         * Appendix F class, at a tool call or resource access (the content they produce is
         * {@code tool_output} or {@code retrieved}), at a parent or origin the bundle does not hold, and at
         * anything ROB-02's closures reached ({@code tainted}: an untrusted memory read or write, content
         * the memory guard never ruled on, or content that used either). An instruction whose source
         * class is not a declared trusted one (none given, say) cannot show a trusted root either, so it
         * fails closed. So does an action whose {@code refs.instruction} names no held Instruction (nothing
         * the bundle holds, or an event or record of another type: SPEC §6.2.3 types the ref
         * Ref→Instruction). A ToolCall that names no instruction at all ({@code refs.instruction} absent,
         * null or not a string, though SPEC §6.2.3 requires it) is not flagged, but the records cannot
         * show where it came from, so {@code agentce:actsOnJudged} is false and CND-05 reads
         * insufficient_evidence for it (HR-5); a Decision's ref is optional, so it is always judged. A
         * closure over all events, so the acting event may precede its instruction and cycles end. */
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
                    if (!isOneOf(dataOf(event).get("source_class"), TRUSTED_INSTRUCTION)) {
                        untrusted.add(node);
                    }
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
                boolean named = instruction != null;
                boolean acts = named
                        && (untrusted.contains(instruction) || !instructionUntrusted.containsKey(instruction));
                String node = Iri.eventIri(event.get("id").asText());
                literal(node, "agentce:actsOnUntrusted", acts);
                literal(node, "agentce:actsOnJudged", named || "Decision".equals(ptype));
            }
        }

        /** Flag whether each Decision kept untrusted content out, and whether the memory guard
         * ruled on all it used (SPEC §7.4, ROB-02).
         *
         * <p>Untrusted content starts at a MemoryWrite that marks its record untrusted ({@link #flagsUntrusted}); a
         * MemoryRead whose {@code trust_min} is untrusted or quarantined; and an instruction with an untrusted
         * Appendix F source class (CND-05's rule), or with none (ROB-02 only). Content the guard never ruled on
         * starts at a MemoryRead not reported by an enforcement point; a record no enforcement-point read at
         * {@code trust_min} trusted covers, unless every write of it is a ruled enforcement-point write (an unruled
         * rewrite can change what the guard ruled on, 18.120); a record another subject's write marked untrusted or
         * rewrote with no ruling, whatever this subject's own records say of it (18.120); a record another subject's
         * unreadable MemoryWrite may have changed ({@link #unreadRecords}, ROB-02 only, 18.172); and a ref the
         * bundle does not hold. Both flow along every
         * edge that carries content into an event (CONTENT_IN, CONTENT_OUT, a read to its consumer, a
         * record to and from its writes) for as many hops as the records show. Closures over all
         * events, so order and cycles never matter. Returns both closures, which CND-05's
         * instruction chain also reads ({@link #actsOnUntrusted}). */
        private Set<String> robustToUntrusted(List<JsonNode> events) {
            Map<String, Set<String>> flows = new LinkedHashMap<>();
            Set<String> tainted = new LinkedHashSet<>();
            Set<String> unruled = new LinkedHashSet<>();
            Set<String> held = new LinkedHashSet<>();
            Set<String> records = new LinkedHashSet<>();
            // Records an enforcement-point read with a trust_min covers; records with a ruled enforcement-point
            // write; records with any other write.
            Set<String> readRuled = new LinkedHashSet<>();
            Set<String> writeRuled = new LinkedHashSet<>();
            Set<String> rewritten = new LinkedHashSet<>();
            // The stores this subject's memory events name each record under (none when they name no store).
            Map<String, Set<String>> stores = new LinkedHashMap<>();
            for (JsonNode event : events) {
                String ptype = ptype(event);
                JsonNode data = dataOf(event);
                JsonNode refs = refsOf(event);
                String node = Iri.eventIri(event.get("id").asText());
                held.add(node);
                boolean enforced = "enforcement_point".equals(str(event.get("agentcesourceclass")));
                String memoryStore = str(data.get("store"));
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
                    if (record != null) {
                        records.add(record);
                        noteStore(stores, record, memoryStore);
                        (ruledWrite(event, data) ? writeRuled : rewritten).add(record);
                    }
                    if (flagsUntrusted(data)) {
                        tainted.add(node);
                    }
                } else if ("MemoryRead".equals(ptype)) {
                    JsonNode read = data.get("record_refs");
                    // A guard's read rules on its records only when it let through trusted records alone (18.172).
                    boolean ruling = enforced && "trusted".equals(str(data.get("trust_min")));
                    if (read != null && read.isArray()) {
                        for (JsonNode ref : read) {
                            String record = str(ref);
                            if (record != null) {
                                records.add(record);
                                noteStore(stores, record, memoryStore);
                                if (ruling) {
                                    readRuled.add(record);
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
                if (!readRuled.contains(record) && (!writeRuled.contains(record) || rewritten.contains(record))) {
                    unruled.add(record);
                }
            }
            // Every source of a flow is a ref some event names.
            for (String ref : flows.keySet()) {
                if ((!held.contains(ref) && !records.contains(ref)) || foreignUntrusted.contains(ref)) {
                    unruled.add(ref);
                }
            }
            Set<String> fed = new LinkedHashSet<>();
            flows.values().forEach(fed::addAll);
            reach(tainted, flows);
            reach(unruled, flows);
            // A record another subject's unreadable write may have changed is one no guard ruled on (SPEC §7.4);
            // nobody could read that content, so it stays out of the closures CND-05 reads, as an unclassed
            // instruction does.
            Set<String> unread = unreadRecords(foreignUnreadable, records, stores);
            unread.retainAll(flows.keySet());
            reach(unread, flows);
            // An instruction that declares no source class cannot show its content came from a trusted source, so
            // ROB-02 reads it as untrusted content (18.127); CND-05's own chain rule already fails it, so it stays
            // out of the closures returned to that rule.
            Set<String> unclassed = new LinkedHashSet<>(instructionUnclassed);
            reach(unclassed, flows);
            // A decision nothing recorded flows into used nothing the guard could have ruled on, so ROB-02 cannot
            // judge it (ruling 15): it reads untrustedContentRuledOn false itself, and taints nothing downstream.
            Set<String> robustTainted = new LinkedHashSet<>(tainted);
            robustTainted.addAll(unclassed);
            Set<String> robustUnruled = new LinkedHashSet<>(unruled);
            robustUnruled.addAll(unread);
            List<JsonNode> decisions = new ArrayList<>();
            for (JsonNode event : events) {
                if ("Decision".equals(ptype(event))) {
                    decisions.add(event);
                    String node = Iri.eventIri(event.get("id").asText());
                    if (!fed.contains(node)) {
                        robustUnruled.add(node);
                    }
                }
            }
            robustLiterals(decisions, robustTainted, robustUnruled);
            tainted.addAll(unruled);
            return tainted;
        }

        /** ROB-02's two literals on each of {@code decisions} (see {@link #robustToUntrusted}). */
        private void robustLiterals(List<JsonNode> decisions, Set<String> tainted, Set<String> unruled) {
            for (JsonNode event : decisions) {
                String node = Iri.eventIri(event.get("id").asText());
                store.addLiteral(
                        node, "agentce:robustToUntrustedContent", tainted.contains(node) ? "false" : "true", BOOL);
                store.addLiteral(
                        node, "agentce:untrustedContentRuledOn", unruled.contains(node) ? "false" : "true", BOOL);
            }
        }

        private static void noteStore(Map<String, Set<String>> stores, String record, String store) {
            Set<String> named = stores.computeIfAbsent(record, k -> new LinkedHashSet<>());
            if (store != null) {
                named.add(store);
            }
        }

        /** The records unreadable MemoryWrites may have changed (SPEC §7.4, 18.172): the one each names; with no
         * readable record_ref, every record of its store, a record no event names a store for counting as one of
         * them (fail closed); with no readable store either, every record. */
        private static Set<String> unreadRecords(
                List<Ingest.UnreadableWrite> writes, Set<String> records, Map<String, Set<String>> stores) {
            Set<String> unread = new LinkedHashSet<>();
            for (Ingest.UnreadableWrite write : writes) {
                if (write.recordRef() != null) {
                    unread.add(write.recordRef());
                    continue;
                }
                for (String record : records) {
                    Set<String> named = stores.getOrDefault(record, Set.of());
                    if (write.store() == null || named.isEmpty() || named.contains(write.store())) {
                        unread.add(record);
                    }
                }
            }
            return unread;
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
            Map<String, Boolean> byHuman = mapOversight(events);
            Set<String> incidentDecisions = incidentDecisions(events);
            Set<String> policyDecisions = new LinkedHashSet<>();
            for (JsonNode event : events) {
                if ("PolicyDecision".equals(ptype(event))) {
                    policyDecisions.add(Iri.eventIri(event.get("id").asText()));
                }
            }
            Map<String, String> heldTypes = new HashMap<>();
            for (JsonNode event : events) {
                heldTypes.put(Iri.eventIri(event.get("id").asText()), ptype(event));
            }
            Gates gates = new Gates(events, this::ptype);
            Set<String> unresolved = unresolvedLinks(events);
            for (JsonNode event : events) {
                String node = Iri.eventIri(event.get("id").asText());
                String ptype = ptype(event);
                JsonNode data = dataOf(event);
                if ("DelegationIssued".equals(ptype)) {
                    chainVerified(node, data);
                }
                if ("ToolCall".equals(ptype)) {
                    executesConsequential(node, refsOf(event));
                    securityRefsResolve(node, refsOf(event), heldTypes);
                }
                if ("ToolCall".equals(ptype) || "Incident".equals(ptype)) {
                    literal(node, "agentce:linkedRecordsResolve", !unresolved.contains(node));
                }
                if ("Decision".equals(ptype)) {
                    oversightMatches(node, data);
                    explanationReconstructable(node);
                    oversightCoverage(node, data);
                    literal(node, "agentce:triggersIncident",
                            incidentDecisions == null || incidentDecisions.contains(node));
                    riskReviewed(node, refsOf(event), policyDecisions);
                    decisionRecord(node, data, gates);
                }
                if ("Override".equals(ptype) || "Interrupt".equals(ptype)) {
                    intervention(node, ptype, event, byHuman.get(node));
                }
                if ("Incident".equals(ptype)) {
                    literal(node, "agentce:incidentResponded",
                            text(data.get("detected_at")) != null
                                    && INCIDENT_RESPONSES.stream().anyMatch(k -> text(data.get(k)) != null));
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
            conductGate(events);
            conductProvenance(events);
            conductRefusals(events);
            precededBy();
        }

        private void literal(String node, String predicate, boolean value) {
            store.addLiteral(node, predicate, value ? "true" : "false", BOOL);
        }

        /** INC-03: the held decisions the {@code Incident} events name in {@code related_refs[]} or
         * {@code refs.decision}, directly or through the {@code refs.decision} of the event named (an
         * {@code Outcome}, a {@code ToolCall}, an {@code Override}, ...). Null when any name does not
         * lead to a held decision: the records cannot show which decision triggered that incident, so
         * every decision is held to the rule (fail closed). */
        private Set<String> incidentDecisions(List<JsonNode> events) {
            Set<String> out = new LinkedHashSet<>();
            for (JsonNode event : events) {
                if (!"Incident".equals(ptype(event))) {
                    continue;
                }
                List<JsonNode> names = new ArrayList<>();
                JsonNode related = dataOf(event).get("related_refs");
                if (related != null && related.isArray()) {
                    related.forEach(names::add);
                }
                JsonNode decision = refsOf(event).get("decision");
                if (!isAbsent(decision)) {
                    names.add(decision);
                }
                if (names.isEmpty()) {
                    return null;
                }
                for (JsonNode name : names) {
                    String ref = str(name);
                    String named = ref == null ? null
                            : decisionTime.containsKey(ref) ? ref : refDecision.get(ref);
                    if (named == null || !decisionTime.containsKey(named)) {
                        return null;
                    }
                    out.add(named);
                }
            }
            return out;
        }

        /** ROB-03 (Art. 15): every {@code refs.authorization}, {@code refs.delegation} and {@code refs.instruction}
         * the call carries names a held event of the type the SPEC gives it (§6.2); a name of another type, or one
         * that is not a string, is as unresolved as a dangling one. */
        private void securityRefsResolve(String node, JsonNode refs, Map<String, String> heldTypes) {
            boolean resolves = true;
            for (Map.Entry<String, String> ref : SECURITY_REFS) {
                if (refs.has(ref.getKey())) {
                    JsonNode value = refs.get(ref.getKey());
                    resolves &= value.isTextual() && ref.getValue().equals(heldTypes.get(value.textValue()));
                }
            }
            literal(node, "agentce:securityRefsResolve", resolves);
        }

        /** REC-06 (Art. 19) and INC-04 (Art. 73): the events from which a dangling reference can be reached through
         * {@code refs.*}, event refs in {@code used[]} and an Incident's {@code related_refs[]}, at any depth, the
         * event itself included. An Incident's {@code related_refs} entry that is not a held event's name is
         * dangling too. */
        private Set<String> unresolvedLinks(List<JsonNode> events) {
            Set<String> broken = new HashSet<>(danglingNodes);
            Map<String, Set<String>> namedBy = new HashMap<>();
            for (JsonNode event : events) {
                String node = Iri.eventIri(event.get("id").asText());
                JsonNode data = dataOf(event);
                List<String> names = refCandidates(event);
                if ("Incident".equals(ptype(event)) && data.has("related_refs")) {
                    JsonNode related = data.get("related_refs");
                    for (JsonNode name : related.isArray() ? related : List.of(related)) {
                        if (name.isTextual() && eventIris.contains(name.textValue())) {
                            names.add(name.textValue());
                        } else {
                            broken.add(node);
                        }
                    }
                }
                for (String name : names) {
                    if (eventIris.contains(name)) {
                        namedBy.computeIfAbsent(name, k -> new HashSet<>()).add(node);
                    }
                }
            }
            Deque<String> stack = new ArrayDeque<>(broken);
            while (!stack.isEmpty()) {
                for (String referrer : namedBy.getOrDefault(stack.pop(), Set.of())) {
                    if (broken.add(referrer)) {
                        stack.push(referrer);
                    }
                }
            }
            return broken;
        }

        /** The literals DOC-02, TRN-04, OVS-05, RSK-01, REC-05, TRN-01 and TRN-05 read on a decision (18.37f). */
        private void decisionRecord(String node, JsonNode data, Gates gates) {
            String time = decisionTime.getOrDefault(node, "");
            List<JsonNode> linked = gates.linked(node, data);
            List<JsonNode> gating = linked == null ? List.of() : linked;
            boolean held = linked != null && linked.stream().noneMatch(pd -> "deny".equals(str(pd.get("decision"))));
            literal(node, "agentce:authorizationHeld", held);
            literal(node, "agentce:authorizationGateCleared", held && gating.stream().allMatch(pd -> {
                String decision = str(pd.get("decision"));
                return "allow".equals(decision)
                        || ("require_approval".equals(decision) && humanApprovers.containsKey(node));
            }));
            literal(node, "agentce:authorityRecorded", held && gating.stream().allMatch(Graph::namesPolicy));
            literal(node, "agentce:decisionDocumented", documented(data));
            List<JsonNode> notices = new ArrayList<>();
            for (String notice : decisionNotice.getOrDefault(node, Set.of())) {
                JsonNode found = gates.notices.get(notice);
                if (found != null) {
                    notices.add(found);
                }
            }
            literal(node, "agentce:explanationTimed", notices.stream().anyMatch(n -> {
                String delivered = str(n.get("delivered_at"));
                if (!"explanation".equals(str(n.get("notice_type"))) || delivered == null) {
                    return false;
                }
                Integer sinceDecision = Structural.compareInstants(delivered, time);
                return sinceDecision != null && sinceDecision >= 0;
            }));
            String person = text(data.get("person_ref"));
            literal(node, "agentce:affectedPersonNotified", notices.stream().anyMatch(n ->
                    text(n.get("person_ref")) != null
                            && text(n.get("delivered_at")) != null
                            && (person == null || person.equals(str(n.get("person_ref"))))));
            JsonNode affects = data.get("affects_natural_person");
            literal(node, "agentce:inDisclosureScope", !(affects != null && affects.isBoolean() && !affects.booleanValue()));
            String session = text(data.get("session_id"));
            literal(node, "agentce:aiInteractionDisclosed", session != null && gates.disclosures.stream().anyMatch(d -> {
                String delivered = str(d.get("delivered_at"));
                if (!"ai_interaction".equals(str(d.get("disclosure_type")))
                        || !session.equals(str(d.get("session_id"))) || delivered == null) {
                    return false;
                }
                Integer sinceDecision = Structural.compareInstants(delivered, time);
                return sinceDecision != null && sinceDecision <= 0;
            }));
        }

        /** RSK-02 (Art. 9): a review of the decision, or a {@code refs.authorization} (or
         * {@code refs.request}) naming a {@code PolicyDecision} the bundle holds. A name that leads to
         * no held policy decision gates nothing. */
        private void riskReviewed(String node, JsonNode refs, Set<String> policyDecisions) {
            boolean gated = decisionReviewed.contains(node);
            for (String key : List.of("authorization", "request")) {
                gated |= isOneOf(refs.get(key), policyDecisions);
            }
            literal(node, "agentce:riskReviewed", gated);
        }

        /** The held decision a {@code refs.decision} names (SPEC §6.3 approvedBy): its IRI, else the ref trimmed of
         * {@link #ID_PAD} when that is a held decision's IRI, else {@code agentce:event/<trimmed ref>} when that is (a
         * bare id); null when it names no held decision. Exact match first, so no name resolves to another held
         * decision. */
        /** The names a refs.decision may mean an event by, in the order they are tried: as written, trimmed of
         * ID_PAD, and agentce:event/&lt;trimmed ref&gt; (a bare id); none when the ref is no string. */
        private List<String> refNames(JsonNode node) {
            String ref = str(node);
            if (ref == null) {
                return List.of();
            }
            String trimmed = Objects.requireNonNullElse(ruleId(ref), "");
            return List.of(ref, trimmed, Iri.eventIri(trimmed));
        }

        private String heldDecision(JsonNode node) {
            for (String name : refNames(node)) {
                if (decisionTime.containsKey(name)) {
                    return name;
                }
            }
            return null;
        }

        /** Whether each ApprovalDecided, Override and Interrupt meets the SPEC §10.4 human-actor rule. An
         * ApprovalDecided reviews the decision it names ({@code agentce:reviewedBy}; OVS-01, INC-03, RSK-02) only when
         * it does, whatever its outcome; it also approves it ({@code agentce:approvedBy}; CND-02, OVS-08) only when its
         * outcome is approve, it is recorded at or before every ToolCall that executes the decision (18.141) and every
         * Outcome that names it (18.144: the decision had already taken effect), and the same human recorded no other decision on it at or after it, or at a time that cannot be placed against it,
         * unless that decision came after every such ToolCall (18.142: each human's most recent decision governs).
         * One that fails the rule is no human's review. Every refs.decision is read through {@link #heldDecision}
         * (18.148), and a ToolCall or Outcome naming no held decision (a null refs.decision included, 18.157) bounds
         * every decision's approvals. */
        private Map<String, Boolean> mapOversight(List<JsonNode> events) {
            Map<String, Boolean> byHuman = new LinkedHashMap<>();
            Map<String, List<String>> actionTimes = new LinkedHashMap<>();
            Map<String, List<String>> outcomeTimes = new LinkedHashMap<>();
            // The time of every ToolCall and Outcome whose refs.decision names no held decision: it may be any
            // decision's effect, so it bounds every decision's approvals (fails closed, 18.148), though it withdraws none.
            List<String> unresolvedTimes = new ArrayList<>();
            Map<String, Map<String, List<Decided>>> decided = new LinkedHashMap<>();
            for (JsonNode event : events) {
                String type = ptype(event);
                JsonNode ref = refsOf(event).get("decision");
                // No refs.decision key is no claim; a present null names no held decision (18.157).
                if (!("ToolCall".equals(type) || "Outcome".equals(type)) || ref == null) {
                    continue;
                }
                String decision = heldDecision(ref);
                if (decision == null) {
                    unresolvedTimes.add(timeOf(event));
                    continue;
                }
                ("ToolCall".equals(type) ? actionTimes : outcomeTimes)
                        .computeIfAbsent(decision, k -> new ArrayList<>()).add(timeOf(event));
            }
            for (JsonNode event : events) {
                String ptype = ptype(event);
                if (!OVERSIGHT_TYPES.contains(ptype)) {
                    continue;
                }
                String node = Iri.eventIri(event.get("id").asText());
                String human = verifiedActor(node, event);
                byHuman.put(node, human != null);
                JsonNode refNode = refsOf(event).get("decision");
                String ref = str(refNode);
                String held = "ApprovalDecided".equals(ptype) ? heldDecision(refNode) : null;
                if (human == null) {
                    // What the deviation register cannot waive (SPEC §10.4): the failing record, and the held
                    // decision a failing approval names.
                    store.humanActorFailed.add(node);
                    if (held != null) {
                        store.humanActorFailed.add(held);
                    }
                }
                if (!"ApprovalDecided".equals(ptype) || ref == null) {
                    continue;
                }
                // A name that leads to no held decision keeps its own key: it reviews and approves no held decision.
                String decision = held != null ? held : ref;
                boolean approve = "approve".equals(str(dataOf(event).get("outcome")));
                String actor = humanId(dataOf(event).get("actor"));
                // A human's own no withdraws that human's approve even when it fails §10.4 (fail closed, 18.142).
                String who = human != null ? human : actor != null && !approve ? Iri.principalIri(actor, key) : null;
                if (who == null) {
                    continue;
                }
                if (human != null) {
                    store.addEdge(decision, "agentce:reviewedBy", node);
                    decisionReviewed.add(decision);
                }
                decided.computeIfAbsent(decision, k -> new LinkedHashMap<>())
                        .computeIfAbsent(who, k -> new ArrayList<>())
                        .add(new Decided(node, timeOf(event), approve));
            }
            for (Map.Entry<String, Map<String, List<Decided>>> byDecision : decided.entrySet()) {
                String decision = byDecision.getKey();
                List<String> actions = actionTimes.getOrDefault(decision, List.of());
                List<String> outcomes = outcomeTimes.getOrDefault(decision, List.of());
                for (Map.Entry<String, List<Decided>> byActor : byDecision.getValue().entrySet()) {
                    List<String> withdrawals = byActor.getValue().stream()
                            .filter(d -> !d.approve() && !afterEvery(d.time(), actions))
                            .map(Decided::time)
                            .toList();
                    for (Decided d : byActor.getValue()) {
                        if (d.approve() && beforeEvery(d.time(), actions) && beforeEvery(d.time(), outcomes)
                                && beforeEvery(d.time(), unresolvedTimes)
                                && withdrawals.stream().allMatch(w -> {
                            Integer order = Structural.compareLiterals(d.time(), w);
                            return order != null && order > 0;
                        })) {
                            store.addEdge(decision, "agentce:approvedBy", d.node());
                            humanApprovers.computeIfAbsent(decision, k -> new LinkedHashSet<>()).add(byActor.getKey());
                        }
                    }
                }
            }
            return byHuman;
        }

        /** OVS-08 (Art. 14(5)): enough distinct human approvers -- two when the decision's observed
         * or domain-declared oversight modality is {@code dual_control}, otherwise one. */
        private void oversightCoverage(String node, JsonNode data) {
            String dtype = str(data.get("decision_type"));
            String declared = dtype != null ? domain.requiredOversight.get(dtype) : null;
            boolean dual = "dual_control".equals(str(data.get("oversight_modality")))
                    || "dual_control".equals(declared);
            int approvers = humanApprovers.getOrDefault(node, Set.of()).size();
            literal(node, "agentce:oversightCoverageComplete", approvers >= (dual ? 2 : 1));
        }

        /** OVS-07: an {@code Override} is effective when it names a held decision and records a
         * replacement that differs from the original; an {@code Interrupt} when a named mechanism
         * halted or paused the agent. Either is by a human when its actor meets the SPEC §10.4 human-actor rule. */
        private void intervention(String node, String ptype, JsonNode event, boolean byHuman) {
            JsonNode data = dataOf(event);
            boolean effective;
            if ("Override".equals(ptype)) {
                effective = heldDecision(refsOf(event).get("decision")) != null
                        && !isAbsent(data.get("replacement"))
                        && !jsonEquals(data.get("replacement"), data.get("original"));
            } else {
                effective = isOneOf(data.get("effect"), INTERRUPT_STOPS)
                        && isOneOf(data.get("mechanism"), INTERRUPT_MECHANISMS);
            }
            literal(node, "agentce:interventionEffective", effective);
            literal(node, "agentce:interventionByHuman", byHuman);
        }

        private void dangling(String node, JsonNode event) {
            for (String value : refCandidates(event)) {
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
            String decision = heldDecision(refs.get("decision"));
            boolean consequential = decision != null
                    && domain.consequential.contains(decisionType.getOrDefault(decision, ""));
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
