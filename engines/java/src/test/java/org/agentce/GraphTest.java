package org.agentce;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
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
        List<JsonNode> events = new ArrayList<>(List.of(
                event("w1", "MemoryWrite", "\"record_ref\": \"mem:r1\", \"trust\": \"untrusted\""),
                event("tc1", "ToolCall", "\"used\": [\"mem:r1\"], \"result_ref\": \"content:t1\""),
                event("mc1", "ModelCall", "\"input_ref\": \"content:t1\", \"output_ref\": \"content:o1\""),
                decisionWith("d0", "\"inputs\": [\"content:o1\"]"),
                decisionWith("d1", "\"inputs\": [\"agentce:event/d0\"]")));
        assertEquals(List.of("false"), robust(events, "d0"));
        assertEquals(List.of("false"), robust(events, "d1"));
        Collections.reverse(events);
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

    private static List<String> ruled(List<JsonNode> events) {
        GraphStore store = Graph.buildGraph(events, DomainBinding.fromDict(CREDIT_DOMAIN));
        return store.literalValues("agentce:event/d1", "agentce:untrustedContentRuledOn");
    }

    private static JsonNode selfReport(JsonNode event) {
        ((com.fasterxml.jackson.databind.node.ObjectNode) event).put("agentcesourceclass", "self_report");
        return event;
    }

    @Test
    void rob02RuledOnWhenTheGuardRuledOnEveryInputAndNotWhenThereWereNone() {
        JsonNode write = event("w1", "MemoryWrite", "\"record_ref\": \"mem:r1\", \"trust\": \"trusted\"");
        assertEquals(List.of("true"), ruled(List.of(write, decisionWith("d1", "\"inputs\": [\"mem:r1\"]"))));
        // A decision nothing recorded flows into used nothing the guard could rule on (ruling 15, 18.127).
        assertEquals(List.of("false"), ruled(List.of(decisionEvent("d1"))));
    }

    @Test
    void rob02NotRuledOnThroughAReadOnlyTheAgentReported() {
        JsonNode write = event("w1", "MemoryWrite", "\"record_ref\": \"mem:r1\", \"trust\": \"trusted\"");
        JsonNode read = selfReport(event("m1", "MemoryRead",
                "\"record_refs\": [\"mem:r1\"], \"refs\": {\"consumer\": \"agentce:event/d1\"}"));
        assertEquals(List.of("false"), ruled(List.of(write, read, decisionEvent("d1"))));
        JsonNode guardRead = event("m2", "MemoryRead", "\"record_refs\": [\"mem:r1\"]");
        assertEquals(List.of("false"), ruled(List.of(write, guardRead, read, decisionEvent("d1"))));
    }

    @Test
    void rob02NotRuledOnARecordNoEnforcementPointCovers() {
        JsonNode unguarded = selfReport(
                event("w1", "MemoryWrite", "\"record_ref\": \"mem:r1\", \"trust\": \"trusted\""));
        JsonNode used = decisionWith("d1", "\"inputs\": [\"mem:r1\"]");
        assertEquals(List.of("false"), ruled(List.of(unguarded, used)));
        JsonNode silent = event("w2", "MemoryWrite", "\"record_ref\": \"mem:r1\"");
        assertEquals(List.of("false"), ruled(List.of(silent, used)));
        // An enforcement-point read covers the record only when it filtered by trust.
        JsonNode guardRead = event("m1", "MemoryRead", "\"record_refs\": [\"mem:r1\"]");
        assertEquals(List.of("false"), ruled(List.of(unguarded, guardRead, used)));
        JsonNode filtered =
                event("m1", "MemoryRead", "\"record_refs\": [\"mem:r1\"], \"trust_min\": \"trusted\"");
        assertEquals(List.of("true"), ruled(List.of(unguarded, filtered, used)));
    }

    @Test
    void rob02AnUnruledRewriteOfAGuardedRecordIsNotRuledOn() {
        JsonNode guarded = event("w1", "MemoryWrite", "\"record_ref\": \"mem:r1\", \"trust\": \"trusted\"");
        JsonNode used = decisionWith("d1", "\"inputs\": [\"mem:r1\"]");
        JsonNode filtered =
                event("m1", "MemoryRead", "\"record_refs\": [\"mem:r1\"], \"trust_min\": \"trusted\"");
        for (JsonNode rewrite : List.of(
                selfReport(event("w2", "MemoryWrite", "\"record_ref\": \"mem:r1\"")),
                selfReport(event("w2", "MemoryWrite", "\"record_ref\": \"mem:r1\", \"trust\": \"trusted\"")),
                event("w2", "MemoryWrite", "\"record_ref\": \"mem:r1\""))) {
            assertEquals(List.of("false"), ruled(List.of(guarded, rewrite, used)));
            assertEquals(List.of("false"), ruled(List.of(rewrite, used, guarded)));
            assertEquals(List.of("true"), ruled(List.of(guarded, rewrite, filtered, used)));
        }
        JsonNode again = event("w2", "MemoryWrite", "\"record_ref\": \"mem:r1\", \"guard_verdict\": \"allow\"");
        assertEquals(List.of("true"), ruled(List.of(guarded, again, used)));
    }

    @Test
    void rob02ARecordAnotherSubjectFlaggedIsNeverRuledOn() {
        JsonNode write = event("w1", "MemoryWrite", "\"record_ref\": \"mem:r1\", \"trust\": \"trusted\"");
        JsonNode filtered = event("m1", "MemoryRead", "\"record_refs\": [\"mem:r1\"], \"trust_min\": \"trusted\", "
                + "\"refs\": {\"consumer\": \"agentce:event/d1\"}");
        List<JsonNode> events = List.of(write, filtered, decisionEvent("d1"));
        assertEquals(List.of("true"), ruled(events));
        for (String[] pair : new String[][] {{"mem:r1", "false"}, {"mem:r2", "true"}}) {
            GraphStore store = Graph.buildGraph(
                    events, DomainBinding.fromDict(CREDIT_DOMAIN), new GraphStore(), Iri.ZERO_KEY, Set.of(pair[0]));
            assertEquals(List.of(pair[1]), store.literalValues("agentce:event/d1", "agentce:untrustedContentRuledOn"));
            assertEquals(List.of("true"), store.literalValues("agentce:event/d1", "agentce:robustToUntrustedContent"));
        }
    }

    @Test
    void rob02AGuardReadRulesOnItsRecordsOnlyAtTrustMinTrusted() {
        // 18.172: a read that let untrusted or quarantined records through, or filtered by nothing, rules on nothing.
        JsonNode unguarded = selfReport(
                event("w1", "MemoryWrite", "\"record_ref\": \"mem:r1\", \"trust\": \"trusted\""));
        JsonNode used = decisionWith("d1", "\"inputs\": [\"mem:r1\"]");
        for (String[] pair : new String[][] {
            {"\"trusted\"", "true"}, {"\"untrusted\"", "false"}, {"\"quarantined\"", "false"}, {"null", "false"}
        }) {
            JsonNode read = event("m1", "MemoryRead", "\"record_refs\": [\"mem:r1\"], \"trust_min\": " + pair[0]);
            assertEquals(List.of(pair[1]), ruled(List.of(unguarded, read, used)), pair[0]);
            assertEquals(List.of(pair[1]), ruled(List.of(used, read, unguarded)), pair[0]);
        }
        // A ruled enforcement-point write still rules on its record whatever the read let through.
        JsonNode ruledWrite = event("w1", "MemoryWrite", "\"record_ref\": \"mem:r1\", \"trust\": \"trusted\"");
        JsonNode read = event("m1", "MemoryRead", "\"record_refs\": [\"mem:r1\"], \"trust_min\": \"untrusted\"");
        assertEquals(List.of("true"), ruled(List.of(ruledWrite, read, used)));
    }

    @Test
    void rob02ARecordAnotherSubjectsUnreadableWriteMayHaveChangedIsNotRuledOn() {
        // SPEC §7.4: the record it names; with no readable record_ref, every record of its store, a record no event
        // names a store for counting as one of them; with no readable store either, every record.
        JsonNode used = decisionWith("d1", "\"inputs\": [\"mem:r1\"]");
        Ingest.UnreadableWrite ref = new Ingest.UnreadableWrite("mem:r1", null);
        Ingest.UnreadableWrite refOther = new Ingest.UnreadableWrite("mem:r1", "other");
        Ingest.UnreadableWrite otherRef = new Ingest.UnreadableWrite("mem:r2", null);
        Ingest.UnreadableWrite kb = new Ingest.UnreadableWrite(null, "kb");
        Ingest.UnreadableWrite otherStore = new Ingest.UnreadableWrite(null, "other");
        Ingest.UnreadableWrite none = new Ingest.UnreadableWrite(null, null);
        for (String store : java.util.Arrays.asList("\"kb\"", "null")) {
            JsonNode write = event("w1", "MemoryWrite",
                    "\"record_ref\": \"mem:r1\", \"trust\": \"trusted\", \"store\": " + store);
            List<JsonNode> events = List.of(write, used);
            Map<List<Ingest.UnreadableWrite>, String> cases = new LinkedHashMap<>();
            cases.put(List.of(), "true");
            cases.put(List.of(ref), "false");
            cases.put(List.of(refOther), "false");
            cases.put(List.of(otherRef), "true");
            cases.put(List.of(kb), "false");
            cases.put(List.of(otherStore), store.equals("null") ? "false" : "true");
            cases.put(List.of(none), "false");
            cases.put(List.of(otherRef, none), "false");
            for (Map.Entry<List<Ingest.UnreadableWrite>, String> c : cases.entrySet()) {
                List<JsonNode> reversed = new ArrayList<>(events);
                Collections.reverse(reversed);
                for (List<JsonNode> order : List.of(events, reversed)) {
                    GraphStore graph = Graph.buildGraph(
                            order, DomainBinding.fromDict(CREDIT_DOMAIN), Set.of(), c.getKey());
                    String label = store + " " + c.getKey();
                    assertEquals(List.of(c.getValue()),
                            graph.literalValues("agentce:event/d1", "agentce:untrustedContentRuledOn"), label);
                    // Nobody could read that content, so it is unruled, never untrusted.
                    assertEquals(List.of("true"),
                            graph.literalValues("agentce:event/d1", "agentce:robustToUntrustedContent"), label);
                }
            }
        }
    }

    @Test
    void rob02FlaggedRecordsNamesEverySubjectThatFlaggedARecord() {
        String[][] writes = {
            {"w1", "a", "\"trust\": \"untrusted\""},
            {"w2", "b", "\"guard_verdict\": \"block\""},
            {"w3", "c", "\"trust\": \"trusted\""},
            {"w4", "d", "\"provenance_origin_class\": \"tool_output\""},
            {"w5", "e", "\"provenance_origin_class\": \"tool_output\", \"trust\": \"trusted\""},
        };
        List<JsonNode> events = new ArrayList<>();
        for (String[] w : writes) {
            JsonNode e = event(w[0], "MemoryWrite", "\"record_ref\": \"mem:r1\", " + w[2]);
            ((ObjectNode) e).put("subject", w[1]);
            events.add(e);
        }
        events.add(event("m1", "MemoryRead", "\"record_refs\": [\"mem:r1\"], \"trust_min\": \"untrusted\""));
        JsonNode claimed = selfReport(event("w6", "MemoryWrite", "\"record_ref\": \"mem:r1\", \"trust\": \"trusted\""));
        ((ObjectNode) claimed).put("subject", "f");
        events.add(claimed);
        assertEquals(Map.of("mem:r1", Set.of("a", "b", "d", "f")), Graph.flaggedRecords(events));
        Collections.reverse(events);
        assertEquals(Map.of("mem:r1", Set.of("a", "b", "d", "f")), Graph.flaggedRecords(events));
    }

    @Test
    void rob02AFlaggedWriteWithNoReadableRecordRefIsUnkeyed() {
        // 18.202: a flagged write whose record_ref is absent, null or empty names no record; it counts against its
        // store.
        String[][] writes = {
            {"w1", "a", "\"store\": \"kb\", \"trust\": \"untrusted\""},
            {"w2", "b", "\"store\": \"kb\", \"trust\": \"untrusted\", \"record_ref\": null"},
            {"w3", "c", "\"store\": \"\", \"trust\": \"untrusted\", \"record_ref\": \"\""},
            {"w4", "d", "\"store\": \"kb\", \"trust\": \"untrusted\", \"record_ref\": \"mem:r1\""},
            {"w5", "e", "\"store\": \"kb\", \"trust\": \"trusted\", \"record_ref\": \"\""},
        };
        List<JsonNode> events = new ArrayList<>();
        for (String[] w : writes) {
            JsonNode e = event(w[0], "MemoryWrite", w[2]);
            ((ObjectNode) e).put("subject", w[1]);
            events.add(e);
        }
        assertEquals(Map.of("mem:r1", Set.of("d")), Graph.flaggedRecords(events));
        Ingest.UnreadableWrite kb = new Ingest.UnreadableWrite(null, "kb");
        assertEquals(List.of(kb, kb, new Ingest.UnreadableWrite(null, null)), Graph.unkeyedFlags(events));
    }

    @Test
    void rob02NotRuledOnARefTheBundleDoesNotHold() {
        for (String ref : List.of("mem:missing", "agentce:event/missing")) {
            assertEquals(List.of("false"), ruled(List.of(decisionWith("d1", "\"inputs\": [\"" + ref + "\"]"))), ref);
        }
        JsonNode other = decisionWith("d2", "\"inputs\": [\"mem:missing\"]");
        JsonNode write = event("w1", "MemoryWrite", "\"record_ref\": \"mem:r1\", \"trust\": \"trusted\"");
        JsonNode used = decisionWith("d1", "\"inputs\": [\"mem:r1\"]");
        assertEquals(List.of("true"), ruled(List.of(write, used, other)));
    }

    @Test
    void rob02RuledOnIsIndependentOfEventOrder() {
        JsonNode write = selfReport(event("w1", "MemoryWrite", "\"record_ref\": \"mem:r1\""));
        List<JsonNode> events = new ArrayList<>(List.of(write, decisionWith("d1", "\"inputs\": [\"mem:r1\"]")));
        assertEquals(List.of("false"), ruled(events));
        Collections.reverse(events);
        assertEquals(List.of("false"), ruled(events));
    }

    @Test
    void rob02AnUntrustedOriginClassWithNoRulingTaints() {
        JsonNode used = decisionWith("d1", "\"inputs\": [\"mem:r1\"]");
        for (List<String> row : List.of(List.of("tool_output", "false"), List.of("user", "true"))) {
            JsonNode write = event("w1", "MemoryWrite",
                    "\"record_ref\": \"mem:r1\", \"provenance_origin_class\": \"" + row.get(0) + "\"");
            assertEquals(List.of(row.get(1)), robust(List.of(write, used), "d1"), row.get(0));
        }
        JsonNode marked = event("w1", "MemoryWrite",
                "\"record_ref\": \"mem:r1\", \"provenance_origin_class\": \"tool_output\", \"trust\": \"trusted\"");
        assertEquals(List.of("true"), robust(List.of(marked, used), "d1"));
    }

    private static JsonNode instruction(String id, String sourceClass, String refsJson) {
        String refs = refsJson.isEmpty() ? "" : ", \"refs\": {" + refsJson + "}";
        return event(id, "Instruction", "\"source_class\": \"" + sourceClass + "\"" + refs);
    }

    private static JsonNode call(String id, String instruction) {
        return event(id, "ToolCall", "\"refs\": {\"instruction\": \"agentce:event/" + instruction + "\"}");
    }

    /** CND-05's flag on {@code acting}, a ToolCall or Decision that acts on an instruction. */
    private static List<String> acts(List<JsonNode> events, String acting) {
        GraphStore store = Graph.buildGraph(events, DomainBinding.fromDict(CREDIT_DOMAIN));
        return store.literalValues("agentce:event/" + acting, "agentce:actsOnUntrusted");
    }

    @Test
    void cnd05ReadsTheInstructionsOwnClass() {
        assertEquals(List.of("true"), acts(List.of(instruction("i1", "tool_output", ""), call("tc1", "i1")), "tc1"));
        assertEquals(List.of("false"), acts(List.of(instruction("i1", "user", ""), call("tc1", "i1")), "tc1"));
    }

    @Test
    void cnd05FollowsTheParentChainForAnyNumberOfHopsInAnyOrder() {
        for (String[] pair : List.of(new String[] {"retrieved", "true"}, new String[] {"service", "false"})) {
            List<JsonNode> events = new ArrayList<>(List.of(
                    instruction("i3", pair[0], ""),
                    instruction("i2", "operator", "\"parent\": \"agentce:event/i3\""),
                    instruction("i1", "user", "\"parent\": \"agentce:event/i2\""),
                    call("tc1", "i1")));
            assertEquals(List.of(pair[1]), acts(events, "tc1"), pair[0]);
            Collections.reverse(events);
            assertEquals(List.of(pair[1]), acts(events, "tc1"), pair[0]);
        }
    }

    @Test
    void cnd05AnOriginToolCallOrResourceAccessProducedUntrustedContent() {
        JsonNode origin = call("tc0", "i0");
        JsonNode access = event("ra0", "ResourceAccess", "\"operation\": \"read\"");
        for (String id : List.of("tc0", "ra0")) {
            List<JsonNode> events = List.of(instruction("i0", "user", ""), origin, access,
                    instruction("i1", "user", "\"origin\": \"agentce:event/" + id + "\""), call("tc1", "i1"));
            assertEquals(List.of("true"), acts(events, "tc1"), id);
        }
        assertEquals(List.of("false"), acts(List.of(instruction("i0", "user", ""), origin), "tc0"));
    }

    @Test
    void cnd05AnOriginReadTheGuardMarkedUntrustedTaintsTheChain() {
        JsonNode write = event("w1", "MemoryWrite", "\"record_ref\": \"mem:r1\", \"trust\": \"trusted\"");
        JsonNode derived = instruction("i1", "memory_trusted", "\"origin\": \"agentce:event/m1\"");
        for (String[] pair : List.of(new String[] {"untrusted", "true"}, new String[] {"trusted", "false"})) {
            JsonNode read = event("m1", "MemoryRead",
                    "\"record_refs\": [\"mem:r1\"], \"trust_min\": \"" + pair[0] + "\"");
            assertEquals(List.of(pair[1]), acts(List.of(write, read, derived, call("tc1", "i1")), "tc1"), pair[0]);
        }
        JsonNode bad = event("w1", "MemoryWrite", "\"record_ref\": \"mem:r1\", \"trust\": \"untrusted\"");
        JsonNode read = event("m1", "MemoryRead", "\"record_refs\": [\"mem:r1\"], \"trust_min\": \"trusted\"");
        assertEquals(List.of("true"), acts(List.of(bad, read, derived, call("tc1", "i1")), "tc1"));
    }

    @Test
    void cnd05EndsOnCyclesAndNeverWalksDownTheChain() {
        List<JsonNode> cycle = List.of(
                instruction("i1", "user", "\"parent\": \"agentce:event/i2\""),
                instruction("i2", "user", "\"parent\": \"agentce:event/i1\""),
                call("tc1", "i1"));
        assertEquals(List.of("false"), acts(cycle, "tc1"));
        List<JsonNode> child = List.of(
                instruction("i1", "user", ""),
                instruction("i2", "tool_output", "\"parent\": \"agentce:event/i1\""),
                call("tc1", "i1"));
        assertEquals(List.of("false"), acts(child, "tc1"));
    }

    @Test
    void cnd05FlagsADecisionActingOnADerivedUntrustedInstruction() {
        List<JsonNode> events = List.of(
                instruction("i0", "tool_output", ""),
                instruction("i1", "user", "\"parent\": \"agentce:event/i0\""),
                decisionWith("d1", "\"refs\": {\"instruction\": \"agentce:event/i1\"}"));
        assertEquals(List.of("true"), acts(events, "d1"));
    }

    /** CND-05's evidence flag on {@code acting}: whether the records show which instruction it acted on. */
    private static List<String> judged(List<JsonNode> events, String acting) {
        GraphStore store = Graph.buildGraph(events, DomainBinding.fromDict(CREDIT_DOMAIN));
        return store.literalValues("agentce:event/" + acting, "agentce:actsOnJudged");
    }

    @Test
    void cnd05AToolCallNamingNoInstructionIsNotJudged() {
        for (String refs : List.of("\"refs\": {}", "\"refs\": {\"instruction\": null}")) {
            List<JsonNode> events = List.of(instruction("i1", "user", ""), event("tc1", "ToolCall", refs));
            assertEquals(List.of("false"), judged(events, "tc1"), refs);
            assertEquals(List.of("false"), acts(events, "tc1"), refs);
        }
    }

    @Test
    void cnd05AToolCallOnAHeldTrustedInstructionIsJudged() {
        assertEquals(List.of("true"), judged(List.of(instruction("i1", "user", ""), call("tc1", "i1")), "tc1"));
    }

    @Test
    void cnd05ADecisionNamingNoInstructionIsJudged() {
        assertEquals(List.of("true"), judged(List.of(decisionEvent("d1")), "d1"));
    }

    @Test
    void cnd05AParentOrOriginTheBundleDoesNotHoldFailsClosed() {
        for (String key : List.of("parent", "origin")) {
            JsonNode derived = instruction("i1", "user", "\"" + key + "\": \"agentce:event/gone\"");
            assertEquals(List.of("true"), acts(List.of(derived, call("tc1", "i1")), "tc1"), key);
        }
    }

    @Test
    void cnd05AnInstructionThatDeclaresNoSourceClassFailsClosed() {
        JsonNode bare = event("i0", "Instruction", "");
        assertEquals(List.of("true"), acts(List.of(bare, call("tc1", "i0")), "tc1"));
        JsonNode derived = instruction("i1", "user", "\"parent\": \"agentce:event/i0\"");
        assertEquals(List.of("true"), acts(List.of(bare, derived, call("tc1", "i1")), "tc1"));
    }

    @Test
    void cnd05AnOriginReadTheGuardNeverRuledOnFailsClosed() {
        JsonNode derived = instruction("i1", "memory_trusted", "\"origin\": \"agentce:event/m1\"");
        JsonNode unruled = Json.parse("{\"id\": \"m1\", \"time\": \"2026-01-01T00:00:00Z\", "
                + "\"agentcesourceclass\": \"self_report\", "
                + "\"data\": {\"@type\": \"MemoryRead\", \"record_refs\": [\"mem:r9\"]}}");
        assertEquals(List.of("true"), acts(List.of(unruled, derived, call("tc1", "i1")), "tc1"));
        JsonNode ruled = event("m1", "MemoryRead", "\"record_refs\": [\"mem:r9\"], \"trust_min\": \"trusted\"");
        assertEquals(List.of("false"), acts(List.of(ruled, derived, call("tc1", "i1")), "tc1"));
    }

    /** An identity-provider SessionStart a session_ref can name, naming the actor its id names (SPEC §10.4). */
    private static JsonNode login(String id, String sourceClass, String agent) {
        return login(id, sourceClass, agent, ", \"principal\": {\"kind\": \"human\", \"id\": \""
                + id.substring("login-".length()) + "\"}");
    }

    /** An identity-provider SessionStart whose data ends with {@code principal} (empty: it names nobody). */
    private static JsonNode login(String id, String sourceClass, String agent, String principal) {
        return Json.parse("{\"id\": \"" + id + "\", \"time\": \"2026-01-01T00:00:00Z\", \"agentcesourceclass\": \""
                + sourceClass + "\", \"data\": {\"@type\": \"SessionStart\", \"agent\": {\"id\": \"" + agent + "\"}"
                + principal + "}}");
    }

    private static final String IDP = "urn:example:service:identity-provider";

    /** The identity-provider logins of the test reviewers alice and bob (login-alice, login-bob). */
    private static final List<JsonNode> LOGINS =
            List.of(login("login-alice", "independent_system", IDP), login("login-bob", "independent_system", IDP));

    /** A human actor with the session_ref of its login, login-&lt;id&gt;. */
    private static String human(String id) {
        return "\"actor\": {\"kind\": \"human\", \"id\": \"" + id + "\"}, \"session_ref\": \"agentce:event/login-" + id.strip()
                + "\"";
    }

    private static List<JsonNode> withLogins(List<JsonNode> events) {
        List<JsonNode> all = new ArrayList<>(events);
        all.addAll(LOGINS);
        return all;
    }

    private static List<String> flag(List<JsonNode> events, JsonNode domain, String node, String predicate) {
        GraphStore store = Graph.buildGraph(events, DomainBinding.fromDict(domain));
        return store.literalValues("agentce:event/" + node, "agentce:" + predicate);
    }

    private static List<String> flag(List<JsonNode> events, String node, String predicate) {
        return flag(events, CREDIT_DOMAIN, node, predicate);
    }

    private static JsonNode incident(String id, String dataJson) {
        return event(id, "Incident", dataJson);
    }

    private static String decisionRef(String id) {
        return "\"refs\": {\"decision\": \"agentce:event/" + id + "\"}";
    }

    @Test
    void inc03OnlyTheDecisionsAnIncidentNamesTriggerIt() {
        List<JsonNode> events = List.of(decisionEvent("d1"), decisionEvent("d2"),
                incident("x1", "\"related_refs\": [\"agentce:event/d1\"]"));
        assertEquals(List.of("true"), flag(events, "d1", "triggersIncident"));
        assertEquals(List.of("false"), flag(events, "d2", "triggersIncident"));
        List<JsonNode> byRef = List.of(decisionEvent("d1"), decisionEvent("d2"), incident("x1", decisionRef("d2")));
        assertEquals(List.of("false"), flag(byRef, "d1", "triggersIncident"));
        assertEquals(List.of("true"), flag(byRef, "d2", "triggersIncident"));
        assertEquals(List.of("false"), flag(List.of(decisionEvent("d1")), "d1", "triggersIncident"));
    }

    @Test
    void inc03AnUntracedIncidentHoldsEveryDecisionToTheRule() {
        List<JsonNode> unnamed = List.of(decisionEvent("d1"), decisionEvent("d2"), incident("x1", ""));
        assertEquals(List.of("true"), flag(unnamed, "d1", "triggersIncident"));
        assertEquals(List.of("true"), flag(unnamed, "d2", "triggersIncident"));
        List<JsonNode> unresolved = List.of(decisionEvent("d1"), decisionEvent("d2"),
                incident("x1", "\"related_refs\": [\"agentce:event/d1\", \"agentce:event/gone\"]"));
        assertEquals(List.of("true"), flag(unresolved, "d1", "triggersIncident"));
        assertEquals(List.of("true"), flag(unresolved, "d2", "triggersIncident"));
    }

    @Test
    void inc03ResolvesThroughTheNamedEventsDecision() {
        List<JsonNode> events = List.of(decisionEvent("d1"), decisionEvent("d2"),
                event("o1", "Override", decisionRef("d2")),
                incident("x1", "\"related_refs\": [\"agentce:event/o1\"]"));
        assertEquals(List.of("false"), flag(events, "d1", "triggersIncident"));
        assertEquals(List.of("true"), flag(events, "d2", "triggersIncident"));
    }

    private static JsonNode approval(String id, String decision, String humanId) {
        return event(id, "ApprovalDecided", decisionRef(decision) + ", \"outcome\": \"approve\", " + human(humanId));
    }

    /** A reject, an edit or no outcome is a review (agentce:reviewedBy; OVS-01, INC-03, RSK-02) but never an
     * approval (agentce:approvedBy; CND-02, OVS-08). */
    @Test
    void onlyAnApproveOutcomeApproves() {
        for (String outcome : new String[] {"\"approve\"", "\"reject\"", "\"edit\"", null}) {
            String data = decisionRef("d1") + (outcome == null ? "" : ", \"outcome\": " + outcome) + ", " + human("alice");
            List<JsonNode> events = withLogins(List.of(decisionEvent("d1"), event("a1", "ApprovalDecided", data)));
            GraphStore store = Graph.buildGraph(events, DomainBinding.fromDict(CREDIT_DOMAIN));
            boolean approved = "\"approve\"".equals(outcome);
            assertEquals(List.of("agentce:event/a1"), store.objects("agentce:event/d1", "agentce:reviewedBy"), data);
            assertEquals(approved ? List.of("agentce:event/a1") : List.of(),
                    store.objects("agentce:event/d1", "agentce:approvedBy"), data);
            assertEquals(List.of(String.valueOf(approved)), flag(events, "d1", "oversightCoverageComplete"), data);
        }
    }

    /** Each human's most recent decision before the action governs (18.142): the same human's reject or edit at or
     * after an approve, or at a time that cannot be placed against it, withdraws it unless it came after every
     * ToolCall; another human's reject does not. */
    @Test
    void aLaterRejectByTheSameHumanWithdrawsTheApproval() {
        String[][] cases = {
            {"alice", "reject", "2026-01-10T02:00:00Z", "false"},
            {"alice", "edit", "2026-01-10T02:00:00Z", "false"},
            {"alice", "reject", "2026-01-10T01:00:00Z", "false"},
            {"alice", "reject", "2026-01-10T03:00:00Z", "false"},
            {"alice", "reject", "2026-01-10T04:00:00Z", "true"},
            {"alice", "reject", "2026-01-10T02:00:00", "false"},
            {"alice", "reject", "2026-01-10T03:30:00+02:00", "false"},
            {"alice", "reject", "2026-01-10T00:30:00Z", "true"},
            {"bob", "reject", "2026-01-10T02:00:00Z", "true"},
        };
        for (String[] c : cases) {
            ObjectNode action = (ObjectNode) event("t0", "ToolCall", decisionRef("d1"));
            action.put("time", "2026-01-10T03:00:00Z");
            ObjectNode approval = (ObjectNode) approval("a1", "d1", "alice");
            approval.put("time", "2026-01-10T01:00:00Z");
            ObjectNode later = (ObjectNode) event(
                    "a2", "ApprovalDecided", decisionRef("d1") + ", \"outcome\": \"" + c[1] + "\", " + human(c[0]));
            later.put("time", c[2]);
            List<JsonNode> events = withLogins(List.of(decisionEvent("d1"), action, approval, later));
            GraphStore store = Graph.buildGraph(events, DomainBinding.fromDict(CREDIT_DOMAIN));
            String label = c[1] + " " + c[2];
            assertEquals(List.of("agentce:event/a1", "agentce:event/a2"),
                    store.objects("agentce:event/d1", "agentce:reviewedBy"), label);
            assertEquals(Boolean.parseBoolean(c[3]) ? List.of("agentce:event/a1") : List.of(),
                    store.objects("agentce:event/d1", "agentce:approvedBy"), label);
        }
    }

    /** A human's own reject withdraws that human's approve even when it names no login or another human's login
     * (18.142, fail closed); it is still no review. */
    @Test
    void theSameHumansRejectWithdrawsEvenWhenItFailsTheHumanActorRule() {
        for (String login : new String[] {"", ", \"session_ref\": \"agentce:event/login-bob\""}) {
            ObjectNode approval = (ObjectNode) approval("a1", "d1", "alice");
            approval.put("time", "2026-01-10T01:00:00Z");
            ObjectNode later = (ObjectNode) event("a2", "ApprovalDecided", decisionRef("d1")
                    + ", \"outcome\": \"reject\", \"actor\": {\"kind\": \"human\", \"id\": \"alice\"}" + login);
            later.put("time", "2026-01-10T02:00:00Z");
            GraphStore store = Graph.buildGraph(
                    withLogins(List.of(decisionEvent("d1"), approval, later)), DomainBinding.fromDict(CREDIT_DOMAIN));
            assertEquals(List.of("agentce:event/a1"), store.objects("agentce:event/d1", "agentce:reviewedBy"), login);
            assertEquals(List.of(), store.objects("agentce:event/d1", "agentce:approvedBy"), login);
        }
    }

    /** An approve gates the decision (agentce:approvedBy; CND-02, OVS-08) only when it is recorded at or before
     * every ToolCall that executes it, compared as instants; a later one is still a review (agentce:reviewedBy). */
    @Test
    void anApproveAfterTheActionRanIsAReviewNotAnApproval() {
        Map<String, Boolean> cases = new LinkedHashMap<>();
        cases.put("2026-01-10T00:00:00Z", true);
        cases.put("2026-01-10T01:00:00Z", true);
        cases.put("2026-01-10T01:00:00.001Z", false);
        cases.put("2026-01-10T00:30:00-05:00", false);
        cases.put("2026-01-10T05:00:00+04:00", true);
        cases.put("2026-01-10T00:30:00", false);
        cases.forEach((approvedAt, approved) -> {
            ObjectNode action = (ObjectNode) event("t0", "ToolCall", decisionRef("d1"));
            action.put("time", "2026-01-10T01:00:00Z");
            ObjectNode approval = (ObjectNode) approval("a1", "d1", "alice");
            approval.put("time", approvedAt);
            List<JsonNode> events = withLogins(List.of(decisionEvent("d1"), action, approval));
            GraphStore store = Graph.buildGraph(events, DomainBinding.fromDict(CREDIT_DOMAIN));
            assertEquals(List.of("agentce:event/a1"), store.objects("agentce:event/d1", "agentce:reviewedBy"), approvedAt);
            assertEquals(approved ? List.of("agentce:event/a1") : List.of(),
                    store.objects("agentce:event/d1", "agentce:approvedBy"), approvedAt);
            assertEquals(List.of(String.valueOf(approved)), flag(events, "d1", "oversightCoverageComplete"), approvedAt);
        });
    }

    /** An Outcome that names the decision shows it had taken effect (18.144): an approve gates it only when recorded
     * at or before every such Outcome too, compared as instants, wherever the Outcome sits in the input. */
    @Test
    void anApproveAfterTheOutcomeIsAReviewNotAnApproval() {
        Map<String, Boolean> cases = new LinkedHashMap<>();
        cases.put("2026-01-10T01:00:00Z", true);
        cases.put("2026-01-10T01:00:01Z", false);
        cases.put("2026-01-10T00:30:00-05:00", false);
        cases.put("2026-01-10T00:30:00", false);
        cases.forEach((approvedAt, approved) -> {
            ObjectNode outcome = (ObjectNode) event("o1", "Outcome", decisionRef("d1"));
            outcome.put("time", "2026-01-10T01:00:00Z");
            ObjectNode approval = (ObjectNode) approval("a1", "d1", "alice");
            approval.put("time", approvedAt);
            List<JsonNode> events = withLogins(List.of(decisionEvent("d1"), approval));
            events.add(outcome);
            GraphStore store = Graph.buildGraph(events, DomainBinding.fromDict(CREDIT_DOMAIN));
            assertEquals(List.of("agentce:event/a1"), store.objects("agentce:event/d1", "agentce:reviewedBy"), approvedAt);
            assertEquals(approved ? List.of("agentce:event/a1") : List.of(),
                    store.objects("agentce:event/d1", "agentce:approvedBy"), approvedAt);
        });
    }

    /** The approval bound reads every refs.decision as the held decision it names (18.148): an Outcome naming the
     * decision by bare id or padded bounds an approve that names it by IRI, an approve naming it by bare id approves
     * it, and an Outcome naming no held decision bounds every decision's approvals. */
    @Test
    void aBareIdNamesTheDecision() {
        String d1 = "agentce:event/d1";
        String[][] cases = {
            {"d1", d1, "false"},
            {d1 + " ", d1, "false"},
            {"\u00a0d1", d1, "false"},
            {"AGENTCE:EVENT/D1", d1, "false"},
            {"a1", d1, "false"},
            {d1, "d1", "false"},
            {null, "d1", "true"},
            {null, " " + d1, "true"},
        };
        for (String[] c : cases) {
            ObjectNode approval = (ObjectNode) approval("a1", "d1", "alice");
            ((ObjectNode) approval.get("data").get("refs")).put("decision", c[1]);
            approval.put("time", "2026-01-10T05:00:00Z");
            List<JsonNode> events = withLogins(List.of(decisionEvent("d1"), approval));
            if (c[0] != null) {
                ObjectNode outcome = (ObjectNode) event("o1", "Outcome", decisionRef("d1"));
                ((ObjectNode) outcome.get("data").get("refs")).put("decision", c[0]);
                outcome.put("time", "2026-01-10T01:00:00Z");
                events.add(outcome);
            }
            GraphStore store = Graph.buildGraph(events, DomainBinding.fromDict(CREDIT_DOMAIN));
            String label = c[0] + " " + c[1];
            assertEquals(List.of("agentce:event/a1"), store.objects(d1, "agentce:reviewedBy"), label);
            assertEquals(Boolean.parseBoolean(c[2]) ? List.of("agentce:event/a1") : List.of(),
                    store.objects(d1, "agentce:approvedBy"), label);
        }
    }

    @Test
    void aBareDecisionRefLinksEveryDecisionEdge() {
        // Every glue read of refs.decision resolves it as approvedBy does (18.156): records naming the decision by bare
        // id or padded, read before the Decision itself, still build each edge to its IRI; a name for no held
        // Decision links none.
        String d1 = "agentce:event/d1";
        List<JsonNode> events = List.of(
                event("t0", "ToolCall", "\"refs\": {\"decision\": \"d1\"}"),
                event("o1", "Outcome", "\"refs\": {\"decision\": \" d1\"}"),
                event("v1", "Override", "\"refs\": {\"decision\": \"agentce:event/d1\\t\"}"),
                event("x1", "Interrupt", "\"refs\": {\"decision\": \"d1\"}"),
                event("n1", "Notice", "\"refs\": {\"decision\": \"\\u00a0d1 \"}"),
                event("t1", "ToolCall", "\"refs\": {\"decision\": \"nope\"}"),
                decisionEvent("d1"));
        GraphStore store = Graph.buildGraph(events, DomainBinding.fromDict(CREDIT_DOMAIN));
        assertEquals(List.of(d1), store.objects("agentce:event/t0", "agentce:executes"));
        String[][] edges = {
            {"agentce:resultedIn", "o1"},
            {"agentce:overriddenBy", "v1"},
            {"agentce:interruptedBy", "x1"},
            {"agentce:notifiedBy", "n1"},
        };
        for (String[] e : edges) {
            assertEquals(List.of("agentce:event/" + e[1]), store.objects(d1, e[0]), e[0]);
        }
        assertEquals(List.of("true"), store.literalValues("agentce:event/t0", "agentce:executesConsequential"));
        assertEquals(List.of("false"), store.literalValues("agentce:event/t1", "agentce:executesConsequential"));
        assertEquals(List.of("nope"), store.objects("agentce:event/t1", "agentce:executes"));
    }

    @Test
    void aNullDecisionRefBoundsEveryApproval() {
        // A present null refs.decision names no held decision, so it bounds the approve; an Outcome with no
        // refs.decision key, or no refs, is no decision's effect (18.157).
        String d1 = "agentce:event/d1";
        String[][] cases = {
            {"\"refs\": {\"decision\": null}", "false"},
            {"\"refs\": {}", "true"},
            {"", "true"},
        };
        for (String[] c : cases) {
            ObjectNode approval = (ObjectNode) approval("a1", "d1", "alice");
            approval.put("time", "2026-01-10T05:00:00Z");
            ObjectNode outcome = (ObjectNode) event("o1", "Outcome", c[0]);
            outcome.put("time", "2026-01-10T01:00:00Z");
            List<JsonNode> events = withLogins(List.of(decisionEvent("d1"), approval, outcome));
            GraphStore store = Graph.buildGraph(events, DomainBinding.fromDict(CREDIT_DOMAIN));
            assertEquals(Boolean.parseBoolean(c[1]) ? List.of("agentce:event/a1") : List.of(),
                    store.objects(d1, "agentce:approvedBy"), c[0]);
        }
    }

    @Test
    void ovs08OneHumanReviewerCoversADecision() {
        assertEquals(List.of("false"), flag(List.of(decisionEvent("d1")), "d1", "oversightCoverageComplete"));
        JsonNode service = event("a1", "ApprovalDecided",
                decisionRef("d1") + ", \"actor\": {\"kind\": \"service\", \"id\": \"svc\"}");
        assertEquals(List.of("false"), flag(List.of(decisionEvent("d1"), service), "d1", "oversightCoverageComplete"));
        assertEquals(List.of("true"),
                flag(withLogins(List.of(decisionEvent("d1"), approval("a1", "d1", "alice"))), "d1",
                        "oversightCoverageComplete"));
    }

    @Test
    void ovs08DualControlNeedsTwoDistinctHumans() {
        JsonNode dual = decisionWith("d1", "\"oversight_modality\": \"dual_control\"");
        assertEquals(List.of("false"), flag(List.of(dual, approval("a1", "d1", "alice"),
                approval("a2", "d1", "alice")), "d1", "oversightCoverageComplete"));
        assertEquals(List.of("true"), flag(withLogins(List.of(dual, approval("a1", "d1", "alice"),
                approval("a2", "d1", "bob"))), "d1", "oversightCoverageComplete"));
        JsonNode declared = Json.parse("{\"decision_types\":[{\"id\":\"agentce:CreditDecision\","
                + "\"subclass_of\":\"agentce:ConsequentialDecision\",\"consequential\":true,"
                + "\"required_oversight_modality\":\"dual_control\"}]}");
        List<JsonNode> one = List.of(decisionEvent("d1"), approval("a1", "d1", "alice"));
        assertEquals(List.of("false"), flag(one, declared, "d1", "oversightCoverageComplete"));
        List<JsonNode> two =
                withLogins(List.of(decisionEvent("d1"), approval("a1", "d1", "alice"), approval("a2", "d1", "bob")));
        assertEquals(List.of("true"), flag(two, declared, "d1", "oversightCoverageComplete"));
    }

    @Test
    void ovs07AnOverrideIsEffectiveWhenItReplacesAHeldDecision() {
        List<JsonNode> effective = withLogins(List.of(decisionEvent("d1"), event("o1", "Override",
                decisionRef("d1") + ", \"original\": \"approve\", \"replacement\": \"deny\", " + human("alice"))));
        assertEquals(List.of("true"), flag(effective, "o1", "interventionEffective"));
        assertEquals(List.of("true"), flag(effective, "o1", "interventionByHuman"));
        for (String data : List.of(
                decisionRef("gone") + ", \"original\": \"approve\", \"replacement\": \"deny\"",
                decisionRef("d1") + ", \"original\": \"approve\"",
                decisionRef("d1") + ", \"original\": \"approve\", \"replacement\": null",
                decisionRef("d1") + ", \"original\": {\"x\": 1}, \"replacement\": {\"x\": 1.0}")) {
            List<JsonNode> events = List.of(decisionEvent("d1"), event("o1", "Override", data));
            assertEquals(List.of("false"), flag(events, "o1", "interventionEffective"), data);
            assertEquals(List.of("false"), flag(events, "o1", "interventionByHuman"), data);
        }
    }

    @Test
    void ovs07AnInterruptIsEffectiveWhenANamedMechanismStopsTheAgent() {
        JsonNode stop = event("i1", "Interrupt", "\"effect\": \"halted\", \"mechanism\": \"kill_switch\", "
                + human("alice"));
        assertEquals(List.of("true"), flag(List.of(stop), "i1", "interventionEffective"));
        assertEquals(List.of("true"), flag(withLogins(List.of(stop)), "i1", "interventionByHuman"));
        for (String data : List.of("\"effect\": \"logged\", \"mechanism\": \"manual\"",
                "\"effect\": \"paused\", \"mechanism\": \"email\"")) {
            JsonNode weak = event("i1", "Interrupt", data);
            assertEquals(List.of("false"), flag(List.of(weak), "i1", "interventionEffective"), data);
            assertEquals(List.of("false"), flag(List.of(weak), "i1", "interventionByHuman"), data);
        }
    }

    @Test
    void rob07AnIncidentIsRespondedToOnlyAfterDetectionAndAResponse() {
        assertEquals(List.of("true"), flag(List.of(incident("x1",
                "\"detected_at\": \"2026-01-01T00:00:00Z\", \"reported_at\": \"2026-01-02T00:00:00Z\"")),
                "x1", "incidentResponded"));
        for (String data : List.of("\"detected_at\": \"2026-01-01T00:00:00Z\"",
                "\"reported_at\": \"2026-01-02T00:00:00Z\"",
                "\"detected_at\": \"2026-01-01T00:00:00Z\", \"reported_at\": \"\"")) {
            assertEquals(List.of("false"), flag(List.of(incident("x1", data)), "x1", "incidentResponded"), data);
        }
    }

    @Test
    void rsk02ADecisionIsRiskReviewedByAReviewOrAHeldPolicyDecisionItsAuthorizationNames() {
        JsonNode policy = event("p1", "PolicyDecision", "\"decision\": \"allow\"");
        JsonNode outcome = event("o1", "Outcome", "\"refs\": {\"decision\": \"agentce:event/d1\"}");
        assertEquals(List.of("true"), flag(withLogins(List.of(decisionEvent("d1"), approval("a1", "d1", "alice"))),
                "d1", "riskReviewed"));
        assertEquals(List.of("true"), flag(List.of(policy,
                decisionWith("d1", "\"refs\": {\"authorization\": \"agentce:event/p1\"}")), "d1", "riskReviewed"));
        assertEquals(List.of("true"), flag(List.of(policy,
                decisionWith("d1", "\"refs\": {\"request\": \"agentce:event/p1\"}")), "d1", "riskReviewed"));
        assertEquals(List.of("false"), flag(List.of(decisionEvent("d1")), "d1", "riskReviewed"));
        for (String ref : List.of("\"agentce:event/missing\"", "\"agentce:event/o1\"", "\"p1\"",
                "[\"agentce:event/p1\"]")) {
            assertEquals(List.of("false"), flag(List.of(policy, outcome,
                    decisionWith("d1", "\"refs\": {\"authorization\": " + ref + "}")), "d1", "riskReviewed"), ref);
        }
    }

    /** SPEC §10.4: an approval or override counts as a human's only when its actor's session_ref names a held
     * identity-provider login of someone other than the agent, from an independent or enforcement-point stream, and
     * the actor is outside the delegation chain; under dual control one login or one id is one human. */
    @Test
    void humanActorRule() {
        String agent = "spiffe://corp/agents/a";
        String chained = "\"decision_type\": \"agentce:CreditDecision\", \"agent\": {\"id\": \"" + agent
                + "\"}, \"acted_for\": [\"owner\"]";
        JsonNode decision = event("d1", "Decision", chained);
        String cover = "oversightCoverageComplete";
        assertEquals(List.of("true"), flag(withLogins(List.of(decision, approval("a1", "d1", "alice"))), "d1", cover));
        assertEquals(List.of("false"), flag(List.of(decision, approval("a1", "d1", "alice")), "d1", cover));
        assertEquals(List.of("false"), flag(List.of(decision, approval("a1", "d1", "alice"),
                login("login-alice", "self_report", IDP)), "d1", cover));
        assertEquals(List.of("false"), flag(List.of(decision, approval("a1", "d1", "alice"),
                login("login-alice", "enforcement_point", agent)), "d1", cover));
        assertEquals(List.of("false"), flag(List.of(decision, approval("a1", "d1", "alice"),
                event("login-alice", "ToolCall", "\"tool\": {\"name\": \"x\"}")), "d1", cover));
        assertEquals(List.of("false"), flag(List.of(decision, approval("a1", "d1", "owner"),
                login("login-owner", "independent_system", IDP)), "d1", cover));
        // The login names someone else, or nobody: it is not the actor's own.
        assertEquals(List.of("false"), flag(List.of(decision, approval("a1", "d1", "alice"), login("login-alice",
                "independent_system", IDP, ", \"principal\": {\"kind\": \"human\", \"id\": \"bob\"}")), "d1", cover));
        assertEquals(List.of("false"), flag(List.of(decision, approval("a1", "d1", "alice"),
                login("login-alice", "independent_system", IDP, "")), "d1", cover));
        // Ids compare after trimming one fixed whitespace set from both ends.
        assertEquals(List.of("true"), flag(List.of(decision, approval("a1", "d1", "alice"), login("login-alice",
                "independent_system", IDP, ", \"principal\": {\"kind\": \"human\", \"id\": \"\\u00a0alice\\ufeff\"}")),
                "d1", cover));
        JsonNode noSession = event("a1", "ApprovalDecided",
                decisionRef("d1") + ", \"outcome\": \"approve\", \"actor\": {\"kind\": \"human\", \"id\": \"alice\"}");
        assertEquals(List.of("false"), flag(withLogins(List.of(decision, noSession)), "d1", cover));
        JsonNode override = event("o1", "Override", decisionRef("d1") + ", " + human("owner"));
        assertEquals(List.of("false"), flag(List.of(decision, override,
                login("login-owner", "independent_system", IDP)), "o1", "interventionByHuman"));
        // Dual control: two ids on one login, or one id on two logins, are one human.
        JsonNode dual = event("d1", "Decision", chained + ", \"oversight_modality\": \"dual_control\"");
        assertEquals(List.of("false"), flag(withLogins(List.of(dual, approval("a1", "d1", "alice"),
                approval("a2", "d1", "alice "))), "d1", cover));
        JsonNode aliceOnBobsLogin = event("a2", "ApprovalDecided", decisionRef("d1") + ", \"outcome\": \"approve\""
                + ", \"actor\": {\"kind\": \"human\", \"id\": \"alice\"}, \"session_ref\": \"agentce:event/login-bob\"");
        assertEquals(List.of("false"), flag(withLogins(List.of(dual, approval("a1", "d1", "alice"), aliceOnBobsLogin)),
                "d1", cover));
        // File order never matters: the login before the decision and after the approval still counts.
        List<JsonNode> reordered = new ArrayList<>(LOGINS);
        reordered.add(approval("a1", "d1", "alice"));
        reordered.add(decision);
        assertEquals(List.of("true"), flag(reordered, "d1", cover));
    }

    /** 18.186: the store lists what the deviation register cannot waive (SPEC §10.4): each failing ApprovalDecided,
     * Override and Interrupt, and the held decision a failing approval names; a passing approval adds nothing. */
    @Test
    void theNotWaivableSetListsFailingOversightRecordsAndTheDecisionTheyName() {
        java.util.function.Function<List<JsonNode>, List<String>> failed = events -> {
            List<String> out = new ArrayList<>(Graph.buildGraph(events, DomainBinding.fromDict(CREDIT_DOMAIN)).humanActorFailed);
            Collections.sort(out);
            return out;
        };
        assertEquals(List.of("agentce:event/a1", "agentce:event/d1"),
                failed.apply(List.of(decisionEvent("d1"), approval("a1", "d1", "alice"))));
        assertEquals(List.of(), failed.apply(withLogins(List.of(decisionEvent("d1"), approval("a1", "d1", "alice")))));
        String service = "\"actor\": {\"kind\": \"service\", \"id\": \"svc\"}";
        assertEquals(List.of("agentce:event/i1", "agentce:event/o1"), failed.apply(List.of(decisionEvent("d1"),
                event("o1", "Override", decisionRef("d1") + ", " + service),
                event("i1", "Interrupt", decisionRef("d1") + ", " + service))));
        // A failing approval naming no held decision adds only itself.
        assertEquals(List.of("agentce:event/a1"),
                failed.apply(List.of(decisionEvent("d1"), approval("a1", "nowhere", "alice"))));
    }
    private static JsonNode taskCall(String id, String tool, String effect, String agentTask, String refsTask) {
        ObjectNode event = (ObjectNode) Json.parse("{\"id\":\"" + id + "\",\"source\":\"urn:src:gw\","
                + "\"subject\":\"spiffe://corp/agents/a\",\"type\":\"org.agent-conformance.evidence.ToolCall.v1\","
                + "\"data\":{\"@type\":\"ToolCall\",\"tool\":{\"name\":\"" + tool + "\"},"
                + "\"effect_class\":\"" + effect + "\"}}");
        if (agentTask != null) {
            event.put("agentcetask", agentTask);
        }
        if (refsTask != null) {
            ((ObjectNode) event.get("data")).putObject("refs").put("task", refsTask);
        }
        return event;
    }

    @Test
    void withinTaskScopeHoldsATaskedToolCallToEveryNamedTasksScope() {
        DomainBinding domain = DomainBinding.fromDict(Json.parse("{\"task_scopes\":["
                + "{\"task_type\":\"review\",\"allowed_tools\":[\"lookup\"],\"allowed_effect_classes\":[\"read\"]},"
                + "{\"task_type\":\"pay\",\"allowed_tools\":[\"pay\"],\"allowed_effect_classes\":[\"spend\"]}]}"));
        List<JsonNode> calls = List.of(
                taskCall("in", "lookup", "read", "review", null),
                taskCall("effect", "lookup", "irreversible", "review", null),
                taskCall("tool", "pay", "read", null, "review"),
                taskCall("unknown", "lookup", "read", "task-1", null),
                taskCall("differ", "lookup", "read", "pay", "review"),
                taskCall("untasked", "ping", "irreversible", null, null));
        GraphStore store = Graph.buildGraph(calls, domain);
        Map<String, String> got = new LinkedHashMap<>();
        for (JsonNode call : calls) {
            String id = call.get("id").asText();
            got.put(id, store.literalValues("agentce:event/" + id, "agentce:withinScope").get(0));
        }
        Map<String, String> expected = new LinkedHashMap<>();
        expected.put("in", "true");
        expected.put("effect", "false");
        expected.put("tool", "false");
        expected.put("unknown", "false");
        expected.put("differ", "false");
        expected.put("untasked", "true");
        assertEquals(expected, got);
    }

    // --- CND-02: actions are the population, and each one's gate (SPEC §7.7.4; 18.187) ------------------

    private static final JsonNode GATED_DOMAIN = Json.parse("{\"decision_types\":["
            + "{\"id\":\"agentce:CreditDecision\",\"subclass_of\":\"agentce:ConsequentialDecision\",\"consequential\":true},"
            + "{\"id\":\"agentce:MinorDecision\",\"subclass_of\":\"agentce:Decision\",\"consequential\":false}],"
            + "\"task_scopes\":["
            + "{\"task_type\":\"review\",\"allowed_effect_classes\":[\"read\",\"write\"],"
            + "\"requires_approval\":[\"irreversible\",\"write\"]},"
            + "{\"task_type\":\"payout\",\"allowed_effect_classes\":[\"spend\"]}]}");

    /** The options of {@link #gateEvents}; each {@code with} method returns a changed copy. */
    private record GateOpts(
            boolean approved, String dtype, String task, String decisionRef, String policyClass, String request,
            String ptype) {
        static final GateOpts DEFAULT = new GateOpts(
                true, "agentce:CreditDecision", "review", "\"agentce:event/d1\"", "enforcement_point", "tc1", "ToolCall");

        GateOpts unapproved() {
            return new GateOpts(false, dtype, task, decisionRef, policyClass, request, ptype);
        }

        GateOpts dtype(String d) {
            return new GateOpts(approved, d, task, decisionRef, policyClass, request, ptype);
        }

        GateOpts task(String t) {
            return new GateOpts(approved, dtype, t, decisionRef, policyClass, request, ptype);
        }

        /** {@code ref} is the JSON of refs.decision ({@code null} leaves refs empty). */
        GateOpts decisionRef(String ref) {
            return new GateOpts(approved, dtype, task, ref, policyClass, request, ptype);
        }

        GateOpts policyClass(String c) {
            return new GateOpts(approved, dtype, task, decisionRef, c, request, ptype);
        }

        GateOpts request(String r) {
            return new GateOpts(approved, dtype, task, decisionRef, policyClass, r, ptype);
        }

        GateOpts ptype(String t) {
            return new GateOpts(approved, dtype, task, decisionRef, policyClass, request, t);
        }
    }

    /** A decision d1, alice's approval of it (unless unapproved), a PolicyDecision pd1 ruling {@code policy} on
     * {@code request}, and the action tc1 executing d1 with {@code effect} (none when null). */
    private static List<JsonNode> gateEvents(String effect, String policy, GateOpts o) {
        List<JsonNode> events = new ArrayList<>();
        events.add(event("d1", "Decision", "\"decision_type\": \"" + o.dtype() + "\""));
        if (o.approved()) {
            events.add(login("login-alice", "independent_system", IDP));
            events.add(Json.parse("{\"id\": \"ap1\", \"time\": \"2026-01-01T00:00:01Z\", "
                    + "\"agentcesourceclass\": \"enforcement_point\", \"data\": {\"@type\": \"ApprovalDecided\", "
                    + decisionRef("d1") + ", \"outcome\": \"approve\", " + human("alice") + "}}"));
        }
        if (policy != null) {
            events.add(Json.parse("{\"id\": \"pd1\", \"time\": \"2026-01-01T00:00:00Z\", \"agentcesourceclass\": \""
                    + o.policyClass() + "\", \"data\": {\"@type\": \"PolicyDecision\", \"decision\": \"" + policy
                    + "\", \"refs\": {\"request\": \"agentce:event/" + o.request() + "\"}}}"));
        }
        String refs = o.decisionRef() == null ? "{}" : "{\"decision\": " + o.decisionRef() + "}";
        String effectJson = effect == null ? "" : ", \"effect_class\": \"" + effect + "\"";
        String task = o.task() == null ? "" : ", \"agentcetask\": \"" + o.task() + "\"";
        events.add(Json.parse("{\"id\": \"tc1\", \"time\": \"2026-01-01T00:00:02Z\", "
                + "\"agentcesourceclass\": \"enforcement_point\"" + task + ", \"data\": {\"@type\": \"" + o.ptype()
                + "\", \"tool\": {\"name\": \"book\"}, \"refs\": " + refs + effectJson + "}}"));
        return events;
    }

    private static List<JsonNode> gateEvents(String effect, String policy) {
        return gateEvents(effect, policy, GateOpts.DEFAULT);
    }

    private static List<Object> gate(List<JsonNode> events, String node) {
        GraphStore store = Graph.buildGraph(events, DomainBinding.fromDict(GATED_DOMAIN));
        String iri = "agentce:event/" + node;
        List<String> met = store.literalValues(iri, "agentce:gateMet");
        List<String> judged = store.literalValues(iri, "agentce:gateJudged");
        return java.util.Arrays.asList(
                store.isA(iri, "agentce:GatedAction"),
                met.isEmpty() ? null : met.get(0),
                judged.isEmpty() ? null : judged.get(0));
    }

    private static List<Object> gate(List<JsonNode> events) {
        return gate(events, "tc1");
    }

    private static final List<Object> SHUT = List.of(true, "false", "true");
    private static final List<Object> OPEN = List.of(true, "true", "true");
    private static final List<Object> OUTSIDE = java.util.Arrays.asList(false, null, null);
    private static final List<Object> UNJUDGED = List.of(true, "true", "false");

    @Test
    void cnd02ARequiresApprovalActionNeedsAnEnforcementPointDecisionAndAnApproval() {
        assertEquals(OPEN, gate(gateEvents("irreversible", "require_approval")));
        assertEquals(OPEN, gate(gateEvents("irreversible", "allow")));
        assertEquals(SHUT, gate(gateEvents("irreversible", "require_approval", GateOpts.DEFAULT.unapproved())));
        // the agent's own allow, an allow naming another call, and no PolicyDecision at all are no gate
        assertEquals(SHUT, gate(gateEvents("irreversible", "require_approval", GateOpts.DEFAULT.policyClass("self_report"))));
        assertEquals(SHUT, gate(gateEvents("irreversible", "require_approval", GateOpts.DEFAULT.request("tcx"))));
        assertEquals(SHUT, gate(gateEvents("irreversible", null)));
    }

    @Test
    void cnd02ADenyClosesTheGateWhateverWasApproved() {
        assertEquals(SHUT, gate(gateEvents("irreversible", "deny")));
        assertEquals(SHUT, gate(gateEvents("external_communication", "deny")));
    }

    @Test
    void cnd02TheApprovalIsTiedToTheDecisionTheActionNames() {
        for (String ref : new String[] {"null", "\"agentce:event/d-missing\"", "\"agentce:event/d2\"", null}) {
            assertEquals(SHUT, gate(gateEvents("irreversible", "require_approval", GateOpts.DEFAULT.decisionRef(ref))));
        }
    }

    @Test
    void cnd02OtherHighEffectClassesNeedAnAllowOrAReleasedRequireApproval() {
        for (String effect : List.of("external_communication", "spend", "physical")) {
            assertEquals(OPEN, gate(gateEvents(effect, "allow", GateOpts.DEFAULT.unapproved())));
            assertEquals(OPEN, gate(gateEvents(effect, "require_approval")));
            assertEquals(SHUT, gate(gateEvents(effect, "require_approval", GateOpts.DEFAULT.unapproved())));
            assertEquals(SHUT, gate(gateEvents(effect, "allow", GateOpts.DEFAULT.policyClass("self_report"))));
        }
    }

    @Test
    void cnd02TheScopeMarksThePopulationAndReadStaysOutside() {
        assertEquals(OUTSIDE, gate(gateEvents("read", null, GateOpts.DEFAULT.unapproved())));
        // write is no high-effect class, but this scope marks it requires_approval
        assertEquals(SHUT, gate(gateEvents("write", "allow", GateOpts.DEFAULT.unapproved())));
        assertEquals(OPEN, gate(gateEvents("write", "allow")));
        // the decision's own type does not matter: the action is what CND-02 counts
        assertEquals(OPEN, gate(gateEvents("irreversible", "require_approval", GateOpts.DEFAULT.dtype("agentce:MinorDecision"))));
    }

    @Test
    void cnd02AHighEffectActionInNoDeclaredScopeIsHeldToTheApprovalPath() {
        for (String task : java.util.Arrays.asList(null, "unknown-task")) {
            GateOpts o = GateOpts.DEFAULT.unapproved().task(task);
            assertEquals(SHUT, gate(gateEvents("external_communication", "allow", o)));
            assertEquals(OUTSIDE, gate(gateEvents("write", "allow", o)));
        }
    }

    @Test
    void cnd02AnActionWithNoEffectClassAndAnUnexecutedDecisionAreUnjudged() {
        assertEquals(UNJUDGED, gate(gateEvents(null, "require_approval")));
        List<JsonNode> events = gateEvents("irreversible", "require_approval");
        assertEquals(UNJUDGED, gate(events.subList(0, events.size() - 1), "d1"));
        // a minor decision no action executes is not in the population
        List<JsonNode> minor =
                gateEvents("irreversible", "require_approval", GateOpts.DEFAULT.dtype("agentce:MinorDecision"));
        assertEquals(OUTSIDE, gate(minor.subList(0, minor.size() - 1), "d1"));
    }

    @Test
    void cnd02AResourceAccessCountsOnlyWithAnEffectClass() {
        assertEquals(OPEN, gate(gateEvents("irreversible", "require_approval", GateOpts.DEFAULT.ptype("ResourceAccess"))));
        assertEquals(OUTSIDE, gate(gateEvents(null, "require_approval", GateOpts.DEFAULT.ptype("ResourceAccess"))));
    }

    @Test
    void cnd02ReleasedByLinksTheApprovalsTheGateRestsOn() {
        java.util.function.Function<List<JsonNode>, List<String>> released = events -> Graph.buildGraph(
                        events, DomainBinding.fromDict(GATED_DOMAIN))
                .objects("agentce:event/tc1", "agentce:releasedBy");
        assertEquals(List.of("agentce:event/ap1"), released.apply(gateEvents("irreversible", "require_approval")));
        // an allow-path action passed on the enforcement point's allow rests on no approval
        assertEquals(List.of(), released.apply(gateEvents("external_communication", "allow")));
        assertEquals(List.of("agentce:event/ap1"), released.apply(gateEvents("external_communication", "require_approval")));
    }

    // --- CND-07: task budgets (SPEC §7.7.4, §7.7.6; 18.175) ---------------------------------------------------

    private static JsonNode budgetCall(String id, String tool, String effect, String task) {
        String effectJson = effect == null ? "" : ",\"effect_class\":\"" + effect + "\"";
        String taskJson = task == null ? "" : ",\"agentcetask\":\"" + task + "\"";
        return Json.parse("{\"id\":\"" + id + "\",\"source\":\"urn:src:gw\",\"subject\":\"spiffe://corp/agents/a\","
                + "\"type\":\"org.agent-conformance.evidence.ToolCall.v1\",\"data\":{\"@type\":\"ToolCall\","
                + "\"tool\":{\"name\":\"" + tool + "\"}" + effectJson + "}" + taskJson + "}");
    }

    private static JsonNode modelCall(String id, String task, String usageJson) {
        String taskJson = task == null ? "" : ",\"agentcetask\":\"" + task + "\"";
        return Json.parse("{\"id\":\"" + id + "\",\"source\":\"urn:src:agent\",\"subject\":\"spiffe://corp/agents/a\","
                + "\"type\":\"org.agent-conformance.evidence.ModelCall.v1\",\"data\":{\"@type\":\"ModelCall\","
                + "\"usage\":" + usageJson + "}" + taskJson + "}");
    }

    /** Each action's withinBudget and whether the records could tell, as "true/judged" and the like. */
    private static Map<String, String> budgetFlags(List<JsonNode> events, String budgetsJson, String scopesJson) {
        String scopes = scopesJson != null
                ? scopesJson
                : "[{\"task_type\":\"review\",\"allowed_effect_classes\":[\"read\",\"spend\"]"
                        + (budgetsJson == null ? "" : ",\"budgets\":" + budgetsJson) + "}]";
        GraphStore store = Graph.buildGraph(events, DomainBinding.fromDict(Json.parse("{\"task_scopes\":" + scopes + "}")));
        Map<String, String> out = new LinkedHashMap<>();
        for (JsonNode e : events) {
            String iri = "agentce:event/" + e.get("id").asText();
            out.put(e.get("id").asText(), store.literalValues(iri, "agentce:withinBudget").get(0)
                    + (store.isA(iri, "agentce:BudgetUnjudged") ? "/untold" : "/judged"));
        }
        return out;
    }

    private static List<String> budgetFlagSet(List<JsonNode> events, String budgetsJson) {
        return budgetFlags(events, budgetsJson, null).values().stream().distinct().toList();
    }

    @Test
    void taskBudgetTokens() {
        List<JsonNode> under = List.of(
                modelCall("m1", "review", "{\"input_tokens\":400,\"output_tokens\":100}"),
                budgetCall("t1", "lookup", "read", "review"));
        assertEquals(Map.of("m1", "true/judged", "t1", "true/judged"), budgetFlags(under, "{\"tokens\":500}", null));
        List<JsonNode> over = new ArrayList<>(under);
        over.add(modelCall("m2", "review", "{\"input_tokens\":1,\"output_tokens\":0}"));
        assertEquals(List.of("false/judged"), budgetFlagSet(over, "{\"tokens\":500}"));
        // a whole number written 500.0 reads as 500, as every engine's reader may give it
        assertEquals(List.of("false/judged"), budgetFlagSet(over, "{\"tokens\":500.0}"));
        List<JsonNode> unread = new ArrayList<>(under);
        unread.add(modelCall("m3", "review", "{\"input_tokens\":1}"));
        assertEquals(List.of("true/untold"), budgetFlagSet(unread, "{\"tokens\":500}"));
        // an overrun the readable usage already shows is told, whatever else is unreadable
        List<JsonNode> both = new ArrayList<>(over);
        both.add(unread.get(2));
        assertEquals(List.of("false/judged"), budgetFlagSet(both, "{\"tokens\":500}"));
        for (String limit : List.of("500.5", "\"500\"", "true", "-1")) {
            assertEquals(List.of("true/untold"), budgetFlagSet(under, "{\"tokens\":" + limit + "}"));
        }
    }

    @Test
    void taskBudgetTokensPast2To53() {
        long big = (1L << 53) - 1;
        String bigUsage = "{\"input_tokens\":" + big + ",\"output_tokens\":" + big + "}";
        List<JsonNode> many = new ArrayList<>();
        for (int i = 0; i < 513; i++) {
            many.add(modelCall("m" + i, "review", bigUsage));
        }
        // 513 calls of 2 * (2^53 - 1) would overflow a long; the sum saturates and reads over
        assertEquals(List.of("false/judged"), budgetFlagSet(many, "{\"tokens\":1000}"));
        List<JsonNode> huge = List.of(modelCall("m", "review", "{\"input_tokens\":" + (1L << 62) + ",\"output_tokens\":0}"));
        assertEquals(List.of("false/judged"), budgetFlagSet(huge, "{\"tokens\":" + big + "}"));
        // past 2^53 - 1 a tokens limit cannot be held exactly in every engine, so it is untold
        List<JsonNode> past = List.of(modelCall("m", "review", "{\"input_tokens\":18014398509481985,\"output_tokens\":0}"));
        assertEquals(List.of("true/untold"), budgetFlagSet(past, "{\"tokens\":18014398509481984}"));
        List<JsonNode> edge = List.of(modelCall("m", "review", "{\"input_tokens\":" + big + ",\"output_tokens\":0}"));
        assertEquals(List.of("true/judged"), budgetFlagSet(edge, "{\"tokens\":" + big + "}"));
        assertEquals(List.of("false/judged"), budgetFlagSet(edge, "{\"tokens\":" + (big - 1) + "}"));
    }

    @Test
    void taskBudgetSpendComputeAndScope() {
        List<JsonNode> spend = List.of(budgetCall("s1", "pay", "spend", "review"));
        assertEquals(Map.of("s1", "false/judged"), budgetFlags(spend, "{\"spend\":0}", null));
        assertEquals(Map.of("s1", "true/untold"), budgetFlags(spend, "{\"spend\":10}", null));
        List<JsonNode> read = List.of(budgetCall("r1", "lookup", "read", "review"));
        assertEquals(Map.of("r1", "true/judged"), budgetFlags(read, "{\"spend\":10}", null));
        // a call with no effect class may have spent, and a task with no model call shows no token use
        assertEquals(Map.of("x1", "true/untold"),
                budgetFlags(List.of(budgetCall("x1", "pay", null, "review")), "{\"spend\":0}", null));
        assertEquals(Map.of("r1", "true/untold"), budgetFlags(read, "{\"tokens\":5}", null));
        // a model call may have cost an amount no record carries
        List<JsonNode> billed = List.of(read.get(0), modelCall("m1", "review", "{\"input_tokens\":1,\"output_tokens\":1}"));
        assertEquals(List.of("true/untold"), budgetFlagSet(billed, "{\"spend\":0}"));
        assertEquals(Map.of("r1", "true/untold"), budgetFlags(read, "{\"compute_seconds\":60}", null));
        assertEquals(Map.of("r1", "true/untold"), budgetFlags(read, "\"lots\"", null));
        assertEquals(Map.of("r1", "true/judged"), budgetFlags(read, null, null));
        List<JsonNode> other = List.of(budgetCall("o1", "lookup", "read", "elsewhere"), budgetCall("u1", "lookup", "read", null));
        assertEquals(Map.of("o1", "true/untold", "u1", "true/judged"), budgetFlags(other, null,
                "[{\"task_type\":\"review\",\"budgets\":{\"tokens\":5}},{\"task_type\":\"pay\"}]"));
        GraphStore none = Graph.buildGraph(other, DomainBinding.fromDict(Json.parse("{}")));
        assertTrue(!none.isA("agentce:event/o1", "agentce:BudgetUnjudged"));
        GraphStore untasked = Graph.buildGraph(
                List.of(modelCall("m0", null, "{\"input_tokens\":9,\"output_tokens\":9}")),
                DomainBinding.fromDict(Json.parse("{\"task_scopes\":[{\"task_type\":\"review\"}]}")));
        assertTrue(!untasked.isA("agentce:event/m0", "agentce:TaskModelCall"));
    }

    // --- CND-04: the instruction chain, the principal's role and the delegation (SPEC §7.7.4; 18.122) ---------

    private static final String DECISION_TYPES = "\"decision_types\":["
            + "{\"id\":\"agentce:CreditDecision\",\"subclass_of\":\"agentce:ConsequentialDecision\",\"consequential\":true},"
            + "{\"id\":\"agentce:MinorDecision\",\"subclass_of\":\"agentce:Decision\",\"consequential\":false}]";

    /** A domain whose one scope, review, authorises {@code rolesJson} (the key left out when null). */
    private static DomainBinding rolesDomain(String rolesJson) {
        String roles = rolesJson == null ? "" : ",\"authorised_principal_roles\":" + rolesJson;
        return DomainBinding.fromDict(Json.parse("{" + DECISION_TYPES + ",\"task_scopes\":[{\"task_type\":\"review\","
                + "\"allowed_effect_classes\":[\"read\"]" + roles + "}]}"));
    }

    private static DomainBinding rolesDomain() {
        return rolesDomain("[\"credit_officer\"]");
    }

    /** The options of {@link #provenanceEvents}: JSON literals for refs.instruction, i1's source_class and
     * principal (null leaves it out), i1's parent and origin events (none when null), the delegation's status
     * (null: no refs.delegation; "unheld": a ref to no held event) and d1's decision type. */
    private record ProvOpts(
            String instruction, String sourceClass, String principal, JsonNode parent, JsonNode origin,
            String delegation, String dtype) {
        static final ProvOpts DEFAULT = new ProvOpts("\"agentce:event/i1\"", "\"user\"",
                "{\"id\": \"user:alice\", \"role\": \"credit_officer\"}", null, null, "verified",
                "agentce:CreditDecision");

        ProvOpts instruction(String i) {
            return new ProvOpts(i, sourceClass, principal, parent, origin, delegation, dtype);
        }

        ProvOpts sourceClass(String c) {
            return new ProvOpts(instruction, c, principal, parent, origin, delegation, dtype);
        }

        ProvOpts principal(String p) {
            return new ProvOpts(instruction, sourceClass, p, parent, origin, delegation, dtype);
        }

        ProvOpts parent(JsonNode p) {
            return new ProvOpts(instruction, sourceClass, principal, p, origin, delegation, dtype);
        }

        ProvOpts origin(JsonNode o) {
            return new ProvOpts(instruction, sourceClass, principal, parent, o, delegation, dtype);
        }

        ProvOpts delegation(String d) {
            return new ProvOpts(instruction, sourceClass, principal, parent, origin, d, dtype);
        }

        ProvOpts dtype(String d) {
            return new ProvOpts(instruction, sourceClass, principal, parent, origin, delegation, d);
        }
    }

    /** A consequential decision d1 executed by tc1, both acting on i1 (CND-04, SPEC §7.7.4). */
    private static List<JsonNode> provenanceEvents(ProvOpts o) {
        List<JsonNode> events = new ArrayList<>();
        events.add(event("d1", "Decision", "\"decision_type\": \"" + o.dtype() + "\", "
                + "\"refs\": {\"instruction\": " + o.instruction() + "}"));
        String principal = o.principal() == null ? "" : ", \"principal\": " + o.principal();
        events.add(event("i1", "Instruction", "\"source_class\": " + o.sourceClass() + principal));
        String refs = "\"decision\": \"agentce:event/d1\", \"instruction\": " + o.instruction();
        if (o.delegation() != null) {
            refs += ", \"delegation\": \"agentce:event/g1\"";
            if (!"unheld".equals(o.delegation())) {
                events.add(event("g1", "DelegationIssued",
                        "\"verification\": {\"status\": \"" + o.delegation() + "\"}"));
            }
        }
        events.add(event("tc1", "ToolCall", "\"tool\": {\"name\": \"book\"}, \"refs\": {" + refs + "}"));
        ObjectNode data = (ObjectNode) events.get(1).get("data");
        for (Map.Entry<String, JsonNode> e : java.util.Arrays.asList(
                Map.entry("parent", o.parent() == null ? Json.nodes().nullNode() : o.parent()),
                Map.entry("origin", o.origin() == null ? Json.nodes().nullNode() : o.origin()))) {
            if (!e.getValue().isNull()) {
                events.add(e.getValue());
                if (!data.has("refs")) {
                    data.set("refs", Json.nodes().objectNode());
                }
                ((ObjectNode) data.get("refs")).put(e.getKey(), "agentce:event/" + e.getValue().get("id").asText());
            }
        }
        return events;
    }

    private static List<JsonNode> provenanceEvents() {
        return provenanceEvents(ProvOpts.DEFAULT);
    }

    private static List<Object> provenance(List<JsonNode> events, String node, DomainBinding domain) {
        GraphStore store = Graph.buildGraph(events, domain);
        String iri = "agentce:event/" + node;
        List<String> met = store.literalValues(iri, "agentce:provenanceMet");
        List<String> judged = store.literalValues(iri, "agentce:provenanceJudged");
        return java.util.Arrays.asList(
                store.isA(iri, "agentce:ConsequentialAction"),
                met.isEmpty() ? null : met.get(0),
                judged.isEmpty() ? null : judged.get(0));
    }

    private static List<Object> provenance(List<JsonNode> events, String node) {
        return provenance(events, node, rolesDomain());
    }

    private static List<Object> provenance(List<JsonNode> events) {
        return provenance(events, "tc1");
    }

    private static List<Object> provenance(ProvOpts o) {
        return provenance(provenanceEvents(o));
    }

    private static List<JsonNode> reversed(List<JsonNode> events) {
        List<JsonNode> out = new ArrayList<>(events);
        Collections.reverse(out);
        return out;
    }

    @Test
    void cnd04PopulationIsConsequentialActions() {
        List<JsonNode> events = provenanceEvents();
        assertEquals(OPEN, provenance(events));
        assertEquals(OPEN, provenance(events, "d1"));
        // a call executing a minor decision, and the minor decision, are outside CND-04
        List<JsonNode> minor = provenanceEvents(ProvOpts.DEFAULT.dtype("agentce:MinorDecision"));
        assertEquals(OUTSIDE, provenance(minor));
        assertEquals(OUTSIDE, provenance(minor, "d1"));
    }

    @Test
    void cnd04ChainMustEndAtAnAuthorisedPrincipal() {
        for (String cls : List.of("user", "operator", "service", "agent_identified")) {
            assertEquals(OPEN, provenance(ProvOpts.DEFAULT.sourceClass("\"" + cls + "\"")));
        }
        for (String bad : List.of("\"tool_output\"", "\"retrieved\"", "\"memory_untrusted\"", "\"memory_trusted\"",
                "\"agent_unidentified\"", "null")) {
            assertEquals(SHUT, provenance(ProvOpts.DEFAULT.sourceClass(bad)));
        }
        for (String principal : java.util.Arrays.asList(null, "{}", "{\"id\": \"\"}", "\"user:alice\"")) {
            assertEquals(SHUT, provenance(ProvOpts.DEFAULT.principal(principal)));
        }
        // every instruction on the parent chain counts, not only the acted-on one
        JsonNode badParent = event("i0", "Instruction", "\"source_class\": \"tool_output\"");
        assertEquals(SHUT, provenance(ProvOpts.DEFAULT.parent(badParent)));
        // the role is read at the chain's end, the parent here
        String goodData = "\"source_class\": \"operator\", \"principal\": {\"id\": \"op:1\", \"role\": \"credit_officer\"}";
        assertEquals(OPEN, provenance(ProvOpts.DEFAULT.parent(event("i0", "Instruction", goodData))));
        // three hops: a user parent whose own parent is tool_output still fails
        JsonNode root = event("i9", "Instruction", "\"source_class\": \"tool_output\"");
        JsonNode middle = event("i0", "Instruction", goodData + ", \"refs\": {\"parent\": \"agentce:event/i9\"}");
        List<JsonNode> deep = new ArrayList<>(provenanceEvents(ProvOpts.DEFAULT.parent(middle)));
        deep.add(root);
        assertEquals(SHUT, provenance(deep));
        // arrival order does not change the reading
        assertEquals(SHUT, provenance(reversed(deep)));
        assertEquals(OPEN, provenance(reversed(provenanceEvents(
                ProvOpts.DEFAULT.parent(event("i0", "Instruction", goodData))))));
        // a parent cycle never ends at a principal: unjudged
        List<JsonNode> cycle = provenanceEvents(ProvOpts.DEFAULT.parent(event("i0", "Instruction", goodData)));
        ((ObjectNode) cycle.get(cycle.size() - 1).get("data"))
                .set("refs", Json.parse("{\"parent\": \"agentce:event/i1\"}"));
        assertEquals(UNJUDGED, provenance(cycle));
        assertEquals(SHUT, provenance(ProvOpts.DEFAULT.principal("{\"id\": \"  \", \"role\": \"credit_officer\"}")));
    }

    @Test
    void cnd04PrincipalRoleMustBeAuthorisedForTheTaskType() {
        List<JsonNode> events = provenanceEvents();
        assertEquals(SHUT, provenance(events, "tc1", rolesDomain("[\"claims_supervisor\"]")));
        // no roles declared, no role on the principal, or no scope for the task: the records cannot show it
        assertEquals(UNJUDGED, provenance(events, "tc1", rolesDomain(null)));
        assertEquals(UNJUDGED, provenance(ProvOpts.DEFAULT.principal("{\"id\": \"user:alice\"}")));
        assertEquals(UNJUDGED, provenance(events, "tc1", DomainBinding.fromDict(GATED_DOMAIN)));
        // an empty list authorises no role
        assertEquals(SHUT, provenance(events, "tc1", rolesDomain("[]")));
        // the Decision takes the task of the call executing it
        List<JsonNode> tasked = provenanceEvents();
        ((ObjectNode) tasked.get(tasked.size() - 1)).put("agentcetask", "review");
        assertEquals(SHUT, provenance(tasked, "d1", rolesDomain("[\"claims_supervisor\"]")));
    }

    @Test
    void cnd04AnInstructionThatIsNotHeldFails() {
        for (String ref : List.of("null", "\"agentce:event/i-missing\"", "\"i1\"", "\"agentce:event/g1\"")) {
            assertEquals(SHUT, provenance(ProvOpts.DEFAULT.instruction(ref)));
        }
    }

    @Test
    void cnd04OriginFromAToolCallOrUntrustedReadFails() {
        assertEquals(SHUT, provenance(ProvOpts.DEFAULT.origin(event("o1", "ToolCall", "\"tool\": {\"name\": \"fetch\"}"))));
        assertEquals(SHUT, provenance(ProvOpts.DEFAULT.origin(event("o1", "MemoryRead", "\"trust_min\": \"untrusted\""))));
        assertEquals(OPEN, provenance(ProvOpts.DEFAULT.origin(event("o1", "MemoryRead", "\"trust_min\": \"trusted\""))));
        for (String trust : java.util.Arrays.asList(null, "\"\"", "\"unknown\"", "\"Untrusted\"", "[\"untrusted\"]")) {
            JsonNode unread = event("o1", "MemoryRead", trust == null ? "" : "\"trust_min\": " + trust);
            assertEquals(UNJUDGED, provenance(ProvOpts.DEFAULT.origin(unread)));
        }
        assertEquals(SHUT, provenance(ProvOpts.DEFAULT.origin(event("o1", "ResourceAccess", ""))));
        // an origin of a type §6.2.3 does not allow there cannot be judged
        assertEquals(UNJUDGED, provenance(ProvOpts.DEFAULT.origin(event("o1", "SessionStart", ""))));
    }

    /** {@code events} with i1 recorded by the agent (self_report) and the enforcement-point witnesses appended. */
    private static List<JsonNode> selfReported(List<JsonNode> events, JsonNode... witnesses) {
        ((ObjectNode) events.get(1)).put("agentcesourceclass", "self_report");
        List<JsonNode> out = new ArrayList<>(events);
        out.addAll(List.of(witnesses));
        return out;
    }

    private static JsonNode pdAllow(String principal, String request) {
        return event("pd1", "PolicyDecision", "\"decision\": \"allow\", \"principal\": " + principal
                + ", \"refs\": {\"request\": \"" + request + "\"}");
    }

    private static JsonNode pdAllow(String principal) {
        return pdAllow(principal, "agentce:event/tc1");
    }

    @Test
    void cnd04ASelfReportedRoleMustMatchTheEnforcementPointRecord() {
        String alice = "{\"id\": \"user:alice\", \"role\": \"credit_officer\"}";
        for (String node : List.of("tc1", "d1")) {
            // the policy decision allowing the call records the same role: read as recorded
            assertEquals(OPEN, provenance(selfReported(provenanceEvents(), pdAllow(alice)), node));
            // another role for the same principal: the agent's word is contradicted
            String other = "{\"id\": \"user:alice\", \"role\": \"loan_manager\"}";
            assertEquals(SHUT, provenance(selfReported(provenanceEvents(), pdAllow(other)), node));
            // no role on the witness: the records cannot show the role
            assertEquals(UNJUDGED,
                    provenance(selfReported(provenanceEvents(), pdAllow("{\"id\": \"user:alice\"}")), node));
        }
        // an enforcement-point twin of the instruction (same content_ref, task and class) recording another role
        String same = "\"content_ref\": \"sha256:aa\", \"refs\": {\"task\": \"task-1\"}";
        JsonNode twin = event("i1-ep", "Instruction", "\"source_class\": \"user\", "
                + "\"principal\": {\"id\": \"user:alice\", \"role\": \"loan_manager\"}, " + same);
        List<JsonNode> events = provenanceEvents();
        ((ObjectNode) events.get(1).get("data")).setAll((ObjectNode) Json.parse("{" + same + "}"));
        assertEquals(SHUT, provenance(selfReported(events, twin)));
        // an instruction with no content_ref has no twin
        assertEquals(OPEN, provenance(selfReported(provenanceEvents(), twin)));
        // a witness for another principal, or allowing another request, is no witness of this role
        String mallory = "{\"id\": \"user:mallory\", \"role\": \"loan_manager\"}";
        assertEquals(OPEN, provenance(selfReported(provenanceEvents(), pdAllow(mallory))));
        assertEquals(OPEN, provenance(selfReported(
                provenanceEvents(), pdAllow("{\"id\": \"user:alice\"}", "agentce:event/tc9"))));
        // an enforcement-point instruction is read as recorded, whatever a policy decision says
        List<JsonNode> recorded = new ArrayList<>(provenanceEvents());
        recorded.add(pdAllow("{\"id\": \"user:alice\"}"));
        assertEquals(OPEN, provenance(recorded));
    }

    @Test
    void cnd04ADefiniteFailureBeatsAnUnjudgedRole() {
        List<JsonNode> events = provenanceEvents(ProvOpts.DEFAULT.delegation("unverified"));
        assertEquals(SHUT, provenance(events, "tc1", rolesDomain(null)));
        assertEquals(SHUT, provenance(events, "tc1", DomainBinding.fromDict(GATED_DOMAIN)));
    }

    @Test
    void cnd04ReadsAuthorisedPrincipalRolesStrictly() {
        List<JsonNode> events = provenanceEvents();
        // padding is trimmed on both sides; the comparison is case-sensitive
        assertEquals(OPEN, provenance(events, "tc1", rolesDomain("[\" credit_officer \"]")));
        assertEquals(OPEN, provenance(ProvOpts.DEFAULT.principal("{\"id\": \"user:alice\", \"role\": \" credit_officer\"}")));
        assertEquals(SHUT, provenance(events, "tc1", rolesDomain("[\"Credit_Officer\"]")));
        // a bare string, null or a list with a non-string member is unreadable, never a shorter list
        for (String roles : List.of("\"credit_officer\"", "null", "[\"credit_officer\", 7]", "[\"loan_manager\", null]")) {
            DomainBinding domain = DomainBinding.fromDict(Json.parse("{\"decision_types\":[{\"id\":\"agentce:CreditDecision\","
                    + "\"subclass_of\":\"agentce:ConsequentialDecision\",\"consequential\":true}],"
                    + "\"task_scopes\":[{\"task_type\":\"review\",\"allowed_effect_classes\":[\"read\"],"
                    + "\"authorised_principal_roles\":" + roles + "}]}"));
            assertEquals(UNJUDGED, provenance(events, "tc1", domain), roles);
        }
    }

    @Test
    void cnd04ReadsTheDelegationChain() {
        assertEquals(SHUT, provenance(ProvOpts.DEFAULT.delegation("unverified")));
        assertEquals(SHUT, provenance(provenanceEvents(ProvOpts.DEFAULT.delegation("unverified")), "d1"));
        // no delegation, or one the bundle does not hold: the records cannot judge it (fail closed)
        assertEquals(UNJUDGED, provenance(ProvOpts.DEFAULT.delegation(null)));
        assertEquals(UNJUDGED, provenance(ProvOpts.DEFAULT.delegation("unheld")));
    }

    // --- CND-06: refusals of instructions and of enforcement-point denies (SPEC §7.7.4; 18.173) ---

    private static final List<String> CND06_FAILS = List.of("true", "false", "true");
    private static final List<String> CND06_MEETS = List.of("true", "true", "true");
    private static final List<String> CND06_UNJUDGED = List.of("true", "true", "false");
    private static final List<String> CND06_OUTSIDE = Collections.nCopies(3, null);

    /** CND-06's literals on {@code node}: in scope, Refusal met, and whether the need for one is shown. */
    private static List<String> refusal(List<JsonNode> events, String node) {
        GraphStore store = Graph.buildGraph(events, DomainBinding.fromDict(CREDIT_DOMAIN));
        List<String> out = new ArrayList<>();
        for (String p : List.of("agentce:refusalInScope", "agentce:refusalMet", "agentce:refusalNeedShown")) {
            List<String> values = store.literalValues("agentce:event/" + node, p);
            out.add(values.isEmpty() ? null : values.get(0));
        }
        return out;
    }

    /** The agent's own Refusal of {@code instructionJson} (a self_report Refusal counts, ruling 16 A). */
    private static JsonNode cnd06Refused(String instructionJson) {
        return selfReport(event("rf1", "Refusal", "\"refs\": {\"instruction\": " + instructionJson + "}"));
    }

    private static JsonNode cnd06Refused() {
        return cnd06Refused("\"agentce:event/i1\"");
    }

    private static JsonNode cnd06User(String principalJson) {
        return event("i1", "Instruction", "\"source_class\": \"user\", \"principal\": " + principalJson);
    }

    private static JsonNode cnd06User() {
        return cnd06User("{\"kind\": \"human\", \"id\": \"u1\"}");
    }

    private static JsonNode cnd06Call(String refsJson) {
        return event("tc1", "ToolCall", "\"refs\": " + refsJson);
    }

    private static JsonNode cnd06Call() {
        return cnd06Call("{\"instruction\": \"agentce:event/i1\"}");
    }

    private static JsonNode cnd06Deny(String refsJson, String word) {
        return event("pd1", "PolicyDecision", "\"decision\": \"" + word + "\", \"refs\": " + refsJson);
    }

    private static final String DENY_TC1 = "{\"request\": \"agentce:event/tc1\"}";

    private static List<JsonNode> with(List<JsonNode> events, JsonNode... more) {
        List<JsonNode> out = new ArrayList<>(events);
        out.addAll(List.of(more));
        return out;
    }

    @Test
    void cnd06AnInstructionFromAnUntrustedOrUnknownSourceNeedsARefusal() {
        for (String data : List.of("", "\"source_class\": 7", "\"source_class\": \"tool_output\"")) {
            List<JsonNode> events = List.of(event("i1", "Instruction", data), cnd06Call());
            assertEquals(CND06_FAILS, refusal(events, "i1"), data);
            assertEquals(CND06_MEETS, refusal(with(events, cnd06Refused()), "i1"), data);
            assertEquals(CND06_FAILS, refusal(with(events, cnd06Refused("7")), "i1"), data);
        }
        assertEquals(CND06_FAILS,
                refusal(List.of(event("i1", "Instruction", "\"source_class\": \"retrieved\"")), "i1"));
    }

    @Test
    void cnd06AnAuthorisedClassItsOwnEnforcementPointRecordShowsIsOutside() {
        assertEquals(CND06_OUTSIDE, refusal(List.of(cnd06User(), cnd06Call()), "i1"));
        assertEquals(CND06_OUTSIDE, refusal(List.of(cnd06User()), "i1"));
        JsonNode memory = event("i1", "Instruction", "\"source_class\": \"memory_trusted\"");
        assertEquals(CND06_OUTSIDE, refusal(List.of(memory, cnd06Call()), "i1"));
    }

    @Test
    void cnd06AnAuthorisedClassTheRecordsDoNotShowIsUnjudged() {
        assertEquals(CND06_UNJUDGED, refusal(List.of(selfReport(cnd06User()), cnd06Call()), "i1"));
        assertEquals(CND06_UNJUDGED, refusal(List.of(selfReport(cnd06User())), "i1"));
        assertEquals(CND06_MEETS, refusal(List.of(selfReport(cnd06User()), cnd06Call(), cnd06Refused()), "i1"));
        for (String principal : List.of("{}", "{\"kind\": \"human\", \"id\": \"\"}", "\"u1\"")) {
            assertEquals(CND06_UNJUDGED, refusal(List.of(cnd06User(principal), cnd06Call()), "i1"), principal);
        }
        JsonNode bare = event("i1", "Instruction", "\"source_class\": \"user\"");
        assertEquals(CND06_UNJUDGED, refusal(List.of(bare, cnd06Call()), "i1"));
        JsonNode orphan = event("i1", "Instruction",
                "\"source_class\": \"user\", \"refs\": {\"parent\": \"agentce:event/gone\"}");
        assertEquals(CND06_UNJUDGED, refusal(List.of(orphan), "i1"));
    }

    @Test
    void cnd06AnEnforcementPointDenyNeedsARefusalOfTheInstructionItBlocked() {
        List<JsonNode> events = List.of(cnd06User(), cnd06Call(), cnd06Deny(DENY_TC1, "deny"));
        assertEquals(CND06_FAILS, refusal(events, "pd1"));
        assertEquals(CND06_MEETS, refusal(with(events, cnd06Refused()), "pd1"));
        assertEquals(CND06_FAILS, refusal(with(events, cnd06Refused("\"agentce:event/i2\"")), "pd1"));
        JsonNode independent = cnd06Deny(DENY_TC1, "deny");
        ((ObjectNode) independent).put("agentcesourceclass", "independent_system");
        assertEquals(CND06_FAILS, refusal(List.of(cnd06User(), cnd06Call(), independent), "pd1"));
        for (JsonNode other : List.of(selfReport(cnd06Deny(DENY_TC1, "deny")), cnd06Deny(DENY_TC1, "allow"),
                cnd06Deny(DENY_TC1, "Deny"))) {
            assertEquals(CND06_OUTSIDE, refusal(List.of(cnd06User(), cnd06Call(), other), "pd1"));
        }
    }

    @Test
    void cnd06ADenyWhoseRequestCannotShowItsInstructionIsUnjudged() {
        for (String refs : List.of("{}", "{\"request\": 7}", "{\"request\": \"agentce:event/gone\"}")) {
            assertEquals(CND06_UNJUDGED, refusal(List.of(cnd06Deny(refs, "deny")), "pd1"), refs);
        }
        assertEquals(CND06_OUTSIDE, refusal(List.of(cnd06Call("{}"), cnd06Deny(DENY_TC1, "deny")), "pd1"));
        assertEquals(CND06_OUTSIDE,
                refusal(List.of(cnd06Call("{\"instruction\": null}"), cnd06Deny(DENY_TC1, "deny")), "pd1"));
        assertEquals(CND06_UNJUDGED,
                refusal(List.of(selfReport(cnd06Call("{}")), cnd06Deny(DENY_TC1, "deny")), "pd1"));
    }

    @Test
    void cnd06ADenyOfACallExecutingAnInstructedDecisionNeedsARefusal() {
        JsonNode instructed = selfReport(decisionWith("d1", "\"refs\": {\"instruction\": \"agentce:event/i1\"}"));
        JsonNode executes = cnd06Call("{\"decision\": \"agentce:event/d1\"}");
        List<JsonNode> events = List.of(cnd06User(), instructed, executes, cnd06Deny(DENY_TC1, "deny"));
        assertEquals(CND06_FAILS, refusal(events, "pd1"));
        assertEquals(CND06_MEETS, refusal(with(events, cnd06Refused()), "pd1"));
        assertEquals(CND06_UNJUDGED, refusal(List.of(cnd06User(), executes, cnd06Deny(DENY_TC1, "deny")), "pd1"));
    }

    @Test
    void cnd06TheLiteralsAreIndependentOfEventOrder() {
        List<JsonNode> events = List.of(
                event("i1", "Instruction", "\"source_class\": \"tool_output\""),
                cnd06Call(),
                cnd06Deny(DENY_TC1, "deny"),
                cnd06Refused(),
                selfReport(event("i2", "Instruction", "\"source_class\": \"user\"")));
        List<JsonNode> reversed = new ArrayList<>(events);
        Collections.reverse(reversed);
        for (String node : List.of("i1", "pd1", "i2")) {
            assertEquals(refusal(events, node), refusal(reversed, node), node);
        }
    }
}
