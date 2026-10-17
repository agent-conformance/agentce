package org.agentce;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.function.Supplier;

/**
 * Assemble assertions by evaluating a catalog against each subject (SPEC §7, §9).
 *
 * <p>For every subject the engine builds that subject's graph, then for every control decides the
 * outcome: {@code not_applicable} when the role does not match or the shape targets nothing,
 * {@code insufficient_evidence} when the minimum evidence is absent, and otherwise the structural
 * verdict against the control's tolerance. Only rung-2 structural controls are evaluated; other rungs
 * become {@code not_assessed}. A faithful port of the reference.
 */
public final class Assess {
    private Assess() {}

    private static final int EVIDENCE_CAP = 20;
    private static final List<String> ROLES = List.of("deployer", "provider");
    private static final String[] EPOCH = {"1970-01-01T00:00:00Z", "1970-01-01T00:00:00Z"};

    private static boolean roleApplies(Set<String> roles, List<String> appliesToRoles) {
        Set<String> targets = new LinkedHashSet<>(appliesToRoles);
        if (targets.contains("both")) {
            targets.addAll(ROLES);
        }
        for (String r : roles) {
            if (targets.contains(r)) {
                return true;
            }
        }
        return false;
    }

    /** A required class of {@code any} or {@code self_report} is satisfied by any observed source
     * class (mirrored by {@link BlindSpots}'s {@code normalizeClass}, which groups both onto the same
     * blind-spot key). Package-private: shared with {@link BlindSpots} rather than duplicated. */
    static final Set<String> SELF_REPORT_EQUIVALENT_CLASSES = Set.of("any", "self_report");

    /** The predicates whose value the SPEC §10.4 human-actor rule decides: a human's approval or review of a
     * decision, enough human approvers, a human intervention, the review half of a risk review (18.186), and an
     * action's CND-02 gate, which a human's approval releases (18.187). */
    static final Set<String> HUMAN_ACTOR_PATHS = Set.of(
            "agentce:approvedBy",
            "agentce:gateMet",
            "agentce:reviewedBy",
            "agentce:oversightCoverageComplete",
            "agentce:interventionByHuman",
            "agentce:riskReviewed");

    /** Whether a violation's constraint reads a predicate the SPEC §10.4 human-actor rule decides and, from its
     * focus, reaches a node that fails the rule ({@code reads} gives each path's predicates, nested shapes
     * included). */
    private static boolean restsOnHumanActorFailure(
            GraphStore store, List<Structural.Violation> violations, Supplier<Map<String, List<String>>> reads) {
        if (store.humanActorFailed.isEmpty()) {
            return false;
        }
        Map<String, List<String>> byPath = reads.get();
        return violations.stream().anyMatch(
                v -> reachesHumanActorFailure(store, v.focus, byPath.getOrDefault(v.path, List.of())));
    }

    /** Whether {@code reads} names a predicate the human-actor rule decides and, walking its other predicates from
     * {@code focus} ({@code ^} for an inverse) in any order and as often as they lead somewhere new, the walk reaches a
     * node failing the rule. It reaches at least what the shape itself reaches, a repeated step included, so it fails
     * closed. */
    static boolean reachesHumanActorFailure(GraphStore store, String focus, List<String> reads) {
        List<String> steps = reads.stream()
                .filter(step -> !HUMAN_ACTOR_PATHS.contains(step.startsWith("^") ? step.substring(1) : step))
                .toList();
        if (steps.size() == reads.size()) {
            return false;
        }
        Set<String> reach = new LinkedHashSet<>(List.of(focus));
        List<String> frontier = List.of(focus);
        while (!frontier.isEmpty()) {
            List<String> next = new ArrayList<>();
            for (String start : frontier) {
                for (String step : steps) {
                    List<String> nodes = step.startsWith("^")
                            ? store.subjects(step.substring(1), start)
                            : store.objects(start, step);
                    for (String node : nodes) {
                        if (reach.add(node)) {
                            next.add(node);
                        }
                    }
                }
            }
            frontier = next;
        }
        return reach.stream().anyMatch(store.humanActorFailed::contains);
    }

