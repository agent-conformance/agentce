package org.agentce;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.stream.Stream;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.Arguments;
import org.junit.jupiter.params.provider.MethodSource;

/**
 * A control's min_source_class, enforced per assertion (SPEC §6.4, HR-5, §7.7.5; item 18.127): the judged class
 * of a focus, the attestation of an instruction it acts on (each of conditions (i)-(iv) and both corroboration
 * branches, with hostile twins that differ from an attested one by one field), the refusal of an unknown minimum
 * at catalog load, and the downgrade, which only ever turns a conformant verdict into insufficient_evidence. Mirrors
 * the Python reference's {@code tests/test_source_class.py}.
 */
class SourceClassTest {
    private static final Path CATALOGS = TestPaths.repoRoot().resolve("spec/catalogs");
    private static final String SUBJECT = "spiffe://corp/agents/source-class";
    private static final String OTHER = "spiffe://corp/agents/other";
    private static final String EP = "enforcement_point";
    private static final String SR = "self_report";
    private static final String IS = "independent_system";
    private static final String ALICE = "{\"id\": \"user:alice\", \"kind\": \"human\"}";
    private static final String MALLORY = "{\"id\": \"user:mallory\", \"kind\": \"human\"}";
    private static final String CONTENT = "sha256:" + "a".repeat(64);
    private static final DomainBinding DOMAIN = DomainBinding.fromDict(Json.parse(
            "{\"decision_types\": [{\"id\": \"dom:CreditDecision\", "
                    + "\"subclass_of\": \"agentce:ConsequentialDecision\", \"consequential\": true}]}"));

    private static String iri(String id) {
        return Iri.eventIri(id);
    }

    private static ObjectNode event(String id, String ptype, String cls, String subject, String data) {
        ObjectNode e = Json.nodes().objectNode();
        e.put("id", id);
        e.put("source", "urn:src:" + cls);
        e.put("subject", subject);
        e.put("type", "org.agent-conformance.evidence." + ptype + ".v1");
        e.put("agentcesourceclass", cls);
        e.put("time", String.format("2026-01-02T00:00:%02dZ", id.length()));
        e.put("specversion", "1.0");
        ObjectNode d = (ObjectNode) Json.parse("{" + data + "}");
        ObjectNode full = Json.nodes().objectNode();
        full.put("@type", ptype);
        full.setAll(d);
        e.set("data", full);
        return e;
    }

    private static ObjectNode event(String id, String ptype, String cls, String data) {
        return event(id, ptype, cls, SUBJECT, data);
    }

    private static ObjectNode data(JsonNode e) {
        return (ObjectNode) e.get("data");
    }

    private static ObjectNode refs(JsonNode e) {
        return (ObjectNode) data(e).get("refs");
    }

    /** An instruction: source_class user, principal alice, content CONTENT, refs.task task-1. */
    private static ObjectNode instruction(String id, String cls) {
        return event(id, "Instruction", cls, "\"source_class\": \"user\", \"principal\": " + ALICE
                + ", \"content_ref\": \"" + CONTENT + "\", \"refs\": {\"task\": \"task-1\"}");
    }

    private static ObjectNode instruction(String cls) {
        return instruction("i1", cls);
    }

    private static ObjectNode with(ObjectNode e, String field, JsonNode value) {
        if (value == null) {
            data(e).remove(field);
        } else {
            data(e).set(field, value);
        }
        return e;
    }

    private static ObjectNode withRef(ObjectNode e, String key, String value) {
        refs(e).put(key, value);
        return e;
    }

    private static JsonNode text(String value) {
        return Json.nodes().textNode(value);
    }

    private static ObjectNode call(String cls) {
        return event("tc1", "ToolCall", cls, "\"agent\": {\"id\": \"" + SUBJECT + "\"}, "
                + "\"tool\": {\"name\": \"lookup\", \"protocol\": \"native\"}, \"effect_class\": \"read\", "
                + "\"refs\": {\"instruction\": \"" + iri("i1") + "\"}");
    }

    private static ObjectNode call() {
        return call(EP);
    }

    private static ObjectNode policy(String cls, String decision, String principal, String request) {
        return event("pd1", "PolicyDecision", cls, "\"engine\": \"custom\", \"policy_id\": \"ingress\", "
                + "\"policy_version\": \"1\", \"decision\": \"" + decision + "\", "
                + (principal == null ? "" : "\"principal\": " + principal + ", ")
                + "\"refs\": {\"request\": \"" + iri(request) + "\"}");
    }

