package org.agentce;

import static org.junit.jupiter.api.Assertions.assertEquals;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.io.IOException;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.Test;

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
}
