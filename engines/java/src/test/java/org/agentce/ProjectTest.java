package org.agentce;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import org.junit.jupiter.api.Test;

/**
 * {@link Project#computeProjectView}: the per-agent rollup and undeclared-agents list (18.14, Hill 7).
 *
 * <p>A faithful port of the Python reference's {@code tests/test_project.py}: same fixtures, same
 * assertions, so all three engines are proven against the same cases.
 */
class ProjectTest {

    private static final String[] WINDOW = {"2026-01-01T00:00:00Z", "2026-02-01T00:00:00Z"};

    private static Assertions.Assertion assertion(String subject, String control, String outcome) {
        Assertions.Assertion a = new Assertions.Assertion();
        a.control = control;
        a.controlVersion = "2026.09";
        a.subject = subject;
        a.outcome = outcome;
        a.rung = 2;
        a.mode = "automated";
        a.window = WINDOW;
        a.population = new int[] {1, 0};
        a.severity = "high";
        a.family = "REC";
        return a;
    }

    private static ObjectNode checkRef(String subject, String control) {
        ObjectNode node = Json.nodes().objectNode();
        node.put("subject", subject);
        node.put("catalog", "cat");
        node.put("control", control);
        node.put("control_version", "2026.09");
        return node;
    }

    private static ObjectNode blindSpot(List<ObjectNode> unlocked, List<ObjectNode> needed, String event) {
        ObjectNode node = Json.nodes().objectNode();
        node.put("event", event);
        node.put("class", "any");
        node.put("ladder_rung", 2);
        node.put("owner_key", "agent_team");
        node.put("step_kind", "code_change");
        node.putArray("supplying_adapters");
        node.put("checks_unlocked", unlocked.size());
        ArrayNode unlockedArr = node.putArray("unlocked_checks");
        unlocked.forEach(unlockedArr::add);
        node.put("needed_by", needed.size());
        ArrayNode neededArr = node.putArray("needed_by_checks");
        needed.forEach(neededArr::add);
        return node;
    }

    private static ObjectNode blindSpotsWrapper(ObjectNode... entries) {
        ObjectNode node = Json.nodes().objectNode();
        ArrayNode arr = node.putArray("blind_spots");
        for (ObjectNode e : entries) {
            arr.add(e);
        }
        node.putArray("no_population");
        return node;
    }

    private static ObjectNode activity(String... agents) {
        ObjectNode node = Json.nodes().objectNode();
        ArrayNode arr = node.putArray("agents");
        for (String a : agents) {
            arr.add(a);
        }
        return node;
    }

    private static List<String> texts(JsonNode arr) {
        List<String> out = new ArrayList<>();
        arr.forEach(n -> out.add(n.asText()));
        return out;
    }

    private static Profile profileWith(String... subjectIds) {
        Profile profile = new Profile();
        for (String id : subjectIds) {
            Profile.Subject subject = new Profile.Subject();
            subject.id = id;
            profile.subjects.add(subject);
        }
        return profile;
    }

    @Test
    void blindSpotsBySubjectRescopesCountsAndRefs() {
        ObjectNode entry = blindSpot(
                List.of(checkRef("A", "REC-01"), checkRef("A", "REC-02"), checkRef("B", "REC-03")), List.of(), "Decision");
        Map<String, List<ObjectNode>> bySubject = Project.blindSpotsBySubject(blindSpotsWrapper(entry));
        assertEquals(List.of("REC-01", "REC-02"), controlNames(bySubject.get("A").get(0).get("unlocked_checks")));
        assertEquals(2, bySubject.get("A").get(0).get("checks_unlocked").asInt());
        assertEquals(List.of("REC-03"), controlNames(bySubject.get("B").get(0).get("unlocked_checks")));
        assertEquals(1, bySubject.get("B").get(0).get("checks_unlocked").asInt());
        assertEquals(3, entry.get("checks_unlocked").asInt());
        assertFalse(bySubject.get("A").get(0).get("checks_unlocked").asInt() == entry.get("checks_unlocked").asInt());
        assertFalse(bySubject.get("B").get(0).get("checks_unlocked").asInt() == entry.get("checks_unlocked").asInt());
    }

    private static List<String> controlNames(JsonNode arr) {
        List<String> out = new ArrayList<>();
        arr.forEach(n -> out.add(n.get("control").asText()));
        return out;
    }

