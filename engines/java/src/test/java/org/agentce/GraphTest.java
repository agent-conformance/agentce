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
}
