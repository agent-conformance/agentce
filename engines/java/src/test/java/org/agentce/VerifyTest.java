package org.agentce;

import static org.junit.jupiter.api.Assertions.assertArrayEquals;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.attribute.PosixFilePermission;
import java.nio.file.attribute.PosixFilePermissions;
import java.security.GeneralSecurityException;
import java.security.KeyPair;
import java.security.KeyPairGenerator;
import java.security.PrivateKey;
import java.security.PublicKey;
import java.security.Signature;
import java.util.Arrays;
import java.util.Base64;
import java.util.List;
import java.util.Set;
import org.junit.jupiter.api.Assumptions;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/**
 * {@code Verify.java}'s DSSE/certificate verification primitives (item 18.28): the compute seam
 * {@code Cli.java}'s {@code cmdVerify} wires up. Mirrors {@code verify.test.ts} exactly, same cases
 * in the same order. {@code CliTest} covers the full {@code agentce verify} command end to end.
 */
class VerifyTest {

    // --- The vendored trust root is a byte-for-byte copy of Python's, as `Verify.java`'s own header
    // claims -- a sync test, not merely a shared fixture, so the two can never silently drift apart. --

    @Test
    void theVendoredJavaTrustRootParsesToTheSameValueAsPythonsDevRootJson() throws Exception {
        JsonNode javaRoot;
        try (var in = Verify.class.getResourceAsStream("/trust/dev-root.json")) {
            javaRoot = Json.parse(new String(in.readAllBytes(), StandardCharsets.UTF_8));
        }
        Path pythonPath = TestPaths.repoRoot()
                .resolve("engines/python/agentce/data/trust/dev-root.json");
        JsonNode pythonRoot = Json.parseFile(pythonPath);
        assertArrayEquals(Canonical.canonicalize(javaRoot), Canonical.canonicalize(pythonRoot));
    }

    // --- Ed25519 test-key generation and a minimal kms Signer wrapper. ------------------------------

    private record EdPair(PrivateKey privateKey, byte[] rawPublicKey) {}

    private static EdPair ed25519RawPair() throws GeneralSecurityException {
        KeyPair pair = KeyPairGenerator.getInstance("Ed25519").generateKeyPair();
        byte[] spki = pair.getPublic().getEncoded();
        byte[] raw = Arrays.copyOfRange(spki, spki.length - 32, spki.length);
        return new EdPair(pair.getPrivate(), raw);
    }

    private static byte[] edSign(PrivateKey key, byte[] data) throws GeneralSecurityException {
        Signature signature = Signature.getInstance("Ed25519");
        signature.initSign(key);
        signature.update(data);
        return signature.sign();
    }

    private record KmsFixture(String keyid, String identity, Verify.TrustRoot trust, EdPair pair) {
        byte[] sign(byte[] data) throws GeneralSecurityException {
            return edSign(pair.privateKey(), data);
        }

        Sign.Signer asSigner() {
            return new Sign.Signer() {
                @Override
                public byte[] sign(byte[] data) {
                    try {
                        return edSign(pair.privateKey(), data);
                    } catch (GeneralSecurityException e) {
                        throw new IllegalStateException(e);
                    }
                }

                @Override
                public String keyid() {
                    return keyid;
                }
            };
        }
    }

    private static KmsFixture kmsFixture(String identity) throws GeneralSecurityException {
        EdPair pair = ed25519RawPair();
        String keyid = Sign.keyidFor(pair.rawPublicKey());
        ObjectNode root = Json.nodes().objectNode();
        ObjectNode keys = root.putObject("keys");
        ObjectNode entry = keys.putObject(keyid);
        entry.put("public_key", Base64.getEncoder().encodeToString(pair.rawPublicKey()));
        entry.put("identity", identity);
        return new KmsFixture(keyid, identity, Verify.TrustRoot.fromDict(root), pair);
    }

    private static ObjectNode signEnvelope(ObjectNode statement, KmsFixture fixture) {
        return Sign.signStatement(statement, fixture.asSigner());
    }

    // --- verifyEnvelope: malformed shape (every check C5 pins in Python's rewritten verify_envelope). --

    @Test
    void verifyEnvelopeRefusesANonObjectEnvelope() throws Exception {
        KmsFixture fixture = kmsFixture("test-identity");
        IllegalArgumentException e1 = assertThrows(
                IllegalArgumentException.class,
                () -> Verify.verifyEnvelope(Json.nodes().numberNode(5), fixture.trust()));
        assertEquals("malformed DSSE envelope", e1.getMessage());
        IllegalArgumentException e2 = assertThrows(
                IllegalArgumentException.class,
                () -> Verify.verifyEnvelope(Json.nodes().nullNode(), fixture.trust()));
        assertEquals("malformed DSSE envelope", e2.getMessage());
    }

    @Test
    void verifyEnvelopeRefusesANonStringPayloadType() throws Exception {
        KmsFixture fixture = kmsFixture("test-identity");
        ObjectNode envelope = Json.nodes().objectNode();
        envelope.put("payloadType", 5);
        envelope.put("payload", "e30=");
        envelope.putArray("signatures");
        assertEquals(
                "malformed DSSE envelope",
                assertThrows(IllegalArgumentException.class, () -> Verify.verifyEnvelope(envelope, fixture.trust()))
                        .getMessage());
    }

    @Test
    void verifyEnvelopeRefusesANonStringPayload() throws Exception {
        KmsFixture fixture = kmsFixture("test-identity");
        ObjectNode envelope = Json.nodes().objectNode();
        envelope.put("payloadType", "t");
        envelope.put("payload", 5);
        envelope.putArray("signatures");
        assertEquals(
                "malformed DSSE envelope",
                assertThrows(IllegalArgumentException.class, () -> Verify.verifyEnvelope(envelope, fixture.trust()))
                        .getMessage());
    }

    @Test
    void verifyEnvelopeRefusesAnUnparseableBase64Payload() throws Exception {
        KmsFixture fixture = kmsFixture("test-identity");
        ObjectNode envelope = Json.nodes().objectNode();
        envelope.put("payloadType", "t");
        envelope.put("payload", "!!!!");
        envelope.putArray("signatures");
        assertEquals(
                "malformed DSSE envelope",
                assertThrows(IllegalArgumentException.class, () -> Verify.verifyEnvelope(envelope, fixture.trust()))
                        .getMessage());
    }

    @Test
    void verifyEnvelopeRefusesANonArraySignaturesField() throws Exception {
        KmsFixture fixture = kmsFixture("test-identity");
        ObjectNode envelope = Json.nodes().objectNode();
        envelope.put("payloadType", "t");
        envelope.put("payload", "e30=");
        envelope.put("signatures", 5);
        assertEquals(
                "malformed DSSE envelope",
                assertThrows(IllegalArgumentException.class, () -> Verify.verifyEnvelope(envelope, fixture.trust()))
                        .getMessage());
    }

