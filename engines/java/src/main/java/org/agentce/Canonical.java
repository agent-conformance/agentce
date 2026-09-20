package org.agentce;

import com.fasterxml.jackson.databind.JsonNode;
import java.math.BigInteger;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.text.Normalizer;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Set;

/**
 * The AgentCE canonical form (SPEC §6.7): RFC 8785 JCS under the evidence profile.
 *
 * <p>The evidence profile forbids non-integer JSON numbers, so the hard part of JCS — shortest
 * round-trip formatting of IEEE-754 doubles — never arises, and the canonical form is a small exact
 * set of rules every engine reproduces byte for byte (see {@code spec/rules/canonical-form.md} and the
 * shared vectors under {@code spec/model/test-vectors/}). This is the Java engine's own implementation;
 * it must agree with the Python reference and the TypeScript engine to the byte.
 *
 * <p>A JSON number is an integer exactly when its source token carries no {@code .}, {@code e}, or
 * {@code E} — the same rule the reference's {@code json.loads} applies (an {@code int} versus a
 * {@code float}). Jackson exposes that distinction as {@link JsonNode#isIntegralNumber()}, so {@code
 * 1e+30}, {@code 1.0}, {@code 1e2} and {@code 0.1} are refused as {@code non_integer_number}, an
 * integer beyond 2^53 - 1 in magnitude as {@code integer_out_of_range}, and {@code 9007199254740991}
 * is kept. No network, no learned component.
 */
public final class Canonical {
    private Canonical() {}

    /** The range every engine represents exactly: an integer beyond 2^53 - 1 in magnitude is refused. */
    private static final BigInteger MAX_SAFE_INTEGER = BigInteger.ONE.shiftLeft(53).subtract(BigInteger.ONE);

    /** A value cannot be put in canonical form under the AgentCE profile (stable {@code reason}). */
    public static final class CanonicalizationError extends RuntimeException {
        private static final long serialVersionUID = 1L;
        public final String reason;

        public CanonicalizationError(String reason, String detail) {
            super(detail == null || detail.isEmpty() ? reason : reason + ": " + detail);
            this.reason = reason;
        }
    }

    private static String shortEscape(int code) {
        return switch (code) {
            case 0x08 -> "\\b";
            case 0x09 -> "\\t";
            case 0x0A -> "\\n";
            case 0x0C -> "\\f";
            case 0x0D -> "\\r";
            case 0x22 -> "\\\"";
            case 0x5C -> "\\\\";
            default -> null;
        };
    }

    private static String nfc(String text) {
        return Normalizer.normalize(text, Normalizer.Form.NFC);
    }

    private static void serialiseString(String text, StringBuilder out) {
        out.append('"');
        String normalised = nfc(text);
        // Iterate UTF-16 code units: control units are escaped; surrogates (>= 0xD800) are appended
        // verbatim and encode to their correct UTF-8 bytes when the string is written out.
        for (int i = 0; i < normalised.length(); i++) {
            char ch = normalised.charAt(i);
            int code = ch;
            String escape = shortEscape(code);
            if (escape != null) {
                out.append(escape);
            } else if (code < 0x20) {
                out.append(String.format("\\u%04x", code));
            } else {
                out.append(ch);
            }
        }
        out.append('"');
    }

    private static void serialiseObject(JsonNode node, StringBuilder out) {
        List<String> keys = new ArrayList<>();
        List<JsonNode> values = new ArrayList<>();
        Set<String> seen = new HashSet<>();
        var fields = node.fieldNames();
        // Field iteration is insertion order; two keys equal only after NFC are the ambiguous case.
        var it = node.fields();
        while (it.hasNext()) {
            var entry = it.next();
            String key = nfc(entry.getKey());
            if (!seen.add(key)) {
                throw new CanonicalizationError("duplicate_key_after_nfc", key);
            }
            keys.add(key);
            values.add(entry.getValue());
        }
        // Sort by UTF-16 code unit (String.compareTo), matching the reference's utf-16-be byte order.
        Integer[] order = new Integer[keys.size()];
        for (int i = 0; i < order.length; i++) {
            order[i] = i;
        }
        java.util.Arrays.sort(order, (a, b) -> keys.get(a).compareTo(keys.get(b)));
        out.append('{');
        for (int i = 0; i < order.length; i++) {
            if (i > 0) {
                out.append(',');
            }
            serialiseString(keys.get(order[i]), out);
            out.append(':');
            serialise(values.get(order[i]), out);
        }
        out.append('}');
        // fields is unused beyond documenting insertion order; keep the reference readable.
        assert fields != null;
    }

    private static void serialise(JsonNode value, StringBuilder out) {
        if (value == null || value.isNull()) {
            out.append("null");
        } else if (value.isBoolean()) {
            out.append(value.booleanValue() ? "true" : "false");
        } else if (value.isNumber()) {
            if (value.isIntegralNumber()) {
                BigInteger integer = value.bigIntegerValue();
                if (integer.abs().compareTo(MAX_SAFE_INTEGER) > 0) {
                    throw new CanonicalizationError("integer_out_of_range", integer.toString());
                }
                out.append(integer.toString());
            } else {
                throw new CanonicalizationError("non_integer_number", value.asText());
            }
        } else if (value.isTextual()) {
            serialiseString(value.textValue(), out);
        } else if (value.isArray()) {
            out.append('[');
            for (int i = 0; i < value.size(); i++) {
                if (i > 0) {
                    out.append(',');
                }
                serialise(value.get(i), out);
            }
            out.append(']');
        } else if (value.isObject()) {
            serialiseObject(value, out);
        } else {
            throw new CanonicalizationError("unsupported_type", value.getNodeType().toString());
        }
    }

    /** The canonical form of a JSON value as a string (SPEC §6.7). */
    public static String canonicalString(JsonNode value) {
        StringBuilder out = new StringBuilder();
        serialise(value, out);
        return out.toString();
    }

    /** The canonical form of a JSON value as UTF-8 bytes (SPEC §6.7). */
    public static byte[] canonicalize(JsonNode value) {
        return canonicalString(value).getBytes(StandardCharsets.UTF_8);
    }

    /** The lowercase hex SHA-256 of the canonical form (basis of {@code integrity.hash}, SPEC §6.6). */
    public static String sha256Hex(JsonNode value) {
        return sha256Hex(canonicalize(value));
    }

    /** The lowercase hex SHA-256 of arbitrary bytes. */
    public static String sha256Hex(byte[] data) {
        try {
            byte[] digest = MessageDigest.getInstance("SHA-256").digest(data);
            StringBuilder hex = new StringBuilder(digest.length * 2);
            for (byte b : digest) {
                hex.append(Character.forDigit((b >> 4) & 0xF, 16));
                hex.append(Character.forDigit(b & 0xF, 16));
            }
            return hex.toString();
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException("SHA-256 unavailable", e);
        }
    }
}
