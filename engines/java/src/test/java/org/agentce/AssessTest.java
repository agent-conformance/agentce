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
            for (Assertions.Assertion a : Assess.assessSubjects(e.getValue(), profile, List.of(catalog), domain)) {
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
}