    @Test
    void verifyEnvelopeRefusesSignaturesXWithThePerEntryResolveMessageNotMalformedEnvelope() throws Exception {
        KmsFixture fixture = kmsFixture("test-identity");
        ObjectNode envelope = Json.nodes().objectNode();
        envelope.put("payloadType", "t");
        envelope.put("payload", "e30=");
        envelope.putArray("signatures").add("x");
        assertEquals(
                "no signature verified against the trust root: no trusted key for keyid None",
                assertThrows(IllegalArgumentException.class, () -> Verify.verifyEnvelope(envelope, fixture.trust()))
                        .getMessage());
    }

    @Test
    void verifyEnvelopeRefusesAnEmptySignaturesArray() throws Exception {
        KmsFixture fixture = kmsFixture("test-identity");
        ObjectNode envelope = Json.nodes().objectNode();
        envelope.put("payloadType", "t");
        envelope.put("payload", "e30=");
        envelope.putArray("signatures");
        assertEquals(
                "DSSE envelope carries no signatures",
                assertThrows(IllegalArgumentException.class, () -> Verify.verifyEnvelope(envelope, fixture.trust()))
                        .getMessage());
    }

    @Test
    void verifyEnvelopeRefusesASignatureEntryMissingSig() throws Exception {
        KmsFixture fixture = kmsFixture("test-identity");
        ObjectNode envelope = Json.nodes().objectNode();
        envelope.put("payloadType", "t");
        envelope.put("payload", "e30=");
        envelope.putArray("signatures").addObject().put("keyid", fixture.keyid());
        assertEquals(
                "no signature verified against the trust root: 'sig'",
                assertThrows(IllegalArgumentException.class, () -> Verify.verifyEnvelope(envelope, fixture.trust()))
                        .getMessage());
    }

    @Test
    void verifyEnvelopeRefusesAnUnresolvableKeyid() throws Exception {
        KmsFixture fixture = kmsFixture("test-identity");
        ObjectNode envelope = Json.nodes().objectNode();
        envelope.put("payloadType", "t");
        envelope.put("payload", "e30=");
        ObjectNode sig = envelope.putArray("signatures").addObject();
        sig.put("keyid", "sha256:deadbeef");
        sig.put("sig", "AAAA");
        assertEquals(
                "no signature verified against the trust root: no trusted key for keyid 'sha256:deadbeef'",
                assertThrows(IllegalArgumentException.class, () -> Verify.verifyEnvelope(envelope, fixture.trust()))
                        .getMessage());
    }

    // --- verifyEnvelope: real signed round trips. ---------------------------------------------------

    @Test
    void verifyEnvelopeVerifiesARealKmsSignedEnvelopeAndReturnsTheIdentityKeyid() throws Exception {
        KmsFixture fixture = kmsFixture("kms-identity");
        ObjectNode envelope = signEnvelope(Json.nodes().objectNode().put("hello", "world"), fixture);
        Verify.VerifiedEnvelope verified = Verify.verifyEnvelope(envelope, fixture.trust());
        assertEquals("kms-identity", verified.identity());
        assertEquals(fixture.keyid(), verified.keyid());
        assertFalse(verified.keyless());
    }

    @Test
    void verifyEnvelopeRefusesATamperedSignatureWithSignatureDoesNotVerify() throws Exception {
        KmsFixture fixture = kmsFixture("test-identity");
        ObjectNode envelope = signEnvelope(Json.nodes().objectNode().put("hello", "world"), fixture);
        ObjectNode entry = (ObjectNode) envelope.get("signatures").get(0);
        byte[] sigBuf = Base64.getDecoder().decode(entry.get("sig").asText());
        sigBuf[0] = (byte) (sigBuf[0] ^ 0xff);
        entry.put("sig", Base64.getEncoder().encodeToString(sigBuf));
        assertEquals(
                "no signature verified against the trust root: signature does not verify",
                assertThrows(IllegalArgumentException.class, () -> Verify.verifyEnvelope(envelope, fixture.trust()))
                        .getMessage());
    }

    @Test
    void verifyEnvelopesSecondSignatureErrorIsUsedWhenThereAreTwoEntriesAndOnlyTheSecondShouldAppear()
            throws Exception {
        KmsFixture fixture = kmsFixture("test-identity");
        ObjectNode envelope = signEnvelope(Json.nodes().objectNode().put("hello", "world"), fixture);
        ArrayNode signatures = (ArrayNode) envelope.get("signatures");
        ObjectNode bad = Json.nodes().objectNode();
        bad.put("keyid", "sha256:unknown");
        bad.put("sig", "AAAA");
        ArrayNode reordered = Json.nodes().arrayNode();
        reordered.add(bad);
        reordered.addAll(signatures);
        envelope.set("signatures", reordered);
        Verify.VerifiedEnvelope verified = Verify.verifyEnvelope(envelope, fixture.trust());
        assertEquals(fixture.keyid(), verified.keyid());
    }

    // --- Certificate-based (keyless) trust resolution. ----------------------------------------------

    private static ObjectNode issueCertificate(
            PrivateKey caPrivateKey, String issuer, String identity, byte[] leafPublicKeyRaw) throws Exception {
        ObjectNode body = Json.nodes().objectNode();
        body.put("issuer", issuer);
        body.put("identity", identity);
        body.put("algorithm", "ed25519");
        body.put("public_key", Base64.getEncoder().encodeToString(leafPublicKeyRaw));
        body.put("not_before", "2026-01-01T00:00:00Z");
        body.put("not_after", "2027-01-01T00:00:00Z");
        byte[] signature = edSign(caPrivateKey, Canonical.canonicalize(body));
        ObjectNode cert = body.deepCopy();
        cert.put("signature", Base64.getEncoder().encodeToString(signature));
        return cert;
    }

    private record CertFixture(Verify.TrustRoot trust, EdPair ca, EdPair leaf, String issuer) {
        ObjectNode sign(ObjectNode statement) throws Exception {
            byte[] payload = Canonical.canonicalize(statement);
            byte[] signature = edSign(leaf.privateKey(), Sign.dssePae("application/vnd.in-toto+json", payload));
            ObjectNode envelope = Json.nodes().objectNode();
            envelope.put("payloadType", "application/vnd.in-toto+json");
            envelope.put("payload", Base64.getEncoder().encodeToString(payload));
            ObjectNode sig = envelope.putArray("signatures").addObject();
            sig.put("keyid", Sign.keyidFor(leaf.rawPublicKey()));
            sig.put("sig", Base64.getEncoder().encodeToString(signature));
            sig.set("cert", issueCertificate(ca.privateKey(), issuer, "keyless-identity", leaf.rawPublicKey()));
            return envelope;
        }
    }

