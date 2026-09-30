package org.agentce;

import static org.junit.jupiter.api.Assertions.assertArrayEquals;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.io.IOException;
import java.math.BigInteger;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.KeyPair;
import java.security.KeyPairGenerator;
import java.security.PrivateKey;
import java.security.interfaces.ECPrivateKey;
import java.security.interfaces.RSAPrivateCrtKey;
import java.security.spec.ECGenParameterSpec;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Base64;
import java.util.HexFormat;
import java.util.List;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/**
 * {@code Sign.java}'s DSSE/in-toto primitives (item 18.26): the compute seam {@code Cli.java}'s
 * {@code cmdSign} wires up. Mirrors {@code sign.test.ts} exactly, plus two cases the TypeScript port
 * does not need: a known-answer test for the seeded-{@code SecureRandom}/{@code KeyPairGenerator}
 * public-key-derivation trick (against RFC 8032 §7.1 TEST 1, byte-for-byte, not merely
 * self-consistency), and the legacy PKCS1/SEC1 PEM re-wrap this Java-only port adds ahead of the
 * PKCS8 attempt loop (round-1 critic correction, {@code contracts/P18-18.26.md} C2). {@code CliTest}
 * covers the full {@code agentce sign} command end to end.
 */
class SignTest {

    // --- DER TLV construction, only what these fixtures need (mirrors Sign.java's own minimal
    // encoder; kept separate since these are test fixtures, never production parsing). --------------

    private static byte[] hex(String text) {
        return HexFormat.of().parseHex(text);
    }

    private static byte[] derLen(int length) {
        if (length < 128) {
            return new byte[] {(byte) length};
        }
        List<Byte> bytes = new ArrayList<>();
        int remaining = length;
        while (remaining > 0) {
            bytes.add(0, (byte) (remaining & 0xFF));
            remaining >>= 8;
        }
        byte[] out = new byte[bytes.size() + 1];
        out[0] = (byte) (0x80 | bytes.size());
        for (int i = 0; i < bytes.size(); i++) {
            out[i + 1] = bytes.get(i);
        }
        return out;
    }

    private static byte[] tlv(int tag, byte[] content) {
        byte[] len = derLen(content.length);
        byte[] out = new byte[1 + len.length + content.length];
        out[0] = (byte) tag;
        System.arraycopy(len, 0, out, 1, len.length);
        System.arraycopy(content, 0, out, 1 + len.length, content.length);
        return out;
    }

    private static byte[] seq(byte[] content) {
        return tlv(0x30, content);
    }

    private static byte[] intTlv(BigInteger value) {
        return tlv(0x02, value.toByteArray());
    }

    private static byte[] concat(byte[]... parts) {
        int total = 0;
        for (byte[] part : parts) {
            total += part.length;
        }
        byte[] out = new byte[total];
        int pos = 0;
        for (byte[] part : parts) {
            System.arraycopy(part, 0, out, pos, part.length);
            pos += part.length;
        }
        return out;
    }

    // --- Ed25519 test-key generation (mirrors sign.test.ts's `ed25519PemPair`); PEM-armoring and
    // signature verification are shared with CliTest via Fixtures. --------------------------------

    private record EdPair(String privatePem, byte[] rawPublicKey) {}

    private static EdPair ed25519PemPair() throws Exception {
        KeyPairGenerator generator = KeyPairGenerator.getInstance("Ed25519");
        KeyPair pair = generator.generateKeyPair();
        String privatePem = Fixtures.toPem("PRIVATE KEY", pair.getPrivate().getEncoded());
        byte[] spki = pair.getPublic().getEncoded();
        byte[] rawPublicKey = Arrays.copyOfRange(spki, spki.length - 32, spki.length);
        return new EdPair(privatePem, rawPublicKey);
    }

    // --- dssePae -------------------------------------------------------------------------------------

    @Test
    void dssePaeBuildsTheExactDsSev1PreAuthenticationEncodingBytes() {
        byte[] payload = "hello".getBytes(StandardCharsets.UTF_8);
        byte[] pae = Sign.dssePae("application/vnd.in-toto+json", payload);
        assertEquals(
                "DSSEv1 28 application/vnd.in-toto+json 5 hello", new String(pae, StandardCharsets.UTF_8));
    }

    @Test
    void dssePaeNeverDoubleEncodesAPayloadContainingRawNonUtf8SafeBytes() {
        byte[] payload = new byte[] {(byte) 0xff, 0x00, 0x41};
        byte[] pae = Sign.dssePae("t", payload);
        // "DSSEv1 " (7) + "1" (1) + " " (1) + "t" (1) + " " (1) + "3" (1) + " " (1) = 13 header bytes.
        assertEquals(13 + payload.length, pae.length);
        assertArrayEquals(payload, Arrays.copyOfRange(pae, 13, pae.length));
    }

