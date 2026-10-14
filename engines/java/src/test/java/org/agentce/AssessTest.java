package org.agentce;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotSame;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertSame;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/**
 * The full assess path (graph → catalog → structural → assertions) is byte-identical to the reference.
 * {@code assess-golden.json} is the reference engine's {@code assess_subjects} output over the base
 * catalog for several event sets; the comparison covers evidence-pointer digests, so any drift in the
 * canonical form, graph, shape parse, or evaluator would break it.
 */
class AssessTest {
    private static final String SUBJECT = "spiffe://corp/agents/a";

    private static List<JsonNode> eventsOf(String controlId, String kase) throws IOException {
        return Fixtures.readJsonl(Fixtures.BASE.resolve("test").resolve(controlId).resolve(kase + ".jsonl"));
    }

    @Test
    void assessSubjectsMatchesReference() throws IOException {
        JsonNode golden = Json.parseFile(TestPaths.testData().resolve("assess-golden.json"));
        Catalog catalog = Catalog.load(Fixtures.BASE);
        DomainBinding domain = DomainBinding.load(Fixtures.BASE.resolve("test/domain.yaml"));
        Profile profile = Profile.fromDict(
                Json.parse("{\"subjects\":[{\"id\":\"" + SUBJECT + "\",\"role\":\"both\"}],\"catalogs\":[\"base/eu-ai-act\"]}"));

        Map<String, List<JsonNode>> scenarios = new LinkedHashMap<>();
        scenarios.put("ovs-passed", eventsOf("OVS-03", "passed"));
        scenarios.put("ovs-failed", eventsOf("OVS-03", "failed"));
        scenarios.put("rec-passed", eventsOf("REC-04", "passed"));
        scenarios.put("empty", List.of());

        ObjectNode result = Json.nodes().objectNode();
        for (Map.Entry<String, List<JsonNode>> e : scenarios.entrySet()) {
            ArrayNode arr = result.putArray(e.getKey());
            for (Assertions.Assertion a : Assess.assessSubjects(e.getValue(), profile, List.of(catalog), domain, List.of())) {
                arr.add(a.toJson());
            }
        }
        assertEquals(Canonical.canonicalString(golden), Canonical.canonicalString(result));
    }

    private static final String AS_OF = "2026-02-01T00:00:00Z";

    private static Assertions.Assertion deviationAssertion(String control, String outcome) {
        Assertions.Assertion a = new Assertions.Assertion();
        a.control = control;
        a.controlVersion = "2026.09";
        a.subject = SUBJECT;
        a.outcome = outcome;
        a.rung = 2;
        a.mode = "automated";
        a.window = new String[] {"2026-01-01T00:00:00Z", AS_OF};
        a.population = new int[] {1, 1};
        a.severity = "high";
        a.family = "AUV";
        return a;
    }

    private static JsonNode deviationEntry(String control, String expiry) {
        ObjectNode entry = Json.nodes().objectNode();
        entry.put("control", control);
        entry.put("rationale", "r");
        entry.put("compensating_control", "c");
        entry.put("owner", "user:a@example.com");
        entry.put("approver", "user:b@example.com");
        entry.put("granted", "2025-06-01T00:00:00Z");
        entry.put("expiry", expiry);
        return entry;
    }

    @Test
    void applyDeviationsAppliesAnUnexpiredEntry() {
        Assess.Applied applied = Assess.applyDeviations(
                List.of(deviationAssertion("AUV-01", "non-conformant")),
                List.of(deviationEntry("AUV-01", "2026-06-01T00:00:00Z")),
                AS_OF);
        assertEquals("partial", applied.assertions().get(0).outcome);
        assertEquals("AUV-01", applied.assertions().get(0).deviation);
        assertEquals(List.of(), applied.expired());
    }

    @Test
    void applyDeviationsIgnoresAnExpiredEntryAndReportsIt() {
        Assess.Applied applied = Assess.applyDeviations(
                List.of(deviationAssertion("AUV-02", "non-conformant")),
                List.of(deviationEntry("AUV-02", "2025-11-01T00:00:00Z")),
                AS_OF);
        assertEquals("non-conformant", applied.assertions().get(0).outcome);
        assertNull(applied.assertions().get(0).deviation);
        assertEquals(List.of("AUV-02"), applied.expired());
    }