    private static CertFixture certFixture() throws Exception {
        EdPair ca = ed25519RawPair();
        EdPair leaf = ed25519RawPair();
        String issuer = "test-ca";
        ObjectNode root = Json.nodes().objectNode();
        ObjectNode authorities = root.putObject("certificate_authorities");
        authorities.putObject(issuer).put("public_key", Base64.getEncoder().encodeToString(ca.rawPublicKey()));
        return new CertFixture(Verify.TrustRoot.fromDict(root), ca, leaf, issuer);
    }

    @Test
    void verifyEnvelopeVerifiesARealCertificateKeylessSignedEnvelope() throws Exception {
        CertFixture fixture = certFixture();
        ObjectNode envelope = fixture.sign(Json.nodes().objectNode().put("hello", "world"));
        Verify.VerifiedEnvelope verified = Verify.verifyEnvelope(envelope, fixture.trust());
        assertEquals("keyless-identity", verified.identity());
        assertTrue(verified.keyless());
    }

    @Test
    void verifyEnvelopeRefusesAnUnknownCertificateIssuer() throws Exception {
        CertFixture fixture = certFixture();
        ObjectNode envelope = fixture.sign(Json.nodes().objectNode().put("hello", "world"));
        ((ObjectNode) envelope.get("signatures").get(0).get("cert")).put("issuer", "nope");
        assertEquals(
                "no signature verified against the trust root: unknown certificate issuer 'nope'",
                assertThrows(IllegalArgumentException.class, () -> Verify.verifyEnvelope(envelope, fixture.trust()))
                        .getMessage());
    }

    @Test
    void verifyEnvelopeCollapsesACorruptedCertificateSignatureToCertificateSignatureDoesNotVerify()
            throws Exception {
        CertFixture fixture = certFixture();
        ObjectNode envelope = fixture.sign(Json.nodes().objectNode().put("hello", "world"));
        ((ObjectNode) envelope.get("signatures").get(0).get("cert")).put("signature", "AAAA");
        assertEquals(
                "no signature verified against the trust root: certificate signature does not verify",
                assertThrows(IllegalArgumentException.class, () -> Verify.verifyEnvelope(envelope, fixture.trust()))
                        .getMessage());
    }

    @Test
    void verifyEnvelopeCollapsesANonObjectCertToCertificateSignatureDoesNotVerify() throws Exception {
        KmsFixture fixture = kmsFixture("test-identity");
        ObjectNode envelope = Json.nodes().objectNode();
        envelope.put("payloadType", "t");
        envelope.put("payload", "e30=");
        envelope.putArray("signatures").addObject().put("cert", "not-an-object");
        assertEquals(
                "no signature verified against the trust root: certificate signature does not verify",
                assertThrows(IllegalArgumentException.class, () -> Verify.verifyEnvelope(envelope, fixture.trust()))
                        .getMessage());
    }

    @Test
    void verifyEnvelopeCollapsesANonStringCertificateIdentityToCertificateSignatureDoesNotVerify() throws Exception {
        // Verifier round-2 (adjacent probes): TypeScript/Java already rejected this; Python did not.
        EdPair ca = ed25519RawPair();
        EdPair leaf = ed25519RawPair();
        String issuer = "test-ca";
        ObjectNode root = Json.nodes().objectNode();
        root.putObject("certificate_authorities")
                .putObject(issuer)
                .put("public_key", Base64.getEncoder().encodeToString(ca.rawPublicKey()));
        Verify.TrustRoot trust = Verify.TrustRoot.fromDict(root);
        ObjectNode body = Json.nodes().objectNode();
        body.put("issuer", issuer);
        body.put("identity", 5);
        body.put("algorithm", "ed25519");
        body.put("public_key", Base64.getEncoder().encodeToString(leaf.rawPublicKey()));
        body.put("not_before", "2026-01-01T00:00:00Z");
        body.put("not_after", "2027-01-01T00:00:00Z");
        byte[] signature = edSign(ca.privateKey(), Canonical.canonicalize(body));
        ObjectNode cert = body.deepCopy();
        cert.put("signature", Base64.getEncoder().encodeToString(signature));
        ObjectNode envelope = Json.nodes().objectNode();
        envelope.put("payloadType", "t");
        envelope.put("payload", "e30=");
        ObjectNode sig = envelope.putArray("signatures").addObject();
        sig.put("sig", Base64.getEncoder().encodeToString(new byte[64]));
        sig.set("cert", cert);
        assertEquals(
                "no signature verified against the trust root: certificate signature does not verify",
                assertThrows(IllegalArgumentException.class, () -> Verify.verifyEnvelope(envelope, trust))
                        .getMessage());
    }

    @Test
    void verifyEnvelopeCollapsesAWrongLengthLeafKeyToCertificateSignatureDoesNotVerify() throws Exception {
        // Verifier round-2 (adjacent probes): leaf-key loading was deferred past this collapsing
        // handler and leaked a native "invalid Ed25519 public key" message instead.
        EdPair ca = ed25519RawPair();
        String issuer = "test-ca";
        ObjectNode root = Json.nodes().objectNode();
        root.putObject("certificate_authorities")
                .putObject(issuer)
                .put("public_key", Base64.getEncoder().encodeToString(ca.rawPublicKey()));
        Verify.TrustRoot trust = Verify.TrustRoot.fromDict(root);
        ObjectNode body = Json.nodes().objectNode();
        body.put("issuer", issuer);
        body.put("identity", "ci@agent-conformance.org");
        body.put("algorithm", "ed25519");
        body.put("public_key", Base64.getEncoder().encodeToString("too-short".getBytes(StandardCharsets.UTF_8)));
        body.put("not_before", "2026-01-01T00:00:00Z");
        body.put("not_after", "2027-01-01T00:00:00Z");
        byte[] signature = edSign(ca.privateKey(), Canonical.canonicalize(body));
        ObjectNode cert = body.deepCopy();
        cert.put("signature", Base64.getEncoder().encodeToString(signature));
        ObjectNode envelope = Json.nodes().objectNode();
        envelope.put("payloadType", "t");
        envelope.put("payload", "e30=");
        ObjectNode sig = envelope.putArray("signatures").addObject();
        sig.put("sig", Base64.getEncoder().encodeToString(new byte[64]));
        sig.set("cert", cert);
        assertEquals(
                "no signature verified against the trust root: certificate signature does not verify",
                assertThrows(IllegalArgumentException.class, () -> Verify.verifyEnvelope(envelope, trust))
                        .getMessage());
    }

    // --- TrustRoot.fromDict: the content-addressing invariant. --------------------------------------

