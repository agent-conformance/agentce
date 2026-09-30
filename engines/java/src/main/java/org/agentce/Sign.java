package org.agentce;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.GeneralSecurityException;
import java.security.KeyFactory;
import java.security.KeyPair;
import java.security.KeyPairGenerator;
import java.security.PrivateKey;
import java.security.SecureRandom;
import java.security.Signature;
import java.security.interfaces.EdECPrivateKey;
import java.security.spec.NamedParameterSpec;
import java.security.spec.PKCS8EncodedKeySpec;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Base64;
import java.util.HexFormat;
import java.util.List;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * {@code agentce sign}, the {@code kms} profile (SPEC §8.7, §9.1): the Ed25519/DSSE/in-toto signing
 * primitives, ported byte-for-byte from Python's {@code signing.py} ({@code _pae}, {@code keyid_for},
 * {@code sign_statement}, {@code _load_ed25519_private_key}) and {@code commands/__init__.py}'s
 * {@code _sign_subjects}. Mirrors {@code Readiness.java}'s shape as the compute seam {@code Cli.java}'s
 * {@code cmdSign} wires up, and {@code sign.ts}'s own Java-appropriate port. The {@code sigstore-*}
 * keyless profiles are accepted as flag values but always refuse offline (handled entirely in
 * {@code Cli.java}, per Python's own {@code _sign_signer}), so this module builds no certificate path.
 *
 * <p><b>No new dependency.</b> A private-key-only PKCS8 PEM's paired public key is derived using only
 * {@code java.security}: seed a {@link SecureRandom} whose {@code nextBytes} returns exactly the raw
 * 32-byte seed, then let {@code KeyPairGenerator.getInstance("Ed25519")} "generate" a key pair from
 * it — the resulting public key is the one paired with that seed (confirmed byte-identical to the
 * reference's own derivation this item, against RFC 8032 §7.1 TEST 1 and real generated keys, pinned
 * by this module's own known-answer test). The actual signing operation uses the JDK's own
 * {@code Signature.getInstance("Ed25519")} with the originally-parsed {@link PrivateKey} directly —
 * the re-derived pair exists only to reach the public key.
 */
public final class Sign {
    private Sign() {}

    /** DSSE payload type for an in-toto Statement (matches {@code signing.py}'s {@code INTOTO_PAYLOAD_TYPE}). */
    public static final String INTOTO_PAYLOAD_TYPE = "application/vnd.in-toto+json";
    /** in-toto Statement schema version (matches {@code signing.py}'s {@code INTOTO_STATEMENT_TYPE}). */
    public static final String INTOTO_STATEMENT_TYPE = "https://in-toto.io/Statement/v1";

    /** The fixed 12-byte RFC 8410 SPKI DER prefix every Ed25519 SPKI public key export carries before
     * its 32 raw key bytes -- confirmed byte-for-byte against independently generated keys; slicing it
     * off gives the exact bytes Python's {@code Encoding.Raw, PublicFormat.Raw} gives directly. */
    private static final byte[] ED25519_SPKI_PREFIX = hex("302a300506032b6570032100");

    /** DSSE Pre-Authentication Encoding: the exact bytes a signature covers, matching Python's
     * {@code _pae} (`b"DSSEv1 %d %s %d %s" % (len(pt), pt, len(payload), payload)`) -- built as
     * discrete byte segments, never a string-concatenation-then-encode round trip that could
     * double-encode a {@code payload} containing raw bytes outside valid UTF-8. */
    public static byte[] dssePae(String payloadType, byte[] payload) {
        byte[] typeBytes = payloadType.getBytes(StandardCharsets.UTF_8);
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        writeAscii(out, "DSSEv1 ");
        writeAscii(out, Integer.toString(typeBytes.length));
        writeAscii(out, " ");
        out.writeBytes(typeBytes);
        writeAscii(out, " ");
        writeAscii(out, Integer.toString(payload.length));
        writeAscii(out, " ");
        out.writeBytes(payload);
        return out.toByteArray();
    }

    private static void writeAscii(ByteArrayOutputStream out, String text) {
        out.writeBytes(text.getBytes(StandardCharsets.US_ASCII));
    }

    /** A content-addressed key id: {@code sha256:} over the raw public key (matches
     * {@code signing.py:118-125}'s {@code keyid_for}, over the raw 32-byte public key, never the
     * DER-wrapped form). */
    public static String keyidFor(byte[] publicKeyRaw) {
        return "sha256:" + Canonical.sha256Hex(publicKeyRaw);
    }

    /** An operator-held signing identity. The {@code kms} profile's only implementation,
     * {@link KmsSigner}, is the sole profile this port signs offline. */
    public interface Signer {
        byte[] sign(byte[] data);

        String keyid();
    }

    /** A key pinned in the trust root by content-addressed {@code keyid} (the {@code kms} profile,
     * SPEC §9.1). */
    public static final class KmsSigner implements Signer {
        private final PrivateKey privateKey;
        private final String keyid;
        private final String publicKeyB64;

        private KmsSigner(PrivateKey privateKey, byte[] publicKeyRaw) {
            this.privateKey = privateKey;
            this.keyid = keyidFor(publicKeyRaw);
            this.publicKeyB64 = Base64.getEncoder().encodeToString(publicKeyRaw);
        }

        /** The actual signing operation: the JDK's own one-shot Ed25519 API over the originally-parsed
         * key (matches Python's {@code private_key.sign(data)}). */
        @Override
        public byte[] sign(byte[] data) {
            try {
                Signature signature = Signature.getInstance("Ed25519");
                signature.initSign(privateKey);
                signature.update(data);
                return signature.sign();
            } catch (GeneralSecurityException e) {
                throw new IllegalStateException("Ed25519 signing failed", e);
            }
        }

        @Override
        public String keyid() {
            return keyid;
        }

        /** The base64 raw public key a claimant publishes for {@code --write-trust-root} (SPEC §9.1). */
        public String publicKeyB64() {
            return publicKeyB64;
        }

        /** Loads and validates an Ed25519 private key PEM (matches {@code _load_ed25519_private_key}'s
         * two-error-path split: an unparseable file, or a parseable key of the wrong algorithm, are
         * distinguished cases, not one). {@code pemPath} must already be a confirmed-existing file
         * (the caller's {@code requireFile} check runs first). */
        public static KmsSigner load(Path pemPath) {
            String pemText;
            try {
                pemText = Files.readString(pemPath, StandardCharsets.UTF_8);
            } catch (IOException e) {
                throw keyUnreadable();
            }
            String[] labelAndDer;
            try {
                labelAndDer = stripPemArmor(pemText);
            } catch (IllegalArgumentException e) {
                throw keyUnreadable();
            }
            String label = labelAndDer[0];
            byte[] der;
            try {
                der = Base64.getMimeDecoder().decode(labelAndDer[1]);
            } catch (IllegalArgumentException e) {
                throw keyUnreadable();
            }
            // Legacy PKCS1 (`RSA PRIVATE KEY`) / SEC1 (`EC PRIVATE KEY`) PEMs are not PKCS8 at all, so
            // the plain PKCS8 attempt loop below would fail structurally for every algorithm on such a
            // file -- re-wrap into a minimal synthetic PKCS8 `PrivateKeyInfo` first, so the loop
            // recognizes the real (non-Ed25519) algorithm rather than wrongly reporting `key_unreadable`
            // for a key that parses fine, just not as Ed25519.
            if ("RSA PRIVATE KEY".equals(label)) {
                der = rewrapPkcs1AsPkcs8(der);
            } else if ("EC PRIVATE KEY".equals(label)) {
                byte[] rewrapped = rewrapSec1AsPkcs8(der);
                if (rewrapped != null) {
                    der = rewrapped;
                }
            }
            PrivateKey found = null;
            boolean isEd25519 = false;
            for (String alg : PKCS8_ALGORITHMS) {
                try {
                    KeyFactory keyFactory = KeyFactory.getInstance(alg);
                    found = keyFactory.generatePrivate(new PKCS8EncodedKeySpec(der));
                    isEd25519 = "Ed25519".equals(alg);
                    break;
                } catch (GeneralSecurityException | IllegalArgumentException ignored) {
                    // Try the next algorithm; every algorithm failing is `key_unreadable`, below.
                }
            }
            if (found == null) {
                throw keyUnreadable();
            }
            if (!isEd25519) {
                throw keyAlgorithm();
            }
            byte[] seed =
                    ((EdECPrivateKey) found)
                            .getBytes()
                            .orElseThrow(() -> new IllegalStateException("Ed25519 key carries no raw seed"));
            return new KmsSigner(found, derivePublicKey(seed));
        }
    }

    /** Every algorithm {@code KeyFactory} is tried against for a PKCS8-shaped key, Ed25519 first
     * (matches the reference's single {@code load_pem_private_key} call, which returns some typed key
     * object for any recognized PKCS8 algorithm). */
    private static final List<String> PKCS8_ALGORITHMS =
            List.of("Ed25519", "RSA", "EC", "DSA", "Ed448", "X25519", "X448", "DiffieHellman");

    private static InputError keyUnreadable() {
        return new InputError(
                "sign.key_unreadable",
                "the signing key file could not be parsed as an unencrypted PEM private key.",
                "supply an unencrypted Ed25519 private key PEM (`openssl genpkey -algorithm ed25519 "
                        + "-out key.pem`, or `agentce catalog sign --new-key <path>`).");
    }

    private static InputError keyAlgorithm() {
        return new InputError(
                "sign.key_algorithm",
                "the signing key is not an Ed25519 private key.",
                "supply an Ed25519 key (the algorithm the engine signs with, SPEC §8.7).");
    }

    /** Strip PEM armor generically ({@code -----BEGIN <label>-----}/{@code -----END <label>-----}, any
     * label a private-key PEM can carry -- {@code PRIVATE KEY}, {@code ENCRYPTED PRIVATE KEY},
     * {@code RSA PRIVATE KEY}, {@code EC PRIVATE KEY}, {@code OPENSSH PRIVATE KEY}, ...), returning
     * {@code {label, base64Body}}. A label mismatch is never itself the error signal -- the body still
     * gets base64-decoded and handed to the next step, which is where an {@code ENCRYPTED PRIVATE KEY}
     * or {@code OPENSSH PRIVATE KEY} body correctly fails. */
    private static final Pattern PEM_ARMOR =
            Pattern.compile("-----BEGIN ([^-]+)-----(.*?)-----END \\1-----", Pattern.DOTALL);

    private static String[] stripPemArmor(String text) {
        Matcher matcher = PEM_ARMOR.matcher(text);
        if (!matcher.find()) {
            throw new IllegalArgumentException("no PEM block found");
        }
        return new String[] {matcher.group(1).trim(), matcher.group(2)};
    }

    /** Derives the Ed25519 public key paired with {@code seed} (the raw 32-byte private key value)
     * using only {@code java.security} -- no BouncyCastle. Confirmed byte-identical to the reference's
     * own derivation, against RFC 8032 §7.1 TEST 1 and real generated keys (see
     * {@code SignTest#derivesTheRfc8032Test1KnownAnswerVector}). */
    private static byte[] derivePublicKey(byte[] seed) {
        try {
            SecureRandom seeded = new FixedSecureRandom(seed);
            KeyPairGenerator generator = KeyPairGenerator.getInstance("Ed25519");
            generator.initialize(NamedParameterSpec.ED25519, seeded);
            KeyPair pair = generator.generateKeyPair();
            byte[] spki = pair.getPublic().getEncoded();
            return Arrays.copyOfRange(spki, spki.length - 32, spki.length);
        } catch (GeneralSecurityException e) {
            throw new IllegalStateException("Ed25519 public-key derivation failed", e);
        }
    }

    /** A {@link SecureRandom} whose {@link #nextBytes} always returns the fixed bytes it was
     * constructed with -- the seeded-random trick {@link #derivePublicKey} relies on. */
    private static final class FixedSecureRandom extends SecureRandom {
        private static final long serialVersionUID = 1L;
        private final byte[] fixed;

        FixedSecureRandom(byte[] fixed) {
            super();
            this.fixed = fixed.clone();
        }

        @Override
        public void nextBytes(byte[] bytes) {
            System.arraycopy(fixed, 0, bytes, 0, bytes.length);
        }
    }

    // --- Minimal DER TLV encoding/decoding, only what the legacy PKCS1/SEC1 re-wrap needs. ---------

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

    private record Tlv(int tag, byte[] content, int totalLen) {}

    private static Tlv readTlv(byte[] data, int offset) {
        int tag = data[offset] & 0xFF;
        int lenByte = data[offset + 1] & 0xFF;
        int length;
        int contentStart;
        if ((lenByte & 0x80) == 0) {
            length = lenByte;
            contentStart = offset + 2;
        } else {
            int lenOfLen = lenByte & 0x7F;
            length = 0;
            for (int i = 0; i < lenOfLen; i++) {
                length = (length << 8) | (data[offset + 2 + i] & 0xFF);
            }
            contentStart = offset + 2 + lenOfLen;
        }
        byte[] content = Arrays.copyOfRange(data, contentStart, contentStart + length);
        return new Tlv(tag, content, (contentStart - offset) + length);
    }

    /** The RFC 5915 SEC1 {@code ECPrivateKey}'s optional {@code parameters [0] EXPLICIT ECParameters}
     * field, when present, holds exactly the named-curve OID as its one child -- returns that child's
     * full TLV bytes (tag, length, and content), or {@code null} when the field is absent (an EC PEM
     * with no embedded curve, out of this item's scope; falls through to {@code sign.key_unreadable}
     * like any other unrecognizable body). */
    private static byte[] extractSec1CurveOidTlv(byte[] sec1Der) {
        Tlv outer = readTlv(sec1Der, 0);
        byte[] content = outer.content();
        int pos = 0;
        while (pos < content.length) {
            Tlv element = readTlv(content, pos);
            if (element.tag() == 0xA0) {
                return element.content();
            }
            pos += element.totalLen();
        }
        return null;
    }

    /** id-ecPublicKey, OID 1.2.840.10045.2.1. */
    private static final byte[] EC_OID_TLV = tlv(0x06, hex("2a8648ce3d0201"));
    /** rsaEncryption, OID 1.2.840.113549.1.1.1. */
    private static final byte[] RSA_OID_TLV = tlv(0x06, hex("2a864886f70d010101"));

    /** Re-wraps a SEC1 {@code EC PRIVATE KEY} body as a minimal synthetic PKCS8 {@code PrivateKeyInfo}
     * (round-1 critic correction, C2): {@code SEQUENCE(INTEGER 0, AlgorithmIdentifier(id-ecPublicKey,
     * <curve OID read from the SEC1 structure's own [0] field>), OCTET STRING(sec1Der))}. This never
     * needs to produce a usable key -- only enough for {@code KeyFactory.getInstance("EC")} to accept
     * it and report a real, recognized (non-Ed25519) key, classifying it as {@code sign.key_algorithm}.
     * Returns {@code null} when the SEC1 body carries no embedded curve OID. */
    private static byte[] rewrapSec1AsPkcs8(byte[] sec1Der) {
        byte[] curveOidTlv;
        try {
            curveOidTlv = extractSec1CurveOidTlv(sec1Der);
        } catch (RuntimeException e) {
            return null;
        }
        if (curveOidTlv == null) {
            return null;
        }
        byte[] algorithmIdentifier = seq(concat(EC_OID_TLV, curveOidTlv));
        byte[] version = tlv(0x02, new byte[] {0});
        byte[] privateKeyOctet = tlv(0x04, sec1Der);
        return seq(concat(version, algorithmIdentifier, privateKeyOctet));
    }

    /** Re-wraps a PKCS1 {@code RSA PRIVATE KEY} body as a minimal synthetic PKCS8 {@code PrivateKeyInfo}
     * (round-1 critic correction, C2): {@code SEQUENCE(INTEGER 0, AlgorithmIdentifier(rsaEncryption,
     * NULL), OCTET STRING(pkcs1Der))} -- the standard convention for an {@code rsaEncryption}
     * {@code AlgorithmIdentifier} carries an explicit {@code NULL} parameters field. */
    private static byte[] rewrapPkcs1AsPkcs8(byte[] pkcs1Der) {
        byte[] algorithmIdentifier = seq(concat(RSA_OID_TLV, tlv(0x05, new byte[0])));
        byte[] version = tlv(0x02, new byte[] {0});
        byte[] privateKeyOctet = tlv(0x04, pkcs1Der);
        return seq(concat(version, algorithmIdentifier, privateKeyOctet));
    }

    // --- DSSE envelope / subjects, shared with Cli.java's cmdSign. -----------------------------------

    /** Produces a DSSE envelope over {@code statement} (canonical bytes) signed by {@code signer} --
     * matches {@code sign_statement} exactly. Returned as an {@link ObjectNode} with
     * {@code payloadType}/{@code payload}/{@code signatures}, ready to merge with {@code role}/
     * {@code profile} and write. A {@code KmsSigner}'s envelope never carries a {@code cert} field
     * (only a keyless signer's would, and this port never builds one). */
    public static ObjectNode signStatement(JsonNode statement, Signer signer) {
        byte[] payload = Canonical.canonicalize(statement);
        byte[] signature = signer.sign(dssePae(INTOTO_PAYLOAD_TYPE, payload));
        ObjectNode envelope = Json.nodes().objectNode();
        envelope.put("payloadType", INTOTO_PAYLOAD_TYPE);
        envelope.put("payload", Base64.getEncoder().encodeToString(payload));
        ArrayNode signatures = envelope.putArray("signatures");
        ObjectNode entry = signatures.addObject();
        entry.put("keyid", signer.keyid());
        entry.put("sig", Base64.getEncoder().encodeToString(signature));
        return envelope;
    }

    /** The in-toto subjects a claim signature covers: the claim body and the manifest, by digest --
     * matches {@code _sign_subjects} (`commands/__init__.py:2577-2596`) exactly. The claim body is
     * every top-level key except {@code signatures} (built by omission, never a JSON round trip that
     * could reorder or coerce values); the manifest, when present, is hashed over its raw file bytes
     * (never re-encoded/normalized). */
    public static ArrayNode signSubjects(Path reportDir, JsonNode claim) {
        ObjectNode body = Json.nodes().objectNode();
        var fields = claim.fields();
        while (fields.hasNext()) {
            var entry = fields.next();
            if (!"signatures".equals(entry.getKey())) {
                body.set(entry.getKey(), entry.getValue());
            }
        }
        ArrayNode subjects = Json.nodes().arrayNode();
        ObjectNode claimSubject = subjects.addObject();
        claimSubject.put("name", "claim.json");
        claimSubject.putObject("digest").put("sha256", Canonical.sha256Hex(body));
        Path manifestPath = reportDir.resolve("manifest.json");
        if (Files.isRegularFile(manifestPath)) {
            try {
                byte[] manifestBytes = Files.readAllBytes(manifestPath);
                ObjectNode manifestSubject = subjects.addObject();
                manifestSubject.put("name", "manifest.json");
                manifestSubject.putObject("digest").put("sha256", Canonical.sha256Hex(manifestBytes));
            } catch (IOException e) {
                throw new IllegalStateException("cannot read " + manifestPath + ": " + e.getMessage(), e);
            }
        }
        return subjects;
    }
}
