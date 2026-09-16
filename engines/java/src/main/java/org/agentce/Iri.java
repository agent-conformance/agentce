package org.agentce;

import java.nio.charset.StandardCharsets;
import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;

/**
 * Deterministic IRIs for graph nodes (SPEC §6.3, IR-12). Events become {@code agentce:event/<id>};
 * principals are pseudonymised {@code agentce:principal/<mac>} where {@code mac} is HMAC-SHA-256 of
 * the principal id under a per-subject key (a run without a key uses the documented all-zero key).
 */
public final class Iri {
    private Iri() {}

    /** The documented default pseudonymisation key (all zero); a real run supplies its own by reference. */
    public static final byte[] ZERO_KEY = new byte[32];

    public static String eventIri(String eventId) {
        return "agentce:event/" + eventId;
    }

    public static String principalIri(String rawId) {
        return principalIri(rawId, ZERO_KEY);
    }

    public static String principalIri(String rawId, byte[] key) {
        try {
            Mac mac = Mac.getInstance("HmacSHA256");
            mac.init(new SecretKeySpec(key, "HmacSHA256"));
            byte[] digest = mac.doFinal(rawId.getBytes(StandardCharsets.UTF_8));
            return "agentce:principal/" + hex(digest);
        } catch (java.security.GeneralSecurityException e) {
            throw new IllegalStateException("HMAC-SHA-256 unavailable", e);
        }
    }

    /** True if {@code value} is a CURIE that refers to an event node. */
    public static boolean isEventRef(String value) {
        return value.startsWith("agentce:event/");
    }

    /** The event id from an {@code agentce:event/<id>} CURIE. */
    public static String eventIdOf(String value) {
        return isEventRef(value) ? value.substring(value.indexOf('/') + 1) : value;
    }

    private static String hex(byte[] data) {
        StringBuilder out = new StringBuilder(data.length * 2);
        for (byte b : data) {
            out.append(Character.forDigit((b >> 4) & 0xF, 16));
            out.append(Character.forDigit(b & 0xF, 16));
        }
        return out.toString();
    }
}