    @Test
    void trustRootFromDictRefusesAKeysEntryWhoseDeclaredIdDoesNotMatchItsOwnKey() throws Exception {
        EdPair pair = ed25519RawPair();
        ObjectNode root = Json.nodes().objectNode();
        ObjectNode keys = root.putObject("keys");
        ObjectNode entry = keys.putObject("sha256:wrong");
        entry.put("public_key", Base64.getEncoder().encodeToString(pair.rawPublicKey()));
        entry.put("identity", "x");
        assertEquals(
                "trust root entry 'sha256:wrong' does not match its own key",
                assertThrows(IllegalArgumentException.class, () -> Verify.TrustRoot.fromDict(root)).getMessage());
    }

    // --- publicKeyFromRaw: the DER-reconstruction round trip. ----------------------------------------

    @Test
    void publicKeyFromRawReconstructsAUsablePublicKeyFromRawBytes() throws Exception {
        EdPair pair = ed25519RawPair();
        byte[] data = "round trip".getBytes(StandardCharsets.UTF_8);
        byte[] sig = edSign(pair.privateKey(), data);
        PublicKey publicKey = Verify.publicKeyFromRaw(pair.rawPublicKey());
        Signature verifier = Signature.getInstance("Ed25519");
        verifier.initVerify(publicKey);
        verifier.update(data);
        assertTrue(verifier.verify(sig));
    }

    // --- verifyCatalog. -------------------------------------------------------------------------------

    private static Path catalogDirWith(Path tmp, String name, String content) throws Exception {
        Files.writeString(tmp.resolve(name), content);
        return tmp;
    }

    @Test
    void verifyCatalogSoftFailsWithTheExactUnsignedSentenceWhenCatalogSigJsonIsAbsent(@TempDir Path dir)
            throws Exception {
        KmsFixture fixture = kmsFixture("test-identity");
        catalogDirWith(dir, "rule.yaml", "x: 1\n");
        ObjectNode result = Verify.verifyCatalog(dir, fixture.trust());
        assertFalse(result.get("verified").asBoolean());
        assertEquals(
                "unsigned: catalog.sig.json is absent, so there is no signature to verify (SPEC §8.7).",
                result.get("reason").asText());
    }

    @Test
    void verifyCatalogSoftFailsOnUnparseableCatalogSigJson(@TempDir Path dir) throws Exception {
        KmsFixture fixture = kmsFixture("test-identity");
        catalogDirWith(dir, "rule.yaml", "x: 1\n");
        catalogDirWith(dir, "catalog.sig.json", "not json");
        ObjectNode result = Verify.verifyCatalog(dir, fixture.trust());
        assertFalse(result.get("verified").asBoolean());
        assertEquals("catalog.sig.json is not readable JSON", result.get("reason").asText());
    }

    @Test
    void verifyCatalogVerifiesARealSignedDirectoryAndReportsVerifiedTrueKeylessFalse(@TempDir Path dir)
            throws Exception {
        KmsFixture fixture = kmsFixture("catalog-signer");
        catalogDirWith(dir, "rule.yaml", "x: 1\n");
        String digest = Catalog.digestTree(dir, java.util.Set.of("catalog.sig.json"));
        ObjectNode statement = Json.nodes().objectNode();
        statement.put("_type", "https://in-toto.io/Statement/v1");
        ObjectNode subject = statement.putArray("subject").addObject();
        subject.put("name", "catalog");
        subject.putObject("digest").put("sha256", digest.substring("sha256:".length()));
        statement.put("predicateType", "https://agent-conformance.org/attestation/catalog/v1");
        statement.putObject("predicate");
        ObjectNode envelope = signEnvelope(statement, fixture);
        Files.writeString(dir.resolve("catalog.sig.json"), Json.pretty(envelope));
        ObjectNode result = Verify.verifyCatalog(dir, fixture.trust());
        assertTrue(result.get("verified").asBoolean());
        assertEquals(digest, result.get("digest").asText());
        assertEquals("catalog-signer", result.get("signer").asText());
        assertFalse(result.get("keyless").asBoolean());
    }

    @Test
    void verifyCatalogSoftFailsWithNoTrailingPeriodWhenTheSignedDigestDoesNotMatchTheDirectory(@TempDir Path dir)
            throws Exception {
        KmsFixture fixture = kmsFixture("test-identity");
        catalogDirWith(dir, "rule.yaml", "x: 1\n");
        ObjectNode statement = Json.nodes().objectNode();
        statement.put("_type", "https://in-toto.io/Statement/v1");
        ObjectNode subject = statement.putArray("subject").addObject();
        subject.put("name", "catalog");
        subject.putObject("digest").put("sha256", "0".repeat(64));
        statement.put("predicateType", "https://agent-conformance.org/attestation/catalog/v1");
        statement.putObject("predicate");
        ObjectNode envelope = signEnvelope(statement, fixture);
        Files.writeString(dir.resolve("catalog.sig.json"), Json.pretty(envelope));
        ObjectNode result = Verify.verifyCatalog(dir, fixture.trust());
        assertFalse(result.get("verified").asBoolean());
        assertEquals(
                "the signature covers a different catalog digest than the directory content",
                result.get("reason").asText());
    }

    // --- verifyRelease: single-file form. -------------------------------------------------------------

    @Test
    void verifyReleaseSoftFailsWithTheFixedSentenceOnUnreadableEnvelopeJson(@TempDir Path dir) throws Exception {
        KmsFixture fixture = kmsFixture("test-identity");
        Path path = dir.resolve("release.dsse.json");
        Files.writeString(path, "not json");
        ObjectNode result = Verify.verifyRelease(path, fixture.trust());
        assertFalse(result.get("verified").asBoolean());
        assertEquals("release envelope is not readable JSON", result.get("reason").asText());
    }

    @Test
    void verifyReleaseSoftFailsOnAnUnresolvableKeyidExitShapeMatchesCatalog(@TempDir Path dir) throws Exception {
        KmsFixture fixture = kmsFixture("test-identity");
        ObjectNode envelope = Json.nodes().objectNode();
        envelope.put("payloadType", "application/vnd.in-toto+json");
        envelope.put("payload", "e30=");
        ObjectNode sig = envelope.putArray("signatures").addObject();
        sig.put("keyid", "sha256:deadbeef");
        sig.put("sig", "AAAA");
        Path path = dir.resolve("release.dsse.json");
        Files.writeString(path, Json.pretty(envelope));
        ObjectNode result = Verify.verifyRelease(path, fixture.trust());
        assertFalse(result.get("verified").asBoolean());
        assertFalse(result.has("error"));
        assertTrue(result.has("reason"));
    }

