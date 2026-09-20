package org.agentce;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.nio.file.Path;

/**
 * The computation seam for the cross-engine vector check ({@code spec/rules/numerics-vectors}).
 *
 * <p>Reads a case file and yields {@code {caseName: result}} so the same file every engine consumes
 * can be scored engine against engine. The Java engine implements {@code literal_order} (exact
 * ordering of {@code xsd:integer} and {@code xsd:dateTime} literals, {@link
 * Structural#compareLiterals}) and {@code canonical_token} (the accept/refuse decision and canonical
 * form of JSON text carrying number tokens, {@link Canonical}); any other algorithm is refused rather
 * than skipped.
 */
public final class Numerics {
    private Numerics() {}

    /** Compute every case in a numerics-vectors file. */
    public static ObjectNode computeVectorFile(JsonNode data) {
        String algorithm = data.get("algorithm").textValue();
        if (!"literal_order".equals(algorithm) && !"canonical_token".equals(algorithm)) {
            throw new IllegalArgumentException("unsupported numerics algorithm " + algorithm);
        }
        ObjectNode out = Json.nodes().objectNode();
        for (JsonNode c : data.get("cases")) {
            String result = "literal_order".equals(algorithm) ? literalOrder(c) : canonicalToken(c);
            out.put(c.get("name").textValue(), result);
        }
        return out;
    }

    private static String literalOrder(JsonNode c) {
        Integer order = Structural.compareLiterals(c.get("lhs").textValue(), c.get("rhs").textValue());
        return order == null ? "incomparable" : order < 0 ? "lt" : order > 0 ? "gt" : "eq";
    }

    private static String canonicalToken(JsonNode c) {
        try {
            return "canonical:" + Canonical.canonicalString(Json.parse(c.get("input").textValue()));
        } catch (Canonical.CanonicalizationError e) {
            return "error:" + e.reason;
        }
    }

    /** Compute a case file from disk. */
    public static ObjectNode computeVectorFile(Path file) {
        return computeVectorFile(Json.parseFile(file));
    }
}
