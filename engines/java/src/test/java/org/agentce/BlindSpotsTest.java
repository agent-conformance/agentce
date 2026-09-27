package org.agentce;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.util.ArrayList;
import java.util.List;
import org.junit.jupiter.api.Test;

/**
 * {@code computeBlindSpots}: the one missing record type that would unlock the most checks (18.5,
 * Hill 2). A faithful port of the Python reference's {@code tests/test_blind_spots.py}: the same
 * grouping, normalization, rung, and sort-order cases, so all three engines are proven against the
 * same rules (RFC 0008 is the operative spec).
 */
class BlindSpotsTest {
    private static final String SUBJECT = "spiffe://corp/agents/a";

    private static Catalog.ControlSpec control(String id, String version, List<JsonNode> minimumEvidence) {
        Catalog.ControlSpec c = new Catalog.ControlSpec();
        c.id = id;
        c.version = version;
        c.title = id;
        c.appliesToRoles = List.of("both");
        c.mode = "automated";
        c.rung = 2;
        c.severity = "medium";
        c.minSourceClass = "any";
        c.minimumEvidence = minimumEvidence;
        c.tolerance = Json.parse("{\"kind\":\"count\",\"max\":0}");
        return c;
    }

    private static JsonNode requirement(String event, String cls) {
        return Json.parse("{\"event\":\"" + event + "\",\"class\":\"" + cls + "\"}");
    }

    private static Profile oneSubjectProfile() {
        return Profile.fromDict(Json.parse("{\"subjects\":[{\"id\":\"" + SUBJECT + "\",\"role\":\"both\"}]}"));
    }

    private static Assertions.Assertion assertion(
            Catalog.ControlSpec control, String outcome, int applicable, int failed, String subject) {
        Assertions.Assertion a = new Assertions.Assertion();
        a.control = control.id;
        a.controlVersion = control.version;
        a.subject = subject;
        a.outcome = outcome;
        a.rung = 2;
        a.mode = "automated";
        a.window = new String[] {"1970-01-01T00:00:00Z", "1970-01-01T00:00:00Z"};
        a.population = new int[] {applicable, failed};
        a.severity = "medium";
        a.family = control.id.split("-", 2)[0];
        return a;
    }

    private static Assertions.Assertion assertion(Catalog.ControlSpec control, String outcome, int applicable, int failed) {
        return assertion(control, outcome, applicable, failed, SUBJECT);
    }

    private static JsonNode event(String eventType, String sourceClass) {
        return Json.parse(
                "{\"id\":\"e-" + eventType + "-" + sourceClass + "\",\"subject\":\"" + SUBJECT
                        + "\",\"agentcesourceclass\":\"" + sourceClass + "\",\"data\":{\"@type\":\"" + eventType + "\"}}");
    }

    @Test
    void aRunWithZeroInsufficientEvidenceAssertionsWritesTheHonestEmptyAnswer() {
        Catalog.ControlSpec c = control("A-01", "1", List.of(requirement("ModelCall", "self_report")));
        Catalog cat = Catalog.forTest("c", "2026.09", List.of(c));
        Profile profile = oneSubjectProfile();
        Assertions.Assertion a = assertion(c, "conformant", 1, 0);
        ObjectNode result = BlindSpots.computeBlindSpots(List.of(a), profile, List.of(cat), List.of());
        assertEquals(0, result.get("blind_spots").size());
        assertEquals(0, result.get("no_population").size());
    }

    @Test
    void anyAndSelfReportRequirementsNormalizeIntoOneBlindSpotNeverTwo() {
        Catalog.ControlSpec c1 = control("A-01", "1", List.of(requirement("Decision", "any")));
        Catalog.ControlSpec c2 = control("A-02", "1", List.of(requirement("Decision", "self_report")));
        Catalog cat = Catalog.forTest("c", "2026.09", List.of(c1, c2));
        Profile profile = oneSubjectProfile();
        Assertions.Assertion a1 = assertion(c1, "insufficient_evidence", 1, 0);
        Assertions.Assertion a2 = assertion(c2, "insufficient_evidence", 1, 0);
        ObjectNode result = BlindSpots.computeBlindSpots(List.of(a1, a2), profile, List.of(cat), List.of());
        assertEquals(1, result.get("blind_spots").size());
        JsonNode bs = result.get("blind_spots").get(0);
        assertEquals("self_report", bs.get("class").asText());
        assertEquals(2, bs.get("checks_unlocked").asInt());
    }