    @Test
    void verifyReleaseSoftFailsOnMalformedFieldTypesWithoutCrashing(@TempDir Path dir) throws Exception {
        KmsFixture fixture = kmsFixture("test-identity");
        ObjectNode a = Json.nodes().objectNode();
        a.put("payloadType", "t");
        a.put("payload", 5);
        a.putArray("signatures");
        ObjectNode b = Json.nodes().objectNode();
        b.put("payloadType", 5);
        b.put("payload", "e30=");
        b.putArray("signatures");
        ObjectNode c = Json.nodes().objectNode();
        c.put("payloadType", "t");
        c.put("payload", "e30=");
        c.putArray("signatures").add("x");
        int i = 0;
        for (ObjectNode bad : new ObjectNode[] {a, b, c}) {
            Path path = dir.resolve("release-" + (i++) + ".dsse.json");
            Files.writeString(path, Json.pretty(bad));
            ObjectNode result = Verify.verifyRelease(path, fixture.trust());
            assertFalse(result.get("verified").asBoolean());
        }
    }

    @Test
    void verifyReleaseVerifiesARealSingleFileKmsSignedRelease(@TempDir Path dir) throws Exception {
        KmsFixture fixture = kmsFixture("release-signer");
        ObjectNode envelope = signEnvelope(Json.nodes().objectNode().put("hello", "release"), fixture);
        Path path = dir.resolve("release.dsse.json");
        Files.writeString(path, Json.pretty(envelope));
        ObjectNode result = Verify.verifyRelease(path, fixture.trust());
        assertTrue(result.get("verified").asBoolean());
        assertEquals("release-signer", result.get("signer").asText());
    }

    @Test
    void verifyReleaseRefusesAByteOrderMarkedEnvelopeInsteadOfSilentlyStrippingIt(@TempDir Path dir)
            throws Exception {
        // Verifier round-1 Finding 4: a leading U+FEFF must make the file unreadable JSON here too,
        // matching Python's `read_text("utf-8")` and this engine's own decoder.
        KmsFixture fixture = kmsFixture("test-identity");
        ObjectNode envelope = Json.nodes().objectNode();
        envelope.put("payloadType", "x");
        envelope.put("payload", "");
        envelope.putArray("signatures");
        Path path = dir.resolve("release.dsse.json");
        Files.writeString(path, "﻿" + Json.pretty(envelope));
        ObjectNode result = Verify.verifyRelease(path, fixture.trust());
        assertFalse(result.get("verified").asBoolean());
        assertEquals("release envelope is not readable JSON", result.get("reason").asText());
    }

    // --- verifyRelease: directory-bundle form. --------------------------------------------------------

    @Test
    void verifyReleaseSoftFailsWithTheFixedSentenceOnAnUnreadableManifestNoManifestDigestSignersField(
            @TempDir Path dir) throws Exception {
        KmsFixture fixture = kmsFixture("test-identity");
        Files.writeString(dir.resolve("release-manifest.json"), "not json");
        Files.writeString(dir.resolve("signatures.json"), "[]");
        ObjectNode result = Verify.verifyRelease(dir, fixture.trust());
        assertFalse(result.get("verified").asBoolean());
        assertEquals("release manifest is not readable JSON", result.get("reason").asText());
        assertFalse(result.has("manifest_digest"));
        assertFalse(result.has("signers"));
    }

    @Test
    void verifyReleaseSoftFailsOnAManifestThatParsesButIsNotAnObject(@TempDir Path dir) throws Exception {
        KmsFixture fixture = kmsFixture("test-identity");
        Files.writeString(dir.resolve("release-manifest.json"), "[]");
        Files.writeString(dir.resolve("signatures.json"), "[]");
        ObjectNode result = Verify.verifyRelease(dir, fixture.trust());
        assertFalse(result.get("verified").asBoolean());
        assertEquals("release manifest is not readable JSON", result.get("reason").asText());
    }

    @Test
    void verifyReleaseSoftFailsWithTheFixedSentenceOnUnreadableSignaturesJsonNoManifestDigestSignersField(
            @TempDir Path dir) throws Exception {
        KmsFixture fixture = kmsFixture("test-identity");
        Files.writeString(dir.resolve("release-manifest.json"), "{}");
        Files.writeString(dir.resolve("signatures.json"), "not json");
        ObjectNode result = Verify.verifyRelease(dir, fixture.trust());
        assertFalse(result.get("verified").asBoolean());
        assertEquals("release signatures are not readable JSON", result.get("reason").asText());
        assertFalse(result.has("manifest_digest"));
        assertFalse(result.has("signers"));
    }

    @Test
    void verifyReleaseFoldsSignaturesJsonValidJsonButNotArrayIntoTheSameNotReadableSentence(@TempDir Path dir)
            throws Exception {
        KmsFixture fixture = kmsFixture("test-identity");
        Files.writeString(dir.resolve("release-manifest.json"), "{}");
        Files.writeString(dir.resolve("signatures.json"), "5");
        ObjectNode result = Verify.verifyRelease(dir, fixture.trust());
        assertFalse(result.get("verified").asBoolean());
        assertEquals("release signatures are not readable JSON", result.get("reason").asText());
        assertFalse(result.has("manifest_digest"));
    }

    @Test
    void verifyReleaseFlagsAnArtifactsEntryWithNoNameAndOneWhoseNameIsNotAString(@TempDir Path dir) throws Exception {
        KmsFixture fixture = kmsFixture("test-identity");
        ObjectNode manifest = Json.nodes().objectNode();
        ArrayNode artifacts = manifest.putArray("artifacts");
        artifacts.addObject().put("not_name", "x");
        artifacts.addObject().put("name", 5);
        Files.writeString(dir.resolve("release-manifest.json"), Json.pretty(manifest));
        Files.writeString(dir.resolve("signatures.json"), "[]");
        ObjectNode result = Verify.verifyRelease(dir, fixture.trust());
        assertFalse(result.get("verified").asBoolean());
        String reason = result.get("reason").asText();
        int count = reason.split("release manifest has an artifact entry with no name", -1).length - 1;
        assertEquals(2, count);
    }

    @Test
    void verifyReleaseFlagsAMissingArtifactFileAndADigestMismatch(@TempDir Path dir) throws Exception {
        KmsFixture fixture = kmsFixture("test-identity");
        ObjectNode manifest = Json.nodes().objectNode();
        ArrayNode artifacts = manifest.putArray("artifacts");
        artifacts.addObject().put("name", "missing.bin").put("digest", "sha256:0");
        artifacts.addObject().put("name", "present.bin").put("digest", "sha256:0");
        Files.writeString(dir.resolve("release-manifest.json"), Json.pretty(manifest));
        Files.writeString(dir.resolve("signatures.json"), "[]");
        Files.writeString(dir.resolve("present.bin"), "actual content");
        ObjectNode result = Verify.verifyRelease(dir, fixture.trust());
        assertFalse(result.get("verified").asBoolean());
        String reason = result.get("reason").asText();
        assertTrue(reason.contains("missing artifact missing.bin"));
        assertTrue(reason.contains("digest mismatch for present.bin"));
    }