    static boolean classOk(String observed, String required) {
        return SELF_REPORT_EQUIVALENT_CLASSES.contains(required) || observed.equals(required);
    }

    private static String eventType(JsonNode event) {
        JsonNode data = event.get("data");
        if (data != null && data.isObject() && data.get("@type") != null && data.get("@type").isTextual()) {
            return data.get("@type").textValue();
        }
        return "";
    }

    private static String subjectOf(JsonNode event) {
        JsonNode s = event.get("subject");
        return s != null && s.isTextual() ? s.textValue() : "";
    }

    private static String sourceClassOf(JsonNode event) {
        JsonNode s = event.get("agentcesourceclass");
        return s != null && s.isTextual() ? s.textValue() : "";
    }

    /** Whether {@code events} carries at least one event satisfying one {@code minimum_evidence}
     * entry -- factored out of {@code hasMinimumEvidence} so the blind-spots ranking (Hill 2) can
     * report, per requirement, which of a control's minimum-evidence entries this subject's own
     * events did and did not satisfy, using the exact same rule the assessment itself used to reach
     * its outcome. Package-private: shared with {@link BlindSpots} rather than duplicated. */
    static boolean requirementMet(List<JsonNode> events, JsonNode requirement) {
        String wantType = requirement.has("event") ? requirement.get("event").asText() : "";
        String requiredClass = requirement.has("class") ? requirement.get("class").asText() : "any";
        for (JsonNode e : events) {
            if (eventType(e).equals(wantType) && classOk(sourceClassOf(e), requiredClass)) {
                return true;
            }
        }
        return false;
    }

    private static boolean hasMinimumEvidence(List<JsonNode> events, List<JsonNode> minimum) {
        for (JsonNode requirement : minimum) {
            if (!requirementMet(events, requirement)) {
                return false;
            }
        }
        return true;
    }

    private static String[] window(Profile profile, List<JsonNode> events) {
        Map<String, String> w = profile.observationWindow;
        if (w.containsKey("start") && w.containsKey("end")) {
            return new String[] {w.get("start"), w.get("end")};
        }
        List<String> times = new ArrayList<>();
        for (JsonNode e : events) {
            JsonNode t = e.get("time");
            if (t != null && t.isTextual() && !t.textValue().isEmpty()) {
                times.add(t.textValue());
            }
        }
        times.sort(Json::byteCompare);
        if (!times.isEmpty()) {
            return new String[] {times.get(0), times.get(times.size() - 1)};
        }
        return EPOCH.clone();
    }

    private static List<Assertions.EvidencePointer> evidence(Map<String, JsonNode> eventsByIri, List<String> focusNodes) {
        List<Assertions.EvidencePointer> pointers = new ArrayList<>();
        int cap = Math.min(focusNodes.size(), EVIDENCE_CAP);
        for (int i = 0; i < cap; i++) {
            String focus = focusNodes.get(i);
            JsonNode event = eventsByIri.get(focus);
            if (event == null) {
                continue;
            }
            String digest = Ingest.digestOf(event);
            if (digest == null) {
                // a record with no canonical form (an integer past 2^53) has no digest to cite
                continue;
            }
            String sc = sourceClassOf(event);
            pointers.add(new Assertions.EvidencePointer(
                    focus, "sha256:" + digest, sc.isEmpty() ? "self_report" : sc));
        }
        return pointers;
    }

    /** The control's family: the letter prefix of its own id (SPEC §7.1; mirrors the Python reference). */
    private static String familyOf(String controlId) {
        int dash = controlId.indexOf('-');
        return dash == -1 ? controlId : controlId.substring(0, dash);
    }

    /**
     * Carry each control's own crosswalk entries into every assertion built for it (SPEC §7.3).
     *
     * <p>{@code verified} is read from the control's {@code verified_against_text} and coerced to a
     * real boolean -- never invented, and never set true by the engine itself; only a human with
     * access to the licensed standard text may flip that flag in the catalog source (human action H4).
     */
    private static List<JsonNode> crosswalkFor(Catalog.ControlSpec control) {
        List<JsonNode> out = new ArrayList<>();
        JsonNode raw = control.raw.get("crosswalk");
        if (raw == null || !raw.isArray()) {
            return out;
        }
        for (JsonNode entry : raw) {
            ObjectNode node = Json.nodes().objectNode();
            node.set("framework", entry.get("framework"));
            node.set("clause", entry.get("clause"));
            JsonNode verified = entry.get("verified_against_text");
            node.put("verified", verified != null && verified.isBoolean() && verified.booleanValue());
            out.add(node);
        }
        return out;
    }