    @Test
    void applyDeviationsLeavesOtherOutcomesUntouched() {
        for (String outcome : List.of("conformant", "insufficient_evidence", "not_assessed", "not_applicable")) {
            Assertions.Assertion input = deviationAssertion("AUV-01", outcome);
            Assess.Applied applied = Assess.applyDeviations(
                    List.of(input), List.of(deviationEntry("AUV-01", "2026-06-01T00:00:00Z")), AS_OF);
            assertSame(input, applied.assertions().get(0));
            assertEquals(outcome, input.outcome);
        }
    }

    @Test
    void applyDeviationsNeverMutatesItsInput() {
        Assertions.Assertion input = deviationAssertion("AUV-01", "non-conformant");
        JsonNode entry = deviationEntry("AUV-01", "2026-06-01T00:00:00Z");
        String before = input.toJson().toString() + entry;
        Assess.Applied applied = Assess.applyDeviations(List.of(input), List.of(entry), AS_OF);
        assertEquals(before, input.toJson().toString() + entry);
        assertNotSame(input, applied.assertions().get(0));
    }

    @Test
    void loadDeviationRegisterRefusesNonUtf8(@TempDir Path dir) throws IOException {
        Path path = dir.resolve("reg.yaml");
        byte[] head = "deviation_register_version: 1\ndeviations:\n  - control: AUV-".getBytes(StandardCharsets.UTF_8);
        byte[] bytes = new byte[head.length + 2];
        System.arraycopy(head, 0, bytes, 0, head.length);
        bytes[head.length] = (byte) 0xff;
        bytes[head.length + 1] = '\n';
        Files.write(path, bytes);
        InputError error = assertThrows(InputError.class, () -> Readiness.loadDeviationRegister(path));
        assertEquals("input.deviation_invalid", error.key);
        assertTrue(error.reason.startsWith("the deviation register at '" + path + "' is not valid UTF-8"));
    }

    /** ROB-02 over a VG-QUARANTINE-NO-PASS case, ingested from a bundle that declares the gate's three sources. */
    private static Assertions.Assertion quarantineRob02(Path root, String kase) throws IOException {
        Path fixtures = TestPaths.repoRoot().resolve("verification/gates/fixtures");
        JsonNode events = Json.parseFile(fixtures.resolve("quarantine_no_pass/cases.json"))
                .get("cases").get(kase).get("events");
        List<JsonNode> list = new ArrayList<>();
        events.forEach(list::add);
        ArrayNode sources = Json.nodes().arrayNode();
        for (String cls : List.of("enforcement_point", "independent_system", "self_report")) {
            sources.addObject().put("id", "urn:src:" + cls).put("class", cls);
        }
        Ingest.Result ingested = Ingest.ingest(Fixtures.writeIngestBundle(root, list, sources));
        Profile profile = Profile.fromDict(
                Json.parse("{\"subjects\":[{\"id\":\"spiffe://corp/agents/rob02-fixture\",\"role\":\"both\"}]}"));
        Catalog catalog = Catalog.load(TestPaths.repoRoot().resolve("spec/catalogs/base/baseline"));
        DomainBinding domain = DomainBinding.load(fixtures.resolve("rob02_untrusted/domain.linkml.yaml"));
        return Assess.assessSubjects(ingested.accepted, profile, List.of(catalog), domain, ingested.withheld).stream()
                .filter(a -> "ROB-02".equals(a.control))
                .findFirst()
                .orElseThrow();
    }

    @Test
    void aLateRecordThatReadsNonConformantIsMarkedForTheExitCode(@TempDir Path tmp) throws IOException {
        Assertions.Assertion late = quarantineRob02(tmp.resolve("late"), "rob02-untrusted-write-quarantined-time-order");
        assertEquals("insufficient_evidence", late.outcome);
        assertTrue(late.withheldNonConformant);
        // Run state for the exit code only: assertions.json keeps its shape.
        assertTrue(!late.toJson().has("withheld_non_conformant"));
        Assertions.Assertion benign = quarantineRob02(tmp.resolve("benign"), "rob02-benign-time-order");
        assertEquals("conformant", benign.outcome);
        assertTrue(!benign.withheldNonConformant);
    }

    // --- 18.186: the deviation register cannot waive the SPEC §10.4 human-actor rule. ---

    private static final Path HUMAN_ACTOR =
            TestPaths.repoRoot().resolve("verification/gates/fixtures/deviations_human_actor");

