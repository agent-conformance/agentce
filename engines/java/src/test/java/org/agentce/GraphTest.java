package org.agentce;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.fasterxml.jackson.databind.JsonNode;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.ArrayList;
import java.util.List;
import org.junit.jupiter.api.Test;

/**
 * The graph builder is byte-identical to the reference (SPEC §6.3). {@code graph-golden.txt} is the
 * reference engine's full triple dump for {@code graph-fixture.json}; the Java builder must reproduce
 * every materialised glue edge, principal HMAC, and closure pair.
 */
class GraphTest {

    private static List<JsonNode> events(JsonNode fixture) {
        List<JsonNode> out = new ArrayList<>();
        fixture.get("events").forEach(out::add);
        return out;
    }

    private static final JsonNode CREDIT_DOMAIN = Json.parse(
            "{\"decision_types\":[{\"id\":\"agentce:CreditDecision\","
                    + "\"subclass_of\":\"agentce:ConsequentialDecision\",\"consequential\":true}]}");

    private static JsonNode decisionEvent(String id) {
        return Json.parse(
                "{\"id\":\"" + id + "\",\"time\":\"2026-01-01T00:00:00Z\",\"agentcesourceclass\":\"self_report\","
                        + "\"data\":{\"@type\":\"Decision\",\"decision_type\":\"agentce:CreditDecision\"}}");
    }

    private static JsonNode noticeEvent(String id, String decisionId, String origin) {
        String refs = origin == null
                ? "{\"decision\":\"agentce:event/" + decisionId + "\"}"
                : "{\"decision\":\"agentce:event/" + decisionId + "\",\"origin\":\"agentce:event/" + origin + "\"}";
        return Json.parse(
                "{\"id\":\"" + id + "\",\"time\":\"2026-01-01T00:00:00Z\",\"agentcesourceclass\":\"independent_system\","
                        + "\"data\":{\"@type\":\"Notice\",\"refs\":" + refs + "}}");
    }

    @Test
    void graphTriplesMatchTheReferenceGolden() throws IOException {
        JsonNode fixture = Json.parseFile(TestPaths.testData().resolve("graph-fixture.json"));
        String golden = Files.readString(TestPaths.testData().resolve("graph-golden.txt"), StandardCharsets.UTF_8);
        GraphStore store = Graph.buildGraph(events(fixture), DomainBinding.fromDict(fixture.get("domain")));
        assertEquals(golden, String.join("\n", store.dumpTriples()) + "\n");
    }

    @Test
    void graphMaterialisesTheHumanChainTerminusAndPrecedence() {
        JsonNode fixture = Json.parseFile(TestPaths.testData().resolve("graph-fixture.json"));
        GraphStore store = Graph.buildGraph(events(fixture), DomainBinding.fromDict(fixture.get("domain")));
        assertEquals("agentce:event/dec-1", store.objects("agentce:event/dec-2", "agentce:precededBy").get(0));
        String terminus = store.objects("agentce:event/dec-1", "agentce:chainTerminus").get(0);
        assertTrue(store.isA(terminus, "agentce:HumanPrincipal"));
    }

    /**
     * TRN-03 (SPEC §7.6, round-2 critic finding 4): the decision's own evidence chain resolves
     * cleanly, but the Notice that notified it carries a dangling {@code refs.*} value of its own
     * (here {@code refs.origin}, a real {@code Refs} key per
     * {@code spec/model/agentce-evidence.linkml.yaml} -- verifier round 1 found the prior fixture
     * used {@code refs.explanation_ref}, a key {@code Refs} does not define, so a real adapter could
     * never produce it) -- {@code dangling} lands that literal on the NOTICE node, not the Decision
     * node, so {@code explanationReconstructable} must check the notice's dangling status too.
     */
    @Test
    void explanationIsNotReconstructableWhenTheNoticeItselfDangles() {
        List<JsonNode> events = List.of(decisionEvent("d1"), noticeEvent("c1", "d1", "missing-origin"));
        GraphStore store = Graph.buildGraph(events, DomainBinding.fromDict(CREDIT_DOMAIN));
        assertEquals(List.of("agentce:event/missing-origin"), store.literalValues("agentce:event/c1", "agentce:danglingRef"));
        assertEquals(List.of(), store.literalValues("agentce:event/d1", "agentce:danglingRef"));
        assertEquals(List.of("false"), store.literalValues("agentce:event/d1", "agentce:explanationReconstructable"));
    }

    /**
     * Verifier-found regression: a decision notified by more than one Notice (one clean, one with
     * its own dangling ref) used to read {@code explanationReconstructable} from whichever Notice
     * was mapped LAST, so the same evidence gave a different verdict depending only on event order.
     * Reconstructable means at least one notifying Notice is clean -- true in both orderings.
     */
    @Test
    void explanationReconstructableIsIndependentOfNoticeOrder() {
        JsonNode decision = decisionEvent("d1");
        JsonNode cleanNotice = noticeEvent("c1", "d1", null);
        JsonNode danglingNotice = noticeEvent("c2", "d1", "missing-origin");

        GraphStore cleanLast = Graph.buildGraph(
                List.of(decision, danglingNotice, cleanNotice), DomainBinding.fromDict(CREDIT_DOMAIN));
        GraphStore danglingLast = Graph.buildGraph(
                List.of(decision, cleanNotice, danglingNotice), DomainBinding.fromDict(CREDIT_DOMAIN));

        assertEquals(List.of("true"), cleanLast.literalValues("agentce:event/d1", "agentce:explanationReconstructable"));
        assertEquals(List.of("true"), danglingLast.literalValues("agentce:event/d1", "agentce:explanationReconstructable"));
    }

