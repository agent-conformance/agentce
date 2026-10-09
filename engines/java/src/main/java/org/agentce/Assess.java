package org.agentce;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

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
            String sc = sourceClassOf(event);
            pointers.add(new Assertions.EvidencePointer(
                    focus, "sha256:" + Canonical.sha256Hex(event), sc.isEmpty() ? "self_report" : sc));
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
            String[] win) {
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
        List<String> focusForEvidence;
        if (!result.failing.isEmpty()) {
            focusForEvidence = new ArrayList<>(result.failing);
            focusForEvidence.sort(Json::byteCompare);
        } else {
            focusForEvidence = result.applicable;
        }
        Assertions.Assertion assertion = Assertions.make(
                control.id, control.version, subject.id, conformant ? "conformant" : "non-conformant",
                control.rung, control.mode, win, new int[] {result.applicable.size(), result.failing.size()},
                control.severity, familyOf(control.id));
        for (Structural.Violation v : result.violations) {
            assertion.violations.add(Structural.violationToJson(v));
        }
        assertion.evidence = evidence(eventsByIri, focusForEvidence);
        assertion.crosswalk = crosswalk;
        assertion.sourceClassSatisfied = Boolean.TRUE;
        JsonNode strength = control.raw.get("evidence_strength");
        assertion.evidenceStrength = strength != null && strength.isTextual() ? strength.textValue() : null;
        return assertion;
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

    /** Evaluate every catalog control against every subject and return the assertions. */
    public static List<Assertions.Assertion> assessSubjects(
            List<JsonNode> accepted, Profile profile, List<Catalog> catalogs, DomainBinding domain) {
        List<Assertions.Assertion> assertions = new ArrayList<>();
        Map<String, List<JsonNode>> eventsBySubject = indexBySubject(accepted);
        for (Profile.Subject subject : profile.subjects) {
            List<JsonNode> subjectEvents = eventsBySubject.getOrDefault(subject.id, java.util.List.of());
            GraphStore store = Graph.buildGraph(subjectEvents, domain);
            Map<String, JsonNode> eventsByIri = new java.util.HashMap<>();
            for (JsonNode event : subjectEvents) {
                if (event.has("id")) {
                    eventsByIri.put(Iri.eventIri(event.get("id").asText()), event);
                }
            }
            Set<String> roles = new LinkedHashSet<>(Applicability.effectiveRoles(subject.role));
            String[] win = window(profile, subjectEvents);
            for (Catalog catalog : catalogs) {
                for (Catalog.ControlSpec control : catalog.controls) {
                    assertions.add(assertControl(store, catalog, control, subject, roles, subjectEvents, eventsByIri, win));
                }
            }
        }
        return assertions;
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

    /** {@link #applyDeviations}' result: the new assertion list and the control ids whose entry had
     * expired by {@code asOf}, in assertion order. */
    record Applied(List<Assertions.Assertion> assertions, List<String> expired) {}

    /** Apply an already-linted deviation register to {@code assertions} (SPEC §13.3.4 Stage 2; a port of
     * {@code assess.apply_deviations}): every {@code non-conformant} assertion whose control has a
     * matching, unexpired entry becomes {@code partial} and carries that control's id as {@code
     * deviation}. Pure: never mutates its inputs and never reads a clock ({@code asOf} is the caller's
     * window end). An entry whose {@code expiry} is before {@code asOf} is never applied; its control id
     * is returned in {@code expired} so the caller reports it as a limitation. */
    static Applied applyDeviations(List<Assertions.Assertion> assertions, List<JsonNode> deviations, String asOf) {
        Map<String, JsonNode> byControl = deviationsByControl(deviations);
        Readiness.CalendarDate asOfDate = Readiness.parseDate(asOf);
        List<Assertions.Assertion> applied = new ArrayList<>();
        List<String> expired = new ArrayList<>();
        for (Assertions.Assertion a : assertions) {
            JsonNode entry = "non-conformant".equals(a.outcome) ? byControl.get(a.control) : null;
            if (entry == null) {
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
        return new Applied(applied, expired);
    }

    /** Python's {@code str(entry.get(field, ""))}: an absent field is the empty string, any present
     * value (an explicit null too) is spelled as Python's {@code str()} spells it. */
    static String fieldStr(JsonNode entry, String field) {
        JsonNode value = entry.get(field);
        return value == null ? "" : Readiness.pyStr(value);
    }
}