    /** The case's assertions by control for its fixture subject. */
    private static Map<String, Assertions.Assertion> humanActorCase(String name, String... drop) {
        Path kase = HUMAN_ACTOR.resolve(name);
        List<String> dropped = List.of(drop);
        List<JsonNode> accepted = Ingest.ingest(Bundle.load(kase.resolve("bundle"))).accepted.stream()
                .filter(e -> !dropped.contains(e.get("id").asText()))
                .toList();
        Profile profile = Profile.fromDict(
                Json.parse("{\"subjects\":[{\"id\":\"spiffe://corp/agents/cnd05-fixture\",\"role\":\"both\"}]}"));
        DomainBinding domain = DomainBinding.load(kase.resolve("domain.linkml.yaml"));
        Map<String, Assertions.Assertion> byControl = new LinkedHashMap<>();
        for (Assertions.Assertion a : Assess.assessSubjects(accepted, profile,
                List.of(Catalog.load(Fixtures.BASE), Catalog.load(Fixtures.CONDUCT)), domain, List.of())) {
            byControl.put(a.control, a);
        }
        return byControl;
    }

    private static List<JsonNode> register(String name) {
        return Readiness.loadDeviationRegister(HUMAN_ACTOR.resolve(name).resolve("deviations.yaml"));
    }

    @Test
    void aFailingApprovalIsNotWaivableOnTheControlsThatReadIt() {
        Map<String, Assertions.Assertion> byControl = humanActorCase("approval");
        List<String> readers = List.of("CND-02", "OVS-01", "OVS-08", "RSK-02");
        List<Assertions.Assertion> chosen = new ArrayList<>();
        for (String control : List.of("CND-02", "OVS-01", "OVS-08", "RSK-02", "OVS-05")) {
            chosen.add(byControl.get(control));
        }
        Assess.Applied applied = Assess.applyDeviations(chosen, register("approval"), chosen.get(0).window[1]);
        assertEquals(List.of(), applied.expired());
        assertEquals(readers, applied.notWaivable());
        for (Assertions.Assertion a : applied.assertions().subList(0, 4)) {
            assertEquals("non-conformant", a.outcome, a.control);
            assertNull(a.deviation, a.control);
        }
        // The decision's missing authorization is no §10.4 failure: the register still waives it.
        assertEquals("partial", applied.assertions().get(4).outcome);
        assertEquals("OVS-05", applied.assertions().get(4).deviation);
    }

    @Test
    void anExpiredEntryOnAFailingApprovalIsReportedNotWaivable() {
        Assertions.Assertion cnd02 = humanActorCase("approval").get("CND-02");
        ObjectNode entry = register("approval").stream()
                .filter(d -> "CND-02".equals(d.get("control").asText()))
                .findFirst().orElseThrow().deepCopy();
        entry.put("expiry", "2020-01-01");
        Assess.Applied applied = Assess.applyDeviations(List.of(cnd02), List.of(entry), cnd02.window[1]);
        assertEquals(List.of(), applied.expired());
        assertEquals(List.of("CND-02"), applied.notWaivable());
        assertEquals("non-conformant", applied.assertions().get(0).outcome);
    }

    @Test
    void aDecisionWithNoApprovalIsWaivableNotNotWaivable() {
        Assertions.Assertion cnd02 = humanActorCase("approval", "ap1").get("CND-02");
        assertEquals("non-conformant", cnd02.outcome);
        assertTrue(!cnd02.humanActorFailed);
        Assess.Applied applied = Assess.applyDeviations(List.of(cnd02), register("approval"), cnd02.window[1]);
        assertEquals(List.of(), applied.notWaivable());
        assertEquals("partial", applied.assertions().get(0).outcome);
        assertEquals("CND-02", applied.assertions().get(0).deviation);
    }

    @Test
    void anOverrideByTheAgentItselfIsNotWaivableOnOvs07() {
        Assertions.Assertion ovs07 = humanActorCase("override").get("OVS-07");
        Assess.Applied applied = Assess.applyDeviations(List.of(ovs07), register("override"), ovs07.window[1]);
        assertEquals(List.of("OVS-07"), applied.notWaivable());
        assertEquals("non-conformant", applied.assertions().get(0).outcome);
        assertNull(applied.assertions().get(0).deviation);
    }