    @Test
    void verifyReleaseFlagsANonObjectSignaturesJsonEntryWithTheFixedSignatureNoneText(@TempDir Path dir)
            throws Exception {
        KmsFixture fixture = kmsFixture("test-identity");
        Files.writeString(dir.resolve("release-manifest.json"), "{}");
        Files.writeString(dir.resolve("signatures.json"), "[\"x\"]");
        ObjectNode result = Verify.verifyRelease(dir, fixture.trust());
        assertFalse(result.get("verified").asBoolean());
        assertEquals("signature (None): signature entry is not an object", result.get("reason").asText());
    }

    @Test
    void verifyReleaseFlagsASignatureEntryMissingItsEnvelopeKeyWithPyStrProfileRendering(@TempDir Path dir)
            throws Exception {
        KmsFixture fixture = kmsFixture("test-identity");
        Files.writeString(dir.resolve("release-manifest.json"), "{}");
        ArrayNode sigs = Json.nodes().arrayNode();
        sigs.addObject().put("profile", "kms");
        Files.writeString(dir.resolve("signatures.json"), Json.pretty(sigs));
        ObjectNode result = Verify.verifyRelease(dir, fixture.trust());
        assertFalse(result.get("verified").asBoolean());
        assertEquals("signature (kms): 'envelope'", result.get("reason").asText());
    }

    @Test
    void verifyReleaseVerifiesARealDirectoryBundleReleaseSignedByOneKmsEntry(@TempDir Path dir) throws Exception {
        KmsFixture fixture = kmsFixture("bundle-signer");
        ObjectNode manifest = Json.nodes().objectNode();
        manifest.putArray("artifacts");
        String manifestDigest = "sha256:" + Canonical.sha256Hex(manifest);
        ObjectNode statement = Json.nodes().objectNode();
        statement.put("_type", "https://in-toto.io/Statement/v1");
        ObjectNode subject = statement.putArray("subject").addObject();
        subject.put("name", "release-manifest.json");
        subject.putObject("digest").put("sha256", manifestDigest.substring("sha256:".length()));
        statement.put("predicateType", "https://agent-conformance.org/attestation/release/v1");
        statement.putObject("predicate");
        ObjectNode envelope = signEnvelope(statement, fixture);
        Files.writeString(dir.resolve("release-manifest.json"), Json.pretty(manifest));
        ArrayNode sigs = Json.nodes().arrayNode();
        ObjectNode sigEntry = sigs.addObject();
        sigEntry.put("profile", "kms");
        sigEntry.set("envelope", envelope);
        Files.writeString(dir.resolve("signatures.json"), Json.pretty(sigs));
        ObjectNode result = Verify.verifyRelease(dir, fixture.trust());
        assertTrue(result.get("verified").asBoolean());
        assertEquals(manifestDigest, result.get("manifest_digest").asText());
        assertEquals(1, result.get("signers").size());
        assertEquals("bundle-signer", result.get("signers").get(0).get("identity").asText());
    }

    @Test
    void verifyReleaseRefusesAnUnsignedBundleWithZeroSignatureEntries(@TempDir Path dir) throws Exception {
        // Verifier round-1 Finding 1 (HIGH, security): no problems and no signers used to mean
        // verified:true -- an unsigned bundle reported as verified.
        KmsFixture fixture = kmsFixture("test-identity");
        ObjectNode manifest = Json.nodes().objectNode();
        manifest.putArray("artifacts");
        Files.writeString(dir.resolve("release-manifest.json"), Json.pretty(manifest));
        Files.writeString(dir.resolve("signatures.json"), "[]");
        ObjectNode result = Verify.verifyRelease(dir, fixture.trust());
        assertFalse(result.get("verified").asBoolean());
        assertEquals("release bundle carries no signatures", result.get("reason").asText());
        assertEquals(0, result.get("signers").size());
    }

    @Test
    void verifyReleaseTreatsANonArrayArtifactsFieldAsEmptyNotACrash(@TempDir Path dir) throws Exception {
        // Verifier round-1 Finding 2.
        KmsFixture fixture = kmsFixture("test-identity");
        for (JsonNode badArtifacts : new JsonNode[] {
            Json.nodes().nullNode(), Json.nodes().numberNode(5), Json.nodes().textNode("ab")
        }) {
            ObjectNode manifest = Json.nodes().objectNode();
            manifest.set("artifacts", badArtifacts);
            Files.writeString(dir.resolve("release-manifest.json"), Json.pretty(manifest));
            Files.writeString(dir.resolve("signatures.json"), "[]");
            ObjectNode result = Verify.verifyRelease(dir, fixture.trust());
            assertFalse(result.get("verified").asBoolean());
            assertEquals("release bundle carries no signatures", result.get("reason").asText());
        }
    }

    @Test
    void verifyReleaseSoftFailsWhenTheManifestCannotBeCanonicalized(@TempDir Path dir) throws Exception {
        // Verifier round-1 Finding 3.
        KmsFixture fixture = kmsFixture("test-identity");
        ObjectNode manifest = Json.nodes().objectNode();
        manifest.putArray("artifacts");
        manifest.put("size", 1.5);
        Files.writeString(dir.resolve("release-manifest.json"), Json.pretty(manifest));
        Files.writeString(dir.resolve("signatures.json"), "[]");
        ObjectNode result = Verify.verifyRelease(dir, fixture.trust());
        assertFalse(result.get("verified").asBoolean());
        assertEquals("release manifest cannot be canonicalized", result.get("reason").asText());
    }

    @Test
    void verifyReleaseSoftFailsOnAnIntegralValuedFloatManifestField(@TempDir Path dir) throws Exception {
        // Verifier round-2: `1.0`/`1e2`/`-0.0` are still non-integer number tokens and must refuse
        // exactly like `1.5` -- confirms Java already agrees with Python here.
        KmsFixture fixture = kmsFixture("test-identity");
        for (String token : new String[] {"1.0", "1e2", "-0.0"}) {
            Files.writeString(dir.resolve("release-manifest.json"), "{\"artifacts\": [], \"size\": " + token + "}");
            Files.writeString(dir.resolve("signatures.json"), "[]");
            ObjectNode result = Verify.verifyRelease(dir, fixture.trust());
            assertFalse(result.get("verified").asBoolean());
            assertEquals("release manifest cannot be canonicalized", result.get("reason").asText());
        }
    }

