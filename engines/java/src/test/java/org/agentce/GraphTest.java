package org.agentce;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.fasterxml.jackson.databind.JsonNode;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.ArrayList;
import java.util.Collections;
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
    void rob02RuledOnWhenTheGuardRuledOnEveryInputOrThereWereNone() {
        JsonNode write = event("w1", "MemoryWrite", "\"record_ref\": \"mem:r1\", \"trust\": \"trusted\"");
        assertEquals(List.of("true"), ruled(List.of(write, decisionWith("d1", "\"inputs\": [\"mem:r1\"]"))));
        assertEquals(List.of("true"), ruled(List.of(decisionEvent("d1"))));
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
    void rob02NotRuledOnARefTheBundleDoesNotHold() {
        for (String ref : List.of("mem:missing", "agentce:event/missing")) {
            assertEquals(List.of("false"), ruled(List.of(decisionWith("d1", "\"inputs\": [\"" + ref + "\"]"))), ref);
        }
        JsonNode other = decisionWith("d2", "\"inputs\": [\"mem:missing\"]");
        assertEquals(List.of("true"), ruled(List.of(decisionEvent("d1"), other)));
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
}