    @Test
    void notWaivableIsPerAssertionAcrossSubjects() {
        Assertions.Assertion failing = humanActorCase("approval").get("CND-02");
        Assertions.Assertion clean = Assertions.copyOf(failing);
        clean.subject = "spiffe://corp/agents/other";
        clean.humanActorFailed = false;
        Assess.Applied applied =
                Assess.applyDeviations(List.of(failing, clean), register("approval"), failing.window[1]);
        assertEquals(List.of("CND-02"), applied.notWaivable());
        assertEquals(List.of("non-conformant", "partial"),
                applied.assertions().stream().map(a -> a.outcome).toList());
    }

    @Test
    void notWaivableReadsMatchByPredicate() {
        GraphStore store = new GraphStore();
        store.humanActorFailed.add("d1");
        for (List<String> reads : List.of(List.of("agentce:approvedBy"),
                List.of("agentce:decidedBy", "^agentce:reviewedBy"),
                List.of("agentce:x", "agentce:interventionByHuman"))) {
            assertTrue(Assess.reachesHumanActorFailure(store, "d1", reads));
        }
        assertTrue(!Assess.reachesHumanActorFailure(store, "d1", List.of("agentce:approvedByProxy")));
        assertTrue(!Assess.reachesHumanActorFailure(store, "d1", List.of("agentce:authorizedBy")));
        assertTrue(!Assess.reachesHumanActorFailure(store, "d2", List.of("agentce:approvedBy")));
    }

    @Test
    void notWaivableWhenTheShapeReachesTheFailingDecisionFromAnotherFocus() {
        GraphStore store = new GraphStore();
        store.humanActorFailed.add("d1");
        store.addEdge("tc1", "agentce:executes", "d1");
        store.addEdge("tc2", "agentce:executes", "d2");
        store.addEdge("a1", "agentce:approves", "d1");
        // Two hops on one predicate, given once (propertyReads dedupes): the walk repeats it.
        store.addEdge("i3", "agentce:derivedFrom", "i2");
        store.addEdge("i2", "agentce:derivedFrom", "d1");
        assertTrue(Assess.reachesHumanActorFailure(store, "i3", List.of("agentce:derivedFrom", "agentce:approvedBy")));
        assertTrue(Assess.reachesHumanActorFailure(store, "tc1", List.of("agentce:executes", "agentce:approvedBy")));
        assertTrue(Assess.reachesHumanActorFailure(store, "tc1", List.of("agentce:executes", "agentce:reviewedBy")));
        assertTrue(Assess.reachesHumanActorFailure(store, "a1",
                List.of("agentce:approves", "^agentce:executes", "agentce:approvedBy")));
        assertTrue(!Assess.reachesHumanActorFailure(store, "tc2", List.of("agentce:executes", "agentce:approvedBy")));
        assertTrue(!Assess.reachesHumanActorFailure(store, "tc1", List.of("agentce:executes", "agentce:authorizedBy")));
    }

    @Test
    void notWaivablePropertyReadsIncludeNestedShapes() {
        Map<String, Psp.Shape> shapes = Psp.parseShapesTtl("""
                @prefix sh: <http://www.w3.org/ns/shacl#> .
                @prefix agentce: <https://agent-conformance.org/vocab/evidence/v1#> .
                agentce:S a sh:NodeShape ; sh:targetClass agentce:ToolCall ;
                  sh:property [ sh:path agentce:executes ; sh:node agentce:N ] ;
                  sh:property [ sh:path ( agentce:executes [ sh:inversePath agentce:about ] ) ; sh:equals agentce:x ] ;
                  sh:property [ sh:path agentce:calls ; sh:qualifiedValueShape agentce:Q ; sh:qualifiedMinCount 1 ] .
                agentce:N a sh:NodeShape ; sh:property [ sh:path agentce:approvedBy ; sh:node agentce:S ] .
                agentce:Q a sh:NodeShape ; sh:property [ sh:path agentce:reviewedBy ; sh:minCount 1 ] .
                """);
        Psp.Shape shape = shapes.values().stream().filter(s -> s.iri.endsWith("#S")).findFirst().orElseThrow();
        assertEquals(Map.of(
                "agentce:executes", List.of("agentce:executes", "agentce:approvedBy", "^agentce:about", "agentce:x",
                        "agentce:calls", "agentce:reviewedBy"),
                "(agentce:executes ^agentce:about)", List.of("agentce:executes", "^agentce:about", "agentce:x"),
                "agentce:calls", List.of("agentce:calls", "agentce:reviewedBy")),
                Structural.propertyReads(shape, shapes));
    }
}