    @Test
    void anAssertionMissingTwoDistinctRequirementsGetsNoPartialCredit() {
        Catalog.ControlSpec c = control(
                "A-01", "1",
                List.of(requirement("ModelCall", "self_report"), requirement("ApprovalDecided", "enforcement_point")));
        Catalog cat = Catalog.forTest("c", "2026.09", List.of(c));
        Profile profile = oneSubjectProfile();
        Assertions.Assertion a = assertion(c, "insufficient_evidence", 1, 0);
        ObjectNode result = BlindSpots.computeBlindSpots(List.of(a), profile, List.of(cat), List.of());
        assertEquals(2, result.get("blind_spots").size());
        for (JsonNode bs : result.get("blind_spots")) {
            assertEquals(0, bs.get("checks_unlocked").asInt());
            assertEquals(1, bs.get("needed_by").asInt());
        }
    }

    @Test
    void anEmptyPopulationAssertionWithExactlyOneMissingRequirementIsNeededByNeverUnlocked() {
        Catalog.ControlSpec c = control("A-01", "1", List.of(requirement("ModelCall", "self_report")));
        Catalog cat = Catalog.forTest("c", "2026.09", List.of(c));
        Profile profile = oneSubjectProfile();
        Assertions.Assertion a = assertion(c, "insufficient_evidence", 0, 0);
        ObjectNode result = BlindSpots.computeBlindSpots(List.of(a), profile, List.of(cat), List.of());
        assertEquals(1, result.get("blind_spots").size());
        assertEquals(0, result.get("blind_spots").get(0).get("checks_unlocked").asInt());
        assertEquals(1, result.get("blind_spots").get(0).get("needed_by").asInt());
    }

    @Test
    void aMinimumEvidenceAssertionWithOneMissingRequirementAndNonEmptyPopulationUnlocksIt() {
        Catalog.ControlSpec c = control("A-01", "1", List.of(requirement("ModelCall", "self_report")));
        Catalog cat = Catalog.forTest("c", "2026.09", List.of(c));
        Profile profile = oneSubjectProfile();
        Assertions.Assertion a = assertion(c, "insufficient_evidence", 1, 0);
        ObjectNode result = BlindSpots.computeBlindSpots(List.of(a), profile, List.of(cat), List.of());
        assertEquals(1, result.get("blind_spots").get(0).get("checks_unlocked").asInt());
        assertEquals(0, result.get("blind_spots").get(0).get("needed_by").asInt());
    }

    @Test
    void anInsufficientEvidenceAssertionWhoseEveryRequirementIsAlreadyMetGoesToNoPopulation() {
        Catalog.ControlSpec c = control("A-01", "1", List.of(requirement("ModelCall", "self_report")));
        Catalog cat = Catalog.forTest("c", "2026.09", List.of(c));
        Profile profile = oneSubjectProfile();
        Assertions.Assertion a = assertion(c, "insufficient_evidence", 0, 0);
        List<JsonNode> events = List.of(event("ModelCall", "self_report"));
        ObjectNode result = BlindSpots.computeBlindSpots(List.of(a), profile, List.of(cat), events);
        assertEquals(0, result.get("blind_spots").size());
        assertEquals(1, result.get("no_population").size());
        JsonNode entry = result.get("no_population").get(0);
        assertEquals("A-01", entry.get("control").asText());
        assertEquals(SUBJECT, entry.get("subject").asText());
    }

    @Test
    void ladderRungIndependentSystemIsRung4RegardlessOfAnyAdapter() {
        Catalog.ControlSpec c = control("A-01", "1", List.of(requirement("BundleLoaded", "independent_system")));
        Catalog cat = Catalog.forTest("c", "2026.09", List.of(c));
        Profile profile = oneSubjectProfile();
        Assertions.Assertion a = assertion(c, "insufficient_evidence", 1, 0);
        ObjectNode result = BlindSpots.computeBlindSpots(List.of(a), profile, List.of(cat), List.of());
        assertEquals(4, result.get("blind_spots").get(0).get("ladder_rung").asInt());
        assertEquals("ticketing_or_iam", result.get("blind_spots").get(0).get("owner_key").asText());
    }

    @Test
    void ladderRungAnExactEnforcementPointRequirementIsRung3() {
        Catalog.ControlSpec c = control("A-01", "1", List.of(requirement("ToolCall", "enforcement_point")));
        Catalog cat = Catalog.forTest("c", "2026.09", List.of(c));
        Profile profile = oneSubjectProfile();
        Assertions.Assertion a = assertion(c, "insufficient_evidence", 1, 0);
        ObjectNode result = BlindSpots.computeBlindSpots(List.of(a), profile, List.of(cat), List.of());
        JsonNode bs = result.get("blind_spots").get(0);
        assertEquals(3, bs.get("ladder_rung").asInt());
        assertEquals("platform_or_security", bs.get("owner_key").asText());
        List<String> adapters = new ArrayList<>();
        bs.get("supplying_adapters").forEach(n -> adapters.add(n.asText()));
        assertFalse(adapters.contains("otel-genai"));
        assertTrue(adapters.contains("mcp-gateway"));
    }

