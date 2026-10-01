package org.agentce;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.io.IOException;
import java.io.InputStream;
import java.nio.ByteBuffer;
import java.nio.CharBuffer;
import java.nio.charset.CharacterCodingException;
import java.nio.charset.CodingErrorAction;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.GeneralSecurityException;
import java.security.KeyFactory;
import java.security.PublicKey;
import java.security.Signature;
import java.security.SignatureException;
import java.security.spec.X509EncodedKeySpec;
import java.util.ArrayList;
import java.util.Base64;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * {@code agentce verify}, the offline DSSE/certificate verification primitives (SPEC §8.7, §9.1):
 * ported byte-for-byte from Python's {@code signing.py} ({@code TrustRoot}, {@code
 * verify_certificate}, {@code verify_envelope}, {@code verify_catalog_directory}) and {@code
 * commands/__init__.py}'s {@code _verify_catalog}/{@code _verify_release}, as actually rewritten by
 * the Python-side fix for the item's own named defect (every exception this module raises corresponds
 * to a path Python's rewritten source raises the same way; see {@code signing.py} and {@code
 * commands/__init__.py} for the reference). {@code Sign.java} only ever signs; this module is this
 * engine's first verify primitive, so it builds its own {@code TrustRoot}/certificate machinery rather
 * than reusing anything from {@code Sign.java} beyond {@code dssePae}/{@code keyidFor}.
 */
public final class Verify {
    private Verify() {}

    /** The detached signature a signed catalog (or corpus) directory carries (SPEC §8.7). */
    public static final String CATALOG_SIGNATURE_NAME = "catalog.sig.json";

    /** The exact unsigned sentence {@code verify_catalog_directory} raises when no signature is present. */
    private static final String UNSIGNED_SENTENCE =
            "unsigned: catalog.sig.json is absent, so there is no signature to verify (SPEC §8.7).";

    /** The fixed 12-byte RFC 8410 SPKI DER prefix every Ed25519 SPKI public key export carries before
     * its 32 raw key bytes -- the same constant {@code Sign.java} slices off; here it is prepended to
     * go the other direction, from raw bytes to a {@link PublicKey} {@link Signature} can use. */
    private static final byte[] ED25519_SPKI_PREFIX = HexFormat.of().parseHex("302a300506032b6570032100");

    private static byte[] concat(byte[] a, byte[] b) {
        byte[] out = new byte[a.length + b.length];
        System.arraycopy(a, 0, out, 0, a.length);
        System.arraycopy(b, 0, out, a.length, b.length);
        return out;
    }

    /** Reconstructs an Ed25519 public key from its raw 32 bytes (the Java-side inverse of {@code
     * Sign.java}'s own DER-slicing trick) -- no new dependency, {@code java.security} only. */
    public static PublicKey publicKeyFromRaw(byte[] raw) {
        try {
            KeyFactory factory = KeyFactory.getInstance("Ed25519");
            return factory.generatePublic(new X509EncodedKeySpec(concat(ED25519_SPKI_PREFIX, raw)));
        } catch (GeneralSecurityException e) {
            throw new IllegalArgumentException("invalid Ed25519 public key", e);
        }
    }

    /** Verifies an Ed25519 signature; never throws on a malformed/wrong-length signature -- {@link
     * Signature#verify} throws {@link SignatureException} in that case (confirmed real probe: lengths
     * 0/3/65 all throw "signature length invalid"), where Node's {@code crypto.verify} and Python's
     * {@code cryptography} both return {@code false}/raise {@code InvalidSignature} with empty text.
     * Folding that case into a plain {@code false} here, in the one shared verification primitive,
     * keeps every caller (the certificate check and the per-signature-entry loop alike) from leaking a
     * Java-only "signature length invalid" message. */
    private static boolean verifyEd25519(byte[] data, PublicKey key, byte[] sig) {
        try {
            Signature signature = Signature.getInstance("Ed25519");
            signature.initVerify(key);
            signature.update(data);
            return signature.verify(sig);
        } catch (SignatureException e) {
            return false;
        } catch (GeneralSecurityException e) {
            throw new IllegalStateException("Ed25519 verification failed", e);
        }
    }

    private static String textOrNull(JsonNode node) {
        return node != null && node.isTextual() ? node.textValue() : null;
    }