    private static ObjectNode policy() {
        return policy(EP, "allow", ALICE, "tc1");
    }

    private static ObjectNode read(String trustMin, String cls) {
        return event("m1", "MemoryRead", cls, "\"store\": \"kb\", \"record_refs\": [\"mem:r1\"]"
                + (trustMin == null ? "" : ", \"trust_min\": \"" + trustMin + "\""));
    }

    private static ObjectNode twin() {
        ObjectNode twin = instruction("i1-ingress", EP);
        twin.put("time", "2026-01-02T00:00:00Z"); // at ingress, before the call
        return twin;
    }

    private static SourceClass.Judge judge(List<? extends JsonNode> events) {
        List<JsonNode> own = new ArrayList<>();
        Map<String, JsonNode> byIri = new LinkedHashMap<>();
        for (JsonNode e : events) {
            if (SUBJECT.equals(e.get("subject").asText())) {
                own.add(e);
                byIri.put(iri(e.get("id").asText()), e);
            }
        }
        return new SourceClass.Judge(Graph.buildGraph(own, DOMAIN), byIri);
    }

    private static boolean attested(JsonNode... events) {
        return judge(List.of(events)).attested(iri("tc1"), iri("i1"));
    }

    // --- the class order and judged_class ---

    @Test
    void theClassOrderIsTheSpecOrderAndAnythingElseRanksLowest() {
        assertEquals(0, SourceClass.RANK.get(SR));
        assertEquals(1, SourceClass.RANK.get(EP));
        assertEquals(2, SourceClass.RANK.get(IS));
        for (JsonNode odd : List.of(text("any"), text("Enforcement_Point"), text(""),
                Json.nodes().nullNode(), Json.nodes().numberNode(1))) {
            assertEquals(0, SourceClass.classRank(odd));
        }
        assertEquals(0, SourceClass.classRank(null));
    }

    @Test
    void judgedClassIsTheLowestOfTheFocusAndWhatItActsOn() {
        String focus = iri("tc1");
        SourceClass.Judged judged = judge(List.of(instruction(SR), call())).judgedClass(focus, List.of("actsOn"));
        assertEquals(0, judged.rank());
        assertEquals(List.of("Instruction"), judged.lowest());
        assertEquals(1, judge(List.of(instruction(EP), call())).judgedClass(focus, List.of("actsOn")).rank());
        judged = judge(List.of(instruction(EP), call(SR))).judgedClass(focus, List.of("actsOn"));
        assertEquals(0, judged.rank());
        assertEquals(List.of("ToolCall"), judged.lowest());
        // Without source_class_from only the focus's own class counts.
        assertEquals(1, judge(List.of(instruction(SR), call())).judgedClass(focus, List.of()).rank());
    }

    @Test
    void aDecisionCountsOnlyWhatItRestsOn() {
        ObjectNode decision = event("d1", "Decision", IS,
                "\"decision_type\": \"dom:CreditDecision\", \"refs\": {\"instruction\": \"" + iri("i1") + "\"}");
        // Its own independent_system class is not counted.
        assertEquals(0, judge(List.of(instruction(SR), decision)).judgedClass(iri("d1"), List.of("actsOn")).rank());
        assertEquals(1, judge(List.of(instruction(EP), decision)).judgedClass(iri("d1"), List.of("actsOn")).rank());
    }

    @Test
    void aDecisionThatReachesNothingIsJudgedThroughItsCallsOrAsSelfReport() {
        ObjectNode lone = event("d2", "Decision", SR, "\"decision_type\": \"dom:CreditDecision\"");
        SourceClass.Judged judged = judge(List.of(lone)).judgedClass(iri("d2"), List.of("actsOn"));
        assertEquals(0, judged.rank());
        assertEquals(List.of("Decision"), judged.lowest());
        String tool = "\"agent\": {\"id\": \"" + SUBJECT + "\"}, "
                + "\"tool\": {\"name\": \"lookup\", \"protocol\": \"native\"}, \"effect_class\": \"read\", ";
        ObjectNode executes = event("tc1", "ToolCall", EP, tool
                + "\"refs\": {\"instruction\": \"" + iri("i1") + "\", \"decision\": \"" + iri("d2") + "\"}");
        assertEquals(1, judge(List.of(instruction(EP), lone, executes)).judgedClass(iri("d2"), List.of("actsOn")).rank());
        assertEquals(0, judge(List.of(instruction(SR), lone, executes)).judgedClass(iri("d2"), List.of("actsOn")).rank());
        ObjectNode bare = event("tc1", "ToolCall", EP, tool + "\"refs\": {\"decision\": \"" + iri("d2") + "\"}");
        judged = judge(List.of(lone, bare)).judgedClass(iri("d2"), List.of("actsOn"));
        assertEquals(0, judged.rank());
        assertEquals(List.of("Decision"), judged.lowest());
    }