    // --- keyidFor ------------------------------------------------------------------------------------

    @Test
    void keyidForIsSha256OverTheRawPublicKeyBytes() throws Exception {
        EdPair pair = ed25519PemPair();
        String keyid = Sign.keyidFor(pair.rawPublicKey());
        assertTrue(keyid.startsWith("sha256:"), keyid);
        assertEquals("sha256:".length() + 64, keyid.length());
    }

    // --- KmsSigner.load / sign -------------------------------------------------------------------

    @Test
    void kmsSignerLoadDerivesTheCorrectPairedPublicKeyFromAPrivateKeyOnlyPem(@TempDir Path dir) throws Exception {
        EdPair pair = ed25519PemPair();
        Path keyPath = dir.resolve("key.pem");
        Files.writeString(keyPath, pair.privatePem());
        Sign.KmsSigner signer = Sign.KmsSigner.load(keyPath);
        assertEquals(Base64.getEncoder().encodeToString(pair.rawPublicKey()), signer.publicKeyB64());
        assertEquals(Sign.keyidFor(pair.rawPublicKey()), signer.keyid());
    }

    @Test
    void kmsSignerSignProducesASignatureThatVerifiesAgainstTheDerivedPublicKey(@TempDir Path dir) throws Exception {
        EdPair pair = ed25519PemPair();
        Path keyPath = dir.resolve("key.pem");
        Files.writeString(keyPath, pair.privatePem());
        Sign.KmsSigner signer = Sign.KmsSigner.load(keyPath);
        byte[] data = "some data to sign".getBytes(StandardCharsets.UTF_8);
        byte[] sig = signer.sign(data);
        assertTrue(Fixtures.edVerify(data, pair.rawPublicKey(), sig));
    }

    @Test
    void kmsSignerLoadRejectsANonEd25519RsaKeyWithSignKeyAlgorithm(@TempDir Path dir) throws Exception {
        KeyPairGenerator generator = KeyPairGenerator.getInstance("RSA");
        generator.initialize(2048);
        PrivateKey rsaKey = generator.generateKeyPair().getPrivate();
        Path keyPath = dir.resolve("rsa.pem");
        Files.writeString(keyPath, Fixtures.toPem("PRIVATE KEY", rsaKey.getEncoded()));
        InputError error = assertThrows(InputError.class, () -> Sign.KmsSigner.load(keyPath));
        assertEquals("sign.key_algorithm", error.key);
    }

    @Test
    void kmsSignerLoadRejectsAnUnparseableFileWithSignKeyUnreadable(@TempDir Path dir) throws IOException {
        Path keyPath = dir.resolve("garbage.pem");
        Files.writeString(keyPath, "not a pem file at all\n");
        InputError error = assertThrows(InputError.class, () -> Sign.KmsSigner.load(keyPath));
        assertEquals("sign.key_unreadable", error.key);
    }

    @Test
    void kmsSignerLoadRejectsAnEncryptedPkcs8KeyWithSignKeyUnreadable(@TempDir Path dir) throws Exception {
        // A real `EncryptedPrivateKeyInfo` DER shape -- SEQUENCE(AlgorithmIdentifier, OCTET STRING),
        // with no leading version INTEGER, unlike plain PKCS8 `PrivateKeyInfo` -- so every algorithm in
        // the PKCS8 attempt loop fails structurally, matching a real PBES2-encrypted key (confirmed
        // against a real `cryptography`-encrypted fixture in the prior session's throwaway program;
        // this DER shape is what makes it fail, not the specific cipher bytes).
        byte[] pbes2Oid = tlv(0x06, hex("2a864886f70d01050d")); // PBES2, 1.2.840.113549.1.5.13
        byte[] algorithmIdentifier = seq(pbes2Oid);
        byte[] encryptedData = tlv(0x04, new byte[] {1, 2, 3, 4, 5, 6, 7, 8});
        byte[] encryptedPrivateKeyInfo = seq(concat(algorithmIdentifier, encryptedData));
        Path keyPath = dir.resolve("encrypted.pem");
        Files.writeString(keyPath, Fixtures.toPem("ENCRYPTED PRIVATE KEY", encryptedPrivateKeyInfo));
        InputError error = assertThrows(InputError.class, () -> Sign.KmsSigner.load(keyPath));
        assertEquals("sign.key_unreadable", error.key);
    }