    @Test
    void verifyReleaseTreatsAnUnsafeArtifactNameAsMissing(@TempDir Path dir) throws Exception {
        // Verifier round-2 (adjacent probes): an absolute path, a `..` escape, or an embedded NUL
        // byte must soft-fail as a missing artifact, never read outside the release directory and
        // never crash with `InvalidPathException`.
        KmsFixture fixture = kmsFixture("test-identity");
        for (String name : new String[] {"/etc/hosts", "../../../../../../etc/hosts", "a\u0000b"}) {
            ObjectNode manifest = Json.nodes().objectNode();
            ArrayNode artifacts = manifest.putArray("artifacts");
            artifacts.addObject().put("name", name).put("digest", "sha256:0");
            Files.writeString(dir.resolve("release-manifest.json"), Json.pretty(manifest));
            Files.writeString(dir.resolve("signatures.json"), "[]");
            ObjectNode result = Verify.verifyRelease(dir, fixture.trust());
            assertFalse(result.get("verified").asBoolean());
            assertTrue(result.get("reason").asText().contains("missing artifact " + name));
        }
    }

    @Test
    void verifyReleaseSoftFailsOnADeeplyNestedManifestInsteadOfCrashing(@TempDir Path dir) throws Exception {
        // Verifier round-2 (adjacent probes): confirms Jackson's own nesting-depth guard already
        // refuses cleanly here (unlike Python's `RecursionError`/TypeScript's stack overflow).
        KmsFixture fixture = kmsFixture("test-identity");
        StringBuilder nested = new StringBuilder();
        nested.append("{\"artifacts\": [], \"nested\": ");
        nested.append("[".repeat(5000));
        nested.append("]".repeat(5000));
        nested.append("}");
        Files.writeString(dir.resolve("release-manifest.json"), nested.toString());
        Files.writeString(dir.resolve("signatures.json"), "[]");
        ObjectNode result = Verify.verifyRelease(dir, fixture.trust());
        assertFalse(result.get("verified").asBoolean());
        assertEquals("release manifest is not readable JSON", result.get("reason").asText());
    }

    @Test
    void verifyReleaseThrowsInputReleaseBundleForADirectoryWithNoManifestSignaturesFiles(@TempDir Path dir)
            throws Exception {
        KmsFixture fixture = kmsFixture("test-identity");
        Files.writeString(dir.resolve("readme.txt"), "not a release");
        InputError error = assertThrows(InputError.class, () -> Verify.verifyRelease(dir, fixture.trust()));
        assertEquals("input.release_bundle", error.key);
    }

    // --- TrustRoot.fromDict mirrors Python's `data.get(field) or {}` (18.36 critic round 2, A). ---

    @Test
    void trustRootFromDictTreatsAFalsyKeysOrAuthoritiesValueAsEmpty() {
        for (String empty : List.of("null", "[]", "\"\"", "0", "false")) {
            for (String field : List.of("keys", "certificate_authorities")) {
                Verify.TrustRoot.fromDict(Json.parse("{\"" + field + "\": " + empty + "}"));
            }
        }
    }

    @Test
    void trustRootFromDictRefusesATruthyKeysValueThatIsNotAMapping() {
        for (String bad : List.of("\"x\"", "[1]", "5", "true")) {
            for (String field : List.of("keys", "certificate_authorities")) {
                assertThrows(IllegalArgumentException.class,
                        () -> Verify.TrustRoot.fromDict(Json.parse("{\"" + field + "\": " + bad + "}")), bad);
            }
        }
    }

    // --- Keyless certificate fields (item 18.63): algorithm, and the validity window's shape and order. --

    /**
     * A certificate-signed envelope whose certificate body the test authority re-signs after setting the
     * fields in {@code changesJson} and deleting {@code deleted}, so only the field checks can refuse it.
     */
    private record Keyless(ObjectNode envelope, Verify.TrustRoot trust) {
        Verify.VerifiedEnvelope verify() {
            return Verify.verifyEnvelope(envelope, trust);
        }
    }

    private static Keyless keylessWith(String changesJson, String... deleted) throws Exception {
        CertFixture fixture = certFixture();
        ObjectNode envelope = fixture.sign(Json.nodes().objectNode().put("hello", "world"));
        ObjectNode cert = (ObjectNode) envelope.get("signatures").get(0).get("cert");
        cert.remove("signature");
        cert.setAll((ObjectNode) Json.parse(changesJson));
        cert.remove(List.of(deleted));
        byte[] signature = edSign(fixture.ca().privateKey(), Canonical.canonicalize(cert));
        cert.put("signature", Base64.getEncoder().encodeToString(signature));
        return new Keyless(envelope, fixture.trust());
    }

    private static void assertCertificateRefusal(String key, String changesJson, String... deleted)
            throws Exception {
        Keyless made = keylessWith(changesJson, deleted);
        String expected = "no signature verified against the trust root: " + key + ": "
                + Messages.errorCause(key).replaceAll("\\.$", "");
        assertEquals(expected, assertThrows(IllegalArgumentException.class, made::verify).getMessage(),
                changesJson);
    }

    @Test
    void keylessCertificateWithAnAlgorithmOtherThanExactlyEd25519IsRefused() throws Exception {
        for (String algorithm : List.of("\"ecdsa-p256\"", "\"ED25519\"", "\"ed25519 \"", "1", "[\"ed25519\"]", "null")) {
            assertCertificateRefusal("verify.certificate_algorithm", "{\"algorithm\": " + algorithm + "}");
        }
        assertCertificateRefusal("verify.certificate_algorithm", "{}", "algorithm");
    }

    @Test
    void keylessCertificateWithAMalformedValidityTimestampIsRefused() throws Exception {
        for (String changes : List.of(
                "{\"not_before\": \"2026-01-01 00:00:00Z\"}",
                "{\"not_before\": \"2026-01-01T00:00:00+00:00\"}",
                "{\"not_before\": \"2026-01-01T00:00:00\"}",
                "{\"not_before\": \"2026-02-29T00:00:00Z\"}",
                "{\"not_before\": \"2026-01-01T24:00:00Z\"}",
                "{\"not_before\": \"2026-01-01T00:00:00Z\\n\"}",
                "{\"not_before\": \"\u0662\u0660\u0662\u0666-01-01T00:00:00Z\"}",
                "{\"not_before\": 1767225600}",
                "{\"not_after\": \"2027-01-32T00:00:00Z\"}",
                "{\"not_after\": \"2027-01-01T00:00:00.Z\"}",
                "{\"not_after\": null}")) {
            assertCertificateRefusal("verify.certificate_validity_malformed", changes);
        }
        assertCertificateRefusal("verify.certificate_validity_malformed", "{}", "not_before");
    }

    @Test
    void keylessCertificateWithAReversedWindowIsRefused() throws Exception {
        assertCertificateRefusal("verify.certificate_validity_inverted",
                "{\"not_before\": \"2027-01-01T00:00:00Z\", \"not_after\": \"2026-01-01T00:00:00Z\"}");
        assertCertificateRefusal("verify.certificate_validity_inverted",
                "{\"not_before\": \"2026-01-01T00:00:00.5Z\", \"not_after\": \"2026-01-01T00:00:00.49Z\"}");
    }