    @Test
    void ladderRungAnEventOnlyEnforcementPointAdaptersSupplyIsRung3EvenForASelfReportKey() {
        Catalog.ControlSpec c = control("A-01", "1", List.of(requirement("AuthzCheck", "any")));
        Catalog cat = Catalog.forTest("c", "2026.09", List.of(c));
        Profile profile = oneSubjectProfile();
        Assertions.Assertion a = assertion(c, "insufficient_evidence", 1, 0);
        ObjectNode result = BlindSpots.computeBlindSpots(List.of(a), profile, List.of(cat), List.of());
        assertEquals(3, result.get("blind_spots").get(0).get("ladder_rung").asInt());
    }

    @Test
    void ladderRungAnOpenTelemetryShapedSelfReportEventIsRung1() {
        Catalog.ControlSpec c = control("A-01", "1", List.of(requirement("ToolCall", "self_report")));
        Catalog cat = Catalog.forTest("c", "2026.09", List.of(c));
        Profile profile = oneSubjectProfile();
        Assertions.Assertion a = assertion(c, "insufficient_evidence", 1, 0);
        ObjectNode result = BlindSpots.computeBlindSpots(List.of(a), profile, List.of(cat), List.of());
        assertEquals(1, result.get("blind_spots").get(0).get("ladder_rung").asInt());
        assertEquals("agent_team", result.get("blind_spots").get(0).get("owner_key").asText());
    }

    @Test
    void ladderRungANonOpenTelemetrySelfReportEventIsRung2() {
        Catalog.ControlSpec c = control("A-01", "1", List.of(requirement("ApprovalDecided", "self_report")));
        Catalog cat = Catalog.forTest("c", "2026.09", List.of(c));
        Profile profile = oneSubjectProfile();
        Assertions.Assertion a = assertion(c, "insufficient_evidence", 1, 0);
        ObjectNode result = BlindSpots.computeBlindSpots(List.of(a), profile, List.of(cat), List.of());
        assertEquals(2, result.get("blind_spots").get(0).get("ladder_rung").asInt());
    }

    @Test
    void ladderRungAnEventNoAdapterDeclaresAtAllIsRung2NotAVacuousRung3() {
        Catalog.ControlSpec c = control("A-01", "1", List.of(requirement("Decision", "self_report")));
        Catalog cat = Catalog.forTest("c", "2026.09", List.of(c));
        Profile profile = oneSubjectProfile();
        Assertions.Assertion a = assertion(c, "insufficient_evidence", 1, 0);
        ObjectNode result = BlindSpots.computeBlindSpots(List.of(a), profile, List.of(cat), List.of());
        JsonNode bs = result.get("blind_spots").get(0);
        assertEquals(2, bs.get("ladder_rung").asInt());
        assertEquals(0, bs.get("supplying_adapters").size());
    }

    @Test
    void crossKeyNonGoalARung3BlindSpotsChecksUnlockedNeverAbsorbsASeparateRung1BlindSpotsCheck() {
        Catalog.ControlSpec rung1 = control("A-01", "1", List.of(requirement("ToolCall", "any")));
        Catalog.ControlSpec rung3 = control("A-02", "1", List.of(requirement("ToolCall", "enforcement_point")));
        Catalog cat = Catalog.forTest("c", "2026.09", List.of(rung1, rung3));
        Profile profile = oneSubjectProfile();
        Assertions.Assertion a1 = assertion(rung1, "insufficient_evidence", 1, 0);
        Assertions.Assertion a2 = assertion(rung3, "insufficient_evidence", 1, 0);
        ObjectNode result = BlindSpots.computeBlindSpots(List.of(a1, a2), profile, List.of(cat), List.of());
        assertEquals(2, result.get("blind_spots").size());
        for (JsonNode bs : result.get("blind_spots")) {
            assertEquals(1, bs.get("checks_unlocked").asInt());
        }
    }

    @Test
    void twoBlindSpotsWithEqualCountsSortByLadderRungEventClassNotDiscoveryOrder() {
        Catalog.ControlSpec c1 = control("A-01", "1", List.of(requirement("ResourceAccess", "self_report")));
        Catalog.ControlSpec c2 = control("A-02", "1", List.of(requirement("ModelCall", "self_report")));
        Catalog cat = Catalog.forTest("c", "2026.09", List.of(c1, c2));
        Profile profile = oneSubjectProfile();
        Assertions.Assertion a1 = assertion(c1, "insufficient_evidence", 1, 0);
        Assertions.Assertion a2 = assertion(c2, "insufficient_evidence", 1, 0);
        ObjectNode result = BlindSpots.computeBlindSpots(List.of(a1, a2), profile, List.of(cat), List.of());
        assertEquals("ModelCall", result.get("blind_spots").get(0).get("event").asText());
        assertEquals("ResourceAccess", result.get("blind_spots").get(1).get("event").asText());
    }