    private static Assertions.Assertion assertControl(
            GraphStore store,
            Catalog catalog,
            Catalog.ControlSpec control,
            Profile.Subject subject,
            Set<String> roles,
            List<JsonNode> events,
            Map<String, JsonNode> eventsByIri,
            String[] win,
            Supplier<SourceClass.Judge> judge) {
        List<JsonNode> crosswalk = crosswalkFor(control);
        if (!roleApplies(roles, control.appliesToRoles)) {
            Assertions.Assertion a = Assertions.make(control.id, control.version, subject.id, "not_applicable", control.rung, control.mode, win, new int[] {0, 0}, control.severity, familyOf(control.id));
            a.crosswalk = crosswalk;
            return a;
        }

        Psp.Shape shape = Catalog.shapeFor(catalog, control);
        if (control.rung != 2 || shape == null) {
            Assertions.Assertion a = Assertions.make(control.id, control.version, subject.id, "not_assessed", control.rung, control.mode, win, new int[] {0, 0}, control.severity, familyOf(control.id));
            a.crosswalk = crosswalk;
            return a;
        }

        Structural.ShapeResult result = Structural.evaluateShape(store, shape, catalog.shapes, control.id);
        if (result.applicable.isEmpty()) {
            Assertions.Assertion a = Assertions.make(control.id, control.version, subject.id, "not_applicable", control.rung, control.mode, win, new int[] {0, 0}, control.severity, familyOf(control.id));
            a.crosswalk = crosswalk;
            return a;
        }
        if (!hasMinimumEvidence(events, control.minimumEvidence)) {
            Assertions.Assertion a = Assertions.make(control.id, control.version, subject.id, "insufficient_evidence", control.rung, control.mode, win, new int[] {result.applicable.size(), 0}, control.severity, familyOf(control.id));
            a.crosswalk = crosswalk;
            return a;
        }

        boolean conformant = Structural.withinTolerance(result.applicable.size(), result.failing.size(), control.tolerance);
        Psp.Shape evidenceShape = Catalog.evidenceShapeFor(catalog, control);
        if (conformant && evidenceShape != null) {
            List<String> unjudged = new ArrayList<>(
                    Structural.evaluateShape(store, evidenceShape, catalog.shapes, control.id).failing);
            if (!unjudged.isEmpty()) {
                // A focus the control cannot judge from these records never counts as a pass; a failure
                // elsewhere still reads non-conformant (above).
                unjudged.sort(Json::byteCompare);
                Assertions.Assertion a = Assertions.make(control.id, control.version, subject.id, "insufficient_evidence", control.rung, control.mode, win, new int[] {result.applicable.size(), 0}, control.severity, familyOf(control.id));
                a.evidence = evidence(eventsByIri, unjudged);
                a.crosswalk = crosswalk;
                return a;
            }
        }
        // HR-5: a pass counts only when every focus's judged class meets the control's minimum (SPEC §6.4). The
        // rule never runs on a non-conformant verdict, which stays non-conformant.
        SourceClass.Below below = conformant
                ? SourceClass.belowMinimum(judge, result.applicable, control.minSourceClass, control.sourceClassFrom)
                : new SourceClass.Below(List.of(), List.of());
        boolean isBelow = !below.foci().isEmpty();
        String outcome = isBelow ? SourceClass.BELOW_MINIMUM_OUTCOME : conformant ? "conformant" : "non-conformant";
        // Below the minimum, the outcome rests on the below foci alone: none counts as failing, and no verdict is
        // given.
        List<String> focusForEvidence;
        if (isBelow) {
            focusForEvidence = below.foci();
        } else if (!result.failing.isEmpty()) {
            focusForEvidence = new ArrayList<>(result.failing);
            focusForEvidence.sort(Json::byteCompare);
        } else {
            focusForEvidence = result.applicable;
        }
        Assertions.Assertion assertion = Assertions.make(
                control.id, control.version, subject.id, outcome,
                control.rung, control.mode, win,
                new int[] {result.applicable.size(), isBelow ? 0 : result.failing.size()},
                control.severity, familyOf(control.id));
        if (!isBelow) {
            for (Structural.Violation v : result.violations) {
                assertion.violations.add(Structural.violationToJson(v));
            }
            assertion.humanActorFailed = "non-conformant".equals(outcome) && restsOnHumanActorFailure(
                    store, result.violations, () -> Structural.propertyReads(shape, catalog.shapes));
            JsonNode strength = control.raw.get("evidence_strength");
            assertion.evidenceStrength = strength != null && strength.isTextual() ? strength.textValue() : null;
        }
        assertion.evidence = evidence(eventsByIri, focusForEvidence);
        assertion.crosswalk = crosswalk;
        assertion.sourceClassSatisfied = !isBelow;
        assertion.classGap = below.kinds();
        return assertion;
    }