    /** {@code java.util.Base64.getDecoder()} throws {@link IllegalArgumentException} on a non-alphabet
     * character or incorrect padding -- the same strict treatment {@code verify.ts}'s {@code
     * b64dStrict} gives (diverges from both CPython's padding-specific errors and Node's silent-drop,
     * a disclosed, shape-only divergence per the contract). A missing/non-string field decodes the
     * same way a malformed one does: both are "not a usable base64 string" to every caller here. */
    private static byte[] b64dStrict(JsonNode node) {
        String text = textOrNull(node);
        if (text == null) {
            throw new IllegalArgumentException("not a base64 string");
        }
        return Base64.getDecoder().decode(text);
    }

    /** A base64-decoded key entry: the raw public key bytes and (for a {@code keys} entry) its identity. */
    public record KeyEntry(byte[] publicKeyRaw, String identity) {}

    private record AuthorityEntry(byte[] publicKeyRaw) {}

    /** Verifies a keyless certificate against the pinned authorities; returns the leaf key and identity
     * it binds. Every way a certificate can be malformed -- not an object, an unknown issuer aside, a
     * missing/non-base64 {@code signature}, a signature that does not verify, a missing {@code
     * public_key}/{@code identity} -- collapses to the one {@code "certificate signature does not
     * verify"} message, mirroring {@code verify_certificate}'s own collapse exactly. */
    private static KeyEntry verifyCertificate(JsonNode cert, Map<String, AuthorityEntry> authorities) {
        if (cert == null || !cert.isObject()) {
            throw new IllegalArgumentException("certificate signature does not verify");
        }
        JsonNode issuerNode = cert.get("issuer");
        String issuer = textOrNull(issuerNode);
        AuthorityEntry ca = issuer != null ? authorities.get(issuer) : null;
        if (ca == null) {
            throw new IllegalArgumentException("unknown certificate issuer " + Readiness.pyRepr(issuerNode));
        }
        try {
            ObjectNode body = Json.nodes().objectNode();
            var fields = cert.fields();
            while (fields.hasNext()) {
                var entry = fields.next();
                if (!"signature".equals(entry.getKey())) {
                    body.set(entry.getKey(), entry.getValue());
                }
            }
            byte[] sig = b64dStrict(cert.get("signature"));
            boolean ok = verifyEd25519(Canonical.canonicalize(body), publicKeyFromRaw(ca.publicKeyRaw()), sig);
            if (!ok) {
                throw new IllegalArgumentException("certificate signature invalid");
            }
            byte[] leafRaw = b64dStrict(cert.get("public_key"));
            String identity = textOrNull(cert.get("identity"));
            if (identity == null) {
                throw new IllegalArgumentException("missing certificate identity");
            }
            return new KeyEntry(leafRaw, identity);
        } catch (RuntimeException e) {
            throw new IllegalArgumentException("certificate signature does not verify");
        }
    }

    /** The offline material a verifier trusts: pinned KMS keys and keyless certificate authorities
     * (mirrors {@code signing.TrustRoot} exactly). */
    public static final class TrustRoot {
        private final Map<String, KeyEntry> keys;
        private final Map<String, AuthorityEntry> authorities;

        private TrustRoot(Map<String, KeyEntry> keys, Map<String, AuthorityEntry> authorities) {
            this.keys = keys;
            this.authorities = authorities;
        }