    @Test
    void anUnattestedIndependentSystemInstructionCountsAsSelfReport() {
        SourceClass.Judged judged = judge(List.of(instruction(IS), call())).judgedClass(iri("tc1"), List.of("actsOn"));
        assertNotNull(judged);
        assertEquals(0, judged.rank());
    }

    // --- attestation (i)-(iv) ---

    @Test
    void iAnInstructionIsAttestedOnlyWhenHeldByItsGraphIri() {
        assertTrue(attested(instruction(EP), call()));
        ObjectNode bare = withRef(call(), "instruction", "i1");
        assertFalse(judge(List.of(instruction(EP), bare)).attested(iri("tc1"), "i1"));
        ObjectNode other = instruction(EP);
        other.put("subject", OTHER);
        assertFalse(attested(other, call()));
    }

    @Test
    void iiEveryParentMustBeHeldAndAttested() {
        ObjectNode parent = with(instruction("i0", EP), "content_ref", text("sha256:" + "0".repeat(64)));
        ObjectNode child = withRef(instruction(EP), "parent", iri("i0"));
        assertTrue(attested(parent, child, call()));
        ObjectNode srParent = with(instruction("i0", SR), "content_ref", text("sha256:" + "0".repeat(64)));
        assertFalse(attested(srParent, child, call()));
        assertFalse(attested(child, call())); // the parent is not held
        ObjectNode cycle = withRef(instruction("i0", EP), "parent", iri("i1"));
        assertTrue(attested(cycle, child, call()));
    }

    static Stream<Arguments> origins() {
        return Stream.of(
                Arguments.of("ep-trusted", read("trusted", EP), true),
                Arguments.of("ep-untrusted", read("untrusted", EP), false),
                Arguments.of("ep-no-trust-min", read(null, EP), false),
                Arguments.of("self-report-read", read("trusted", SR), false));
    }

    @ParameterizedTest(name = "{0}")
    @MethodSource("origins")
    void iiiAnOriginMustBeAnEnforcementPointReadAtTrustMinTrusted(String name, JsonNode read, boolean expected) {
        ObjectNode instruction = withRef(with(instruction(EP), "source_class", text("memory_trusted")), "origin", iri("m1"));
        assertEquals(expected, attested(read, instruction, call()));
        ObjectNode gone = withRef(with(instruction(EP), "source_class", text("memory_trusted")), "origin", iri("gone"));
        assertFalse(attested(gone, call()));
    }

    @Test
    void ivASelfReportInstructionIsNotAttestedOnItsOwn() {
        assertFalse(attested(instruction(SR), call()));
    }

    @Test
    void ivAnAttestedEnforcementPointTwinWitnessesTheClass() {
        assertTrue(attested(instruction(SR), twin(), call()));
        ObjectNode nobody = with(with(instruction(SR), "principal", null), "source_class", text("memory_trusted"));
        ObjectNode twin = with(with(twin(), "principal", null), "source_class", text("memory_trusted"));
        assertTrue(attested(nobody, twin, call()));
    }

    @Test
    void ivAnEnforcementPointPolicyDecisionForTheFocusWitnessesTheClass() {
        assertTrue(attested(instruction(SR), call(), policy()));
    }