    /** {@code make}, called at most once: on the first {@code get}, and its value returned from then on. */
    private static <T> Supplier<T> lazy(Supplier<T> make) {
        return new Supplier<>() {
            private T value;

            @Override
            public T get() {
                if (value == null) {
                    value = make.get();
                }
                return value;
            }
        };
    }

    /** Group {@code accepted} by subject id in one pass over the list. Package-private: shared with
     * {@link BlindSpots} rather than duplicated. */
    static Map<String, List<JsonNode>> indexBySubject(List<JsonNode> accepted) {
        Map<String, List<JsonNode>> index = new java.util.HashMap<>();
        for (JsonNode e : accepted) {
            index.computeIfAbsent(subjectOf(e), k -> new ArrayList<>()).add(e);
        }
        return index;
    }

    /** The subject's held events plus the readable withheld ones (18.119). A withheld record whose id is taken
     * (a duplicate_id) joins under a fresh id, so it is judged as the record it is instead of merging with the
     * held one. */
    private static List<JsonNode> counterfactual(List<JsonNode> subjectEvents, List<JsonNode> readable) {
        Set<String> taken = new java.util.HashSet<>();
        for (JsonNode e : subjectEvents) {
            taken.add(Readiness.pyStr(e.get("id")));
        }
        List<JsonNode> events = new ArrayList<>(subjectEvents);
        for (int position = 0; position < readable.size(); position++) {
            JsonNode event = readable.get(position);
            String eventId = Readiness.pyStr(event.get("id"));
            if (taken.contains(eventId)) {
                event = ((ObjectNode) event).deepCopy().put("id", eventId + "#withheld-" + position);
            }
            taken.add(event.get("id").asText());
            events.add(event);
        }
        return events;
    }

    private static List<Assertions.EvidencePointer> withheldEvidence(List<Ingest.Withheld> withheld) {
        Map<String, Assertions.EvidencePointer> pointers = new java.util.HashMap<>();
        for (Ingest.Withheld w : withheld) {
            if (w.record().eventId == null || w.digest() == null) {
                continue;
            }
            String sc = w.event() != null ? sourceClassOf(w.event()) : "";
            Assertions.EvidencePointer pointer = new Assertions.EvidencePointer(
                    Iri.eventIri(w.record().eventId), "sha256:" + w.digest(), sc.isEmpty() ? "self_report" : sc);
            pointers.put(pointer.ref + "\u0000" + pointer.digest + "\u0000" + pointer.sourceClass, pointer);
        }
        List<String> keys = new ArrayList<>(pointers.keySet());
        keys.sort(Json::byteCompare);
        List<Assertions.EvidencePointer> out = new ArrayList<>();
        for (String key : keys.subList(0, Math.min(keys.size(), EVIDENCE_CAP))) {
            out.add(pointers.get(key));
        }
        return out;
    }

    /** The message key naming an insufficient_evidence outcome whose withheld records read non-conformant (18.149). */
    public static final String WITHHELD_KEY = "withheld_reads_non_conformant";

