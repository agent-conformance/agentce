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
import java.util.regex.Matcher;
import java.util.regex.Pattern;

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

    /** A base64 field's bytes, or {@link IllegalArgumentException}. A missing/non-string field fails
     * the same way a malformed one does: both are "not a usable base64 string" to every caller here. */
    private static byte[] b64dStrict(JsonNode node) {
        String text = textOrNull(node);
        if (text == null) {
            throw new IllegalArgumentException("not a base64 string");
        }
        return b64dStrict(text);
    }

    private static final java.util.regex.Pattern BASE64 = java.util.regex.Pattern.compile("[A-Za-z0-9+/]*={0,2}");

    /** Python's {@code b64decode(validate=True)} and {@code verify.ts}'s {@code b64dStrict}: padding
     * required (Java's default decoder accepts it missing, which would let a payload or key with its
     * padding stripped verify here alone). */
    private static byte[] b64dStrict(String text) {
        if (text.length() % 4 != 0 || !BASE64.matcher(text).matches()) {
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
            throw new IllegalArgumentException("unknown certificate issuer " + describeUntrusted(issuerNode));
        }
        KeyEntry leaf;
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
            publicKeyFromRaw(leafRaw); // validate eagerly, as Python's `load_public_ed25519` does, so
            // a malformed leaf key collapses to this method's own message, not a later deferred one.
            String identity = textOrNull(cert.get("identity"));
            if (identity == null) {
                throw new IllegalArgumentException("missing certificate identity");
            }
            leaf = new KeyEntry(leafRaw, identity);
        } catch (RuntimeException e) {
            throw new IllegalArgumentException("certificate signature does not verify");
        }
        checkCertificateFields(cert);
        return leaf;
    }

    /** RFC 3339 {@code date-time} in UTC (§5.6): {@code T}/{@code Z} in either case, ASCII digits only, an
     * optional fraction. */
    private static final Pattern RFC3339_UTC = Pattern.compile(
            "([0-9]{4})-([0-9]{2})-([0-9]{2})[Tt]([0-9]{2}):([0-9]{2}):([0-9]{2})(?:\\.([0-9]+))?[Zz]");
    private static final int[] DAYS_IN_MONTH = {31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31};

    /** {@code <key>: <cause>} with the catalogue's cause text, minus its final period (the aggregate
     * reasons that wrap it add their own); mirrors {@code signing._certificate_refusal}. */
    private static IllegalArgumentException certificateRefusal(String key) {
        String cause = Messages.errorCause(key);
        if (cause.endsWith(".")) {
            cause = cause.substring(0, cause.length() - 1);
        }
        return new IllegalArgumentException(key + ": " + cause);
    }

    /** A text form of an RFC 3339 UTC timestamp that sorts in time order, or {@code null} if {@code value}
     * is not one: the 14 date-time digits, then the fraction without trailing zeros. Mirrors
     * {@code signing._utc_order_key}. */
    private static String utcOrderKey(JsonNode value) {
        if (value == null || !value.isTextual()) {
            return null;
        }
        Matcher m = RFC3339_UTC.matcher(value.textValue());
        if (!m.matches()) {
            return null;
        }
        int year = Integer.parseInt(m.group(1));
        int month = Integer.parseInt(m.group(2));
        int day = Integer.parseInt(m.group(3));
        int hour = Integer.parseInt(m.group(4));
        int minute = Integer.parseInt(m.group(5));
        int second = Integer.parseInt(m.group(6));
        boolean leap = year % 4 == 0 && (year % 100 != 0 || year % 400 == 0);
        if (month < 1 || month > 12) {
            return null;
        }
        int days = month == 2 && leap ? 29 : DAYS_IN_MONTH[month - 1];
        if (day < 1 || day > days || hour > 23 || minute > 59 || second > 60) {
            return null;
        }
        String fraction = m.group(7) == null ? "" : m.group(7).replaceAll("0+$", "");
        return m.group(1) + m.group(2) + m.group(3) + m.group(4) + m.group(5) + m.group(6) + "." + fraction;
    }

    /** {@code algorithm} exactly {@code ed25519}, and a well-formed RFC 3339 UTC window with {@code
     * not_before <= not_after}, never compared with the clock; mirrors {@code
     * signing._check_certificate_fields}. */
    private static void checkCertificateFields(JsonNode cert) {
        JsonNode algorithm = cert.get("algorithm");
        if (algorithm == null || !algorithm.isTextual() || !"ed25519".equals(algorithm.textValue())) {
            throw certificateRefusal("verify.certificate_algorithm");
        }
        String notBefore = utcOrderKey(cert.get("not_before"));
        String notAfter = utcOrderKey(cert.get("not_after"));
        if (notBefore == null || notAfter == null) {
            throw certificateRefusal("verify.certificate_validity_malformed");
        }
        if (notBefore.compareTo(notAfter) > 0) {
            throw certificateRefusal("verify.certificate_validity_inverted");
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
         * signing.py:295-297}). {@code data} is already an object: the top-level check is a separate
         * stage in {@link #loadTrustRoot}, as in Python's {@code load_trust_root}. {@code keys}/{@code
         * certificate_authorities} each go through {@link #asMapping}, Python's {@code data.get(field)
         * or {}} (18.36). */
        public static TrustRoot fromDict(JsonNode data) {
            Map<String, KeyEntry> keys = new LinkedHashMap<>();
            var keyIt = asMapping(data.get("keys")).fields();
            while (keyIt.hasNext()) {
                var entry = keyIt.next();
                String keyid = entry.getKey();
                JsonNode value = entry.getValue();
                byte[] raw = b64dStrict(value.get("public_key"));
                if (!Sign.keyidFor(raw).equals(keyid)) {
                    // Python's `from_dict` raises its own VerificationError here, which
                    // `load_trust_root` passes through unwrapped; TrustRootError marks it the same way.
                    throw new TrustRootError(
                            "trust root entry " + Readiness.pyRepr(Json.nodes().textNode(keyid))
                                    + " does not match its own key");
                }
                String identity = textOrNull(value.get("identity"));
                keys.put(keyid, new KeyEntry(raw, identity != null ? identity : keyid));
            }
            Map<String, AuthorityEntry> authorities = new LinkedHashMap<>();
            var authIt = asMapping(data.get("certificate_authorities")).fields();
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
                throw new IllegalArgumentException("no trusted key for keyid " + describeUntrusted(keyidNode));
            }
            return found;
        }
    }

    /** The path to the trust root vendored in the engine package (a byte-for-byte copy of {@code
     * engines/python/agentce/data/trust/dev-root.json}, pinned by {@code VerifyTest}'s sync test). */
    private static final String VENDORED_TRUST_RESOURCE = "/trust/dev-root.json";

    /** Marks the error {@link TrustRoot#fromDict} raises itself (the content-addressing check), as
     * opposed to a malformed shape or key, which {@link #loadTrustRoot} re-wraps (mirrors Python's
     * {@code VerificationError} raised directly inside {@code from_dict}). */
    private static final class TrustRootError extends IllegalArgumentException {
        private static final long serialVersionUID = 1L;

        TrustRootError(String message) {
            super(message);
        }
    }

    /** Python's {@code (value or {})}: a falsy value is an empty mapping; an object is itself; any
     * other value throws, as Python's {@code .items()} on it would. */
    private static JsonNode asMapping(JsonNode value) {
        if (value != null && value.isObject()) {
            return value;
        }
        if (!Readiness.pyTruthy(value)) {
            return Json.nodes().objectNode();
        }
        throw new IllegalArgumentException("not a mapping");
    }

    /** Reads and parses a trust root file ({@code --trust-root}-shaped JSON) at {@code path}, in
     * {@code signing.load_trust_root}'s three stages: readable JSON, a top-level object, then
     * {@link TrustRoot#fromDict}, whose shape and key errors are re-wrapped as Python's {@code except
     * (AttributeError, KeyError, TypeError, ValueError)} re-wraps them (18.36). */
    public static TrustRoot loadTrustRoot(Path path) {
        JsonNode data;
        try {
            data = Json.parseFile(path);
        } catch (RuntimeException e) {
            throw new IllegalArgumentException(path + " is not readable JSON: " + e.getMessage(), e);
        }
        if (data == null || !data.isObject()) {
            throw new IllegalArgumentException(path + " does not hold a trust-root object");
        }
        try {
            return TrustRoot.fromDict(data);
        } catch (TrustRootError e) {
            throw e;
        } catch (RuntimeException e) {
            throw new IllegalArgumentException(path + " is not a usable trust root: " + e.getMessage(), e);
        }
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
            payload = b64dStrict(rawPayloadNode.textValue());
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
                    throw new IllegalArgumentException("no trusted key for keyid None");
                }
                KeyEntry resolved = trust.resolve(entry);
                JsonNode sigNode = entry.get("sig");
                if (sigNode == null || !sigNode.isTextual()) {
                    // Mirrors Python's `KeyError` repr for the same missing-key access.
                    throw new IllegalArgumentException("'sig'");
                }
                byte[] sigBytes;
                try {
                    sigBytes = b64dStrict(sigNode.textValue());
                } catch (IllegalArgumentException e) {
                    throw new IllegalArgumentException(SIG_NOT_BASE64);
                }
                boolean ok = verifyEd25519(pae, publicKeyFromRaw(resolved.publicKeyRaw()), sigBytes);
                if (!ok) {
                    // Python's `SIGNATURE_INVALID`: a bad signature has a sentence of its own (18.68).
                    throw new IllegalArgumentException("signature does not verify");
                }
                String keyid = textOrNull(entry.get("keyid"));
                // `resolve`'s own test: a "cert": null entry resolves as a key (18.68).
                JsonNode cert = entry.get("cert");
                boolean keyless = cert != null && !cert.isNull();
                return new VerifiedEnvelope(payload, resolved.identity(), keyid, keyless);
            } catch (RuntimeException e) {
                lastError = e.getMessage() != null ? e.getMessage() : "";
            }
        }
        throw new IllegalArgumentException("no signature verified against the trust root: " + lastError);
    }

    /** The deepest container nesting {@link #parseUntrustedJson} accepts (mirrors {@code
     * signing.MAX_JSON_DEPTH}, which is Jackson's own default limit). */
    static final int MAX_JSON_DEPTH = 1000;

    private static final java.util.regex.Pattern PLAIN_ASCII =
            java.util.regex.Pattern.compile("[ !#-&(-\\[\\]-~]*");

    private static final String SIG_NOT_BASE64 = "'sig' is not valid base64";
    private static final String STATEMENT_UNREADABLE = "the signed statement is not readable JSON";
    private static final String STATEMENT_NO_DIGEST = "the signed statement carries no subject digest";

    /** Mirrors {@code signing.parse_untrusted_json}: parse JSON {@code verify} reads from an untrusted
     * file or signed payload, or throw {@link IllegalArgumentException}. Strict UTF-8 with no
     * byte-order mark, standard JSON only, containers nested at most {@link #MAX_JSON_DEPTH} deep, and
     * every string and key well-formed Unicode. Callers turn the exception into their own fixed reason
     * text; its message is never shown. */
    static JsonNode parseUntrustedJson(byte[] raw) {
        JsonNode value;
        try {
            value = Json.parse(decodeStrict(raw).toString());
        } catch (CharacterCodingException | RuntimeException e) {
            throw new IllegalArgumentException("not readable JSON", e);
        }
        if (value == null || value.isMissingNode()) {
            throw new IllegalArgumentException("not readable JSON");
        }
        java.util.ArrayDeque<Map.Entry<JsonNode, Integer>> stack = new java.util.ArrayDeque<>();
        stack.push(Map.entry(value, 1));
        while (!stack.isEmpty()) {
            var top = stack.pop();
            JsonNode node = top.getKey();
            int depth = top.getValue();
            if (node.isTextual()) {
                requireWellFormed(node.textValue());
            } else if (node.isContainerNode()) {
                if (depth > MAX_JSON_DEPTH) {
                    throw new IllegalArgumentException("not readable JSON");
                }
                if (node.isObject()) {
                    node.fieldNames().forEachRemaining(Verify::requireWellFormed);
                }
                for (JsonNode child : node) {
                    stack.push(Map.entry(child, depth + 1));
                }
            }
        }
        return value;
    }

    static JsonNode readUntrustedJsonFile(Path path) {
        try {
            return parseUntrustedJson(Files.readAllBytes(path));
        } catch (IOException e) {
            throw new IllegalArgumentException("not readable JSON", e);
        }
    }

    private static void requireWellFormed(String text) {
        for (int i = 0; i < text.length(); i++) {
            char c = text.charAt(i);
            if (Character.isHighSurrogate(c) && i + 1 < text.length() && Character.isLowSurrogate(text.charAt(i + 1))) {
                i++;
            } else if (Character.isSurrogate(c)) {
                throw new IllegalArgumentException("not readable JSON");
            }
        }
    }

    /** Mirrors {@code signing.describe_untrusted}: how a refusal names a value read from an untrusted
     * document (a keyid, an issuer) -- {@code None}, a quoted plain-ASCII string, or a fixed
     * description. */
    static String describeUntrusted(JsonNode value) {
        if (value == null || value.isNull()) {
            return "None";
        }
        if (value.isTextual()) {
            return PLAIN_ASCII.matcher(value.textValue()).matches()
                    ? "'" + value.textValue() + "'"
                    : "<a string with special characters>";
        }
        return "<not a string>";
    }

    /** Mirrors {@code signing.statement_subject_digest}: the {@code sha256:} digest of the first
     * subject of the in-toto Statement in {@code payload}, or one of two fixed refusals. */
    private static String statementSubjectDigest(byte[] payload) {
        JsonNode statement;
        try {
            statement = parseUntrustedJson(payload);
        } catch (IllegalArgumentException e) {
            throw new IllegalArgumentException(STATEMENT_UNREADABLE, e);
        }
        JsonNode subject = statement.isObject() ? statement.get("subject") : null;
        JsonNode first = subject != null && subject.isArray() && !subject.isEmpty() ? subject.get(0) : null;
        JsonNode digest = first != null && first.isObject() ? first.get("digest") : null;
        JsonNode sha256 = digest != null && digest.isObject() ? digest.get("sha256") : null;
        if (sha256 == null || !sha256.isTextual()) {
            throw new IllegalArgumentException(STATEMENT_NO_DIGEST);
        }
        return "sha256:" + sha256.textValue();
    }

    static CharBuffer decodeStrict(byte[] bytes) throws CharacterCodingException {
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
        String digest;
        try {
            digest = Catalog.digestTree(dir, Set.of(CATALOG_SIGNATURE_NAME));
        } catch (IllegalStateException e) {
            throw catalogUnreadable(dir, e);
        }
        Path sigPath = dir.resolve(CATALOG_SIGNATURE_NAME);
        if (!Files.isRegularFile(sigPath)) {
            return catalogSoftFail(digest, UNSIGNED_SENTENCE);
        }
        if (Bundle.cannotOpen(sigPath)) {
            throw InputError.unreadable(
                    "input.catalog_unreadable", "the catalog directory", dir, CATALOG_SIGNATURE_NAME,
                    "catalog directory");
        }
        JsonNode envelope;
        try {
            envelope = readUntrustedJsonFile(sigPath);
        } catch (IllegalArgumentException e) {
            return catalogSoftFail(digest, CATALOG_SIGNATURE_NAME + " is not readable JSON");
        }
        VerifiedEnvelope verified;
        try {
            verified = verifyEnvelope(envelope, trust);
        } catch (RuntimeException e) {
            return catalogSoftFail(digest, e.getMessage());
        }
        String signedDigest;
        try {
            signedDigest = statementSubjectDigest(verified.payload());
        } catch (IllegalArgumentException e) {
            return catalogSoftFail(digest, e.getMessage());
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

    /** Throws {@code input.release_unreadable} naming {@code path} when a permission error stops it
     * being read (Python's PermissionError branch of {@code cmd_verify}'s release wrapper, 18.68). */
    private static void requireReleaseReadable(Path releasePath, Path path) {
        if (Bundle.permissionDenied(path) || Bundle.cannotOpen(path)) {
            String rel = path.equals(releasePath) ? "" : releasePath.relativize(path).toString().replace('\\', '/');
            throw InputError.unreadable(
                    "input.release_unreadable", "the release artifact", releasePath, rel, "release");
        }
    }

    /** {@code input.catalog_unreadable}, naming the file or folder that cannot be read (Python's
     * {@code _catalog_unreadable}, 18.68). */
    static InputError catalogUnreadable(Path dir, Throwable err) {
        return InputError.unreadable(
                "input.catalog_unreadable", "the catalog directory", dir, InputError.unreadableRel(dir, err),
                "catalog directory");
    }

    /** Verifies a release bundle (or a single DSSE envelope) offline against {@code trust} -- the
     * item's core fix target, built correct from the start: every branch uses the soft-fail shape, and
     * every new JSON-parse-failure path this function adds uses a fixed, engine-neutral reason text
     * (mirrors {@code _verify_release}, {@code commands/__init__.py:758-875}). */
    public static ObjectNode verifyRelease(Path releasePath, TrustRoot trust) {
        if (Files.isRegularFile(releasePath)) {
            requireReleaseReadable(releasePath, releasePath);
            JsonNode envelope;
            try {
                envelope = readUntrustedJsonFile(releasePath);
            } catch (IllegalArgumentException e) {
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
            requireReleaseReadable(releasePath, manifestPath);
            requireReleaseReadable(releasePath, signaturesPath);
            throw new InputError(
                    "input.release_bundle",
                    releasePath + " is not a release bundle (release-manifest.json/signatures.json).",
                    "pass the --out directory produced by the release tooling.");
        }

        requireReleaseReadable(releasePath, manifestPath);
        JsonNode manifest;
        try {
            manifest = readUntrustedJsonFile(manifestPath);
        } catch (IllegalArgumentException e) {
            return releaseSoftFail(releasePath, "release manifest is not readable JSON");
        }
        if (!manifest.isObject()) {
            return releaseSoftFail(releasePath, "release manifest is not readable JSON");
        }

        String manifestDigest;
        try {
            manifestDigest = "sha256:" + Canonical.sha256Hex(manifest);
        } catch (Canonical.CanonicalizationError e) {
            return releaseSoftFail(releasePath, "release manifest cannot be canonicalized");
        }
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
            Path artifactFile = Bundle.confineToRoot(releasePath, name);
            if (artifactFile != null) {
                requireReleaseReadable(releasePath, artifactFile);
            }
            byte[] content;
            try {
                content = artifactFile == null ? null : Files.readAllBytes(artifactFile);
            } catch (IOException e) {
                content = null; // unreadable (a directory, no permission, gone) counts as missing, as in Python
            }
            if (content == null) {
                problems.add("missing artifact " + name);
                continue;
            }
            String actual = "sha256:" + Canonical.sha256Hex(content);
            JsonNode expected = artifact.get("digest");
            if (expected == null || !actual.equals(expected.asText())) {
                problems.add("digest mismatch for " + name);
            }
        }

        requireReleaseReadable(releasePath, signaturesPath);
        JsonNode signatureEntries;
        try {
            signatureEntries = readUntrustedJsonFile(signaturesPath);
        } catch (IllegalArgumentException e) {
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
            String profile = textOrNull(raw.get("profile"));
            try {
                if (!raw.has("envelope")) {
                    throw new IllegalArgumentException("'envelope'");
                }
                VerifiedEnvelope verified = verifyEnvelope(raw.get("envelope"), trust);
                if (!statementSubjectDigest(verified.payload()).equals(manifestDigest)) {
                    throw new IllegalArgumentException("signature does not cover the release manifest");
                }
                ObjectNode signer = Json.nodes().objectNode();
                if (profile != null) {
                    signer.put("profile", profile);
                } else {
                    signer.putNull("profile");
                }
                signer.put("identity", verified.identity());
                signers.add(signer);
            } catch (RuntimeException e) {
                String message = e.getMessage() != null ? e.getMessage() : "";
                problems.add("signature (" + (profile != null ? profile : "None") + "): " + message);
            }
        }
        if (problems.isEmpty() && signers.isEmpty()) {
            problems.add("release bundle carries no signatures");
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