        /** Loads a trust root from its JSON shape. Every {@code keys} entry's declared id must equal
         * {@code keyidFor} of the key it maps to -- a forged/corrupted entry (the real signer's own
         * keyid mapped to an attacker's key) is refused, not silently accepted (matches {@code
         * signing.py:295-297}). */
        public static TrustRoot fromDict(JsonNode data) {
            Map<String, KeyEntry> keys = new LinkedHashMap<>();
            JsonNode keysIn = data != null && data.isObject() ? data.path("keys") : Json.nodes().objectNode();
            var keyIt = keysIn.fields();
            while (keyIt.hasNext()) {
                var entry = keyIt.next();
                String keyid = entry.getKey();
                JsonNode value = entry.getValue();
                byte[] raw = b64dStrict(value.get("public_key"));
                if (!Sign.keyidFor(raw).equals(keyid)) {
                    throw new IllegalArgumentException(
                            "trust root entry " + Readiness.pyRepr(Json.nodes().textNode(keyid))
                                    + " does not match its own key");
                }
                String identity = textOrNull(value.get("identity"));
                keys.put(keyid, new KeyEntry(raw, identity != null ? identity : keyid));
            }
            Map<String, AuthorityEntry> authorities = new LinkedHashMap<>();
            JsonNode authsIn = data != null && data.isObject()
                    ? data.path("certificate_authorities")
                    : Json.nodes().objectNode();
            var authIt = authsIn.fields();
            while (authIt.hasNext()) {
                var entry = authIt.next();
                authorities.put(entry.getKey(), new AuthorityEntry(b64dStrict(entry.getValue().get("public_key"))));
            }
            return new TrustRoot(keys, authorities);
        }

        /** Resolves a DSSE signature entry to the key/identity that must verify it ({@code cert}
         * checked before {@code keyid}, matching {@code signing.py:324-329}'s own order). */
        KeyEntry resolve(JsonNode signature) {
            JsonNode cert = signature.get("cert");
            if (cert != null && !cert.isNull()) {
                return verifyCertificate(cert, authorities);
            }
            JsonNode keyidNode = signature.get("keyid");
            String keyid = textOrNull(keyidNode);
            KeyEntry found = keyid != null ? keys.get(keyid) : null;
            if (found == null) {
                throw new IllegalArgumentException("no trusted key for keyid " + Readiness.pyRepr(keyidNode));
            }
            return found;
        }
    }

    /** The path to the trust root vendored in the engine package (a byte-for-byte copy of {@code
     * engines/python/agentce/data/trust/dev-root.json}, pinned by {@code VerifyTest}'s sync test). */
    private static final String VENDORED_TRUST_RESOURCE = "/trust/dev-root.json";

    /** Reads and parses a trust root file ({@code --trust-root}-shaped JSON) at {@code path}. */
    public static TrustRoot loadTrustRoot(Path path) {
        return TrustRoot.fromDict(Json.parseFile(path));
    }

    /** Loads the trust root vendored in the engine package. */
    public static TrustRoot vendoredTrust() {
        try (InputStream in = Verify.class.getResourceAsStream(VENDORED_TRUST_RESOURCE)) {
            if (in == null) {
                throw new IllegalStateException("vendored trust root not on classpath");
            }
            return TrustRoot.fromDict(Json.parse(new String(in.readAllBytes(), StandardCharsets.UTF_8)));
        } catch (IOException e) {
            throw new IllegalStateException("cannot read vendored trust root", e);
        }
    }

    /** The result of a successful DSSE envelope verification. */
    public record VerifiedEnvelope(byte[] payload, String identity, String keyid, boolean keyless) {}