    static Stream<Arguments> hostile() {
        ObjectNode otherSubject = twin();
        otherSubject.put("subject", OTHER);
        ObjectNode srTwin = twin();
        srTwin.put("agentcesourceclass", SR);
        return Stream.of(
                Arguments.of("twin-other-content", List.of(with(twin(), "content_ref", text("sha256:" + "b".repeat(64))))),
                Arguments.of("twin-other-task", List.of(withRef(twin(), "task", "task-2"))),
                Arguments.of("twin-other-subject", List.of(otherSubject)),
                Arguments.of("twin-other-authorised-class", List.of(with(twin(), "source_class", text("operator")))),
                Arguments.of("twin-other-principal", List.of(with(twin(), "principal", Json.parse(MALLORY)))),
                Arguments.of("twin-self-report-parent", List.of(
                        withRef(twin(), "parent", iri("i0")),
                        with(instruction("i0", SR), "content_ref", text("x")))),
                Arguments.of("twin-tool_output-parent", List.of(
                        withRef(twin(), "parent", iri("i0")),
                        with(with(instruction("i0", EP), "source_class", text("tool_output")), "content_ref", text("x")))),
                Arguments.of("twin-untrusted-origin", List.of(
                        withRef(twin(), "origin", iri("m1")), read("untrusted", EP))),
                Arguments.of("pd-other-request", List.of(policy(EP, "allow", ALICE, "tc9"))),
                Arguments.of("pd-self-report", List.of(policy(SR, "allow", ALICE, "tc1"))),
                Arguments.of("pd-independent-system", List.of(policy(IS, "allow", ALICE, "tc1"))),
                Arguments.of("pd-deny", List.of(policy(EP, "deny", ALICE, "tc1"))),
                Arguments.of("pd-other-principal", List.of(policy(EP, "allow", MALLORY, "tc1"))),
                Arguments.of("pd-no-principal", List.of(policy(EP, "allow", null, "tc1"))),
                Arguments.of("twin-self-report", List.of(srTwin)));
    }

    @ParameterizedTest(name = "{0}")
    @MethodSource("hostile")
    void hostileCorroborationNeverLiftsASelfReportInstruction(String name, List<JsonNode> hostile) {
        List<JsonNode> events = new ArrayList<>(List.of(instruction(SR), call()));
        events.addAll(hostile);
        assertFalse(attested(events.toArray(JsonNode[]::new)));
    }

    @Test
    void aPolicyDecisionDoesNotLiftASelfReportParent() {
        ObjectNode parent = with(instruction("i0", SR), "content_ref", text("sha256:" + "0".repeat(64)));
        ObjectNode child = withRef(instruction(SR), "parent", iri("i0"));
        assertFalse(attested(parent, child, call(), policy()));
        // Nor does it lift a self-report parent below an enforcement-point child.
        ObjectNode epChild = withRef(instruction(EP), "parent", iri("i0"));
        assertFalse(attested(parent, epChild, call(), policy()));
    }

    @Test
    void aCorroboratedInstructionStillNeedsItsOrigin() {
        ObjectNode instruction = withRef(instruction(SR), "origin", iri("m1"));
        assertFalse(attested(read("untrusted", EP), instruction, call(), policy()));
        assertTrue(attested(read("trusted", EP), instruction, call(), policy()));
    }

    // --- the refusal of an unknown minimum ---

    static Stream<Arguments> unknownMinimums() {
        return Stream.of(
                Arguments.of("independent", "min_source_class: independent", "\"independent\""),
                Arguments.of("missing", null, "(missing)"),
                Arguments.of("null", "min_source_class: null", "null"),
                Arguments.of("empty", "min_source_class: \"\"", "\"\""),
                Arguments.of("number", "min_source_class: 1", "1"),
                Arguments.of("case", "min_source_class: Enforcement_Point", "\"Enforcement_Point\""),
                Arguments.of("any", "min_source_class: any", "\"any\""));
    }

    private static void copyTree(Path from, Path to) throws IOException {
        try (Stream<Path> walk = Files.walk(from)) {
            for (Path p : (Iterable<Path>) walk::iterator) {
                Path target = to.resolve(from.relativize(p).toString());
                if (Files.isDirectory(p)) {
                    Files.createDirectories(target);
                } else {
                    Files.copy(p, target);
                }
            }
        }
    }

