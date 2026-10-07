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

    /** A verify refusal: its text, and the catalogue keys the text was built from in reading order (one
     * key for a leaf refusal, a wrapper's own key followed by its inner refusal's keys). {@code verify
     * --json} lists them as {@code reason_keys}. Mirrors {@code signing.VerificationError}. */
    static final class VerifyRefusal extends IllegalArgumentException {
        private static final long serialVersionUID = 1L;
        private final transient List<String> keys;

        VerifyRefusal(String text, List<String> keys) {
            super(text);
            this.keys = List.copyOf(keys);
        }

        List<String> keys() {
            return keys;
        }
    }

    private static final Pattern PARAM = Pattern.compile("\\{(\\w+)\\}");

    /** The refusal for catalogue {@code key}: its cause with {@code {params}} filled in (name, value
     * pairs). Mirrors {@code VerificationError.refusal}. */
    static VerifyRefusal refusal(String key, String... params) {
        return refusal(key, null, params);
    }

    /** A wrapper refusal: {@code inner} fills {@code {inner}} and adds its keys. */
    static VerifyRefusal refusal(String key, RuntimeException inner, String... params) {
        Map<String, String> vars = new LinkedHashMap<>();
        for (int i = 0; i + 1 < params.length; i += 2) {
            vars.put(params[i], params[i + 1]);
        }
        List<String> keys = new ArrayList<>(List.of(key));
        if (inner != null) {
            vars.put("inner", inner.getMessage() != null ? inner.getMessage() : "");
            if (inner instanceof VerifyRefusal r) {
                keys.addAll(r.keys());
            }
        }
        Matcher m = PARAM.matcher(ErrorCatalogue.errorCause(key));
        StringBuilder text = new StringBuilder();
        while (m.find()) {
            String value = vars.get(m.group(1));
            m.appendReplacement(text, Matcher.quoteReplacement(value != null ? value : m.group()));
        }
        m.appendTail(text);
        return new VerifyRefusal(text.toString(), keys);
    }

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
            throw refusal("verify.certificate_signature_invalid");
        }
        JsonNode issuerNode = cert.get("issuer");
        String issuer = textOrNull(issuerNode);
        AuthorityEntry ca = issuer != null ? authorities.get(issuer) : null;
        if (ca == null) {
            throw refusal("verify.certificate_issuer_unknown", "issuer", describeUntrusted(issuerNode));
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
            throw refusal("verify.certificate_signature_invalid");
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
    private static VerifyRefusal certificateRefusal(String key) {
        String cause = ErrorCatalogue.errorCause(key);
        if (cause.endsWith(".")) {
            cause = cause.substring(0, cause.length() - 1);
        }
        return new VerifyRefusal(key + ": " + cause, List.of(key));
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
         * certificate_authorities} each go through {@link #trustMapping} and {@link #trustEntry},
         * Python's {@code _trust_entries} (18.36, 18.80). */
        public static TrustRoot fromDict(JsonNode data) {
            Map<String, KeyEntry> keys = new LinkedHashMap<>();
            var keyIt = trustMapping(data, "keys", "key id to key entry").fields();
            while (keyIt.hasNext()) {
                var entry = keyIt.next();
                String keyid = entry.getKey();
                JsonNode value = trustEntry("keys", keyid, entry.getValue());
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
            var authIt = trustMapping(data, "certificate_authorities", "id to entry").fields();
            while (authIt.hasNext()) {
                var entry = authIt.next();
                JsonNode value = trustEntry("certificate_authorities", entry.getKey(), entry.getValue());
                authorities.put(entry.getKey(), new AuthorityEntry(b64dStrict(value.get("public_key"))));
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
                throw refusal("verify.keyid_untrusted", "keyid", describeUntrusted(keyidNode));
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

    /** A trust root's {@code keys} or {@code certificate_authorities} mapping, as Python's {@code
     * signing._trust_entries}: a falsy value is no entries, any other non-object gets one stable cause,
     * the same words in all three engines (18.80). */
    private static JsonNode trustMapping(JsonNode data, String field, String shape) {
        JsonNode value = data.get(field);
        if (value != null && value.isObject()) {
            return value;
        }
        if (!Readiness.pyTruthy(value)) {
            return Json.nodes().objectNode();
        }
        throw new IllegalArgumentException(field + " is not a mapping of " + shape);
    }

    /** One entry of {@link #trustMapping}'s mapping: an object with a {@code public_key}, or one stable
     * cause naming the entry (its id quoted as Python's repr quotes it). */
    private static JsonNode trustEntry(String field, String id, JsonNode entry) {
        String quoted = Readiness.pyRepr(id);
        if (entry == null || !entry.isObject()) {
            throw new IllegalArgumentException(field + " entry " + quoted + " is not a mapping");
        }
        JsonNode publicKey = entry.get("public_key");
        if (publicKey == null || publicKey.isNull()) {
            throw new IllegalArgumentException(field + " entry " + quoted + " has no public_key");
        }
        return entry;
    }

    /** Reads and parses a trust root file ({@code --trust-root}-shaped JSON) at {@code path}, in
     * {@code signing.load_trust_root}'s three stages: readable JSON, a top-level object, then
     * {@link TrustRoot#fromDict}, whose shape and key errors are re-wrapped as Python's {@code except
     * (AttributeError, KeyError, TypeError, ValueError)} re-wraps them (18.36). The bytes go through
     * {@link #readUntrustedJsonFile}, the rule all three engines' loaders share (18.81), with a fixed
     * text past {@link #MAX_JSON_DEPTH}; any other refusal names the parser's own error, if it has one. */
    public static TrustRoot loadTrustRoot(Path path) {
        JsonNode data;
        try {
            data = readUntrustedJsonFile(path);
        } catch (JsonTooDeep e) {
            String tooDeep = refusal("verify.json_too_deep", "what", "it", "limit", String.valueOf(MAX_JSON_DEPTH))
                    .getMessage();
            throw new IllegalArgumentException(path + " is not readable JSON: " + tooDeep, e);
        } catch (RuntimeException e) {
            Throwable cause = e.getCause();
            String detail = cause != null && cause.getMessage() != null ? ": " + cause.getMessage() : "";
            throw new IllegalArgumentException(path + " is not readable JSON" + detail, e);
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
            throw refusal("verify.envelope_malformed");
        }
        JsonNode payloadTypeNode = envelope.get("payloadType");
        if (payloadTypeNode == null || !payloadTypeNode.isTextual()) {
            throw refusal("verify.envelope_malformed");
        }
        JsonNode rawPayloadNode = envelope.get("payload");
        if (rawPayloadNode == null || !rawPayloadNode.isTextual()) {
            throw refusal("verify.envelope_malformed");
        }
        byte[] payload;
        try {
            payload = b64dStrict(rawPayloadNode.textValue());
        } catch (IllegalArgumentException e) {
            throw refusal("verify.envelope_malformed");
        }
        JsonNode signatures = envelope.get("signatures");
        if (signatures == null || !signatures.isArray()) {
            throw refusal("verify.envelope_malformed");
        }
        if (signatures.isEmpty()) {
            throw refusal("verify.envelope_no_signatures");
        }
        byte[] pae = Sign.dssePae(payloadTypeNode.textValue(), payload);
        RuntimeException lastError = null;
        for (JsonNode entry : signatures) {
            try {
                if (!entry.isObject()) {
                    // A non-object entry has no keyid to resolve; route it through the same
                    // missing-keyid refusal `TrustRoot.resolve` gives for an absent `keyid`.
                    throw refusal("verify.keyid_untrusted", "keyid", describeUntrusted(null));
                }
                KeyEntry resolved = trust.resolve(entry);
                JsonNode sigNode = entry.get("sig");
                if (sigNode == null || !sigNode.isTextual()) {
                    throw refusal("verify.signature_sig_missing");
                }
                byte[] sigBytes;
                try {
                    sigBytes = b64dStrict(sigNode.textValue());
                } catch (IllegalArgumentException e) {
                    throw refusal("verify.signature_not_base64");
                }
                boolean ok = verifyEd25519(pae, publicKeyFromRaw(resolved.publicKeyRaw()), sigBytes);
                if (!ok) {
                    // A bad signature has a sentence of its own (18.68).
                    throw refusal("verify.signature_invalid");
                }
                String keyid = textOrNull(entry.get("keyid"));
                // `resolve`'s own test: a "cert": null entry resolves as a key (18.68).
                JsonNode cert = entry.get("cert");
                boolean keyless = cert != null && !cert.isNull();
                return new VerifiedEnvelope(payload, resolved.identity(), keyid, keyless);
            } catch (RuntimeException e) {
                lastError = e;
            }
        }
        throw refusal("verify.no_signature_verified", lastError);
    }

    /** The deepest container nesting {@link #parseUntrustedJson} accepts (mirrors {@code
     * signing.MAX_JSON_DEPTH}, which is Jackson's own default limit); all three engines enforce it with
     * the same byte pre-scan. */
    static final int MAX_JSON_DEPTH = 1000;

    /** A document nested past {@link #MAX_JSON_DEPTH}; its message stays "not readable JSON" for
     * callers that do not tell depth apart. Mirrors {@code signing.JsonTooDeep}. */
    static final class JsonTooDeep extends IllegalArgumentException {
        private static final long serialVersionUID = 1L;

        JsonTooDeep() {
            super("not readable JSON");
        }
    }

    /** Whether {@code raw} opens more than {@link #MAX_JSON_DEPTH} containers at once, counting
     * {@code [} and {@code {} up and {@code ]} and {@code }} down outside strings (a backslash in a
     * string skips the next byte). Run before parsing, so depth is found first whatever else is wrong
     * with the bytes. Mirrors {@code signing._too_deep}. */
    private static boolean tooDeep(byte[] raw) {
        int depth = 0;
        boolean inString = false;
        for (int i = 0; i < raw.length; i++) {
            byte b = raw[i];
            if (inString) {
                if (b == '\\') {
                    i++;
                } else if (b == '"') {
                    inString = false;
                }
            } else if (b == '"') {
                inString = true;
            } else if (b == '[' || b == '{') {
                if (++depth > MAX_JSON_DEPTH) {
                    return true;
                }
            } else if (b == ']' || b == '}') {
                depth--;
            }
        }
        return false;
    }

    /** {@link #parseUntrustedJson} for one input verify reads, refused with that input's own key:
     * {@code verify.json_too_deep} naming {@code what} past the depth limit, else {@code
     * unreadableKey}. Mirrors {@code signing.parse_verify_input}. */
    private static JsonNode parseVerifyInput(byte[] raw, String unreadableKey, String what) {
        try {
            return parseUntrustedJson(raw);
        } catch (JsonTooDeep e) {
            throw refusal("verify.json_too_deep", "what", what, "limit", String.valueOf(MAX_JSON_DEPTH));
        } catch (IllegalArgumentException e) {
            throw refusal(unreadableKey);
        }
    }

    /** Reads one JSON file of a release with {@link #parseVerifyInput} (mirrors {@code
     * _load_release_json}); the caller checks the shape. */
    private static JsonNode loadVerifyInput(Path path, String unreadableKey, String what) {
        byte[] raw;
        try {
            raw = Files.readAllBytes(path);
        } catch (IOException e) {
            throw refusal(unreadableKey);
        }
        return parseVerifyInput(raw, unreadableKey, what);
    }

    private static final java.util.regex.Pattern PLAIN_ASCII =
            java.util.regex.Pattern.compile("[ !#-&(-\\[\\]-~]*");


    /** Mirrors {@code signing.parse_untrusted_json}: parse JSON {@code verify} reads from an untrusted
     * file or signed payload, or throw {@link IllegalArgumentException}. Strict UTF-8 with no
     * byte-order mark, standard JSON only, containers nested at most {@link #MAX_JSON_DEPTH} deep, and
     * every string and key well-formed Unicode. Callers turn the exception into their own fixed reason
     * text; its message is never shown. A document too deep throws {@link JsonTooDeep}, whatever else
     * is wrong with it. */
    static JsonNode parseUntrustedJson(byte[] raw) {
        if (tooDeep(raw)) {
            throw new JsonTooDeep();
        }
        JsonNode value;
        try {
            value = Json.parse(decodeStrict(raw).toString());
        } catch (CharacterCodingException | RuntimeException e) {
            throw new IllegalArgumentException("not readable JSON", e);
        }
        if (value == null || value.isMissingNode()) {
            throw new IllegalArgumentException("not readable JSON");
        }
        java.util.ArrayDeque<JsonNode> stack = new java.util.ArrayDeque<>();
        stack.push(value);
        while (!stack.isEmpty()) {
            JsonNode node = stack.pop();
            if (node.isTextual()) {
                requireWellFormed(node.textValue());
            } else if (node.isContainerNode()) {
                if (node.isObject()) {
                    node.fieldNames().forEachRemaining(Verify::requireWellFormed);
                }
                for (JsonNode child : node) {
                    stack.push(child);
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
        JsonNode statement = parseVerifyInput(payload, "verify.statement_unreadable", "the signed statement");
        JsonNode subject = statement.isObject() ? statement.get("subject") : null;
        JsonNode first = subject != null && subject.isArray() && !subject.isEmpty() ? subject.get(0) : null;
        JsonNode digest = first != null && first.isObject() ? first.get("digest") : null;
        JsonNode sha256 = digest != null && digest.isObject() ? digest.get("sha256") : null;
        if (sha256 == null || !sha256.isTextual()) {
            throw refusal("verify.statement_no_digest");
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
            return catalogSoftFail(digest, refusal("verify.catalog_unsigned"));
        }
        if (Bundle.cannotOpen(sigPath)) {
            throw catalogUnreadable(dir, CATALOG_SIGNATURE_NAME);
        }
        VerifiedEnvelope verified;
        String signedDigest;
        try {
            JsonNode envelope =
                    loadVerifyInput(sigPath, "verify.catalog_signature_unreadable", CATALOG_SIGNATURE_NAME);
            verified = verifyEnvelope(envelope, trust);
            signedDigest = statementSubjectDigest(verified.payload());
        } catch (RuntimeException e) {
            return catalogSoftFail(digest, e);
        }
        if (!signedDigest.equals(digest)) {
            return catalogSoftFail(digest, refusal("verify.catalog_digest_mismatch"));
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
    private static ObjectNode catalogSoftFail(String digest, RuntimeException refusal) {
        ObjectNode out = Json.nodes().objectNode();
        out.put("digest", digest);
        putRefused(out, List.of(refusal));
        return out;
    }

    /** A verified:false result's {@code reason} and {@code reason_keys}: the refusals' texts joined
     * with "; " and their keys in the same order (mirrors {@code commands._refused}). */
    private static void putRefused(ObjectNode out, List<? extends RuntimeException> refusals) {
        List<String> texts = new ArrayList<>();
        ArrayNode keys = Json.nodes().arrayNode();
        for (RuntimeException r : refusals) {
            texts.add(r.getMessage() != null ? r.getMessage() : "");
            if (r instanceof VerifyRefusal v) {
                v.keys().forEach(keys::add);
            }
        }
        out.put("verified", false);
        out.put("reason", String.join("; ", texts));
        out.set("reason_keys", keys);
    }

    /** The shared soft-fail shape for a release that cannot be verified at all (no {@code
     * manifest_digest}/{@code signers} field): mirrors {@code _verify_release_soft_fail}'s own
     * three-field shape exactly -- every early-return soft-fail in {@link #verifyRelease}, including
     * the directory-bundle branch's own JSON-parse failures, uses this shape. */
    private static ObjectNode releaseSoftFail(Path releasePath, RuntimeException refusal) {
        ObjectNode out = Json.nodes().objectNode();
        out.put("release", releasePath.toString());
        putRefused(out, List.of(refusal));
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
        return catalogUnreadable(dir, InputError.unreadableRel(dir, err));
    }

    static InputError catalogUnreadable(Path dir, String rel) {
        return InputError.unreadable(
                "input.catalog_unreadable", "the catalog directory", dir, rel, "catalog directory");
    }

    /** Verifies a release bundle (or a single DSSE envelope) offline against {@code trust} -- the
     * item's core fix target, built correct from the start: every branch uses the soft-fail shape, and
     * every new JSON-parse-failure path this function adds uses a fixed, engine-neutral reason text
     * (mirrors {@code _verify_release}, {@code commands/__init__.py:758-875}). */
    public static ObjectNode verifyRelease(Path releasePath, TrustRoot trust) {
        if (Files.isRegularFile(releasePath)) {
            requireReleaseReadable(releasePath, releasePath);
            try {
                JsonNode envelope =
                        loadVerifyInput(releasePath, "verify.release_envelope_unreadable", "release envelope");
                VerifiedEnvelope verified = verifyEnvelope(envelope, trust);
                ObjectNode out = Json.nodes().objectNode();
                out.put("release", releasePath.toString());
                out.put("verified", true);
                out.put("signer", verified.identity());
                putNullableKeyid(out, verified);
                out.put("keyless", verified.keyless());
                return out;
            } catch (RuntimeException e) {
                return releaseSoftFail(releasePath, e);
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
            manifest = loadVerifyInput(manifestPath, "verify.release_manifest_unreadable", "release manifest");
        } catch (VerifyRefusal e) {
            return releaseSoftFail(releasePath, e);
        }
        if (!manifest.isObject()) {
            return releaseSoftFail(releasePath, refusal("verify.release_manifest_unreadable"));
        }

        String manifestDigest;
        try {
            manifestDigest = "sha256:" + Canonical.sha256Hex(manifest);
        } catch (Canonical.CanonicalizationError e) {
            return releaseSoftFail(releasePath, refusal("verify.release_manifest_uncanonical"));
        }
        List<VerifyRefusal> problems = new ArrayList<>();
        JsonNode artifacts =
                manifest.has("artifacts") && manifest.get("artifacts").isArray()
                        ? manifest.get("artifacts")
                        : Json.nodes().arrayNode();
        for (JsonNode artifact : artifacts) {
            JsonNode nameNode = artifact != null && artifact.isObject() ? artifact.get("name") : null;
            if (nameNode == null || !nameNode.isTextual()) {
                problems.add(refusal("verify.release_artifact_unnamed"));
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
                problems.add(refusal("verify.release_artifact_missing", "name", name));
                continue;
            }
            String actual = "sha256:" + Canonical.sha256Hex(content);
            JsonNode expected = artifact.get("digest");
            if (expected == null || !actual.equals(expected.asText())) {
                problems.add(refusal("verify.release_artifact_digest", "name", name));
            }
        }

        requireReleaseReadable(releasePath, signaturesPath);
        JsonNode signatureEntries;
        try {
            signatureEntries =
                    loadVerifyInput(signaturesPath, "verify.release_signatures_unreadable", "release signatures");
        } catch (VerifyRefusal e) {
            return releaseSoftFail(releasePath, e);
        }
        if (!signatureEntries.isArray()) {
            return releaseSoftFail(releasePath, refusal("verify.release_signatures_unreadable"));
        }

        List<ObjectNode> signers = new ArrayList<>();
        for (JsonNode raw : signatureEntries) {
            if (raw == null || !raw.isObject()) {
                problems.add(refusal(
                        "verify.release_signature",
                        refusal("verify.release_signature_not_object"),
                        "profile",
                        "None"));
                continue;
            }
            String profile = textOrNull(raw.get("profile"));
            try {
                if (!raw.has("envelope")) {
                    throw refusal("verify.release_envelope_missing");
                }
                VerifiedEnvelope verified = verifyEnvelope(raw.get("envelope"), trust);
                if (!statementSubjectDigest(verified.payload()).equals(manifestDigest)) {
                    throw refusal("verify.release_manifest_not_covered");
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
                problems.add(refusal("verify.release_signature", e, "profile", profile != null ? profile : "None"));
            }
        }
        if (problems.isEmpty() && signers.isEmpty()) {
            problems.add(refusal("verify.release_no_signatures"));
        }

        ObjectNode out = Json.nodes().objectNode();
        out.put("release", releasePath.toString());
        out.put("verified", problems.isEmpty());
        out.put("manifest_digest", manifestDigest);
        ArrayNode signersArr = out.putArray("signers");
        signers.forEach(signersArr::add);
        if (!problems.isEmpty()) {
            putRefused(out, problems);
        }
        return out;
    }
}
