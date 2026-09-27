package org.agentce;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeSet;

/**
 * Blind spots: the one missing record type that would unlock the most checks (Hill 2).
 *
 * <p>{@link #computeBlindSpots} groups every {@code insufficient_evidence} assertion by the
 * normalized (event, class) requirement its subject's own events did and did not satisfy, and ranks
 * each group by how many checks supplying it would provably unlock. A faithful port of the Python
 * reference ({@code agentce/blind_spots.py}); RFC 0008 is the operative spec for every rule here.
 * Read-only over {@code (assertions, profile, catalogs, events)}: it never affects an outcome or the
 * verdict.
 */
public final class BlindSpots {
    private BlindSpots() {}

    /** The seven OpenTelemetry-GenAI-shaped trace events (RFC 0008 Sec.3): a self-report requirement
     * for one of these is rung 1 ("what happened"); every other self-report requirement is rung 2. */
    private static final Set<String> OTEL_SHAPED_EVENTS = Set.of(
            "ModelCall", "ToolCall", "ResourceAccess", "MemoryRead", "MemoryWrite", "SessionStart", "SessionEnd");

    private record OwnerStep(String ownerKey, String stepKind) {}

    /** (ownerKey, stepKind) by rung (RFC 0008 Sec.3; {@code VALUE-PROP.md}'s evidence-ladder table
     * verbatim). */
    private static final Map<Integer, OwnerStep> OWNER_AND_STEP_BY_RUNG = Map.of(
            1, new OwnerStep("agent_team", "code_change"),
            2, new OwnerStep("agent_team", "code_change"),
            3, new OwnerStep("platform_or_security", "request"),
            4, new OwnerStep("ticketing_or_iam", "request"));

    private static String normalizeClass(String cls) {
        return Assess.SELF_REPORT_EQUIVALENT_CLASSES.contains(cls) ? "self_report" : cls;
    }

    private static volatile Map<String, Map<String, String>> cachedEventProducers;

    /** The vendored, per-event map of adapter name to that adapter's default trust class (RFC 0008
     * Sec.5), kept in sync with every {@code adapters/*\/support-matrix.yaml} by
     * {@code tools/support_matrix_sync_check.py}. */
    private static Map<String, Map<String, String>> eventProducers() {
        Map<String, Map<String, String>> local = cachedEventProducers;
        if (local == null) {
            try (InputStream in = BlindSpots.class.getResourceAsStream("/support_matrices/event_producers.json")) {
                if (in == null) {
                    throw new IllegalStateException("vendored event_producers.json not on classpath");
                }
                JsonNode root = Json.parse(new String(in.readAllBytes(), StandardCharsets.UTF_8));
                Map<String, Map<String, String>> parsed = new LinkedHashMap<>();
                root.fields().forEachRemaining(entry -> {
                    Map<String, String> byAdapter = new LinkedHashMap<>();
                    entry.getValue().fields().forEachRemaining(a -> byAdapter.put(a.getKey(), a.getValue().asText()));
                    parsed.put(entry.getKey(), byAdapter);
                });
                local = parsed;
                cachedEventProducers = local;
            } catch (IOException e) {
                throw new IllegalStateException("cannot read vendored event_producers.json", e);
            }
        }
        return local;
    }

    /** The evidence-ladder rung for a grouping key: by who can actually supply {@code event} today
     * (RFC 0008 Sec.3), not only the catalog's literal required class. */
    private static int ladderRung(String event, String normalizedClass) {
        if (normalizedClass.equals("independent_system")) {
            return 4;
        }
        if (normalizedClass.equals("enforcement_point")) {
            return 3;
        }
        Map<String, String> producers = eventProducers().getOrDefault(event, Map.of());
        if (producers.isEmpty()) {
            // No adapter declares this event at all: a generic, adapter-free event (Decision/Outcome/
            // Incident) any agent can emit directly -- not a vacuous rung 3.
            return 2;
        }
        boolean allEnforcementPoint = producers.values().stream().allMatch(c -> c.equals("enforcement_point"));
        if (allEnforcementPoint) {
            // The catalog would accept self-reported evidence in principle, but today only a gateway,
            // policy engine, identity provider, or supply-chain verifier ever produces this event.
            return 3;
        }
        return OTEL_SHAPED_EVENTS.contains(event) ? 1 : 2;
    }

    /** Only the adapters whose default class actually satisfies this key (RFC 0008 Sec.3), reusing
     * {@link Assess#classOk} -- never every adapter that merely declares the event. */
    private static List<String> supplyingAdapters(String event, String normalizedClass) {
        Map<String, String> producers = eventProducers().getOrDefault(event, Map.of());
        TreeSet<String> out = new TreeSet<>(Json::byteCompare);
        for (Map.Entry<String, String> e : producers.entrySet()) {
            if (Assess.classOk(e.getValue(), normalizedClass)) {
                out.add(e.getKey());
            }
        }
        return new ArrayList<>(out);
    }