    @Test
    void kmsSignerLoadRejectsAnOpenSshFormatKeyWithSignKeyUnreadable(@TempDir Path dir) throws IOException {
        // Not ASN.1 at all -- the OpenSSH private-key body is base64 over a bespoke binary format
        // starting with the literal magic string, confirmed empirically the prior session against a
        // real `ssh-keygen -t ed25519` fixture.
        byte[] body = ("openssh-key-v1\0" + "arbitrary-non-asn1-body").getBytes(StandardCharsets.UTF_8);
        Path keyPath = dir.resolve("openssh.pem");
        Files.writeString(keyPath, Fixtures.toPem("OPENSSH PRIVATE KEY", body));
        InputError error = assertThrows(InputError.class, () -> Sign.KmsSigner.load(keyPath));
        assertEquals("sign.key_unreadable", error.key);
    }

    // --- Legacy PKCS1/SEC1 re-wrap (Java-only; round-1 critic correction, C2) ----------------------

    @Test
    void kmsSignerLoadRejectsALegacySec1EcPrivateKeyPemWithSignKeyAlgorithm(@TempDir Path dir) throws Exception {
        KeyPairGenerator generator = KeyPairGenerator.getInstance("EC");
        generator.initialize(new ECGenParameterSpec("secp256r1"));
        ECPrivateKey ecKey = (ECPrivateKey) generator.generateKeyPair().getPrivate();
        byte[] privateValue = fixedLength(ecKey.getS(), 32);
        // RFC 5915 `ECPrivateKey`: SEQUENCE(INTEGER 1, OCTET STRING <d>, [0] EXPLICIT ECParameters);
        // the `[0]` field's one child is the named-curve OID -- prime256v1, 1.2.840.10045.3.1.7 -- the
        // exact field `Sign.java`'s `extractSec1CurveOidTlv` reads. No `[1]` public-key field: the
        // production re-wrap logic never reads it.
        byte[] curveOid = tlv(0x06, hex("2a8648ce3d030107"));
        byte[] parameters = tlv(0xA0, curveOid);
        byte[] sec1Der = seq(concat(tlv(0x02, new byte[] {1}), tlv(0x04, privateValue), parameters));
        Path keyPath = dir.resolve("ec-sec1.pem");
        Files.writeString(keyPath, Fixtures.toPem("EC PRIVATE KEY", sec1Der));
        InputError error = assertThrows(InputError.class, () -> Sign.KmsSigner.load(keyPath));
        assertEquals("sign.key_algorithm", error.key);
    }

    @Test
    void kmsSignerLoadRejectsALegacyPkcs1RsaPrivateKeyPemWithSignKeyAlgorithm(@TempDir Path dir) throws Exception {
        KeyPairGenerator generator = KeyPairGenerator.getInstance("RSA");
        generator.initialize(2048);
        RSAPrivateCrtKey rsaKey = (RSAPrivateCrtKey) generator.generateKeyPair().getPrivate();
        // RFC 2313 PKCS1 `RSAPrivateKey`: SEQUENCE(version, modulus, publicExponent, privateExponent,
        // prime1, prime2, exponent1, exponent2, coefficient). `BigInteger#toByteArray()` already gives
        // the minimal two's-complement encoding DER's INTEGER wants.
        byte[] pkcs1Der = seq(concat(
                intTlv(BigInteger.ZERO),
                intTlv(rsaKey.getModulus()),
                intTlv(rsaKey.getPublicExponent()),
                intTlv(rsaKey.getPrivateExponent()),
                intTlv(rsaKey.getPrimeP()),
                intTlv(rsaKey.getPrimeQ()),
                intTlv(rsaKey.getPrimeExponentP()),
                intTlv(rsaKey.getPrimeExponentQ()),
                intTlv(rsaKey.getCrtCoefficient())));
        Path keyPath = dir.resolve("rsa-pkcs1.pem");
        Files.writeString(keyPath, Fixtures.toPem("RSA PRIVATE KEY", pkcs1Der));
        InputError error = assertThrows(InputError.class, () -> Sign.KmsSigner.load(keyPath));
        assertEquals("sign.key_algorithm", error.key);
    }

    private static byte[] fixedLength(BigInteger value, int length) {
        byte[] raw = value.toByteArray();
        byte[] out = new byte[length];
        if (raw.length >= length) {
            System.arraycopy(raw, raw.length - length, out, 0, length);
        } else {
            System.arraycopy(raw, 0, out, length - raw.length, raw.length);
        }
        return out;
    }

    // --- RFC 8032 §7.1 TEST 1 known-answer test ----------------------------------------------------