    @ParameterizedTest(name = "{0}")
    @MethodSource("unknownMinimums")
    void anUnknownMinimumIsRefusedAtLoad(String name, String line, String shown, @TempDir Path tmp) throws IOException {
        Path catalogDir = tmp.resolve("conduct");
        copyTree(CATALOGS.resolve("overlays/conduct"), catalogDir);
        Path control = catalogDir.resolve("controls/CND-05.yaml");
        String text = Files.readString(control, StandardCharsets.UTF_8);
        String replacement = line == null ? "" : "  " + line + "\n";
        String changed = text.replace("  min_source_class: enforcement_point\n", replacement);
        assertFalse(changed.equals(text));
        Files.writeString(control, changed, StandardCharsets.UTF_8);
        InputError error = assertThrows(InputError.class, () -> Catalog.load(catalogDir));
        assertEquals("catalog.control.min_source_class_unknown", error.key);
        assertEquals(3, error.exitCode);
        assertEquals("control CND-05 declares min_source_class " + shown + ", which is not a source class: the value "
                + "must be exactly self_report, enforcement_point or independent_system (SPEC §6.4), so the engine "
                + "could not tell which records the control accepts.", error.reason);
    }

    static Stream<Arguments> unknownSourceClassFrom() {
        return Stream.of(
                Arguments.of("case", "source_class_from: [actson]", "[\"actson\"]"),
                Arguments.of("string", "source_class_from: actsOn", "\"actsOn\""),
                Arguments.of("unknown", "source_class_from: [parent]", "[\"parent\"]"),
                Arguments.of("null", "source_class_from: null", "null"),
                Arguments.of("number", "source_class_from: [actsOn, 1]", "[\"actsOn\", 1]"));
    }

    @ParameterizedTest(name = "{0}")
    @MethodSource("unknownSourceClassFrom")
    void anUnknownSourceClassFromIsRefusedAtLoad(String name, String line, String shown, @TempDir Path tmp)
            throws IOException {
        Path catalogDir = tmp.resolve("conduct");
        copyTree(CATALOGS.resolve("overlays/conduct"), catalogDir);
        Path control = catalogDir.resolve("controls/CND-05.yaml");
        String text = Files.readString(control, StandardCharsets.UTF_8);
        String changed = text.replace("  source_class_from: [actsOn]\n", "  " + line + "\n");
        assertFalse(changed.equals(text));
        Files.writeString(control, changed, StandardCharsets.UTF_8);
        InputError error = assertThrows(InputError.class, () -> Catalog.load(catalogDir));
        assertEquals("catalog.control.source_class_from_unknown", error.key);
        assertEquals(3, error.exitCode);
        assertTrue(error.reason.startsWith("control CND-05 declares source_class_from " + shown + ", "), error.reason);
    }

    @Test
    void everyBundledAndSpecCatalogDeclaresAKnownMinimum() throws IOException {
        List<Path> folders = new ArrayList<>();
        for (Path root : List.of(CATALOGS, TestPaths.repoRoot().resolve("engines/java/src/main/resources/catalogs"))) {
            try (Stream<Path> walk = Files.walk(root, 3)) {
                walk.filter(p -> p.getFileName().toString().equals("catalog.yaml"))
                        .forEach(p -> folders.add(p.getParent()));
            }
        }
        assertFalse(folders.isEmpty());
        for (Path folder : folders) {
            for (Catalog.ControlSpec control : Catalog.load(folder).controls) {
                assertTrue(SourceClass.RANK.containsKey(control.minSourceClass), folder + " " + control.id);
            }
        }
    }

    // --- the downgrade, through assess ---

    private static Catalog conduct() {
        return Catalog.load(CATALOGS.resolve("overlays/conduct"));
    }

    private static Assertions.Assertion assess(List<? extends JsonNode> events, String control) {
        return assess(events, control, DOMAIN);
    }

    private static Assertions.Assertion assess(
            List<? extends JsonNode> events, String control, DomainBinding domain) {
        Profile profile = Profile.fromDict(Json.parse("{\"subjects\": [{\"id\": \"" + SUBJECT
                + "\", \"role\": \"both\"}, {\"id\": \"" + OTHER + "\", \"role\": \"both\"}]}"));
        for (Assertions.Assertion a : Assess.assessSubjects(
                new ArrayList<>(events), profile, List.of(conduct()), domain, List.of())) {
            if (a.control.equals(control) && a.subject.equals(SUBJECT)) {
                return a;
            }
        }
        throw new AssertionError("no " + control);
    }

    private static Assertions.Assertion assess(List<? extends JsonNode> events) {
        return assess(events, "CND-05");
    }

