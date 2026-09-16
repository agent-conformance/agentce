package org.agentce;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.DeserializationFeature;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.JsonNodeFactory;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;

/**
 * JSON parsing and the two non-canonical serialisations the engine needs.
 *
 * <p>Parsing keeps integer tokens as integers and fractional/exponent tokens as decimals (via {@link
 * DeserializationFeature#USE_BIG_INTEGER_FOR_INTS} and {@link
 * DeserializationFeature#USE_BIG_DECIMAL_FOR_FLOATS}), so {@link Canonical} sees the same int-versus-float
 * distinction the Python reference's {@code json.loads} produces. {@link #pretty} reproduces {@code
 * json.dumps(value, sort_keys=True, indent=2)} (with {@code ensure_ascii=True}), the framing every
 * {@code --json} envelope, manifest, and implementation report uses.
 */
public final class Json {
    private Json() {}

    private static final ObjectMapper MAPPER = new ObjectMapper()
            .enable(DeserializationFeature.USE_BIG_INTEGER_FOR_INTS)
            .enable(DeserializationFeature.USE_BIG_DECIMAL_FOR_FLOATS);

    /** The node factory for building output trees. */
    public static JsonNodeFactory nodes() {
        return JsonNodeFactory.instance;
    }

    /** Parse a JSON document. */
    public static JsonNode parse(String text) {
        try {
            return MAPPER.readTree(text);
        } catch (JsonProcessingException e) {
            throw new IllegalArgumentException("invalid JSON: " + e.getOriginalMessage(), e);
        }
    }

    /** Parse a JSON file (UTF-8). */
    public static JsonNode parseFile(Path path) {
        try {
            return MAPPER.readTree(Files.readString(path, StandardCharsets.UTF_8));
        } catch (IOException e) {
            throw new IllegalArgumentException("cannot read JSON " + path + ": " + e.getMessage(), e);
        }
    }

    /**
     * Compare two strings by their UTF-8 bytes (SQLite BINARY collation, the reference's graph
     * ordering, and Python's {@code sorted()} on keys — UTF-8 byte order equals Unicode code-point
     * order).
     */
    public static int byteCompare(String a, String b) {
        byte[] ba = a.getBytes(StandardCharsets.UTF_8);
        byte[] bb = b.getBytes(StandardCharsets.UTF_8);
        int n = Math.min(ba.length, bb.length);
        for (int i = 0; i < n; i++) {
            int ai = ba[i] & 0xFF;
            int bi = bb[i] & 0xFF;
            if (ai != bi) {
                return ai - bi;
            }
        }
        return ba.length - bb.length;
    }

    /** A double-quoted JSON string literal (like {@code JSON.stringify(s)}), for diagnostics. */
    public static String quote(String text) {
        StringBuilder out = new StringBuilder();
        prettyString(text, out);
        return out.toString();
    }

    /** {@code json.dumps(value, sort_keys=True, indent=2)} with {@code ensure_ascii=True}, no trailing newline. */
    public static String pretty(JsonNode value) {
        StringBuilder out = new StringBuilder();
        prettyValue(value, 0, out);
        return out.toString();
    }

    private static void prettyValue(JsonNode value, int depth, StringBuilder out) {
        if (value == null || value.isNull()) {
            out.append("null");
        } else if (value.isBoolean()) {
            out.append(value.booleanValue() ? "true" : "false");
        } else if (value.isNumber()) {
            if (value.isIntegralNumber()) {
                out.append(value.bigIntegerValue().toString());
            } else {
                out.append(value.decimalValue().toString());
            }
        } else if (value.isTextual()) {
            prettyString(value.textValue(), out);
        } else if (value.isArray()) {
            prettyArray(value, depth, out);
        } else if (value.isObject()) {
            prettyObject(value, depth, out);
        } else {
            throw new IllegalArgumentException("cannot serialise " + value.getNodeType());
        }
    }

    private static void indent(int depth, StringBuilder out) {
        for (int i = 0; i < depth * 2; i++) {
            out.append(' ');
        }
    }

    private static void prettyArray(JsonNode value, int depth, StringBuilder out) {
        if (value.isEmpty()) {
            out.append("[]");
            return;
        }
        out.append("[\n");
        for (int i = 0; i < value.size(); i++) {
            if (i > 0) {
                out.append(",\n");
            }
            indent(depth + 1, out);
            prettyValue(value.get(i), depth + 1, out);
        }
        out.append('\n');
        indent(depth, out);
        out.append(']');
    }

    private static void prettyObject(JsonNode value, int depth, StringBuilder out) {
        List<String> keys = new ArrayList<>();
        value.fieldNames().forEachRemaining(keys::add);
        if (keys.isEmpty()) {
            out.append("{}");
            return;
        }
        keys.sort(Json::byteCompare);
        out.append("{\n");
        boolean first = true;
        for (String key : keys) {
            if (!first) {
                out.append(",\n");
            }
            first = false;
            indent(depth + 1, out);
            prettyString(key, out);
            out.append(": ");
            prettyValue(value.get(key), depth + 1, out);
        }
        out.append('\n');
        indent(depth, out);
        out.append('}');
    }

    /** Escape a string as {@code json.dumps} with {@code ensure_ascii=True}. */
    private static void prettyString(String text, StringBuilder out) {
        out.append('"');
        for (int i = 0; i < text.length(); i++) {
            char ch = text.charAt(i);
            int code = ch;
            switch (code) {
                case 0x22 -> out.append("\\\"");
                case 0x5C -> out.append("\\\\");
                case 0x08 -> out.append("\\b");
                case 0x09 -> out.append("\\t");
                case 0x0A -> out.append("\\n");
                case 0x0C -> out.append("\\f");
                case 0x0D -> out.append("\\r");
                default -> {
                    if (code >= 0x20 && code <= 0x7E) {
                        out.append(ch);
                    } else {
                        out.append(String.format("\\u%04x", code));
                    }
                }
            }
        }
        out.append('"');
    }
}