    /**
     * Pins the seeded-{@code SecureRandom}/{@code KeyPairGenerator} public-key-derivation trick against
     * RFC 8032's own TEST 1 vector, byte-for-byte -- not merely that the derived key round-trips
     * against itself. The vector was independently re-derived the prior session against Python's own
     * {@code cryptography} library before being pinned here (journal, {@code 18.26.md}).
     */
    @Test
    void derivesTheRfc8032Test1KnownAnswerVector(@TempDir Path dir) throws Exception {
        byte[] seed = hex("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60");
        byte[] expectedPublicKey = hex("d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a");
        byte[] expectedSignature = hex(
                "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901"
                        + "555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a1"
                        + "00b");
        // The fixed 17-byte PKCS8 `PrivateKeyInfo` prefix RFC 8410 defines for a raw Ed25519 seed:
        // SEQUENCE(INTEGER 0, SEQUENCE(OID 1.3.101.112), OCTET STRING(OCTET STRING(seed))) --
        // `30 2e 02 01 00 30 05 06 03 2b 65 70 04 22 04 20`, then the 32-byte seed.
        byte[] pkcs8Prefix = hex("302e020100300506032b657004220420");
        byte[] pkcs8 = concat(pkcs8Prefix, seed);
        Path keyPath = dir.resolve("rfc8032-test1.pem");
        Files.writeString(keyPath, Fixtures.toPem("PRIVATE KEY", pkcs8));

        Sign.KmsSigner signer = Sign.KmsSigner.load(keyPath);
        assertEquals(Base64.getEncoder().encodeToString(expectedPublicKey), signer.publicKeyB64());

        byte[] signature = signer.sign(new byte[0]);
        assertArrayEquals(expectedSignature, signature);
    }

    // --- signStatement / signSubjects -----------------------------------------------------------

    @Test
    void signStatementCanonicalizesSignsAndBase64EncodesWithNoCertField(@TempDir Path dir) throws Exception {
        EdPair pair = ed25519PemPair();
        Path keyPath = dir.resolve("key.pem");
        Files.writeString(keyPath, pair.privatePem());
        Sign.KmsSigner signer = Sign.KmsSigner.load(keyPath);

        ObjectNode statement = Json.nodes().objectNode();
        statement.put("b", 2);
        statement.put("a", 1);
        ObjectNode envelope = Sign.signStatement(statement, signer);

        assertEquals("application/vnd.in-toto+json", envelope.get("payloadType").asText());
        ArrayNode signatures = (ArrayNode) envelope.get("signatures");
        assertEquals(1, signatures.size());
        assertEquals(signer.keyid(), signatures.get(0).get("keyid").asText());
        assertFalse(signatures.get(0).has("cert"));

        byte[] payload = Base64.getDecoder().decode(envelope.get("payload").asText());
        assertEquals("{\"a\":1,\"b\":2}", new String(payload, StandardCharsets.UTF_8));
        byte[] sig = Base64.getDecoder().decode(signatures.get(0).get("sig").asText());
        assertTrue(Fixtures.edVerify(Sign.dssePae("application/vnd.in-toto+json", payload), pair.rawPublicKey(), sig));
    }

    @Test
    void signSubjectsHashesTheClaimBodyMinusSignaturesAndWhenPresentTheManifestsRawBytes(@TempDir Path dir)
            throws Exception {
        ObjectNode claim = Json.nodes().objectNode();
        claim.putObject("claimant").put("org", "acme");
        ArrayNode sigs = claim.putArray("signatures");
        sigs.addObject().put("keyid", "x").put("sig", "y");

        ArrayNode subjectsNoManifest = Sign.signSubjects(dir, claim);
        assertEquals(1, subjectsNoManifest.size());
        assertEquals("claim.json", subjectsNoManifest.get(0).get("name").asText());

        Files.writeString(dir.resolve("manifest.json"), "{\"k\":\"v\"}");
        ArrayNode subjectsWithManifest = Sign.signSubjects(dir, claim);
        assertEquals(2, subjectsWithManifest.size());
        assertEquals("manifest.json", subjectsWithManifest.get(1).get("name").asText());

        // The claim.json digest is identical whether or not `signatures` is present, since it is
        // always excluded from the hashed body.
        ObjectNode claimNoSignatures = Json.nodes().objectNode();
        claimNoSignatures.putObject("claimant").put("org", "acme");
        ArrayNode subjectsNoSig = Sign.signSubjects(dir, claimNoSignatures);
        assertEquals(
                subjectsWithManifest.get(0).get("digest").get("sha256").asText(),
                subjectsNoSig.get(0).get("digest").get("sha256").asText());
    }
}