    /** Verifies a DSSE envelope against {@code trust}; throws on any failure. Shape is validated
     * explicitly, one ordered check at a time -- mirrors {@code verify_envelope} exactly as rewritten
     * by the Python-side fix, so the two are a real mirror, not an analogy (see {@code
     * signing.py:365-417}). */
    public static VerifiedEnvelope verifyEnvelope(JsonNode envelope, TrustRoot trust) {
        if (envelope == null || !envelope.isObject()) {
            throw new IllegalArgumentException("malformed DSSE envelope");
        }
        JsonNode payloadTypeNode = envelope.get("payloadType");
        if (payloadTypeNode == null || !payloadTypeNode.isTextual()) {
            throw new IllegalArgumentException("malformed DSSE envelope");
        }
        JsonNode rawPayloadNode = envelope.get("payload");
        if (rawPayloadNode == null || !rawPayloadNode.isTextual()) {
            throw new IllegalArgumentException("malformed DSSE envelope");
        }
        byte[] payload;
        try {
            payload = Base64.getDecoder().decode(rawPayloadNode.textValue());
        } catch (IllegalArgumentException e) {
            throw new IllegalArgumentException("malformed DSSE envelope");
        }
        JsonNode signatures = envelope.get("signatures");
        if (signatures == null || !signatures.isArray()) {
            throw new IllegalArgumentException("malformed DSSE envelope");
        }
        if (signatures.isEmpty()) {
            throw new IllegalArgumentException("DSSE envelope carries no signatures");
        }
        byte[] pae = Sign.dssePae(payloadTypeNode.textValue(), payload);
        String lastError = "";
        for (JsonNode entry : signatures) {
            try {
                if (!entry.isObject()) {
                    // A non-object entry has no keyid to resolve; route it through the same
                    // missing-keyid message `TrustRoot.resolve` gives for an absent `keyid`, not a
                    // second ad-hoc string.
                    throw new IllegalArgumentException("no trusted key for keyid " + Readiness.pyRepr(null));
                }
                KeyEntry resolved = trust.resolve(entry);
                JsonNode sigNode = entry.get("sig");
                if (sigNode == null || !sigNode.isTextual()) {
                    // Mirrors Python's `KeyError` repr for the same missing-key access.
                    throw new IllegalArgumentException("'sig'");
                }
                byte[] sigBytes = Base64.getDecoder().decode(sigNode.textValue());
                boolean ok = verifyEd25519(pae, publicKeyFromRaw(resolved.publicKeyRaw()), sigBytes);
                if (!ok) {
                    // An empty message, matching `cryptography`'s `InvalidSignature` -- `str()` of
                    // which is empty -- so the aggregate message below renders with nothing after the
                    // colon-space.
                    throw new IllegalArgumentException("");
                }
                String keyid = textOrNull(entry.get("keyid"));
                return new VerifiedEnvelope(payload, resolved.identity(), keyid, entry.has("cert"));
            } catch (RuntimeException e) {
                lastError = e.getMessage() != null ? e.getMessage() : "";
            }
        }
        throw new IllegalArgumentException("no signature verified against the trust root: " + lastError);
    }

    /** Mirrors {@code signing.statement_subject_digest}: the {@code sha256:} digest of an in-toto
     * Statement's single subject. A statement with no {@code subject} key renders the same {@code
     * "'subject'"} {@code KeyError}-repr text Python gives (pinned); every other malformed shape is
     * disclosed as shape-only. */
    private static String statementSubjectDigest(JsonNode statement) {
        if (statement == null || !statement.isObject() || !statement.has("subject")) {
            throw new IllegalArgumentException("'subject'");
        }
        JsonNode subject = statement.get("subject");
        if (!subject.isArray() || subject.isEmpty()) {
            throw new IllegalArgumentException("list index out of range");
        }
        JsonNode first = subject.get(0);
        JsonNode digest = first != null && first.isObject() ? first.get("digest") : null;
        JsonNode sha256 = digest != null && digest.isObject() ? digest.get("sha256") : null;
        if (sha256 == null || !sha256.isTextual()) {
            throw new IllegalArgumentException("'sha256'");
        }
        return "sha256:" + sha256.textValue();
    }

    /** Reads a JSON file with a fatal UTF-8 decode (matches Python's {@code read_text("utf-8")}
     * refusing invalid byte sequences, unlike {@code Json.parseFile}'s lenient {@code
     * Files.readString}); throws on a decode or parse failure, never on a valid-JSON-but-wrong-shape
     * value (the caller decides what "wrong shape" means). */
    private static JsonNode readJsonFileStrict(Path path) {
        byte[] bytes;
        try {
            bytes = Files.readAllBytes(path);
        } catch (IOException e) {
            throw new IllegalArgumentException(e.getMessage(), e);
        }
        String text;
        try {
            text = StandardCharsets.UTF_8
                    .newDecoder()
                    .onMalformedInput(CodingErrorAction.REPORT)
                    .onUnmappableCharacter(CodingErrorAction.REPORT)
                    .decode(ByteBuffer.wrap(bytes))
                    .toString();
        } catch (CharacterCodingException e) {
            throw new IllegalArgumentException("invalid UTF-8: " + e.getMessage(), e);
        }
        return Json.parse(text);
    }

    private static CharBuffer decodeStrict(byte[] bytes) throws CharacterCodingException {
        return StandardCharsets.UTF_8
                .newDecoder()
                .onMalformedInput(CodingErrorAction.REPORT)
                .onUnmappableCharacter(CodingErrorAction.REPORT)
                .decode(ByteBuffer.wrap(bytes));
    }