    /** A single {@code {subject, catalog, control, control_version}} reference (RFC 0008 Sec.6: four
     * fields, never a bare control id, since two catalogs can share a control id and version). */
    private record CheckRef(String subject, String catalog, String control, String controlVersion) {
        ObjectNode toJson() {
            ObjectNode node = Json.nodes().objectNode();
            node.put("subject", subject);
            node.put("catalog", catalog);
            node.put("control", control);
            node.put("control_version", controlVersion);
            return node;
        }
    }

    private static int compareCheckRefs(CheckRef a, CheckRef b) {
        String[] ka = {a.subject(), a.catalog(), a.control(), a.controlVersion()};
        String[] kb = {b.subject(), b.catalog(), b.control(), b.controlVersion()};
        return Arrays.compare(ka, kb, Json::byteCompare);
    }

    private record Triple(String subjectId, String catalogId, Catalog.ControlSpec control) {}

    /** The exact {@code (subject.id, catalog.id, control)} sequence {@link Assess#assessSubjects}
     * builds, in its own iteration order (RFC 0008 Sec.6) -- so {@code assertions} (that same loop's
     * own output) can be paired with the {@code (catalog, control)} it came from by position, without
     * a {@code catalog} field on {@link Assertions.Assertion} itself. */
    private static List<Triple> replayTriples(Profile profile, List<Catalog> catalogs) {
        List<Triple> triples = new ArrayList<>();
        for (Profile.Subject subject : profile.subjects) {
            for (Catalog catalog : catalogs) {
                for (Catalog.ControlSpec control : catalog.controls) {
                    triples.add(new Triple(subject.id, catalog.id, control));
                }
            }
        }
        return triples;
    }

    private static final class Group {
        final String event;
        final String cls;
        final List<CheckRef> unlocked = new ArrayList<>();
        final List<CheckRef> needed = new ArrayList<>();

        Group(String event, String cls) {
            this.event = event;
            this.cls = cls;
        }
    }

    /** {@code (subjectId, event, cls)}: a value-equality key for {@code metCache}, so the cache is a
     * plain {@link HashMap} lookup rather than per-lookup string-byte comparisons against a sorted
     * tree -- final output order comes only from the explicit sorts below, never from this map's
     * iteration order. */
    private record MetKey(String subjectId, String event, String cls) {}

    /** {@code (event, cls)}: a value-equality key for {@code groups} and {@code normalizedByKey}, for
     * the same reason as {@link MetKey}. */
    private record GroupKey(String event, String cls) {}