    /** A control quarantine may have flattered reads insufficient_evidence naming the withheld records (18.119).
     * {@code nonConformant} says judging the readable withheld records with the held ones reads non-conformant
     * (18.149). */
    private static Assertions.Assertion notEnoughWithWithheld(
            Assertions.Assertion assertion, List<Ingest.Withheld> withheld, boolean nonConformant) {
        Assertions.Assertion copy = Assertions.copyOf(assertion);
        copy.outcome = "insufficient_evidence";
        copy.population = new int[] {assertion.population[0], 0};
        copy.violations = new ArrayList<>();
        copy.evidence = withheldEvidence(withheld);
        copy.sourceClassSatisfied = null;
        copy.evidenceStrength = null;
        copy.withheldNonConformant = nonConformant;
        copy.classGap = List.of();
        return copy;
    }

    private static Map<String, JsonNode> eventsByIriOf(List<JsonNode> events) {
        Map<String, JsonNode> byIri = new java.util.HashMap<>();
        for (JsonNode event : events) {
            if (event.has("id")) {
                byIri.put(Iri.eventIri(event.get("id").asText()), event);
            }
        }
        return byIri;
    }

    /**
     * The families whose controls judge times (SPEC §6.6: INC, and REC retention, which no catalog has yet): a
     * time_suspect stream's records are left out of them.
     */
    private static final Set<String> TIMING_FAMILIES = Set.of("INC");

    /** What other subjects' records say of one subject's records: the records their writes marked untrusted or
     * rewrote with no ruling (18.120), and the record_ref and store of each of their MemoryWrites ingest could not
     * read (18.172). */
    private record Foreign(Set<String> untrusted, List<Ingest.UnreadableWrite> unreadable) {
        GraphStore graph(List<JsonNode> events, DomainBinding domain) {
            return Graph.buildGraph(events, domain, untrusted, unreadable);
        }
    }

    /** One capped reading of a subject's records, its graph and judge built on first use. */
    private static final class View {
        final List<JsonNode> events;
        final Supplier<GraphStore> store;
        final Supplier<Map<String, JsonNode>> byIri;
        final Supplier<SourceClass.Judge> judge;

        View(List<JsonNode> events, DomainBinding domain, Foreign foreign) {
            this.events = events;
            this.store = lazy(() -> foreign.graph(events, domain));
            this.byIri = lazy(() -> eventsByIriOf(events));
            this.judge = lazy(() -> new SourceClass.Judge(store.get(), byIri.get()));
        }
    }

    /**
     * The integrity caps of SPEC §6.6 and §8.1 over one subject's records, as ceilings on an outcome read from them
     * as declared. A pass must also hold with every record on a broken stream left out, and in a timing control every
     * time_suspect record (else insufficient_evidence), and then with every enforcement-point record on a weak stream
     * left out too (else partial, weak_integrity). Only a pass is capped: a violation is never changed.
     */
    private static final class CappedViews {
        final boolean any;
        private final List<JsonNode> events;
        private final DomainBinding domain;
        private final Foreign foreign;
        private final Set<String> broken;
        private final Set<String> weak;
        private final Set<String> suspect;
        private final Map<String, View> views = new java.util.HashMap<>();

        CappedViews(List<JsonNode> events, Integrity.Caps caps, DomainBinding domain, Foreign foreign) {
            this.events = events;
            this.domain = domain;
            this.foreign = foreign;
            Set<String> ids = new java.util.HashSet<>();
            for (JsonNode e : events) {
                ids.add(Readiness.pyStr(e.get("id")));
            }
            this.broken = mine(caps.broken(), ids);
            this.weak = mine(caps.weak(), ids);
            this.suspect = mine(caps.timeSuspect(), ids);
            this.any = !broken.isEmpty() || !weak.isEmpty() || !suspect.isEmpty();
        }

        private static Set<String> mine(Set<String> capped, Set<String> ids) {
            Set<String> out = new java.util.HashSet<>(capped);
            out.retainAll(ids);
            return out;
        }

        private View view(boolean withWeak, boolean timing) {
            return views.computeIfAbsent(withWeak + "|" + timing, key -> {
                Set<String> dropped = new java.util.HashSet<>(broken);
                if (withWeak) {
                    dropped.addAll(weak);
                }
                if (timing) {
                    dropped.addAll(suspect);
                }
                List<JsonNode> kept = new ArrayList<>();
                for (JsonNode e : events) {
                    if (!dropped.contains(Readiness.pyStr(e.get("id")))) {
                        kept.add(e);
                    }
                }
                return new View(kept, domain, foreign);
            });
        }