    @Test
    void aSelfReportInstructionReadsInsufficientEvidenceNamingWhatIsMissing() {
        Assertions.Assertion assertion = assess(List.of(instruction(SR), call()));
        assertEquals("insufficient_evidence", SourceClass.BELOW_MINIMUM_OUTCOME);
        assertEquals(SourceClass.BELOW_MINIMUM_OUTCOME, assertion.outcome);
        assertEquals(Boolean.FALSE, assertion.sourceClassSatisfied);
        assertEquals(List.of(iri("tc1")), assertion.evidence.stream().map(e -> e.ref).toList());
        assertEquals(List.of("Instruction"), assertion.classGap);
        assertTrue(assertion.toJson().has("source_class_satisfied"));
        assertTrue(assertion.violations.isEmpty());
        assertNull(assertion.evidenceStrength);
    }

    @Test
    void anAttestedInstructionStaysConformant() {
        for (List<ObjectNode> events : List.of(
                List.of(instruction(EP), call()),
                List.of(instruction(SR), twin(), call()),
                List.of(instruction(SR), call(), policy()))) {
            Assertions.Assertion assertion = assess(events);
            assertEquals("conformant", assertion.outcome);
            assertEquals(Boolean.TRUE, assertion.sourceClassSatisfied);
        }
    }

    @Test
    void theOtherSubjectsTwinNeverLiftsThisSubjectsInstruction() {
        ObjectNode other = twin();
        other.put("subject", OTHER);
        assertEquals("insufficient_evidence", assess(List.of(instruction(SR), other, call())).outcome);
    }

    @Test
    void theDowngradeNeverTouchesANonConformantVerdict() {
        Assertions.Assertion assertion =
                assess(List.of(with(instruction(SR), "source_class", text("tool_output")), call()));
        assertEquals("non-conformant", assertion.outcome);
        assertEquals(Boolean.TRUE, assertion.sourceClassSatisfied);
        assertTrue(assertion.classGap.isEmpty());
    }

    @Test
    void aDecisionActingOnNothingIsNotHeldToAClass() {
        ObjectNode decision = event("d1", "Decision", SR, "\"decision_type\": \"dom:CreditDecision\"");
        ObjectNode call = withRef(call(), "decision", iri("d1"));
        assertEquals("conformant", assess(List.of(instruction(EP), call, decision)).outcome);
    }

    @Test
    void cnd04ReadsTheDelegationItRestsOn() {
        // CND-04 reads the chain's last principal's role against the task type's scope (SPEC §7.7.4, inbox 355).
        DomainBinding roles = DomainBinding.fromDict(Json.parse(
                "{\"decision_types\": [{\"id\": \"dom:CreditDecision\", "
                        + "\"subclass_of\": \"agentce:ConsequentialDecision\", \"consequential\": true}], "
                        + "\"task_scopes\": [{\"task_type\": \"review\", "
                        + "\"authorised_principal_roles\": [\"credit_officer\"]}]}"));
        ObjectNode decision = event("d1", "Decision", SR, "\"decision_type\": \"dom:CreditDecision\", "
                + "\"refs\": {\"instruction\": \"" + iri("i1") + "\"}");
        ObjectNode call = withRef(call(), "decision", iri("d1"));
        // No delegation the action rests on: the records cannot show the chain verified (ruling 14 B).
        assertEquals("insufficient_evidence",
                assess(List.of(officer(), call, decision), "CND-04", roles).outcome);
        ObjectNode delegated = withRef(withRef(call(), "decision", iri("d1")), "delegation", iri("dl1"));
        // A delegation the records do not hold.
        assertEquals("insufficient_evidence",
                assess(List.of(officer(), delegated, decision), "CND-04", roles).outcome);
        Assertions.Assertion downgraded =
                assess(List.of(officer(), delegated, decision, delegation(SR, "verified")), "CND-04", roles);
        assertEquals("insufficient_evidence", downgraded.outcome);
        assertEquals(List.of("DelegationIssued"), downgraded.classGap);
        assertEquals("conformant", assess(
                List.of(officer(), delegated, decision, delegation(EP, "verified")), "CND-04", roles).outcome);
        assertEquals("non-conformant", assess(
                List.of(officer(), delegated, decision, delegation(EP, "failed")), "CND-04", roles).outcome);
    }

