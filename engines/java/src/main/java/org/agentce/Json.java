package org.agentce;

import com.fasterxml.jackson.core.JsonFactory;
import com.fasterxml.jackson.core.JsonParseException;
import com.fasterxml.jackson.core.JsonParser;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.core.JsonToken;
import com.fasterxml.jackson.core.StreamReadConstraints;
import com.fasterxml.jackson.core.StreamReadFeature;
import com.fasterxml.jackson.core.util.JsonParserDelegate;
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

    /** CPython's {@code sys.int_max_str_digits} default: the most digits {@code int(str)} reads. */
    static final int MAX_INT_STR_DIGITS = 4300;

    /**
     * Python's {@code json.loads} refuses an integer literal longer than {@link #MAX_INT_STR_DIGITS}
     * and accepts a fractional/exponent literal of any length. Jackson's default caps every number
     * token at 1000 characters, so that cap is lifted and Python's integer rule applied instead
     * ({@link IntegerDigitLimit}). Python reads a string or an object key of any length, and Jackson
     * caps them at 20,000,000 and 50,000 characters, so those caps are lifted too: a signed segment's
     * envelope over the cap would otherwise read unsigned in Java alone.
     */
    private static final ObjectMapper MAPPER = new ObjectMapper(JsonFactory.builder()
                    .streamReadConstraints(StreamReadConstraints.builder()
                            .maxNumberLength(Integer.MAX_VALUE)
                            .maxStringLength(Integer.MAX_VALUE)
                            .maxNameLength(Integer.MAX_VALUE)
                            .build())
                    .enable(StreamReadFeature.USE_FAST_BIG_NUMBER_PARSER)
                    .build())
            .enable(DeserializationFeature.USE_BIG_INTEGER_FOR_INTS)
            .enable(DeserializationFeature.USE_BIG_DECIMAL_FOR_FLOATS);

    /** Refuses an over-long integer token before Jackson builds its {@code BigInteger}. */
    private static final class IntegerDigitLimit extends JsonParserDelegate {
        IntegerDigitLimit(JsonParser parser) {
            super(parser);
        }

        @Override
        public JsonToken nextToken() throws IOException {
            JsonToken token = delegate.nextToken();
            if (token == JsonToken.VALUE_NUMBER_INT && delegate.getTextLength() > MAX_INT_STR_DIGITS) {
                boolean negative = delegate.getTextCharacters()[delegate.getTextOffset()] == '-';
                if (delegate.getTextLength() - (negative ? 1 : 0) > MAX_INT_STR_DIGITS) {
                    throw new JsonParseException(
                            this, "integer literal exceeds " + MAX_INT_STR_DIGITS + " digits");
                }
            }
            return token;
        }

        /** A float token reads as a {@code BigDecimal}, which has no negative zero, so {@code -0.0} would read
         * as {@code 0.0}. Jackson builds a double node for a token it reports as NaN; reporting a negative
         * zero that way keeps its sign (SPEC 8.7 renders it {@code -0.0}, as Python's {@code repr} does). */
        @Override
        public boolean isNaN() throws IOException {
            if (delegate.currentToken() == JsonToken.VALUE_NUMBER_FLOAT) {
                String text = delegate.getText();
                if (text.startsWith("-") && text.substring(1).split("[eE]", 2)[0].matches("[0.]+")) {
                    return true;
                }
            }
            return delegate.isNaN();
        }
    }

    /** The node factory for building output trees. */
    public static JsonNodeFactory nodes() {
        return JsonNodeFactory.instance;
    }

    /** One non-blank JSON Lines line: its 1-based number and its text, as {@link #jsonlLines} returns it. */
    public record Line(int number, String text) {}

    /** The non-blank JSON Lines of {@code text}: LF alone ends a line, a CRLF's CR is dropped, a line
     * holding only spaces and tabs is blank. Mirrors Python's {@code safe_json.jsonl_lines} and {@code
     * json.ts}'s {@code jsonlRecords}. */
    public static List<Line> jsonlLines(String text) {
        String[] lines = text.split("\n", -1);
        List<Line> out = new ArrayList<>();
        for (int i = 0; i < lines.length; i++) {
            String line = lines[i].endsWith("\r") ? lines[i].substring(0, lines[i].length() - 1) : lines[i];
            if (!line.chars().allMatch(c -> c == ' ' || c == '\t')) {
                out.add(new Line(i + 1, line));
            }
        }
        return out;
    }

    /**
     * Parse a JSON document, refusing trailing content after the root value and an empty or
     * whitespace-only document. {@code readTree} alone stops at the end of the first value and
     * silently ignores whatever follows; Python's {@code json.loads} and JavaScript's {@code
     * JSON.parse} both refuse it, so a parser left un-checked here was the one engine that would
     * sign a {@code claim.json} carrying appended garbage (18.26 round-3 verifier finding).
     * {@code readTree} also returns {@code null} (not an exception) for an empty or
     * whitespace-only document, which Python's {@code json.loads} and the engine's own {@code
     * parseJson} both reject as a {@code JSONDecodeError}/syntax error; returning {@code null}
     * here instead of throwing pushes a {@code NullPointerException} onto every caller that
     * assumes a parse either throws or returns a node (18.29 verifier round 1, F1).
     */
    public static JsonNode parse(String text) {
        try (JsonParser parser = new IntegerDigitLimit(MAPPER.getFactory().createParser(text))) {
            JsonNode node = MAPPER.readTree(parser);
            if (node == null) {
                throw new IllegalArgumentException("invalid JSON: no content");
            }
            if (parser.nextToken() != null) {
                throw new IllegalArgumentException("invalid JSON: unexpected trailing content after the document");
            }
            return node;
        } catch (JsonProcessingException e) {
            throw new IllegalArgumentException("invalid JSON: " + e.getOriginalMessage(), e);
        } catch (IOException e) {
            throw new IllegalArgumentException("cannot parse JSON: " + e.getMessage(), e);
        }
    }

    /** The deepest container nesting a strict reader accepts (Jackson's own default limit); all three
     * engines enforce it with the same byte pre-scan, so they refuse at the same depth. Mirrors {@code
     * safe_json.MAX_JSON_DEPTH}. */
    static final int MAX_JSON_DEPTH = 1000;

    /** Whether {@code raw} opens more than {@link #MAX_JSON_DEPTH} containers at once, counting
     * {@code [} and {@code {} up and {@code ]} and {@code }} down outside strings (a backslash in a
     * string skips the next byte). Run before parsing, so depth is found first whatever else is wrong
     * with the bytes. Mirrors {@code safe_json.too_deep}. */
    static boolean tooDeep(byte[] raw) {
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

    /** Parse one report file (or, with {@code line}, that JSON Lines line of one) under the JSON profile
     * (SPEC 8.7, §13.3.4 stage 4): every engine's readiness, sign and {@code report --validate} read
     * report files by this rule. Outside it throws {@link JsonProfileError}. Mirrors {@code
     * safe_json.load_report_json} and {@code json.ts}'s {@code parseReportJson}. */
    public static JsonNode parseReport(String text, Integer line) {
        checkProfile(text, line);
        return parse(text);
    }

    /** {@link #parseReport(String, Integer)} on a whole file. */
    public static JsonNode parseReport(String text) {
        return parseReport(text, null);
    }

    /** Parse a JSON file (UTF-8). */
    public static JsonNode parseFile(Path path) {
        String text;
        try {
            text = Files.readString(path, StandardCharsets.UTF_8);
        } catch (IOException e) {
            throw new IllegalArgumentException("cannot read JSON " + path + ": " + e.getMessage(), e);
        }
        return parse(text);
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

    /** Compact JSON, no indentation, field order as inserted (never sorted) -- for a caller whose
     * own field order is already the contract, such as a seam verb matching another engine's
     * {@code JSON.stringify} output byte for byte. */
    public static String compact(JsonNode value) {
        try {
            return MAPPER.writeValueAsString(value);
        } catch (JsonProcessingException e) {
            throw new IllegalStateException("cannot serialise JSON", e);
        }
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

    // --- The JSON profile (SPEC 8.7) ---------------------------------------------------------------
    //
    // One hand-written check, the same rules as Python's safe_json.check_profile and json.ts's
    // checkProfile, runs before Jackson sees the text, so a file outside the profile is refused with
    // the rule and line whatever Jackson would have done. Rule ids are the rule values of
    // spec/report/json-cases.json.

    private static final String LINE_BREAKS = "\f\u0085  ";
    private static final String SIMPLE_ESCAPES = "\"\\/bfnrt";

    /** Text outside the JSON profile: {@code rule} is a case-table rule id, {@code line} is 1-based. The
     * message is the phrase every engine's refusal carries. */
    public static final class JsonProfileError extends IllegalArgumentException {
        private static final long serialVersionUID = 1L;
        private final String rule;
        private final int line;

        public JsonProfileError(String rule, int line) {
            super("outside the JSON profile: " + rule + " at line " + line + " (SPEC 8.7)");
            this.rule = rule;
            this.line = line;
        }

        public String rule() {
            return rule;
        }

        public int line() {
            return line;
        }
    }

    /** Whether an input's leading byte-order mark is ignored (an adopter's tool may write one) or refused
     * (a file an engine wrote). */
    public enum Bom { IGNORE, REFUSE }

    private static int hex4(String text, int i) {
        if (i + 4 > text.length()) {
            return -1;
        }
        int v = 0;
        for (int k = i; k < i + 4; k++) {
            int d = Character.digit(text.charAt(k), 16);
            if (d < 0 || text.charAt(k) > 'f') {
                return -1;
            }
            v = v * 16 + d;
        }
        return v;
    }

    /** Scan the string whose quote is at {@code start}; return the index after it, its decoded value in {@code out}. */
    private static int scanString(String text, int start, int line, StringBuilder out) {
        int i = start + 1;
        while (true) {
            if (i >= text.length()) {
                throw new JsonProfileError("syntax", line);
            }
            char c = text.charAt(i);
            if (c == '"') {
                return i + 1;
            }
            if (c < ' ') {
                throw new JsonProfileError("syntax", line);
            }
            if (c != '\\') {
                out.append(c);
                i += 1;
                continue;
            }
            char e = i + 1 < text.length() ? text.charAt(i + 1) : 0;
            if (e != 0 && SIMPLE_ESCAPES.indexOf(e) >= 0) {
                out.append(switch (e) {
                    case 'b' -> '\b';
                    case 'f' -> '\f';
                    case 'n' -> '\n';
                    case 'r' -> '\r';
                    case 't' -> '\t';
                    default -> e;
                });
                i += 2;
                continue;
            }
            if (e != 'u') {
                throw new JsonProfileError("syntax", line);
            }
            int cp = hex4(text, i + 2);
            if (cp < 0) {
                throw new JsonProfileError("syntax", line);
            }
            i += 6;
            if (cp >= 0xDC00 && cp <= 0xDFFF) {
                throw new JsonProfileError("lone_surrogate", line);
            }
            if (cp >= 0xD800 && cp <= 0xDBFF) {
                int low = text.startsWith("\\u", i) ? hex4(text, i + 2) : -1;
                if (low < 0xDC00 || low > 0xDFFF) {
                    throw new JsonProfileError("lone_surrogate", line);
                }
                cp = 0x10000 + ((cp - 0xD800) << 10) + (low - 0xDC00);
                i += 6;
            }
            out.appendCodePoint(cp);
        }
    }

    private static boolean isDigit(String text, int i) {
        return i < text.length() && text.charAt(i) >= '0' && text.charAt(i) <= '9';
    }

    private static char at(String text, int i) {
        return i < text.length() ? text.charAt(i) : 0;
    }

    /** Scan the number starting at {@code start} (a {@code -} or a digit); return the index after it. */
    private static int scanNumber(String text, int start, int line) {
        int i = start;
        if (at(text, i) == '-') {
            i += 1;
            if (text.startsWith("Infinity", i)) {
                throw new JsonProfileError("infinity", line);
            }
        }
        if (!isDigit(text, i)) {
            throw new JsonProfileError("syntax", line);
        }
        if (at(text, i) == '0' && isDigit(text, i + 1)) {
            throw new JsonProfileError("leading_zero", line);
        }
        while (isDigit(text, i)) {
            i += 1;
        }
        boolean isFloat = false;
        if (at(text, i) == '.') {
            isFloat = true;
            i += 1;
            if (!isDigit(text, i)) {
                throw new JsonProfileError("syntax", line);
            }
            while (isDigit(text, i)) {
                i += 1;
            }
        }
        if (at(text, i) == 'e' || at(text, i) == 'E') {
            isFloat = true;
            i += 1;
            if (at(text, i) == '+' || at(text, i) == '-') {
                i += 1;
            }
            if (!isDigit(text, i)) {
                throw new JsonProfileError("syntax", line);
            }
            while (isDigit(text, i)) {
                i += 1;
            }
        }
        String lexeme = text.substring(start, i);
        if (isFloat) {
            if (Double.isInfinite(Double.parseDouble(lexeme))) {
                throw new JsonProfileError("infinity", line);
            }
        } else if (lexeme.length() - (lexeme.startsWith("-") ? 1 : 0) > MAX_INT_STR_DIGITS) {
            throw new JsonProfileError("integer_digit_limit", line);
        }
        return i;
    }

    private enum Expect { VALUE, FIRST, MEMBER, KEY, FIRST_KEY, COLON, AFTER, END }

    private static final String[][] WORDS = {
        {"true", ""}, {"false", ""}, {"null", ""}, {"NaN", "nan"}, {"Infinity", "infinity"},
    };

    /** Throw {@link JsonProfileError} when {@code text} (one JSON document, or one JSON Lines line when
     * {@code jsonlLine} gives its number) is outside the profile. Iterative, so depth costs no recursion.
     * Mirrors Python's {@code safe_json.check_profile}. */
    public static void checkProfile(String text, Integer jsonlLine) {
        int line = jsonlLine == null ? 1 : jsonlLine;
        int n = text.length();
        int i = 0;
        // Each open container: an object's keys seen, or null for an array.
        java.util.ArrayDeque<java.util.Optional<java.util.Set<String>>> stack = new java.util.ArrayDeque<>();
        Expect expect = Expect.VALUE;
        boolean sawCr = false;
        while (true) {
            while (i < n && " \t\n\r".indexOf(text.charAt(i)) >= 0) {
                if (text.charAt(i) == '\n') {
                    line += 1;
                } else if (text.charAt(i) == '\r') {
                    sawCr = true;
                }
                i += 1;
            }
            if (i >= n) {
                if (expect != Expect.END) {
                    throw new JsonProfileError("syntax", line);
                }
                return;
            }
            char c = text.charAt(i);
            if (c == '﻿') {
                throw new JsonProfileError("bom", line);
            }
            if (c == '/') {
                throw new JsonProfileError("comment", line);
            }
            if (jsonlLine != null && LINE_BREAKS.indexOf(c) >= 0) {
                throw new JsonProfileError("jsonl_line_split", line);
            }
            if (expect == Expect.END) {
                throw new JsonProfileError(jsonlLine != null && sawCr ? "jsonl_line_split" : "extra_value", line);
            }
            java.util.Optional<java.util.Set<String>> top = stack.peek();
            boolean topIsArray = top != null && top.isEmpty();
            if (expect == Expect.VALUE || expect == Expect.FIRST || expect == Expect.MEMBER) {
                if (c == ']' && expect == Expect.FIRST && topIsArray) {
                    stack.pop();
                    i += 1;
                } else if (c == ']' || c == '}') {
                    throw new JsonProfileError(
                            expect == Expect.VALUE && !stack.isEmpty() ? "trailing_comma" : "syntax", line);
                } else if (c == '[' || c == '{') {
                    stack.push(c == '[' ? java.util.Optional.empty() : java.util.Optional.of(new java.util.HashSet<>()));
                    if (stack.size() > MAX_JSON_DEPTH) {
                        throw new JsonProfileError("depth_over_limit", line);
                    }
                    i += 1;
                    expect = c == '[' ? Expect.FIRST : Expect.FIRST_KEY;
                    continue;
                } else if (c == '"') {
                    i = scanString(text, i, line, new StringBuilder());
                } else if (c == '-' || (c >= '0' && c <= '9')) {
                    i = scanNumber(text, i, line);
                } else if (c == '+') {
                    throw new JsonProfileError("leading_plus", line);
                } else {
                    String[] word = null;
                    for (String[] w : WORDS) {
                        if (text.startsWith(w[0], i)) {
                            word = w;
                            break;
                        }
                    }
                    if (word == null) {
                        throw new JsonProfileError("syntax", line);
                    }
                    if (!word[1].isEmpty()) {
                        throw new JsonProfileError(word[1], line);
                    }
                    i += word[0].length();
                }
                expect = stack.isEmpty() ? Expect.END : Expect.AFTER;
            } else if (expect == Expect.KEY || expect == Expect.FIRST_KEY) {
                if (c == '}' && expect == Expect.FIRST_KEY) {
                    stack.pop();
                    i += 1;
                    expect = stack.isEmpty() ? Expect.END : Expect.AFTER;
                } else if (c == '}') {
                    throw new JsonProfileError("trailing_comma", line);
                } else if (c == '"') {
                    StringBuilder key = new StringBuilder();
                    i = scanString(text, i, line, key);
                    if (!top.orElseThrow().add(key.toString())) {
                        throw new JsonProfileError("duplicate_key", line);
                    }
                    expect = Expect.COLON;
                } else {
                    throw new JsonProfileError("syntax", line);
                }
            } else if (expect == Expect.COLON) {
                if (c != ':') {
                    throw new JsonProfileError("syntax", line);
                }
                i += 1;
                expect = Expect.MEMBER;
            } else {
                if (c == ',') {
                    i += 1;
                    expect = topIsArray ? Expect.VALUE : Expect.KEY;
                } else if ((c == ']' && topIsArray) || (c == '}' && top != null && top.isPresent())) {
                    stack.pop();
                    i += 1;
                    expect = stack.isEmpty() ? Expect.END : Expect.AFTER;
                } else {
                    throw new JsonProfileError("syntax", line);
                }
            }
        }
    }

    /** The index of the first byte of {@code raw} that does not start a well-formed UTF-8 sequence
     * (overlong forms, encoded surrogates and code points past U+10FFFF included), or -1. */
    private static int invalidUtf8At(byte[] raw, int from) {
        int i = from;
        while (i < raw.length) {
            int b = raw[i] & 0xFF;
            if (b < 0x80) {
                i += 1;
                continue;
            }
            int need;
            int lo = 0x80;
            int hi = 0xBF;
            if (b >= 0xC2 && b <= 0xDF) {
                need = 1;
            } else if (b >= 0xE0 && b <= 0xEF) {
                need = 2;
                if (b == 0xE0) lo = 0xA0;
                if (b == 0xED) hi = 0x9F;
            } else if (b >= 0xF0 && b <= 0xF4) {
                need = 3;
                if (b == 0xF0) lo = 0x90;
                if (b == 0xF4) hi = 0x8F;
            } else {
                return i;
            }
            for (int k = 1; k <= need; k++) {
                if (i + k >= raw.length) {
                    return i;
                }
                int c = raw[i + k] & 0xFF;
                if (c < (k == 1 ? lo : 0x80) || c > (k == 1 ? hi : 0xBF)) {
                    return i;
                }
            }
            i += need + 1;
        }
        return -1;
    }

    /** Whether {@code raw} starts with a UTF-8 byte-order mark. */
    public static boolean hasBom(byte[] raw) {
        return raw.length >= 3 && (raw[0] & 0xFF) == 0xEF && (raw[1] & 0xFF) == 0xBB && (raw[2] & 0xFF) == 0xBF;
    }

    /** {@code raw} without one leading byte-order mark (SPEC 8.7: an input's mark is ignored). */
    public static byte[] stripBom(byte[] raw) {
        return hasBom(raw) ? java.util.Arrays.copyOfRange(raw, 3, raw.length) : raw;
    }

    /** {@code raw} as text under the profile's encoding rules: one leading mark ignored or refused, then
     * strict UTF-8. Mirrors Python's {@code safe_json._decode}. */
    public static String decodeProfile(byte[] raw, Bom bom) {
        int start = 0;
        if (hasBom(raw)) {
            if (bom == Bom.REFUSE) {
                throw new JsonProfileError("bom", 1);
            }
            start = 3;
        }
        int bad = invalidUtf8At(raw, start);
        if (bad >= 0) {
            int lines = 1;
            for (int k = start; k < bad; k++) {
                if (raw[k] == '\n') lines += 1;
            }
            throw new JsonProfileError("invalid_utf8", lines);
        }
        return new String(raw, start, raw.length - start, StandardCharsets.UTF_8);
    }

    /** One JSON document under the profile; integers and floats stay apart by syntax (IntNode/BigIntegerNode
     * vs DecimalNode). Mirrors Python's {@code safe_json.read_json}. */
    public static JsonNode readJson(byte[] raw, Bom bom) {
        String text = decodeProfile(raw, bom);
        checkProfile(text, null);
        return parse(text);
    }

    /** One JSON Lines line ({@code line} is its number in the file) under the profile. */
    public static JsonNode readJsonlLine(String text, int line) {
        checkProfile(text, line);
        return parse(text);
    }

    /** One JSON Lines line as bytes: its 1-based number and its bytes. */
    public record ByteLine(int number, byte[] bytes) {}

    /** The non-blank JSON Lines of {@code raw} (its mark already handled): LF alone ends a line, a CRLF's
     * CR is dropped, a line holding only spaces and tabs is blank. Mirrors {@code safe_json.jsonl_records}. */
    public static List<ByteLine> jsonlByteRecords(byte[] raw) {
        List<ByteLine> out = new ArrayList<>();
        int start = 0;
        int number = 1;
        for (int i = 0; i <= raw.length; i++) {
            if (i == raw.length || raw[i] == '\n') {
                int end = i;
                if (end > start && raw[end - 1] == '\r') end -= 1;
                boolean blank = true;
                for (int k = start; k < end; k++) {
                    if (raw[k] != ' ' && raw[k] != '\t') {
                        blank = false;
                        break;
                    }
                }
                if (!blank) {
                    out.add(new ByteLine(number, java.util.Arrays.copyOfRange(raw, start, end)));
                }
                start = i + 1;
                number += 1;
            }
        }
        return out;
    }

    /** Data shipped inside the package, under the same profile with a mark refused; a failure is a
     * build defect. */
    public static JsonNode readBundled(byte[] raw) {
        return readJson(raw, Bom.REFUSE);
    }

    /** Parse {@code text} as YAML's JSON-compatible tree with the given mapper (Yaml.java's reader), kept
     * here so this file is the one raw-parser call site. */
    static JsonNode readTreeWith(ObjectMapper mapper, String text) throws JsonProcessingException {
        return mapper.readTree(text);
    }

    /** Whether {@code value} read as a JSON integer (SPEC 8.7: the form is by syntax). */
    public static boolean isJsonInteger(JsonNode value) {
        return value != null && value.isIntegralNumber();
    }

    /** The refusal sentence for {@code noun}: the profile rule when {@code exc} (or its cause) names one,
     * else {@code <noun> is not valid JSON.} Mirrors Python's {@code safe_json.not_valid}. */
    public static String notValid(String noun, Throwable exc) {
        Throwable cause = exc instanceof JsonProfileError ? exc : exc.getCause();
        return cause instanceof JsonProfileError
                ? noun + " is " + cause.getMessage() + "."
                : noun + " is not valid JSON.";
    }

    /** The one rendering of a JSON number in reason and report text (SPEC 8.7): an integer as its digits,
     * a float as the shortest decimal that reads back to the same double, {@code .0} when integral,
     * exponent form {@code d.ddde+XX} at 1e16 and above or below 1e-4, {@code -0.0} for negative zero
     * (Python's {@code repr}). */
    public static String renderNumber(JsonNode value) {
        if (value.isIntegralNumber()) {
            return value.bigIntegerValue().toString();
        }
        return renderFloat(value.isBigDecimal() ? value.decimalValue().doubleValue() : value.doubleValue());
    }

    static String renderFloat(double v) {
        if (Double.isNaN(v)) {
            return "nan";
        }
        if (Double.isInfinite(v)) {
            // Python's repr of a literal past the double range (1e400 reads as inf).
            return v > 0 ? "inf" : "-inf";
        }
        if (v == 0.0 && 1.0 / v < 0) {
            return "-0.0";
        }
        if (v == 0.0) {
            return "0.0";
        }
        String sign = v < 0 ? "-" : "";
        double abs = Math.abs(v);
        // The fewest significant digits, correctly rounded from the exact binary value, that read back to
        // the same double (Python's repr; Double.toString gives 4.9E-324 where repr gives 5e-324).
        java.math.BigDecimal exactAbs = new java.math.BigDecimal(abs);
        java.math.BigDecimal shortest = exactAbs;
        for (int p = 1; p <= 17; p++) {
            shortest = exactAbs.round(new java.math.MathContext(p, java.math.RoundingMode.HALF_EVEN));
            if (shortest.doubleValue() == abs) {
                break;
            }
        }
        shortest = shortest.stripTrailingZeros();
        String digits = shortest.unscaledValue().toString();
        int e = digits.length() - shortest.scale() - 1;
        if (abs >= 1e16 || abs < 1e-4) {
            String frac = digits.length() > 1 ? "." + digits.substring(1) : "";
            return sign + digits.charAt(0) + frac + "e" + (e < 0 ? "-" : "+")
                    + String.format("%02d", Math.abs(e));
        }
        if (e < 0) {
            return sign + "0." + "0".repeat(-e - 1) + digits;
        }
        String intPart = digits.length() > e + 1 ? digits.substring(0, e + 1) : digits + "0".repeat(e + 1 - digits.length());
        String fracPart = digits.length() > e + 1 ? digits.substring(e + 1) : "";
        return sign + intPart + "." + (fracPart.isEmpty() ? "0" : fracPart);
    }

    /** A JSON value as reason text: numbers by {@link #renderNumber}, literals as JSON writes them, a
     * string JSON-quoted (non-ASCII kept), a container as compact JSON with its keys sorted. Mirrors
     * Python's {@code render_value}. */
    public static String renderValue(JsonNode value) {
        if (value == null || value.isNull()) {
            return "null";
        }
        if (value.isBoolean()) {
            return value.booleanValue() ? "true" : "false";
        }
        if (value.isNumber()) {
            return renderNumber(value);
        }
        if (value.isTextual()) {
            return quoteKeepUnicode(value.textValue());
        }
        StringBuilder out = new StringBuilder();
        if (value.isArray()) {
            out.append('[');
            for (int k = 0; k < value.size(); k++) {
                if (k > 0) out.append(',');
                out.append(renderValue(value.get(k)));
            }
            return out.append(']').toString();
        }
        List<String> keys = new ArrayList<>();
        value.fieldNames().forEachRemaining(keys::add);
        keys.sort(Json::byteCompare);
        out.append('{');
        boolean first = true;
        for (String key : keys) {
            if (!first) out.append(',');
            first = false;
            out.append(quoteKeepUnicode(key)).append(':').append(renderValue(value.get(key)));
        }
        return out.append('}').toString();
    }

    /** {@code json.dumps(s, ensure_ascii=False)}. */
    private static String quoteKeepUnicode(String text) {
        StringBuilder out = new StringBuilder("\"");
        for (int k = 0; k < text.length(); k++) {
            char ch = text.charAt(k);
            switch (ch) {
                case '"' -> out.append("\\\"");
                case '\\' -> out.append("\\\\");
                case '\b' -> out.append("\\b");
                case '\t' -> out.append("\\t");
                case '\n' -> out.append("\\n");
                case '\f' -> out.append("\\f");
                case '\r' -> out.append("\\r");
                default -> {
                    if (ch < 0x20) {
                        out.append(String.format("\\u%04x", (int) ch));
                    } else {
                        out.append(ch);
                    }
                }
            }
        }
        return out.append('"').toString();
    }

    /** Equality of two JSON values (SPEC 8.7): numbers by exact value, values of different JSON types never
     * equal ({@code 1} is not {@code true}), arrays element-wise, objects by key set and values. Mirrors
     * Python's {@code json_equal}. */
    public static boolean jsonEqual(JsonNode left, JsonNode right) {
        if (left.isNumber() || right.isNumber()) {
            if (!left.isNumber() || !right.isNumber()) {
                return false;
            }
            if (left.isIntegralNumber() || right.isIntegralNumber()) {
                java.math.BigDecimal l = left.isIntegralNumber()
                        ? new java.math.BigDecimal(left.bigIntegerValue()) : exact(left);
                java.math.BigDecimal r = right.isIntegralNumber()
                        ? new java.math.BigDecimal(right.bigIntegerValue()) : exact(right);
                return l.compareTo(r) == 0;
            }
            return asDouble(left) == asDouble(right);
        }
        if (left.getNodeType() != right.getNodeType()) {
            return false;
        }
        if (left.isArray()) {
            if (left.size() != right.size()) return false;
            for (int k = 0; k < left.size(); k++) {
                if (!jsonEqual(left.get(k), right.get(k))) return false;
            }
            return true;
        }
        if (left.isObject()) {
            if (left.size() != right.size()) return false;
            var names = left.fieldNames();
            while (names.hasNext()) {
                String key = names.next();
                if (!right.has(key) || !jsonEqual(left.get(key), right.get(key))) return false;
            }
            return true;
        }
        return left.equals(right);
    }

    private static double asDouble(JsonNode value) {
        return value.isBigDecimal() ? value.decimalValue().doubleValue() : value.doubleValue();
    }

    /** A float node's double as an exact decimal (Python compares an int with a float's exact value). */
    private static java.math.BigDecimal exact(JsonNode value) {
        return new java.math.BigDecimal(asDouble(value));
    }
}