    /** Verifies a catalog directory's detached signature against {@code trust} (mirrors {@code
     * _verify_catalog}/{@code verify_catalog_directory}, {@code signing.py:391-426}, {@code
     * commands/__init__.py:321-352}). */
    public static ObjectNode verifyCatalog(Path dir, TrustRoot trust) {
        String digest = Catalog.digestTree(dir, Set.of(CATALOG_SIGNATURE_NAME));
        Path sigPath = dir.resolve(CATALOG_SIGNATURE_NAME);
        if (!Files.isRegularFile(sigPath)) {
            return catalogSoftFail(digest, UNSIGNED_SENTENCE);
        }
        JsonNode envelope;
        try {
            envelope = readJsonFileStrict(sigPath);
        } catch (RuntimeException e) {
            return catalogSoftFail(digest, CATALOG_SIGNATURE_NAME + " is not readable JSON: " + e.getMessage());
        }
        VerifiedEnvelope verified;
        try {
            verified = verifyEnvelope(envelope, trust);
        } catch (RuntimeException e) {
            return catalogSoftFail(digest, e.getMessage());
        }
        String signedDigest;
        try {
            JsonNode statement = Json.parse(decodeStrict(verified.payload()).toString());
            signedDigest = statementSubjectDigest(statement);
        } catch (RuntimeException | CharacterCodingException e) {
            return catalogSoftFail(digest, "the signed statement carries no catalog digest: " + e.getMessage());
        }
        if (!signedDigest.equals(digest)) {
            return catalogSoftFail(
                digest, "the signature covers a different catalog digest than the directory content");
        }
        ObjectNode out = Json.nodes().objectNode();
        out.put("verified", true);
        out.put("digest", digest);
        out.put("signer", verified.identity());
        putNullableKeyid(out, verified);
        out.put("keyless", verified.keyless());
        return out;
    }

    /** The shared soft-fail shape for a catalog that cannot be verified: mirrors {@code
     * _verify_catalog}'s own three-field shape; every early-return soft-fail in {@link
     * #verifyCatalog} uses this shape. */
    private static ObjectNode catalogSoftFail(String digest, String reason) {
        ObjectNode out = Json.nodes().objectNode();
        out.put("verified", false);
        out.put("digest", digest);
        out.put("reason", reason);
        return out;
    }

    /** The shared soft-fail shape for a release that cannot be verified at all (no {@code
     * manifest_digest}/{@code signers} field): mirrors {@code _verify_release_soft_fail}'s own
     * three-field shape exactly -- every early-return soft-fail in {@link #verifyRelease}, including
     * the directory-bundle branch's own JSON-parse failures, uses this shape. */
    private static ObjectNode releaseSoftFail(Path releasePath, String reason) {
        ObjectNode out = Json.nodes().objectNode();
        out.put("release", releasePath.toString());
        out.put("verified", false);
        out.put("reason", reason);
        return out;
    }

    /** Sets {@code out}'s {@code "keyid"} field to {@code verified.keyid()}, or JSON {@code null}
     * when the signer had none -- shared by {@link #verifyCatalog} and {@link #verifyRelease}'s
     * single-file success path, which both carry the same optional field. */
    private static void putNullableKeyid(ObjectNode out, VerifiedEnvelope verified) {
        if (verified.keyid() != null) {
            out.put("keyid", verified.keyid());
        } else {
            out.putNull("keyid");
        }
    }