        Assertions.Assertion ceiling(
                Assertions.Assertion assertion, java.util.function.Function<View, Assertions.Assertion> judge) {
            boolean timing = !suspect.isEmpty() && TIMING_FAMILIES.contains(assertion.family);
            if ((!broken.isEmpty() || timing) && !"conformant".equals(judge.apply(view(false, timing)).outcome)) {
                Assertions.Assertion copy = Assertions.copyOf(assertion);
                copy.outcome = "insufficient_evidence";
                copy.population = new int[] {assertion.population[0], 0};
                copy.violations = new ArrayList<>();
                copy.sourceClassSatisfied = false;
                copy.evidenceStrength = null;
                return copy;
            }
            if (!weak.isEmpty() && !"conformant".equals(judge.apply(view(true, timing)).outcome)) {
                Assertions.Assertion copy = Assertions.copyOf(assertion);
                copy.outcome = "partial";
                return copy;
            }
            return assertion;
        }
    }

    /**
     * Evaluate every catalog control against every subject and return the assertions. {@code withheld} is
     * required, so every caller decides what ingest withheld; it is ingest's quarantined records that may hold evidence (18.119): quarantine never gives a better outcome. A
     * control that reads conformant, or not_applicable for want of a population, reads insufficient_evidence
     * naming them when a record ingest could not read may belong to the subject, or when judging the subject's
     * readable withheld records with its held ones changes that outcome.
     */
    public static List<Assertions.Assertion> assessSubjects(
            List<JsonNode> accepted,
            Profile profile,
            List<Catalog> catalogs,
            DomainBinding domain,
            List<Ingest.Withheld> withheld,
            Integrity.Caps integrity,
            JsonNode coverage) {
        List<Assertions.Assertion> assertions = new ArrayList<>();
        Map<String, List<JsonNode>> eventsBySubject = indexBySubject(accepted);
        // A record another subject's write marked untrusted, held or withheld, is never ruled on for this one (18.120);
        // such a write with no readable record_ref, under any subject, counts against its store's records, as an
        // unreadable write does (18.202).
        List<JsonNode> flagSources = new ArrayList<>(accepted);
        for (Ingest.Withheld w : withheld) {
            if (w.event() != null) {
                flagSources.add(w.event());
            }
        }
        Map<String, Set<String>> flagged = Graph.flaggedRecords(flagSources);
        List<Ingest.UnreadableWrite> unkeyed = Graph.unkeyedFlags(flagSources);
        for (Profile.Subject subject : profile.subjects) {
            List<JsonNode> subjectEvents = eventsBySubject.getOrDefault(subject.id, java.util.List.of());
            Set<String> foreignUntrusted = new LinkedHashSet<>();
            flagged.forEach((record, by) -> {
                if (by.stream().anyMatch(s -> !s.equals(subject.id))) {
                    foreignUntrusted.add(record);
                }
            });
            List<Ingest.UnreadableWrite> foreignUnreadable = new ArrayList<>();
            for (Ingest.Withheld w : withheld) {
                if (w.write() != null && w.subject() != null && !w.subject().equals(subject.id)) {
                    foreignUnreadable.add(w.write());
                }
            }
            foreignUnreadable.addAll(unkeyed);
            Foreign foreign = new Foreign(foreignUntrusted, foreignUnreadable);
            GraphStore store = foreign.graph(subjectEvents, domain);
            Map<String, JsonNode> eventsByIri = eventsByIriOf(subjectEvents);
            Set<String> roles = new LinkedHashSet<>(Applicability.effectiveRoles(subject.role));
            String[] win = window(profile, subjectEvents);
            List<Ingest.Withheld> own = new ArrayList<>();
            List<JsonNode> readable = new ArrayList<>();
            boolean unreadable = false;
            for (Ingest.Withheld w : withheld) {
                if (w.subject() != null && !w.subject().equals(subject.id)) {
                    continue;
                }
                own.add(w);
                // A readable record always names its subject (the schema requires it), so it is this subject's.
                if (w.event() == null) {
                    unreadable = true;
                } else {
                    readable.add(w.event());
                }
            }
            // The comparison runs whenever a readable record is withheld, even beside an unreadable one: the
            // unreadable record already decides the outcome, but the exit code still needs what the readable ones
            // show (18.149).
            List<JsonNode> cfEvents = readable.isEmpty() ? List.of() : counterfactual(subjectEvents, readable);
            GraphStore cfStore = cfEvents.isEmpty()
                    ? store
                    : foreign.graph(cfEvents, domain);
            Map<String, JsonNode> cfByIri = eventsByIriOf(cfEvents);
            // Each graph's judge is built once, on the first control whose minimum it may fall below.
            Supplier<SourceClass.Judge> judge = lazy(() -> new SourceClass.Judge(store, eventsByIri));
            Supplier<SourceClass.Judge> cfJudge = lazy(() -> new SourceClass.Judge(cfStore, cfByIri));
            CappedViews capped = new CappedViews(subjectEvents, integrity, domain, foreign);
            int first = assertions.size();
            List<Boolean> roleAppliesAt = new ArrayList<>();
            for (Catalog catalog : catalogs) {
                for (Catalog.ControlSpec control : catalog.controls) {
                    roleAppliesAt.add(roleApplies(roles, control.appliesToRoles));
                    Assertions.Assertion assertion =
                            assertControl(store, catalog, control, subject, roles, subjectEvents, eventsByIri, win, judge);
                    if (capped.any && "conformant".equals(assertion.outcome)) {
                        assertion = capped.ceiling(assertion, view -> assertControl(
                                view.store.get(), catalog, control, subject, roles, view.events, view.byIri.get(), win,
                                view.judge));
                    }
                    boolean flattered = !own.isEmpty()
                            && ("conformant".equals(assertion.outcome)
                                    || ("not_applicable".equals(assertion.outcome)
                                            && roleApplies(roles, control.appliesToRoles)));
                    // Held records that read insufficient_evidence may lack only what a late record supplies, so a
                    // readable withheld record is judged with them too: it can still read non-conformant (18.149).
                    boolean isShort = !readable.isEmpty() && "insufficient_evidence".equals(assertion.outcome);
                    if (!(flattered || isShort)) {
                        assertions.add(assertion);
                        continue;
                    }
                    String withWithheld = readable.isEmpty()
                            ? assertion.outcome
                            : assertControl(cfStore, catalog, control, subject, roles, cfEvents, cfByIri, win, cfJudge)
                                    .outcome;
                    if ((unreadable && flattered) || !assertion.outcome.equals(withWithheld)) {
                        assertion = notEnoughWithWithheld(assertion, own, "non-conformant".equals(withWithheld));
                    }
                    assertions.add(assertion);
                }
            }
            if (coverageUnknown(coverage, subject.id)) {
                for (int i = first; i < assertions.size(); i++) {
                    if (roleAppliesAt.get(i - first)) {
                        assertions.set(i, uncovered(assertions.get(i)));
                    }
                }
            }
        }
        return assertions;
    }