    @Test
    void aBlindSpotUnlockingMoreChecksRanksFirstEvenWhenDiscoveredLater() {
        // The primary sort key is real, not an accident of discovery order: unlike the tie-broken
        // test above, these two groups differ on checks_unlocked, and the higher-unlocking group
        // (BEvent) is discovered *after* the lower one (AEvent) -- so a missing or discovery-order
        // ranking would put AEvent first, and only an actual sort by checks_unlocked puts BEvent
        // first as it must.
        Catalog.ControlSpec low = control("C-22", "1", List.of(requirement("AEvent", "any")));
        Catalog.ControlSpec high1 = control("C-23", "1", List.of(requirement("BEvent", "any")));
        Catalog.ControlSpec high2 = control("C-24", "1", List.of(requirement("BEvent", "any")));
        Catalog cat = Catalog.forTest("c", "2026.09", List.of(low, high1, high2));
        Profile profile = oneSubjectProfile();
        Assertions.Assertion aLow = assertion(low, "insufficient_evidence", 1, 0);
        Assertions.Assertion aHigh1 = assertion(high1, "insufficient_evidence", 1, 0);
        Assertions.Assertion aHigh2 = assertion(high2, "insufficient_evidence", 1, 0);
        ObjectNode result =
                BlindSpots.computeBlindSpots(List.of(aLow, aHigh1, aHigh2), profile, List.of(cat), List.of());
        assertEquals(2, result.get("blind_spots").size());
        assertEquals("BEvent", result.get("blind_spots").get(0).get("event").asText());
        assertEquals(2, result.get("blind_spots").get(0).get("checks_unlocked").asInt());
        assertEquals("AEvent", result.get("blind_spots").get(1).get("event").asText());
        assertEquals(1, result.get("blind_spots").get(1).get("checks_unlocked").asInt());
    }

    @Test
    void twoCatalogsSharingAControlIdAndVersionNeverCollide() {
        Catalog.ControlSpec c = control("SHARED-01", "1", List.of(requirement("ModelCall", "self_report")));
        Catalog catA = Catalog.forTest("cat-a", "2026.09", List.of(c));
        Catalog catB = Catalog.forTest("cat-b", "2026.09", List.of(c));
        Profile profile = oneSubjectProfile();
        Assertions.Assertion aA = assertion(c, "insufficient_evidence", 1, 0);
        Assertions.Assertion aB = assertion(c, "conformant", 1, 0);
        ObjectNode result = BlindSpots.computeBlindSpots(List.of(aA, aB), profile, List.of(catA, catB), List.of());
        assertEquals(1, result.get("blind_spots").size());
        assertEquals(1, result.get("blind_spots").get(0).get("checks_unlocked").asInt());
        assertEquals(
                "cat-a", result.get("blind_spots").get(0).get("unlocked_checks").get(0).get("catalog").asText());
    }

    @Test
    void aLengthMismatchBetweenAssertionsAndReplayedTriplesIsRefusedLoudly() {
        Catalog.ControlSpec c = control("A-01", "1", List.of(requirement("ModelCall", "self_report")));
        Catalog cat = Catalog.forTest("c", "2026.09", List.of(c));
        Profile profile = oneSubjectProfile();
        assertThrows(
                IllegalArgumentException.class,
                () -> BlindSpots.computeBlindSpots(List.of(), profile, List.of(cat), List.of()));
    }

    @Test
    void aPositionalMismatchBetweenAssertionsAndTheReplayedSequenceIsRefusedLoudly() {
        Catalog.ControlSpec c = control("A-01", "1", List.of(requirement("ModelCall", "self_report")));
        Catalog cat = Catalog.forTest("c", "2026.09", List.of(c));
        Profile profile = oneSubjectProfile();
        Assertions.Assertion wrong =
                assertion(c, "insufficient_evidence", 1, 0, "spiffe://corp/agents/other");
        assertThrows(
                IllegalArgumentException.class,
                () -> BlindSpots.computeBlindSpots(List.of(wrong), profile, List.of(cat), List.of()));
    }

    @Test
    void repeatedCallsWithTheSameInputAreDeterministic() {
        Catalog.ControlSpec c = control("A-01", "1", List.of(requirement("ModelCall", "self_report")));
        Catalog cat = Catalog.forTest("c", "2026.09", List.of(c));
        Profile profile = oneSubjectProfile();
        Assertions.Assertion a = assertion(c, "insufficient_evidence", 1, 0);
        ObjectNode first = BlindSpots.computeBlindSpots(List.of(a), profile, List.of(cat), List.of());
        ObjectNode second = BlindSpots.computeBlindSpots(List.of(a), profile, List.of(cat), List.of());
        assertEquals(first, second);
    }
}