    /** An enforcement-point instruction whose principal, alice, holds the credit_officer role. */
    private static ObjectNode officer() {
        ObjectNode i = instruction(EP);
        ((ObjectNode) data(i).get("principal")).put("role", "credit_officer");
        return i;
    }

    private static ObjectNode delegation(String cls, String status) {
        return event("dl1", "DelegationIssued", cls, "\"token_ref\": \"jti-1\", "
                + "\"issuer\": \"https://idp.example\", \"chain\": [{\"id\": \"user:alice\", \"kind\": \"human\"}, "
                + "{\"id\": \"" + SUBJECT + "\", \"kind\": \"agent\"}], "
                + "\"verification\": {\"status\": \"" + status + "\", \"method\": \"rfc8693\"}");
    }

    @Test
    void aBlindSpotNamesTheRecordBelowTheMinimum() {
        Profile profile = Profile.fromDict(Json.parse("{\"subjects\": [{\"id\": \"" + SUBJECT + "\", \"role\": \"both\"}]}"));
        List<JsonNode> events = List.of(instruction(SR), call());
        List<Catalog> catalogs = List.of(conduct());
        List<Assertions.Assertion> assertions = Assess.assessSubjects(events, profile, catalogs, DOMAIN, List.of());
        ObjectNode spots = BlindSpots.computeBlindSpots(assertions, profile, catalogs, events);
        JsonNode spot = null;
        for (JsonNode s : spots.get("blind_spots")) {
            if ("Instruction".equals(s.get("event").asText())) {
                spot = s;
            }
        }
        assertNotNull(spot);
        assertEquals("enforcement_point", spot.get("class").asText());
        assertEquals(0, spot.get("checks_unlocked").asInt());
        assertTrue(spot.get("needed_by").asInt() >= 1);
    }

    // --- ROB-02 ---

    private static Assertions.Assertion rob02(List<? extends JsonNode> events) {
        Profile profile = Profile.fromDict(Json.parse("{\"subjects\": [{\"id\": \"" + SUBJECT + "\", \"role\": \"both\"}]}"));
        Catalog catalog = Catalog.load(CATALOGS.resolve("base/baseline"));
        for (Assertions.Assertion a : Assess.assessSubjects(
                new ArrayList<>(events), profile, List.of(catalog), DOMAIN, List.of())) {
            if ("ROB-02".equals(a.control)) {
                return a;
            }
        }
        throw new AssertionError("no ROB-02");
    }

    private static List<ObjectNode> memory() {
        return List.of(
                event("w0", "MemoryWrite", EP, "\"store\": \"kb\", \"record_ref\": \"mem:r0\", "
                        + "\"trust\": \"trusted\", \"guard_verdict\": \"allow\""),
                event("g0", "MemoryRead", EP, "\"store\": \"kb\", \"record_refs\": [\"mem:r0\"], "
                        + "\"trust_min\": \"trusted\""));
    }

    private static List<ObjectNode> plus(List<ObjectNode> base, ObjectNode... more) {
        List<ObjectNode> all = new ArrayList<>(base);
        all.addAll(List.of(more));
        return all;
    }

    @Test
    void rob02AnInstructionWithoutASourceClassTaintsTheDecision() {
        ObjectNode decision = event("d1", "Decision", SR, "\"decision_type\": \"dom:CreditDecision\", "
                + "\"inputs\": [\"mem:r0\"], \"refs\": {\"instruction\": \"" + iri("i1") + "\"}");
        assertEquals("conformant", rob02(plus(memory(), instruction(EP), decision)).outcome);
        ObjectNode unclassed = with(instruction(EP), "source_class", null);
        assertEquals("non-conformant", rob02(plus(memory(), unclassed, decision)).outcome);
    }

    @Test
    void rob02ADecisionThatUsedNothingRecordedReadsInsufficientEvidence() {
        ObjectNode used = event("d1", "Decision", SR, "\"decision_type\": \"dom:CreditDecision\", \"inputs\": [\"mem:r0\"]");
        assertEquals("conformant", rob02(plus(memory(), used)).outcome);
        ObjectNode nothing = event("d1", "Decision", SR, "\"decision_type\": \"dom:CreditDecision\"");
        assertEquals("insufficient_evidence", rob02(plus(memory(), nothing)).outcome);
    }