    private static JsonNode event(String id, String ptype, String dataJson) {
        String body = dataJson.isEmpty() ? "" : ", " + dataJson;
        return Json.parse("{\"id\": \"" + id + "\", \"time\": \"2026-01-01T00:00:00Z\", "
                + "\"agentcesourceclass\": \"enforcement_point\", \"data\": {\"@type\": \"" + ptype + "\"" + body + "}}");
    }

    private static JsonNode decisionWith(String id, String dataJson) {
        return event(id, "Decision", "\"decision_type\": \"agentce:CreditDecision\", " + dataJson);
    }

    private static List<String> robust(List<JsonNode> events, String decisionId) {
        GraphStore store = Graph.buildGraph(events, DomainBinding.fromDict(CREDIT_DOMAIN));
        return store.literalValues("agentce:event/" + decisionId, "agentce:robustToUntrustedContent");
    }

    @Test
    void rob02GuardMarksAndConsumedReadsTaintADecision() {
        JsonNode dec = decisionWith("d1", "\"inputs\": [\"mem:r1\"]");
        for (String marks : List.of(
                "\"trust\": \"untrusted\"",
                "\"trust\": \"quarantined\"",
                "\"trust\": \"trusted\", \"guard_verdict\": \"quarantine\"",
                "\"guard_verdict\": \"block\"")) {
            JsonNode write = event("w1", "MemoryWrite", "\"record_ref\": \"mem:r1\", " + marks);
            assertEquals(List.of("false"), robust(List.of(write, dec), "d1"), marks);
        }
        JsonNode trusted = event("w1", "MemoryWrite", "\"record_ref\": \"mem:r1\", \"guard_verdict\": \"sanitize\"");
        assertEquals(List.of("true"), robust(List.of(trusted, dec), "d1"));
        assertEquals(List.of("true"), robust(List.of(decisionEvent("d1")), "d1"));
        JsonNode read = event("m1", "MemoryRead",
                "\"trust_min\": \"untrusted\", \"refs\": {\"consumer\": \"agentce:event/d1\"}");
        assertEquals(List.of("false"), robust(List.of(read, decisionEvent("d1")), "d1"));
    }

    @Test
    void rob02TaintFollowsContentThroughAnyNumberOfHopsInAnyOrder() {
        List<JsonNode> events = new java.util.ArrayList<>(List.of(
                event("w1", "MemoryWrite", "\"record_ref\": \"mem:r1\", \"trust\": \"untrusted\""),
                event("tc1", "ToolCall", "\"used\": [\"mem:r1\"], \"result_ref\": \"content:t1\""),
                event("mc1", "ModelCall", "\"input_ref\": \"content:t1\", \"output_ref\": \"content:o1\""),
                decisionWith("d0", "\"inputs\": [\"content:o1\"]"),
                decisionWith("d1", "\"inputs\": [\"agentce:event/d0\"]")));
        assertEquals(List.of("false"), robust(events, "d0"));
        assertEquals(List.of("false"), robust(events, "d1"));
        java.util.Collections.reverse(events);
        assertEquals(List.of("false"), robust(events, "d1"));
    }

    @Test
    void rob02OriginTaintedWriteAndDerivedInstructionTaintADecision() {
        JsonNode read = event("m1", "MemoryRead",
                "\"trust_min\": \"untrusted\", \"refs\": {\"consumer\": \"agentce:event/mc1\"}");
        JsonNode viaOrigin = decisionWith("d1", "\"refs\": {\"origin\": \"agentce:event/mc1\"}");
        assertEquals(List.of("false"), robust(List.of(read, event("mc1", "ModelCall", ""), viaOrigin), "d1"));
        JsonNode bad = event("w1", "MemoryWrite", "\"record_ref\": \"mem:r1\", \"trust\": \"untrusted\"");
        JsonNode good = event("w2", "MemoryWrite", "\"record_ref\": \"mem:r1\", \"trust\": \"trusted\"");
        JsonNode viaWrite = decisionWith("d1", "\"inputs\": [\"agentce:event/w2\"]");
        assertEquals(List.of("false"), robust(List.of(bad, good, viaWrite), "d1"));
        JsonNode origin = event("i1", "Instruction", "\"source_class\": \"tool_output\"");
        JsonNode derived = event("i2", "Instruction",
                "\"source_class\": \"user\", \"refs\": {\"parent\": \"agentce:event/i1\"}");
        JsonNode acting = decisionWith("d1", "\"refs\": {\"instruction\": \"agentce:event/i2\"}");
        assertEquals(List.of("false"), robust(List.of(origin, derived, acting), "d1"));
    }

    @Test
    void rob02ACycleOfInputsEndsAndStaysTrustedWithoutASource() {
        JsonNode first = decisionWith("d1", "\"inputs\": [\"agentce:event/d2\"]");
        JsonNode second = decisionWith("d2", "\"inputs\": [\"agentce:event/d1\"]");
        assertEquals(List.of("true"), robust(List.of(first, second), "d1"));
    }
}
