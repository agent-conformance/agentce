package org.agentce;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

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

    private static JsonNode block(String sigRef) {
        ObjectNode integrity = Json.nodes().objectNode();
        integrity.put("sig_ref", sigRef);
        return integrity;
    }

    @Test
    void sigRefEscapingTheBundleRootIsNotTreatedAsSigned(@TempDir Path tempDir) throws IOException {
        Path bundleRoot = tempDir.resolve("bundle");
        Files.createDirectories(bundleRoot.resolve("attestations"));
        Path outside = tempDir.resolve("sig.bin");
        Files.writeString(outside, "signature bytes\n", StandardCharsets.UTF_8);
        Files.createSymbolicLink(bundleRoot.resolve("attestations").resolve("alias.sig"), outside);

        // sig_ref is evidence content: neither a literal '..' nor a symlink out may pass as a signature.
        assertFalse(Integrity.isSigned(block("../sig.bin"), bundleRoot));
        assertFalse(Integrity.isSigned(block("attestations/alias.sig"), bundleRoot));
    }

    @Test
    void sigRefInsideTheBundleRootIsTreatedAsSigned(@TempDir Path tempDir) throws IOException {
        Path bundleRoot = tempDir.resolve("bundle");
        Files.createDirectories(bundleRoot.resolve("attestations"));
        Files.writeString(
                bundleRoot.resolve("attestations").resolve("sig.bin"),
                "signature bytes\n",
                StandardCharsets.UTF_8);

        assertTrue(Integrity.isSigned(block("attestations/sig.bin"), bundleRoot));
        assertFalse(Integrity.isSigned(block("attestations/absent.bin"), bundleRoot));
    }
}