    @Test
    void anOddMinimumIsShownAsPythonsJsonDumps() {
        assertEquals("[\"a\", 1]", Catalog.pyDumps(Json.parse("[\"a\", 1]")));
        assertEquals("{\"k\": true}", Catalog.pyDumps(Json.parse("{\"k\": true}")));
        assertEquals("\"é\"", Catalog.pyDumps(text("é")));
    }

    // --- CND-02: the minimum governs the approvals that release an action's gate (SPEC §7.7.4; 18.187) ------

    private static List<ObjectNode> cnd02Events(List<String> approvals, String callClass, String decision) {
        List<ObjectNode> events = new ArrayList<>();
        events.add(event("d1", "Decision", SR, "\"decision_type\": \"dom:CreditDecision\""));
        for (int i = 0; i < approvals.size(); i++) {
            int n = i + 1;
            String human = "{\"id\": \"user:approver-" + n + "\", \"kind\": \"human\"}";
            events.add(event("login" + n, "SessionStart", IS,
                    "\"agent\": {\"id\": \"urn:example:service:identity-provider\"}, "
                            + "\"environment\": \"idp-login\", \"principal\": " + human));
            events.add(event("ap" + n, "ApprovalDecided", approvals.get(i),
                    "\"outcome\": \"approve\", \"refs\": {\"decision\": \"" + iri("d1") + "\"}, \"actor\": " + human
                            + ", \"session_ref\": \"" + iri("login" + n) + "\""));
        }
        events.add(policy(EP, decision, "{\"id\": \"" + SUBJECT + "\", \"kind\": \"agent\"}", "tc1"));
        events.add(event("tc1", "ToolCall", callClass, "\"agent\": {\"id\": \"" + SUBJECT + "\"}, "
                + "\"tool\": {\"name\": \"book\", \"protocol\": \"native\"}, \"effect_class\": \"irreversible\", "
                + "\"refs\": {\"decision\": \"" + iri("d1") + "\", \"authorization\": \"" + iri("pd1") + "\"}"));
        for (int n = 0; n < events.size(); n++) {
            events.get(n).put("time", String.format("2026-01-02T00:00:%02dZ", n));
        }
        return events;
    }

    private static List<ObjectNode> cnd02Events(List<String> approvals, String callClass) {
        return cnd02Events(approvals, callClass, "require_approval");
    }

    private static List<ObjectNode> cnd02Events(List<String> approvals) {
        return cnd02Events(approvals, EP);
    }

    @Test
    void cnd02AnActionRestsOnTheBestApprovalThatReleasesIt() {
        String focus = iri("tc1");
        List<String> from = List.of("releasedBy");
        assertEquals(new SourceClass.Judged(SourceClass.RANK.get(IS), List.of("ApprovalDecided")),
                judge(cnd02Events(List.of(IS))).judgedClass(focus, from));
        assertEquals(new SourceClass.Judged(SourceClass.RANK.get(EP), List.of("ApprovalDecided")),
                judge(cnd02Events(List.of(EP))).judgedClass(focus, from));
        // one approval meeting the minimum releases the gate beside a weaker one
        assertEquals((int) SourceClass.RANK.get(IS), judge(cnd02Events(List.of(EP, IS))).judgedClass(focus, from).rank());
        // an action only the agent recorded stays self_report, whatever approved it
        assertEquals(new SourceClass.Judged(SourceClass.RANK.get(SR), List.of("ToolCall")),
                judge(cnd02Events(List.of(IS), SR)).judgedClass(focus, from));
    }

    @Test
    void cnd02OutcomesThroughAssess() {
        assertEquals("conformant", assess(cnd02Events(List.of(IS)), "CND-02").outcome);
        assertEquals("insufficient_evidence", assess(cnd02Events(List.of(EP)), "CND-02").outcome);
        assertEquals("conformant", assess(cnd02Events(List.of(SR, IS)), "CND-02").outcome);
        assertEquals("insufficient_evidence", assess(cnd02Events(List.of(IS), SR), "CND-02").outcome);
        assertEquals("non-conformant", assess(cnd02Events(List.of()), "CND-02").outcome);
        assertEquals("non-conformant", assess(cnd02Events(List.of(IS), EP, "deny"), "CND-02").outcome);
    }
}