    /** The families whose controls read insufficient_evidence while a subject's coverage is unknown (SPEC §6.5). */
    static final Set<String> COVERAGE_FAMILIES = Set.of("INT", "REC");

    /**
     * True when {@code coverage} (computeCoverage's structure) does not show the subject's capture reconciled against
     * an independent denominator: its roll-up is unknown, or the subject has no entry at all (SPEC §6.5).
     */
    static boolean coverageUnknown(JsonNode coverage, String subject) {
        JsonNode subjects = coverage == null ? null : coverage.get("subjects");
        JsonNode entry = subjects != null && subjects.isObject() ? subjects.get(subject) : null;
        if (entry == null || !entry.isObject()) {
            return true;
        }
        JsonNode status = entry.get("coverage_status");
        return status != null && "unknown".equals(status.asText());
    }

    /**
     * An INT or REC outcome that reads better than insufficient_evidence while coverage is unknown reads
     * insufficient_evidence (SPEC §6.5): a call that bypassed capture would not be in the records. A violation stays.
     */
    static Assertions.Assertion uncovered(Assertions.Assertion assertion) {
        if (!COVERAGE_FAMILIES.contains(assertion.family)
                || !Set.of("conformant", "partial", "not_applicable").contains(assertion.outcome)) {
            return assertion;
        }
        Assertions.Assertion copy = Assertions.copyOf(assertion);
        copy.outcome = "insufficient_evidence";
        copy.population = new int[] {assertion.population[0], 0};
        copy.violations = new ArrayList<>();
        copy.sourceClassSatisfied = null;
        copy.evidenceStrength = null;
        copy.coverageUnknown = true;
        return copy;
    }

