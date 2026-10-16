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

    @Test
    void aRecordNeverBreaksAnotherSourcesChainByClaimingItsStream() {
        // 18.151: chains are keyed on (source, claimed stream), so the agent's bad-hash record naming the gateway's
        // stream gets its own result and leaves the gateway's chain unbroken; with two sources on one id, neither reads
        // verified.
        JsonNode head = signedEvent("attestations/sg1.sig");
        ObjectNode tail = signedEvent("attestations/sg2.sig").deepCopy();
        tail.put("id", "sg2");
        ObjectNode tailBlock = (ObjectNode) tail.get("data").get("integrity");
        tailBlock.put("prev", head.get("data").get("integrity").get("hash").textValue());
        tailBlock.put("hash", Integrity.recomputeHash(tail));
        ObjectNode hijack = signedEvent(null).deepCopy();
        hijack.put("id", "x9");
        hijack.put("source", "agent-1");
        ObjectNode hijackBlock = (ObjectNode) hijack.get("data").get("integrity");
        hijackBlock.put("hash", "f".repeat(64));
        hijackBlock.put("prev", "e".repeat(64));
        assertEquals(List.of("s-signed failed", "s-signed verified_weak"), statuses(List.of(head, hijack, tail)));
        assertEquals(List.of("s-signed verified"), statuses(List.of(head, tail)));
    }

    private static List<String> statuses(List<JsonNode> events) {
        return Integrity.verifyBundle(events, Json.nodes().objectNode(), null).stream()
                .map(r -> r.stream + " " + r.status)
                .toList();
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

    private static final String CAP_STREAM = "urn:stream:gw";

    private static List<JsonNode> capChain(int count, String strength, String cls) {
        List<JsonNode> events = new java.util.ArrayList<>();
        String prev = Integrity.GENESIS_PREV;
        for (int i = 0; i < count; i++) {
            ObjectNode event = (ObjectNode) Json.parse("{\"id\":\"e" + i + "\",\"source\":\"urn:src:gw\",\"subject\":\"s\","
                    + "\"time\":\"2026-01-01T00:0" + i + ":00Z\",\"data\":{\"@type\":\"ToolCall\",\"integrity\":{\"prev\":\""
                    + prev + "\",\"stream\":\"" + CAP_STREAM + "\",\"strength\":\"" + strength + "\"}}}");
            if (cls != null) {
                event.put("agentcesourceclass", cls);
            }
            ObjectNode block = (ObjectNode) event.get("data").get("integrity");
            block.put("hash", Integrity.recomputeHash(event));
            prev = block.get("hash").asText();
            events.add(event);
        }
        return events;
    }

    private static JsonNode anchoredAt(String at) {
        return Json.parse("{\"streams\":[{\"stream\":\"" + CAP_STREAM + "\",\"anchors\":[{\"kind\":\"rfc3161\",\"ref\":\"r\",\"at\":\""
                + at + "\"}]}]}");
    }

    private static Integrity.Caps capsOf(List<JsonNode> events, JsonNode manifest) {
        return Integrity.integrityCaps(events, events, Integrity.verifyStreams(events, manifest, null));
    }

    @Test
    void timeCheckAnchorWithoutOffsetAnchorsNothing() {
        for (String at : List.of("2026-01-01T09:00:00", "")) {
            Integrity.Result result = Integrity.verifyBundle(capChain(2, "export_anchored", null), anchoredAt(at), null).get(0);
            assertEquals("time_suspect", result.status);
            assertEquals(null, result.firstBadIndex);
            assertEquals("verified_weak", result.strengthStatus);
        }
    }

    @Test
    void timeCheckKeepsTheStrengthReadingOfASuspectStream() {
        Integrity.Result result =
                Integrity.verifyBundle(capChain(2, "source_signed", null), anchoredAt("2026-01-01T00:00:30Z"), null).get(0);
        assertEquals("time_suspect", result.status);
        assertEquals(1, result.firstBadIndex);
        assertEquals("unsigned", result.strengthStatus);
    }

    @Test
    void integrityCapBrokenCapsEnforcementPointAndIndependentSystemNeverSelfReport() {
        for (String cls : List.of("enforcement_point", "independent_system")) {
            List<JsonNode> tampered = capChain(2, "source_signed", cls);
            ((ObjectNode) tampered.get(0)).put("note", "changed after it was hashed");
            assertEquals(java.util.Set.of("e0", "e1"), capsOf(tampered, Json.parse("{}")).broken());
        }
        List<JsonNode> own = capChain(2, "source_signed", "self_report");
        ((ObjectNode) own.get(0)).put("note", "changed after it was hashed");
        assertEquals(Integrity.Caps.NONE, capsOf(own, Json.parse("{}")));
        List<JsonNode> missing = capChain(1, "source_signed", "enforcement_point");
        ((ObjectNode) missing.get(0).get("data")).remove("integrity");
        assertEquals(java.util.Set.of("e0"), capsOf(missing, Json.parse("{}")).broken());
    }

    @Test
    void integrityCapWeakCapsOnlyEnforcementPointAndASuspectWeakStreamStaysWeak() {
        assertEquals(java.util.Set.of("e0", "e1"), capsOf(capChain(2, "export_chained", "enforcement_point"), Json.parse("{}")).weak());
        assertEquals(Integrity.Caps.NONE, capsOf(capChain(2, "export_chained", "independent_system"), Json.parse("{}")));
        Integrity.Caps suspect = capsOf(capChain(2, "export_chained", "enforcement_point"), anchoredAt("2026-01-01T00:00:30Z"));
        assertEquals(java.util.Set.of("e0", "e1"), suspect.timeSuspect());
        assertEquals(java.util.Set.of("e0", "e1"), suspect.weak());
    }
}
