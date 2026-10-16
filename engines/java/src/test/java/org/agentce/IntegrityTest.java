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
import java.security.GeneralSecurityException;
import java.security.KeyPairGenerator;
import java.util.Base64;
import java.util.HashMap;
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

    /** A fresh Ed25519 signer (KmsSigner loads only from a PEM file). */
    private static Sign.KmsSigner testSigner() {
        try {
            byte[] pkcs8 = KeyPairGenerator.getInstance("Ed25519").generateKeyPair().getPrivate().getEncoded();
            String pem = "-----BEGIN PRIVATE KEY-----\n"
                    + Base64.getMimeEncoder(64, "\n".getBytes(StandardCharsets.US_ASCII)).encodeToString(pkcs8)
                    + "\n-----END PRIVATE KEY-----\n";
            Path file = Files.createTempFile("agentce-signer-", ".pem");
            try {
                Files.writeString(file, pem, StandardCharsets.US_ASCII);
                return Sign.KmsSigner.load(file);
            } finally {
                Files.delete(file);
            }
        } catch (GeneralSecurityException | IOException e) {
            throw new IllegalStateException(e);
        }
    }

    private static Verify.TrustRoot trustOf(Sign.KmsSigner signer) {
        ObjectNode doc = Json.nodes().objectNode();
        ObjectNode key = doc.putObject("keys").putObject(signer.keyid());
        key.put("public_key", signer.publicKeyB64());
        key.put("identity", "test source");
        return Verify.TrustRoot.fromDict(doc);
    }

    /** A DSSE envelope over an in-toto Statement naming {@code hashes} as subjects. */
    private static String envelopeFor(List<String> hashes, Sign.KmsSigner signer) {
        ObjectNode statement = Json.nodes().objectNode();
        statement.put("_type", "https://in-toto.io/Statement/v1");
        ArrayNode subjects = statement.putArray("subject");
        for (String hash : hashes) {
            ObjectNode subject = subjects.addObject();
            subject.put("name", "stream");
            subject.putObject("digest").put("sha256", hash);
        }
        statement.put("predicateType", "https://agent-conformance.org/test");
        statement.putObject("predicate");
        return Sign.signStatement(statement, signer).toString();
    }

    private static final Sign.KmsSigner SOURCE = testSigner();
    private static final Verify.TrustRoot SOURCE_TRUST = trustOf(SOURCE);

    private static String hashOf(JsonNode event) {
        return event.get("data").get("integrity").get("hash").textValue();
    }

    @Test
    void sourceSignedStreamIsUnsignedWithoutSigRefBundleOrTrustRoot(@TempDir Path tempDir) throws IOException {
        JsonNode event = signedEvent("attestations/sg1.sig");
        Files.createDirectories(tempDir.resolve("attestations"));
        Files.writeString(tempDir.resolve("attestations/sg1.sig"), envelopeFor(List.of(hashOf(event)), SOURCE));
        ObjectNode manifest = Json.nodes().objectNode();
        assertEquals("verified", Integrity.verifyBundle(List.of(event), manifest, tempDir, SOURCE_TRUST).get(0).status);
        assertEquals("unsigned", Integrity.verifyBundle(List.of(event), manifest, tempDir, null).get(0).status);
        assertEquals("unsigned", Integrity.verifyBundle(List.of(event), manifest, null, SOURCE_TRUST).get(0).status);
        assertEquals(
                "unsigned",
                Integrity.verifyBundle(List.of(signedEvent(null)), manifest, tempDir, SOURCE_TRUST).get(0).status);
    }

    @Test
    void aRecordNeverBreaksAnotherSourcesChainByClaimingItsStream(@TempDir Path tempDir) throws IOException {
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
        Files.createDirectories(tempDir.resolve("attestations"));
        for (String name : List.of("sg1.sig", "sg2.sig")) {
            Files.writeString(
                    tempDir.resolve("attestations").resolve(name), envelopeFor(List.of(hashOf(head), hashOf(tail)), SOURCE));
        }
        assertEquals(
                List.of("s-signed failed", "s-signed verified_weak"), statuses(List.of(head, hijack, tail), tempDir));
        assertEquals(List.of("s-signed verified"), statuses(List.of(head, tail), tempDir));
    }

    private static List<String> statuses(List<JsonNode> events, Path root) {
        return Integrity.verifyBundle(events, Json.nodes().objectNode(), root, SOURCE_TRUST).stream()
                .map(r -> r.stream + " " + r.status)
                .toList();
    }

    private static JsonNode block(String sigRef) {
        ObjectNode integrity = Json.nodes().objectNode();
        integrity.put("sig_ref", sigRef);
        integrity.put("hash", HASH);
        return integrity;
    }

    private static final String HASH = "a".repeat(64);

    private static boolean signed(String sigRef, Path bundleRoot, Verify.TrustRoot trust) {
        return Integrity.isSigned(block(sigRef), bundleRoot, trust, new HashMap<>());
    }

    @Test
    void sigRefEscapingTheBundleRootIsNotTreatedAsSigned(@TempDir Path tempDir) throws IOException {
        Path bundleRoot = tempDir.resolve("bundle");
        Files.createDirectories(bundleRoot.resolve("attestations"));
        Path outside = tempDir.resolve("sig.bin");
        Files.writeString(outside, envelopeFor(List.of(HASH), SOURCE), StandardCharsets.UTF_8);
        Files.createSymbolicLink(bundleRoot.resolve("attestations").resolve("alias.sig"), outside);

        // sig_ref is evidence content: neither a literal '..' nor a symlink out may pass as a signature.
        assertFalse(signed("../sig.bin", bundleRoot, SOURCE_TRUST));
        assertFalse(signed("attestations/alias.sig", bundleRoot, SOURCE_TRUST));
    }

    @Test
    void sigRefInsideTheBundleRootSignsOnlyWithATrustedEnvelopeOverTheHash(@TempDir Path tempDir) throws IOException {
        Path attestations = Files.createDirectories(tempDir.resolve("bundle").resolve("attestations"));
        Path bundleRoot = attestations.getParent();
        Files.writeString(attestations.resolve("good.sig"), envelopeFor(List.of(HASH), SOURCE));
        Files.writeString(attestations.resolve("other.sig"), envelopeFor(List.of(HASH), testSigner()));
        Files.writeString(attestations.resolve("elsewhere.sig"), envelopeFor(List.of("b".repeat(64)), SOURCE));
        Files.writeString(attestations.resolve("sig.bin"), "signature bytes\n", StandardCharsets.UTF_8);

        assertTrue(signed("attestations/good.sig", bundleRoot, SOURCE_TRUST));
        assertFalse(signed("attestations/good.sig", bundleRoot, null));
        assertFalse(signed("attestations/other.sig", bundleRoot, SOURCE_TRUST));
        assertFalse(signed("attestations/elsewhere.sig", bundleRoot, SOURCE_TRUST));
        assertFalse(signed("attestations/sig.bin", bundleRoot, SOURCE_TRUST));
        assertFalse(signed("attestations/absent.bin", bundleRoot, SOURCE_TRUST));
    }

    @Test
    void aTrustedEnvelopeOverJacksonsDefaultStringAndNameCapsStillSigns(@TempDir Path tempDir) throws IOException {
        Path attestations = Files.createDirectories(tempDir.resolve("bundle").resolve("attestations"));
        ObjectNode envelope = (ObjectNode) Json.parse(envelopeFor(List.of(HASH), SOURCE));
        envelope.put("padding", "x".repeat(20_000_001));
        envelope.put("k".repeat(50_001), "v");
        Files.writeString(attestations.resolve("big.sig"), envelope.toString());

        assertTrue(signed("attestations/big.sig", attestations.getParent(), SOURCE_TRUST));
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
    void timeCheckAnchorOfTheWrongTypeAnchorsNothing() {
        String placeable = "\"2026-01-01T09:00:00Z\"";
        for (String anchor : List.of("1.5", "true", "null", "[" + placeable + "]", "{\"at\":1.5}", "{\"at\":[" + placeable + "]}")) {
            JsonNode manifest = Json.parse("{\"streams\":[{\"stream\":\"" + CAP_STREAM + "\",\"anchors\":[" + anchor + "]}]}");
            Integrity.Result result = Integrity.verifyBundle(capChain(2, "export_anchored", null), manifest, null).get(0);
            assertEquals("time_suspect", result.status);
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
