package org.agentce;

import static org.junit.jupiter.api.Assertions.assertEquals;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.util.List;
import org.junit.jupiter.api.Test;

/**
 * Integrity verification is byte-identical to the reference (SPEC §6.6). The fixture exercises every
 * status branch (verified, verified_weak, failed, gap, reordered, time_suspect).
 */
class IntegrityTest {

    @Test
    void integrityResultsMatchGolden() {
        JsonNode fixture = Json.parseFile(TestPaths.testData().resolve("integrity-fixture.json"));
        JsonNode golden = Json.parseFile(TestPaths.testData().resolve("integrity-golden.json"));
        ArrayNode results = Json.nodes().arrayNode();
        for (Integrity.Result r : Integrity.verifyBundle(Fixtures.toList(fixture.get("events")), fixture.get("manifest"), null)) {
            results.add(r.toJson());
        }
        assertEquals(Canonical.canonicalString(golden), Canonical.canonicalString(results));
    }

    private static JsonNode signedEvent(String sigRef) {
        ObjectNode integrity = Json.nodes().objectNode();
        integrity.put("hash", "");
        integrity.put("prev", Integrity.GENESIS_PREV);
        integrity.put("stream", "s-signed");
        integrity.put("strength", "source_signed");
        if (sigRef != null) {
            integrity.put("sig_ref", sigRef);
        }
        ObjectNode event = Json.nodes().objectNode();
        event.put("id", "sg1");
        event.put("source", "src-1");
        event.put("subject", "subj-A");
        event.put("time", "2026-01-01T00:00:00.000Z");
        ObjectNode data = event.putObject("data");
        data.put("@type", "Decision");
        data.set("integrity", integrity);
        integrity.put("hash", Integrity.recomputeHash(event));
        return event;
    }

    @Test
    void sourceSignedStreamIsVerifiedWithSigRefAndUnsignedWithout() {
        List<Integrity.Result> withSig = Integrity.verifyBundle(List.of(signedEvent("attestations/sg1.sig")), Json.nodes().objectNode(), null);
        assertEquals("verified", withSig.get(0).status);
        List<Integrity.Result> withoutSig = Integrity.verifyBundle(List.of(signedEvent(null)), Json.nodes().objectNode(), null);
        assertEquals("unsigned", withoutSig.get(0).status);
    }
}