    /** Return {@code {"blind_spots": [...], "no_population": [...]}} for {@code assertions}.
     *
     * <p>{@code assertions} must be {@link Assess#assessSubjects}'s own output, in its own order --
     * that order is meaningful input here (RFC 0008 Sec.6), not incidental: it is replayed positionally
     * against {@code profile}/{@code catalogs} to recover each assertion's {@code (catalog, control)}
     * origin without a schema change to {@link Assertions.Assertion}. A pure function otherwise: no
     * I/O beyond reading the vendored {@code event_producers.json}, no network, no clock. */
    public static ObjectNode computeBlindSpots(
            List<Assertions.Assertion> assertions, Profile profile, List<Catalog> catalogs, List<JsonNode> events) {
        List<Triple> triples = replayTriples(profile, catalogs);
        if (triples.size() != assertions.size()) {
            throw new IllegalArgumentException(
                    "computeBlindSpots: " + assertions.size() + " assertions but " + triples.size()
                            + " (subject, catalog, control) triples replayed from profile/catalogs -- assertions "
                            + "must be assessSubjects' own output.");
        }

        Map<String, List<JsonNode>> eventsBySubject = Assess.indexBySubject(events);
        // Keyed by a (subjectId, event, cls) value-equality record, never a delimiter-joined string:
        // two distinct triples whose fields happen to abut at a boundary must never collide into the
        // same cache entry (the exact class of bug 18.4's activity port fixed for its own tuple keys).
        Map<MetKey, Boolean> metCache = new HashMap<>();
        java.util.function.BiFunction<String, JsonNode, Boolean> met = (subjectId, requirement) -> {
            String event = requirement.has("event") ? requirement.get("event").asText() : "";
            String cls = requirement.has("class") ? requirement.get("class").asText() : "any";
            MetKey key = new MetKey(subjectId, event, cls);
            Boolean cached = metCache.get(key);
            if (cached == null) {
                cached = Assess.requirementMet(eventsBySubject.getOrDefault(subjectId, List.of()), requirement);
                metCache.put(key, cached);
            }
            return cached;
        };

        Map<GroupKey, Group> groups = new HashMap<>();
        List<CheckRef> noPopulation = new ArrayList<>();

        for (int i = 0; i < triples.size(); i++) {
            Triple t = triples.get(i);
            Assertions.Assertion a = assertions.get(i);
            if (!a.subject.equals(t.subjectId()) || !a.control.equals(t.control().id)
                    || !a.controlVersion.equals(t.control().version)) {
                throw new IllegalArgumentException(
                        "computeBlindSpots: assertion is (" + a.subject + ", " + a.control + ", " + a.controlVersion
                                + ") but the replayed sequence expects (" + t.subjectId() + ", " + t.catalogId() + ", "
                                + t.control().id + ", " + t.control().version
                                + ") -- assertions is not assessSubjects' own order.");
            }
            if (!"insufficient_evidence".equals(a.outcome)) {
                continue;
            }
            List<JsonNode> missing = new ArrayList<>();
            for (JsonNode r : t.control().minimumEvidence) {
                if (!met.apply(t.subjectId(), r)) {
                    missing.add(r);
                }
            }
            Set<GroupKey> normalizedByKey = new HashSet<>();
            for (JsonNode r : missing) {
                String event = r.has("event") ? r.get("event").asText() : "";
                String cls = normalizeClass(r.has("class") ? r.get("class").asText() : "any");
                normalizedByKey.add(new GroupKey(event, cls));
            }
            CheckRef checkRef = new CheckRef(t.subjectId(), t.catalogId(), t.control().id, t.control().version);
            if (normalizedByKey.isEmpty()) {
                // Every minimum_evidence entry is actually satisfied, yet the shape's own structural
                // population was still empty (RFC 0008 Sec.4) -- checked regardless of population[0].
                noPopulation.add(checkRef);
                continue;
            }
            // The minimum-evidence branch's non-empty missing set is provably sufficient only when
            // exactly one key is missing (no partial credit); the empty-population branch's non-empty
            // missing set is informative but never provably sufficient on its own, so it always lands
            // in needed_by.
            boolean unlocked = a.population[0] > 0 && normalizedByKey.size() == 1;
            for (GroupKey key : normalizedByKey) {
                Group group = groups.computeIfAbsent(key, k -> new Group(k.event(), k.cls()));
                (unlocked ? group.unlocked : group.needed).add(checkRef);
            }
        }

        List<ObjectNode> blindSpots = new ArrayList<>();
        for (Group group : groups.values()) {
            int rung = ladderRung(group.event, group.cls);
            OwnerStep ownerStep = OWNER_AND_STEP_BY_RUNG.get(rung);
            List<CheckRef> unlockedSorted = new ArrayList<>(group.unlocked);
            unlockedSorted.sort(BlindSpots::compareCheckRefs);
            List<CheckRef> neededSorted = new ArrayList<>(group.needed);
            neededSorted.sort(BlindSpots::compareCheckRefs);
            ObjectNode node = Json.nodes().objectNode();
            node.put("event", group.event);
            node.put("class", group.cls);
            node.put("ladder_rung", rung);
            node.put("owner_key", ownerStep.ownerKey());
            node.put("step_kind", ownerStep.stepKind());
            ArrayNode adapters = node.putArray("supplying_adapters");
            supplyingAdapters(group.event, group.cls).forEach(adapters::add);
            node.put("checks_unlocked", unlockedSorted.size());
            ArrayNode unlockedArr = node.putArray("unlocked_checks");
            unlockedSorted.forEach(c -> unlockedArr.add(c.toJson()));
            node.put("needed_by", neededSorted.size());
            ArrayNode neededArr = node.putArray("needed_by_checks");
            neededSorted.forEach(c -> neededArr.add(c.toJson()));
            blindSpots.add(node);
        }
        blindSpots.sort((x, y) -> {
            int cu = Integer.compare(y.get("checks_unlocked").asInt(), x.get("checks_unlocked").asInt());
            if (cu != 0) {
                return cu;
            }
            int nb = Integer.compare(y.get("needed_by").asInt(), x.get("needed_by").asInt());
            if (nb != 0) {
                return nb;
            }
            int rung = Integer.compare(x.get("ladder_rung").asInt(), y.get("ladder_rung").asInt());
            if (rung != 0) {
                return rung;
            }
            int byEvent = Json.byteCompare(x.get("event").asText(), y.get("event").asText());
            return byEvent != 0 ? byEvent : Json.byteCompare(x.get("class").asText(), y.get("class").asText());
        });
        noPopulation.sort(BlindSpots::compareCheckRefs);

        ObjectNode out = Json.nodes().objectNode();
        ArrayNode blindSpotsArr = out.putArray("blind_spots");
        blindSpots.forEach(blindSpotsArr::add);
        ArrayNode noPopulationArr = out.putArray("no_population");
        noPopulation.forEach(c -> noPopulationArr.add(c.toJson()));
        return out;
    }
}
