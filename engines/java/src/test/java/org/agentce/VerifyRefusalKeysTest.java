package org.agentce;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.io.IOException;
import java.io.UncheckedIOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.GeneralSecurityException;
import java.security.KeyPairGenerator;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Base64;
import java.util.List;
import java.util.Set;
import java.util.TreeSet;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import java.util.stream.Stream;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/**
 * Verify refusals carry stable message keys (18.64), the same behaviour Python's
 * {@code tests/test_verify_refusal_keys.py} and TypeScript's {@code verifyRefusalKeys.test.ts} pin;
 * {@code VG-VERIFY-REFUSAL-KEYS} runs all three.
 */
class VerifyRefusalKeysTest {

    private static final Path EU_AI_ACT = TestPaths.repoRoot().resolve("spec/catalogs/base/eu-ai-act");

    @TempDir
    Path tmp;

    @Test
    void everyVerifyKeyVerifyJavaNamesIsInTheCatalogue() throws IOException {
        String source = Files.readString(
                TestPaths.repoRoot().resolve("engines/java/src/main/java/org/agentce/Verify.java"));
        Set<String> named = new TreeSet<>();
        Matcher m = Pattern.compile("\"(verify\\.[a-z_]+)\"").matcher(source);
        while (m.find()) {
            named.add(m.group(1));
        }
        assertTrue(named.contains("verify.json_too_deep"));
        named.removeAll(ErrorCatalogue.messageKeys().keySet());
        assertEquals(Set.of(), named);
    }

    @Test
    void leafRefusalIsItsCauseWithParamsFilled() {
        Verify.VerifyRefusal leaf = Verify.refusal("verify.keyid_untrusted", "keyid", "'k1'");
        assertEquals("no trusted key for keyid 'k1'", leaf.getMessage());
        assertEquals(List.of("verify.keyid_untrusted"), leaf.keys());
    }

    @Test
    void wrapperListsItsKeyThenTheInnerKeys() {
        Verify.VerifyRefusal wrapped =
                Verify.refusal("verify.no_signature_verified", Verify.refusal("verify.signature_invalid"));
        Verify.VerifyRefusal outer = Verify.refusal("verify.release_signature", wrapped, "profile", "kms");
        assertEquals(
                "signature (kms): no signature verified against the trust root: signature does not verify",
                outer.getMessage());
        assertEquals(
                List.of("verify.release_signature", "verify.no_signature_verified", "verify.signature_invalid"),
                outer.keys());
    }

    @Test
    void missingSigFieldIsASentenceNotAKeyErrorRepr() throws GeneralSecurityException {
        byte[] spki = KeyPairGenerator.getInstance("Ed25519").generateKeyPair().getPublic().getEncoded();
        byte[] raw = Arrays.copyOfRange(spki, spki.length - 32, spki.length);
        String keyid = Sign.keyidFor(raw);
        ObjectNode root = Json.nodes().objectNode();
        root.putObject("keys").putObject(keyid).put("public_key", Base64.getEncoder().encodeToString(raw));
        ObjectNode envelope = Json.nodes().objectNode();
        envelope.put("payloadType", "application/vnd.in-toto+json");
        envelope.put("payload", "e30=");
        envelope.putArray("signatures").addObject().put("keyid", keyid);
        Verify.VerifyRefusal refused = assertThrows(
                Verify.VerifyRefusal.class,
                () -> Verify.verifyEnvelope(envelope, Verify.TrustRoot.fromDict(root)));
        assertEquals(
                "no signature verified against the trust root: a signature entry has no sig field",
                refused.getMessage());
        assertEquals(List.of("verify.no_signature_verified", "verify.signature_sig_missing"), refused.keys());
        assertEquals(
                "a signature entry has no envelope", ErrorCatalogue.errorCause("verify.release_envelope_missing"));
    }

    private static byte[] nested(int depth) {
        return ("[".repeat(depth) + "]".repeat(depth)).getBytes(StandardCharsets.US_ASCII);
    }

    private static byte[] concat(byte[]... parts) {
        int length = 0;
        for (byte[] part : parts) {
            length += part.length;
        }
        byte[] out = new byte[length];
        int at = 0;
        for (byte[] part : parts) {
            System.arraycopy(part, 0, out, at, part.length);
            at += part.length;
        }
        return out;
    }

    private List<String> reasonKeys(ObjectNode result) {
        List<String> keys = new ArrayList<>();
        JsonNode node = result.get("reason_keys");
        if (node != null) {
            node.forEach(k -> keys.add(k.asText()));
        }
        return keys;
    }

    private int copies;

    private ObjectNode verifySignatureBytes(byte[] raw) throws IOException {
        Path dir = tmp.resolve("catalog-" + copies++);
        try (Stream<Path> walk = Files.walk(EU_AI_ACT)) {
            walk.forEach(src -> {
                try {
                    Files.copy(src, dir.resolve(EU_AI_ACT.relativize(src).toString()));
                } catch (IOException e) {
                    throw new UncheckedIOException(e);
                }
            });
        }
        Files.write(dir.resolve(Verify.CATALOG_SIGNATURE_NAME), raw);
        return Verify.verifyCatalog(dir, Verify.vendoredTrust());
    }

    @Test
    void jsonTooDeepStartsOnePastTheLimit() throws IOException {
        ObjectNode atLimit = verifySignatureBytes(nested(Verify.MAX_JSON_DEPTH));
        assertEquals(false, atLimit.get("verified").asBoolean());
        assertNotEquals(List.of("verify.json_too_deep"), reasonKeys(atLimit));
        ObjectNode past = verifySignatureBytes(nested(Verify.MAX_JSON_DEPTH + 1));
        assertEquals(List.of("verify.json_too_deep"), reasonKeys(past));
        assertEquals(
                "catalog.sig.json nests containers more than " + Verify.MAX_JSON_DEPTH + " levels deep",
                past.get("reason").asText());
    }

    @Test
    void depthIsFoundBeforeAnyOtherProblem() throws IOException {
        byte[] deep = nested(Verify.MAX_JSON_DEPTH + 1);
        List<byte[]> cases = List.of(
                Arrays.copyOf(deep, deep.length - 1), // truncated
                "[".repeat(100_000).getBytes(StandardCharsets.US_ASCII), // truncated, far past the limit
                concat(deep, " x".getBytes(StandardCharsets.US_ASCII)), // trailing garbage
                concat("[\"\\ud800\",".getBytes(StandardCharsets.US_ASCII), nested(Verify.MAX_JSON_DEPTH),
                        "]".getBytes(StandardCharsets.US_ASCII)), // a lone surrogate first
                concat(new byte[] {(byte) 0xef, (byte) 0xbb, (byte) 0xbf}, deep)); // a byte-order mark
        for (byte[] raw : cases) {
            assertEquals(List.of("verify.json_too_deep"), reasonKeys(verifySignatureBytes(raw)));
        }
    }

    @Test
    void bracketsInsideStringsDoNotCount() {
        byte[] raw = ("[\"" + "[".repeat(Verify.MAX_JSON_DEPTH + 5) + "\\\"[\"]").getBytes(StandardCharsets.US_ASCII);
        assertNotNull(Verify.parseUntrustedJson(raw));
    }

    @Test
    void otherCallersKeepTheNotReadableText() {
        IllegalArgumentException refused = assertThrows(
                IllegalArgumentException.class, () -> Verify.parseUntrustedJson(nested(Verify.MAX_JSON_DEPTH + 1)));
        assertEquals("not readable JSON", refused.getMessage());
    }
}