    /** Index a deviation register by its {@code control} field ({@code assess.deviations_by_control}).
     * Shared by every reader of an already-loaded register ({@link #applyDeviations}, the expired-entry
     * limitation text, {@link Report#renderOscal}, {@link AuditorView#computeAuditorView}) so the keying
     * rule lives in one place; a later entry for the same control replaces an earlier one, as a Python
     * dict comprehension does. */
    static Map<String, JsonNode> deviationsByControl(List<JsonNode> deviations) {
        Map<String, JsonNode> byControl = new LinkedHashMap<>();
        if (deviations != null) {
            for (JsonNode entry : deviations) {
                byControl.put(Readiness.pyStr(entry.get("control")), entry);
            }
        }
        return byControl;
    }

    /** {@link #applyDeviations}' result: the new assertion list, the control ids whose entry had expired by
     * {@code asOf}, and the control ids whose entry the SPEC §10.4 human-actor rule kept from applying, each in
     * assertion order. */
    record Applied(List<Assertions.Assertion> assertions, List<String> expired, List<String> notWaivable) {}

    /** Apply an already-linted deviation register to {@code assertions} (SPEC §13.3.4 Stage 2; a port of
     * {@code assess.apply_deviations}): every {@code non-conformant} assertion whose control has a
     * matching, unexpired entry becomes {@code partial} and carries that control's id as {@code
     * deviation}. Pure: never mutates its inputs and never reads a clock ({@code asOf} is the caller's
     * window end). An entry whose {@code expiry} is before {@code asOf} is never applied; its control id
     * is returned in {@code expired} so the caller reports it as a limitation. An entry is never applied to an
     * assertion that rests on a record failing the SPEC §10.4 human-actor rule, expired or not: "the deviation
     * register cannot waive this rule"; its control is returned in {@code notWaivable} (18.186). */
    static Applied applyDeviations(List<Assertions.Assertion> assertions, List<JsonNode> deviations, String asOf) {
        Map<String, JsonNode> byControl = deviationsByControl(deviations);
        Readiness.CalendarDate asOfDate = Readiness.parseDate(asOf);
        List<Assertions.Assertion> applied = new ArrayList<>();
        List<String> expired = new ArrayList<>();
        List<String> notWaivable = new ArrayList<>();
        for (Assertions.Assertion a : assertions) {
            JsonNode entry = "non-conformant".equals(a.outcome) ? byControl.get(a.control) : null;
            if (entry == null) {
                applied.add(a);
                continue;
            }
            if (a.humanActorFailed) {
                notWaivable.add(a.control);
                applied.add(a);
                continue;
            }
            JsonNode expiryNode = entry.get("expiry");
            Readiness.CalendarDate expiry =
                    expiryNode != null && expiryNode.isTextual() ? Readiness.parseDate(expiryNode.textValue()) : null;
            if (expiry != null && asOfDate != null && Readiness.compareDate(expiry, asOfDate) < 0) {
                expired.add(a.control);
                applied.add(a);
                continue;
            }
            Assertions.Assertion copy = Assertions.copyOf(a);
            copy.outcome = "partial";
            copy.deviation = a.control;
            applied.add(copy);
        }
        return new Applied(applied, expired, notWaivable);
    }

    /** Python's {@code str(entry.get(field, ""))}: an absent field is the empty string, any present
     * value (an explicit null too) is spelled as Python's {@code str()} spells it. */
    static String fieldStr(JsonNode entry, String field) {
        JsonNode value = entry.get(field);
        return value == null ? "" : Readiness.pyStr(value);
    }
}