    @Test
    void keylessCertificateWithAWellFormedWindowVerifiesAndTheAlgorithmIsCheckedFirst() throws Exception {
        for (String changes : List.of(
                "{\"not_before\": \"2027-01-01T00:00:00Z\", \"not_after\": \"2027-01-01T00:00:00Z\"}",
                "{\"not_before\": \"2026-01-01t00:00:00z\"}",
                "{\"not_after\": \"2026-12-31T23:59:60Z\"}",
                "{\"not_before\": \"2024-02-29T00:00:00Z\"}",
                "{\"not_before\": \"2026-01-01T00:00:00.5Z\", \"not_after\": \"2026-01-01T00:00:00.50Z\"}",
                "{\"not_before\": \"2026-01-01T00:00:00.50Z\", \"not_after\": \"2026-01-01T00:00:00.5Z\"}")) {
            assertTrue(keylessWith(changes).verify().keyless(), changes);
        }
        assertCertificateRefusal("verify.certificate_algorithm", "{\"algorithm\": \"rsa\", \"not_before\": \"junk\"}");
    }

    @Test
    void keylessCertificateWithAWindowWhollyIn1970OrWhollyIn2999VerifiesSoTheClockIsNeverRead() throws Exception {
        for (String year : List.of("1970", "2999")) {
            String changes = "{\"not_before\": \"" + year + "-01-01T00:00:00Z\", \"not_after\": \"" + year
                    + "-01-01T00:10:00Z\"}";
            assertTrue(keylessWith(changes).verify().keyless(), changes);
        }
    }

    // --- 18.68: an input that cannot be read is refused with one key per target, naming the path. ---

    private static final Path QUICKSTART_EVIDENCE = TestPaths.repoRoot().resolve("corpus/quickstart/evidence");
    private static final String STREAM = "events/urn-agentce-source-langgraph-gateway-eu-1.jsonl";

    private static void assumeNotRoot() {
        // Root reads a mode-000 file anyway.
        Assumptions.assumeFalse("root".equals(System.getProperty("user.name")), "running as root");
    }

    /** Runs {@code action} with {@code path} made unreadable (chmod 000), restoring it after. */
    private static InputError unreadableRefusal(Path path, Runnable action) throws Exception {
        Set<PosixFilePermission> mode = Files.getPosixFilePermissions(path);
        Files.setPosixFilePermissions(path, PosixFilePermissions.fromString("---------"));
        try {
            return assertThrows(InputError.class, action::run);
        } finally {
            Files.setPosixFilePermissions(path, mode);
        }
    }

    private static Path evidenceCopy(Path dir) throws Exception {
        Path bundle = dir.resolve("evidence");
        try (var paths = Files.walk(QUICKSTART_EVIDENCE)) {
            for (Path source : (Iterable<Path>) paths::iterator) {
                Files.copy(source, bundle.resolve(QUICKSTART_EVIDENCE.relativize(source).toString()));
            }
        }
        return bundle;
    }

    private static Verify.TrustRoot noKeys() {
        return Verify.TrustRoot.fromDict(Json.parse("{\"keys\": {}}"));
    }

    @Test
    void verifyUnreadableInputBundleDirectoryNamesTheManifest(@TempDir Path dir) throws Exception {
        assumeNotRoot();
        Path bundle = evidenceCopy(dir);
        InputError err = unreadableRefusal(bundle, () -> Bundle.load(bundle));
        assertEquals("input.bundle_unreadable", err.key);
        assertEquals(
                "the evidence bundle " + bundle + " holds a file or folder that cannot be read: manifest.json.",
                err.reason);
    }

    @Test
    void verifyUnreadableInputEventsDirectoryNamesAFileUnderIt(@TempDir Path dir) throws Exception {
        assumeNotRoot();
        Path bundle = evidenceCopy(dir);
        InputError err = unreadableRefusal(bundle.resolve("events"), () -> Bundle.load(bundle));
        assertEquals("input.bundle_unreadable", err.key);
        assertTrue(err.reason.contains("cannot be read: events/"), err.reason);
    }

    @Test
    void verifyUnreadableInputStreamFileIsNamed(@TempDir Path dir) throws Exception {
        assumeNotRoot();
        Path bundle = evidenceCopy(dir);
        InputError err = unreadableRefusal(bundle.resolve(STREAM), () -> Bundle.load(bundle));
        assertEquals("input.bundle_unreadable", err.key);
        assertEquals(
                "the evidence bundle " + bundle + " holds a file or folder that cannot be read: " + STREAM + ".",
                err.reason);
    }

    @Test
    void verifyUnreadableInputReleaseDirectory(@TempDir Path dir) throws Exception {
        assumeNotRoot();
        Path release = Files.createDirectory(dir.resolve("release"));
        Files.writeString(release.resolve("release-manifest.json"), "{}");
        InputError err = unreadableRefusal(release, () -> Verify.verifyRelease(release, noKeys()));
        assertEquals("input.release_unreadable", err.key);
        assertTrue(err.reason.startsWith("the release artifact " + release + " "), err.reason);
    }

    @Test
    void verifyUnreadableInputReleaseArtifactFolder(@TempDir Path dir) throws Exception {
        assumeNotRoot();
        Path release = Files.createDirectories(dir.resolve("release").resolve("sub"));
        Files.writeString(release.resolve("a.txt"), "a\n");
        Path root = release.getParent();
        Files.writeString(root.resolve("release-manifest.json"),
                "{\"artifacts\": [{\"name\": \"sub/a.txt\", \"sha256\": \"00\"}]}");
        Files.writeString(root.resolve("signatures.json"), "{\"signatures\": []}");
        InputError err = unreadableRefusal(release, () -> Verify.verifyRelease(root, noKeys()));
        assertEquals("input.release_unreadable", err.key);
        assertTrue(err.reason.endsWith("cannot be read: sub/a.txt."), err.reason);
    }

    @Test
    void verifyUnreadableInputCatalogSubdirectoryIsNamed(@TempDir Path dir) throws Exception {
        assumeNotRoot();
        Files.writeString(dir.resolve("catalog.yaml"), "id: demo\n");
        Files.createDirectory(dir.resolve("controls"));
        Files.writeString(dir.resolve("controls/c.yaml"), "id: c\n");
        InputError err = unreadableRefusal(dir.resolve("controls"), () -> Verify.verifyCatalog(dir, noKeys()));
        assertEquals("input.catalog_unreadable", err.key);
        assertEquals(
                "the catalog directory " + dir + " holds a file or folder that cannot be read: controls.", err.reason);
    }
}
