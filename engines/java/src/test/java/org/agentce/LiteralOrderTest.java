package org.agentce;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.fasterxml.jackson.databind.JsonNode;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.Test;

/** Exact literal ordering matches the shared edge vectors and the structural evaluator (SPEC §6.7). */
class LiteralOrderTest {
    private static final String PREFIX = "@prefix sh: <http://www.w3.org/ns/shacl#> .\n"
            + "@prefix agentce: <https://agent-conformance.org/vocab/evidence/v1#> .\n"
            + "@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .\n";
    private static final String THING = "agentce:Thing";
    private static final String FOCUS = "agentce:event/x";
    private static final String SHAPE = "https://agent-conformance.org/vocab/evidence/v1#S";

    @Test
    void theSharedEdgeVectorsAreReproduced() {
        JsonNode data = Json.parseFile(TestPaths.repoRoot().resolve("spec/rules/numerics-vectors/cases/edge-comparison.json"));
        JsonNode results = Numerics.computeVectorFile(data);
        assertTrue(data.get("cases").size() >= 40, "expected the full shared edge-vector set");
        for (JsonNode c : data.get("cases")) {
            String name = c.get("name").textValue();
            assertEquals(c.get("expected").textValue(), results.get(name).textValue(), name);
        }
    }

    @Test
    void theSharedCanonicalNumberVectorsAreReproduced() {
        JsonNode data = Json.parseFile(TestPaths.repoRoot().resolve("spec/rules/numerics-vectors/cases/edge-canonical-numbers.json"));
        JsonNode results = Numerics.computeVectorFile(data);
        assertTrue(data.get("cases").size() >= 30, "expected the full shared number-token vector set");
        for (JsonNode c : data.get("cases")) {
            String name = c.get("name").textValue();
            assertEquals(c.get("expected").textValue(), results.get(name).textValue(), name);
        }
    }

    private static boolean flagged(String constraint, String... literals) {
        GraphStore store = new GraphStore();
        store.addSubclassClosure(java.util.Collections.singletonList(new String[] {THING, THING}));
        store.addType(FOCUS, THING);
        for (int i = 0; i < literals.length; i += 3) {
            store.addLiteral(FOCUS, literals[i], literals[i + 1], literals[i + 2]);
        }
        String ttl = PREFIX + "agentce:S a sh:NodeShape ; sh:targetClass " + THING + " ; sh:property [ sh:path agentce:n ; "
                + constraint + " ] .\n";
        Map<String, Psp.Shape> shapes = Psp.parseShapesTtl(ttl);
        return Structural.evaluateShape(store, shapes.get(SHAPE), shapes, "C").failing.contains(FOCUS);
    }

    @Test
    void minInclusiveAtTheDoubleBoundaryIsExact() {
        String[] value = {"agentce:n", "9007199254740992", "xsd:integer"};
        assertTrue(flagged("sh:minInclusive 9007199254740993", value));
        assertFalse(flagged("sh:minInclusive 9007199254740992", value));
        assertTrue(flagged("sh:maxInclusive 9007199254740991", value));
    }

    @Test
    void anAwareDateTimeAgainstANaiveBoundIsAViolationNotACrash() {
        String[] value = {"agentce:n", "2026-01-01T00:00:00Z", "xsd:dateTime"};
        assertTrue(flagged("sh:minInclusive \"2026-01-01T00:00:00\"", value));
        assertFalse(flagged("sh:minInclusive \"2026-01-01T00:00:00Z\"", value));
    }

    @Test
    void lessThanIsStrictOverEqualInstantsWrittenDifferently() {
        GraphStore store = new GraphStore();
        store.addSubclassClosure(java.util.Collections.singletonList(new String[] {THING, THING}));
        store.addType(FOCUS, THING);
        store.addLiteral(FOCUS, "agentce:a", "2026-01-01T00:00:00Z", "xsd:dateTime");
        store.addLiteral(FOCUS, "agentce:b", "2026-01-01T01:00:00+01:00", "xsd:dateTime");
        String ttl = PREFIX + "agentce:S a sh:NodeShape ; sh:targetClass " + THING
                + " ; sh:property [ sh:path agentce:a ; sh:lessThan agentce:b ] .\n";
        Map<String, Psp.Shape> shapes = Psp.parseShapesTtl(ttl);
        assertTrue(Structural.evaluateShape(store, shapes.get(SHAPE), shapes, "C").failing.contains(FOCUS));
    }
}
