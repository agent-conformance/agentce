package org.agentce;

import com.fasterxml.jackson.databind.JsonNode;
import java.util.ArrayList;
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

    private static boolean classOk(String observed, String required) {
        return required.equals("any") || required.equals("self_report") || observed.equals(required);
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

    private static boolean hasMinimumEvidence(List<JsonNode> events, List<JsonNode> minimum) {
        for (JsonNode requirement : minimum) {
            String wantType = requirement.has("event") ? requirement.get("event").asText() : "";
            String requiredClass = requirement.has("class") ? requirement.get("class").asText() : "any";
            boolean found = false;
            for (JsonNode e : events) {
                if (eventType(e).equals(wantType) && classOk(sourceClassOf(e), requiredClass)) {
                    found = true;
                    break;
                }
            }
            if (!found) {
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

    private static Assertions.Assertion assertControl(
            GraphStore store,
            Catalog catalog,
            Catalog.ControlSpec control,
            Profile.Subject subject,
            Set<String> roles,
            List<JsonNode> events,
            Map<String, JsonNode> eventsByIri,
            String[] win) {
        if (!roleApplies(roles, control.appliesToRoles)) {
            return Assertions.make(control.id, control.version, subject.id, "not_applicable", control.rung, control.mode, win, new int[] {0, 0});
        }

        Psp.Shape shape = Catalog.shapeFor(catalog, control);
        if (control.rung != 2 || shape == null) {
            return Assertions.make(control.id, control.version, subject.id, "not_assessed", control.rung, control.mode, win, new int[] {0, 0});
        }

        Structural.ShapeResult result = Structural.evaluateShape(store, shape, catalog.shapes, control.id);
        if (result.applicable.isEmpty()) {
            return Assertions.make(control.id, control.version, subject.id, "not_applicable", control.rung, control.mode, win, new int[] {0, 0});
        }
        if (!hasMinimumEvidence(events, control.minimumEvidence)) {
            return Assertions.make(control.id, control.version, subject.id, "insufficient_evidence", control.rung, control.mode, win, new int[] {result.applicable.size(), 0});
        }

        boolean conformant = Structural.withinTolerance(result.applicable.size(), result.failing.size(), control.tolerance);
        List<String> focusForEvidence;
        if (!result.failing.isEmpty()) {
            focusForEvidence = new ArrayList<>(result.failing);
            focusForEvidence.sort(Json::byteCompare);
        } else {
            focusForEvidence = result.applicable;
        }
        Assertions.Assertion assertion = Assertions.make(
                control.id, control.version, subject.id, conformant ? "conformant" : "non-conformant",
                control.rung, control.mode, win, new int[] {result.applicable.size(), result.failing.size()});
        for (Structural.Violation v : result.violations) {
            assertion.violations.add(Structural.violationToJson(v));
        }
        assertion.evidence = evidence(eventsByIri, focusForEvidence);
        assertion.sourceClassSatisfied = Boolean.TRUE;
        JsonNode strength = control.raw.get("evidence_strength");
        assertion.evidenceStrength = strength != null && strength.isTextual() ? strength.textValue() : null;
        return assertion;
    }

    /** Group {@code accepted} by subject id in one pass over the list. */
    private static Map<String, List<JsonNode>> indexBySubject(List<JsonNode> accepted) {
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
}