    /** Verifies a release bundle (or a single DSSE envelope) offline against {@code trust} -- the
     * item's core fix target, built correct from the start: every branch uses the soft-fail shape, and
     * every new JSON-parse-failure path this function adds uses a fixed, engine-neutral reason text
     * (mirrors {@code _verify_release}, {@code commands/__init__.py:758-875}). */
    public static ObjectNode verifyRelease(Path releasePath, TrustRoot trust) {
        if (Files.isRegularFile(releasePath)) {
            JsonNode envelope;
            try {
                envelope = readJsonFileStrict(releasePath);
            } catch (RuntimeException e) {
                return releaseSoftFail(releasePath, "release envelope is not readable JSON");
            }
            try {
                VerifiedEnvelope verified = verifyEnvelope(envelope, trust);
                ObjectNode out = Json.nodes().objectNode();
                out.put("release", releasePath.toString());
                out.put("verified", true);
                out.put("signer", verified.identity());
                putNullableKeyid(out, verified);
                out.put("keyless", verified.keyless());
                return out;
            } catch (RuntimeException e) {
                return releaseSoftFail(releasePath, e.getMessage());
            }
        }

        Path manifestPath = releasePath.resolve("release-manifest.json");
        Path signaturesPath = releasePath.resolve("signatures.json");
        if (!Files.isRegularFile(manifestPath) || !Files.isRegularFile(signaturesPath)) {
            throw new InputError(
                    "input.release_bundle",
                    releasePath + " is not a release bundle (release-manifest.json/signatures.json).",
                    "pass the --out directory produced by the release tooling.");
        }

        JsonNode manifest;
        try {
            manifest = readJsonFileStrict(manifestPath);
        } catch (RuntimeException e) {
            return releaseSoftFail(releasePath, "release manifest is not readable JSON");
        }
        if (!manifest.isObject()) {
            return releaseSoftFail(releasePath, "release manifest is not readable JSON");
        }

        String manifestDigest = "sha256:" + Canonical.sha256Hex(manifest);
        List<String> problems = new ArrayList<>();
        JsonNode artifacts =
                manifest.has("artifacts") && manifest.get("artifacts").isArray()
                        ? manifest.get("artifacts")
                        : Json.nodes().arrayNode();
        for (JsonNode artifact : artifacts) {
            JsonNode nameNode = artifact != null && artifact.isObject() ? artifact.get("name") : null;
            if (nameNode == null || !nameNode.isTextual()) {
                problems.add("release manifest has an artifact entry with no name");
                continue;
            }
            String name = nameNode.textValue();
            Path artifactFile = releasePath.resolve(name);
            if (!Files.isRegularFile(artifactFile)) {
                problems.add("missing artifact " + name);
                continue;
            }
            String actual;
            try {
                actual = "sha256:" + Canonical.sha256Hex(Files.readAllBytes(artifactFile));
            } catch (IOException e) {
                throw new IllegalStateException("cannot read " + artifactFile + ": " + e.getMessage(), e);
            }
            JsonNode expected = artifact.get("digest");
            if (expected == null || !actual.equals(expected.asText())) {
                problems.add("digest mismatch for " + name);
            }
        }

        JsonNode signatureEntries;
        try {
            signatureEntries = readJsonFileStrict(signaturesPath);
        } catch (RuntimeException e) {
            return releaseSoftFail(releasePath, "release signatures are not readable JSON");
        }
        if (!signatureEntries.isArray()) {
            return releaseSoftFail(releasePath, "release signatures are not readable JSON");
        }

        List<ObjectNode> signers = new ArrayList<>();
        for (JsonNode raw : signatureEntries) {
            if (raw == null || !raw.isObject()) {
                problems.add("signature (None): signature entry is not an object");
                continue;
            }
            JsonNode profile = raw.get("profile");
            try {
                if (!raw.has("envelope")) {
                    throw new IllegalArgumentException("'envelope'");
                }
                VerifiedEnvelope verified = verifyEnvelope(raw.get("envelope"), trust);
                JsonNode statement = Json.parse(decodeStrict(verified.payload()).toString());
                if (!statementSubjectDigest(statement).equals(manifestDigest)) {
                    throw new IllegalArgumentException("signature does not cover the release manifest");
                }
                ObjectNode signer = Json.nodes().objectNode();
                signer.set("profile", profile != null ? profile : Json.nodes().nullNode());
                signer.put("identity", verified.identity());
                signers.add(signer);
            } catch (RuntimeException | CharacterCodingException e) {
                String message = e.getMessage() != null ? e.getMessage() : "";
                problems.add("signature (" + Readiness.pyStr(profile) + "): " + message);
            }
        }

        ObjectNode out = Json.nodes().objectNode();
        out.put("release", releasePath.toString());
        out.put("verified", problems.isEmpty());
        out.put("manifest_digest", manifestDigest);
        ArrayNode signersArr = out.putArray("signers");
        signers.forEach(signersArr::add);
        if (!problems.isEmpty()) {
            out.put("reason", String.join("; ", problems));
        }
        return out;
    }
}