    @Test
    void blindSpotsBySubjectDropsEntriesThatDoNotTouchASubject() {
        ObjectNode entry = blindSpot(List.of(checkRef("A", "REC-01")), List.of(), "Decision");
        Map<String, List<ObjectNode>> bySubject = Project.blindSpotsBySubject(blindSpotsWrapper(entry));
        assertFalse(bySubject.containsKey("B"));
    }

    @Test
    void noPopulationBySubjectGroupsByOwnField() {
        ArrayNode noPopulation = Json.nodes().arrayNode();
        noPopulation.add(checkRef("A", "REC-04"));
        noPopulation.add(checkRef("B", "REC-05"));
        noPopulation.add(checkRef("A", "REC-06"));
        Map<String, List<ObjectNode>> grouped = Project.noPopulationBySubject(noPopulation);
        assertEquals(List.of("REC-04", "REC-06"), controlNames2(grouped.get("A")));
        assertEquals(List.of("REC-05"), controlNames2(grouped.get("B")));
    }

    private static List<String> controlNames2(List<ObjectNode> entries) {
        List<String> out = new ArrayList<>();
        entries.forEach(n -> out.add(n.get("control").asText()));
        return out;
    }

    @Test
    void threeSubjectRollupDeclaredUndeclaredAndTopGaps() {
        // A: declared, has events, no undeclared agents. B: declared, no events at all (still a row).
        // C: NOT declared -- discovered only via an event's data.agent.id.
        Profile profile = profileWith("A", "B");
        List<Assertions.Assertion> assertions = List.of(
                assertion("A", "REC-01", "conformant"), assertion("C", "REC-01", "insufficient_evidence"));
        Set<String> declaredSubjectIds = new LinkedHashSet<>(List.of("A", "B"));
        Map<String, ObjectNode> activityBySubject = new LinkedHashMap<>();
        activityBySubject.put("A", activity("A"));
        activityBySubject.put("B", activity());
        activityBySubject.put("C", activity("C"));
        ObjectNode sharedGap =
                blindSpot(List.of(checkRef("A", "REC-01")), List.of(checkRef("C", "REC-01")), "ModelCall");
        ObjectNode otherGap = blindSpot(List.of(checkRef("B", "REC-02")), List.of(), "ToolCall");
        ObjectNode blindSpots = blindSpotsWrapper(sharedGap, otherGap);

        ObjectNode view = Project.computeProjectView(assertions, profile, declaredSubjectIds, activityBySubject, blindSpots);

        Map<String, JsonNode> rows = new LinkedHashMap<>();
        view.get("agents").forEach(row -> rows.put(row.get("id").asText(), row));
        assertEquals(Set.of("A", "B", "C"), rows.keySet());
        assertTrue(rows.get("A").get("declared").asBoolean());
        assertTrue(rows.get("B").get("declared").asBoolean());
        assertFalse(rows.get("C").get("declared").asBoolean());
        assertEquals(1, rows.get("C").get("blind_spots_count").asInt());
        assertEquals(List.of("C"), texts(view.get("undeclared_agents")));

        assertEquals(2, view.get("top_gaps").size());
        assertEquals(List.of("A", "C"), texts(view.get("top_gaps").get(0).get("agents")));
        assertEquals(List.of("B"), texts(view.get("top_gaps").get(1).get("agents")));
        // top_gaps reuses computeBlindSpots' own global entries, not per-subject-filtered copies.
        assertEquals(sharedGap.get("checks_unlocked").asInt(), view.get("top_gaps").get(0).get("checks_unlocked").asInt());
    }

    @Test
    void singleSubjectProfileStillReturnsAValidOneRowView() {
        Profile profile = profileWith("A");
        List<Assertions.Assertion> assertions = List.of(assertion("A", "REC-01", "conformant"));
        Map<String, ObjectNode> activityBySubject = new LinkedHashMap<>();
        activityBySubject.put("A", activity("A"));
        ObjectNode blindSpots = blindSpotsWrapper();

        ObjectNode view = Project.computeProjectView(
                assertions, profile, new LinkedHashSet<>(List.of("A")), activityBySubject, blindSpots);

        assertEquals(1, view.get("agents").size());
        JsonNode row = view.get("agents").get(0);
        assertEquals("A", row.get("id").asText());
        assertTrue(row.get("declared").asBoolean());
        assertEquals(0, row.get("blind_spots_count").asInt());
        assertEquals(List.of("A"), texts(row.get("agents_observed")));
        assertEquals(List.of(), texts(view.get("undeclared_agents")));
        assertEquals(0, view.get("top_gaps").size());
    }
}
