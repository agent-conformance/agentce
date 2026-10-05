package org.agentce;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.JsonNodeFactory;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.io.IOException;
import java.io.StringReader;
import java.io.UncheckedIOException;
import java.math.BigInteger;
import java.nio.charset.CharacterCodingException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import org.yaml.snakeyaml.LoaderOptions;
import org.yaml.snakeyaml.Yaml;
import org.yaml.snakeyaml.nodes.MappingNode;
import org.yaml.snakeyaml.nodes.Node;
import org.yaml.snakeyaml.nodes.NodeTuple;
import org.yaml.snakeyaml.nodes.ScalarNode;
import org.yaml.snakeyaml.nodes.SequenceNode;
import org.yaml.snakeyaml.nodes.Tag;

/**
 * {@code agentce readiness} (SPEC §13.3.4): the report-readiness verdict (READY / READY WITH
 * LIMITATIONS / NOT READY) and the deviation-register linter it applies when a register is given. A
 * byte-for-byte port of the Python reference's {@code readiness.py} ({@code compute_readiness}, {@code
 * deviation_lint}, {@code normalize_deviation_dates}, {@code parse_date}), mirroring {@code
 * readiness.ts}'s TypeScript port and {@code Diff.java}'s shape as the compute seam {@code Cli.java}'s
 * {@code cmdReadiness} wires up.
 */
public final class Readiness {
    private Readiness() {}

    public static final String READY = "READY";
    public static final String READY_WITH_LIMITATIONS = "READY WITH LIMITATIONS";
    public static final String NOT_READY = "NOT READY";

    /** Default maximum deviation lifetime in days (SPEC §13.3.4; a catalog may set its own). */
    public static final int DEFAULT_MAX_DEVIATION_DAYS = 180;

    /** Integrity statuses that block a report from being signed (SPEC §13.3.4). */
    private static final Set<String> GAP_INTEGRITY_STATUSES = Set.of("failed", "gap", "reordered");
    /** The integrity and coverage family whose findings can never be accepted as a deviation (§13.3.4). */
    private static final String INT_FAMILY = "INT";
    private static final List<String> DEVIATION_FIELDS =
            List.of("rationale", "compensating_control", "owner", "approver", "granted", "expiry");

    /** {@code compute_readiness}'s return shape (SPEC §13.3.4 stage 4). */
    public record Verdict(String verdict, List<String> reasons, List<String> limitations) {}

    // --- pyStr/pyRepr/pyTruthy/pyGet/pyEquals over JsonNode (this module's own copies, matching
    // readiness.ts's identically-named helpers character for character) ---------------------------

    /** Python's {@code str()} for a value this module ever prints: {@code None}/{@code True}/{@code
     * False} spelled Python's way, else the plain text. */
    public static String pyStr(JsonNode value) {
        if (value == null || value.isNull() || value.isMissingNode()) {
            return "None";
        }
        if (value.isBoolean()) {
            return value.booleanValue() ? "True" : "False";
        }
        if (value.isTextual()) {
            return value.textValue();
        }
        if (value.isIntegralNumber()) {
            return value.bigIntegerValue().toString();
        }
        if (value.isNumber()) {
            double d = value.doubleValue();
            if (Double.isNaN(d) || Double.isInfinite(d)) {
                // decimalValue() throws for NaN/Infinity (BigDecimal can't represent them). The
                // exact text here ("NaN"/"Infinity" vs Python's "nan"/"inf") is the same already-
                // accepted number-rendering divergence pyStr has for other float forms; this only
                // closes the crash.
                return Double.toString(d);
            }
            return value.decimalValue().toString();
        }
        return value.toString();
    }

    /** A code point Python's {@code str.isprintable()} would call printable: {@code U+0020} itself, or
     * a code point whose Unicode general category is neither {@code C*} (control/format/surrogate/
     * private-use/unassigned) nor {@code Z*} other than {@code U+0020}. */
    private static boolean isPrintableCodePoint(int codePoint) {
        if (codePoint == 0x20) {
            return true;
        }
        return switch (Character.getType(codePoint)) {
            case Character.CONTROL, Character.FORMAT, Character.SURROGATE, Character.PRIVATE_USE,
                    Character.UNASSIGNED, Character.SPACE_SEPARATOR, Character.LINE_SEPARATOR,
                    Character.PARAGRAPH_SEPARATOR -> false;
            default -> true;
        };
    }

    /** Mirrors Python's {@code repr()} for the narrow set of types this module ever feeds it: a
     * string (quote choice, backslash/control-char escaping, the same as {@code readiness.ts}'s
     * {@code pyRepr}), else {@link #pyStr}. */
    static String pyRepr(String value) {
        return pyRepr(Json.nodes().textNode(value));
    }

    public static String pyRepr(JsonNode value) {
        if (value == null || !value.isTextual()) {
            return pyStr(value);
        }
        String s = value.textValue();
        boolean hasSingle = s.indexOf('\'') >= 0;
        boolean hasDouble = s.indexOf('"') >= 0;
        char quote = hasSingle && !hasDouble ? '"' : '\'';
        StringBuilder body = new StringBuilder();
        int i = 0;
        while (i < s.length()) {
            int cp = s.codePointAt(i);
            int charCount = Character.charCount(cp);
            if (cp == '\\') {
                body.append("\\\\");
            } else if (cp == quote) {
                body.append('\\').append(quote);
            } else if (cp == '\t') {
                body.append("\\t");
            } else if (cp == '\n') {
                body.append("\\n");
            } else if (cp == '\r') {
                body.append("\\r");
            } else if (isPrintableCodePoint(cp)) {
                body.appendCodePoint(cp);
            } else if (cp <= 0xff) {
                body.append(String.format("\\x%02x", cp));
            } else if (cp <= 0xffff) {
                body.append(String.format("\\u%04x", cp));
            } else {
                body.append(String.format("\\U%08x", cp));
            }
            i += charCount;
        }
        return quote + body.toString() + quote;
    }

    /** Python's implicit falsiness over a {@link JsonNode}: absent/{@code null}/{@code false}/{@code
     * 0}/{@code ""}/an empty array/an empty object. */
    static boolean pyTruthy(JsonNode value) {
        if (value == null || value.isNull() || value.isMissingNode()) {
            return false;
        }
        if (value.isBoolean()) {
            return value.booleanValue();
        }
        if (value.isTextual()) {
            return !value.textValue().isEmpty();
        }
        if (value.isIntegralNumber()) {
            return value.bigIntegerValue().signum() != 0;
        }
        if (value.isNumber()) {
            // doubleValue(), not decimalValue() (see pyStr): Python float truthiness, NaN is truthy.
            return value.doubleValue() != 0.0;
        }
        if (value.isArray() || value.isObject()) {
            return value.size() > 0;
        }
        return true;
    }

    /** Python's {@code dict.get(key, default)}: substitutes {@code default} only when {@code key} is
     * <b>absent</b>, never when it is present with a falsy or {@code null} value. */
    private static JsonNode pyGet(JsonNode obj, String key, JsonNode defaultValue) {
        return obj.has(key) ? obj.get(key) : defaultValue;
    }

    /** Python's {@code ==} for a YAML-parsed value: recursive structural equality over strings,
     * numbers, booleans, {@code null}, arrays, and plain mappings -- {@code owner}/{@code approver} can
     * hold any of these on hostile input, not only a string. */
    private static boolean pyEquals(JsonNode a, JsonNode b) {
        if (a.equals(b)) {
            return true;
        }
        if (a.isArray() && b.isArray()) {
            if (a.size() != b.size()) {
                return false;
            }
            for (int i = 0; i < a.size(); i++) {
                if (!pyEquals(a.get(i), b.get(i))) {
                    return false;
                }
            }
            return true;
        }
        if (a.isObject() && b.isObject()) {
            if (a.size() != b.size()) {
                return false;
            }
            var fields = a.fieldNames();
            while (fields.hasNext()) {
                String k = fields.next();
                if (!b.has(k) || !pyEquals(a.get(k), b.get(k))) {
                    return false;
                }
            }
            return true;
        }
        if (a.isNumber() && b.isNumber()) {
            if (a.isIntegralNumber() && b.isIntegralNumber()) {
                return a.bigIntegerValue().equals(b.bigIntegerValue());
            }
            // doubleValue() (see pyStr): primitive `==` is Python's float equality, NaN != NaN.
            return a.doubleValue() == b.doubleValue();
        }
        return false;
    }

    // --- Deviation-register YAML loading (SnakeYAML compose(), PyYAML-matching implicit resolution) --

    /** A YAML scalar SnakeYAML's own resolver tagged {@code tag:yaml.org,2002:timestamp} -- the raw,
     * unrewritten matched text, never a Java {@code Date}, so {@link #pythonizeTimestamp} can render it
     * exactly as PyYAML's own reference would (Jackson's/SnakeYAML's high-level constructors instead
     * preserve the source text verbatim, a third, wrong rendering next to Python's {@code
     * str(datetime)} whenever the value carries a time component). */
    private record PyyamlTimestamp(String text) {}

    private static final Set<String> PYYAML_BOOL_TRUE = Set.of("yes", "true", "on");

    /** PyYAML's own {@code Resolver}, {@code tag:yaml.org,2002:int} implicit-resolution regex
     * ({@code yaml/resolver.py}), copied verbatim -- SnakeYAML's own {@code Tag.INT} resolution
     * disagrees with this (confirmed against real SnakeYAML 2.3 and PyYAML: e.g. {@code 0o17} is an
     * int to neither, but SnakeYAML also fails to tag {@code 0x1F}/{@code 0b101} as {@code Tag.INT}
     * the way PyYAML's grammar does), so this engine tests the raw scalar text against its own
     * PyYAML-ported regex instead of trusting SnakeYAML's resolved {@link Tag}, exactly like
     * {@code readiness.ts}'s {@code PYYAML_INT_RESOLVE_RE}. */
    private static final java.util.regex.Pattern PYYAML_INT_RESOLVE_RE = java.util.regex.Pattern.compile(
            "^(?:[-+]?0b[0-1_]+|[-+]?0[0-7_]+|[-+]?(?:0|[1-9][0-9_]*)"
                    + "|[-+]?0x[0-9a-fA-F_]+|[-+]?[1-9][0-9_]*(?::[0-5]?[0-9])+)$");

    /** PyYAML's own {@code Resolver}, {@code tag:yaml.org,2002:float} implicit-resolution regex
     * ({@code yaml/resolver.py}), copied verbatim -- same rationale as {@link #PYYAML_INT_RESOLVE_RE}. */
    private static final java.util.regex.Pattern PYYAML_FLOAT_RESOLVE_RE = java.util.regex.Pattern.compile(
            "^(?:[-+]?[0-9][0-9_]*\\.[0-9_]*(?:[eE][-+][0-9]+)?"
                    + "|\\.[0-9][0-9_]*(?:[eE][-+][0-9]+)?"
                    + "|[-+]?[0-9][0-9_]*(?::[0-5]?[0-9])+\\.[0-9_]*"
                    + "|[-+]?\\.(?:inf|Inf|INF)|\\.(?:nan|NaN|NAN))$");

    /** A cheap first-character gate before running either PyYAML resolve regex against a plain
     * scalar: both patterns above only ever match a string starting with a digit, {@code +}, {@code
     * -}, or {@code .} -- every other plain scalar (a control id, a reason string, ...) can skip both
     * regex matches entirely. Purely an optimization; {@code true} is not itself a guarantee of a
     * match, only a necessary precondition. */
    private static boolean couldBeNumeric(String text) {
        if (text.isEmpty()) {
            return false;
        }
        char c = text.charAt(0);
        return (c >= '0' && c <= '9') || c == '+' || c == '-' || c == '.';
    }

    /** PyYAML's {@code construct_yaml_int} ({@code yaml/constructor.py:237-263}), copied verbatim:
     * strip {@code _}, take the sign, then dispatch on the {@code 0b}/{@code 0x}/leading-{@code
     * 0}/colon-sexagesimal/plain-decimal forms in that exact order (order matters, since these
     * prefixes overlap textually -- {@code 0b101} starts with {@code 0} too). */
    private static BigInteger pyyamlConstructInt(String data) {
        String value = data.replace("_", "");
        int sign = 1;
        if (value.charAt(0) == '-') {
            sign = -1;
        }
        if (value.charAt(0) == '+' || value.charAt(0) == '-') {
            value = value.substring(1);
        }
        BigInteger magnitude;
        if (value.equals("0")) {
            return BigInteger.ZERO;
        } else if (value.startsWith("0b")) {
            magnitude = new BigInteger(value.substring(2), 2);
        } else if (value.startsWith("0x")) {
            magnitude = new BigInteger(value.substring(2), 16);
        } else if (value.charAt(0) == '0') {
            magnitude = new BigInteger(value, 8);
        } else if (value.contains(":")) {
            String[] parts = value.split(":");
            BigInteger out = BigInteger.ZERO;
            BigInteger base = BigInteger.ONE;
            for (int i = parts.length - 1; i >= 0; i--) {
                out = out.add(BigInteger.valueOf(Long.parseLong(parts[i])).multiply(base));
                base = base.multiply(BigInteger.valueOf(60));
            }
            magnitude = out;
        } else {
            magnitude = new BigInteger(value);
        }
        return sign < 0 ? magnitude.negate() : magnitude;
    }

    /** PyYAML's {@code construct_yaml_float} ({@code yaml/constructor.py:270-292}), copied verbatim:
     * lower-case, strip {@code _}, take the sign, then dispatch on {@code .inf}/{@code .nan}/
     * colon-sexagesimal/plain-decimal. */
    private static double pyyamlConstructFloat(String data) {
        String value = data.replace("_", "").toLowerCase(Locale.ROOT);
        int sign = 1;
        if (value.charAt(0) == '-') {
            sign = -1;
        }
        if (value.charAt(0) == '+' || value.charAt(0) == '-') {
            value = value.substring(1);
        }
        if (value.equals(".inf")) {
            return sign * Double.POSITIVE_INFINITY;
        }
        if (value.equals(".nan")) {
            return Double.NaN;
        }
        if (value.contains(":")) {
            String[] parts = value.split(":");
            double out = 0;
            double base = 1;
            for (int i = parts.length - 1; i >= 0; i--) {
                out += Double.parseDouble(parts[i]) * base;
                base *= 60;
            }
            return sign * out;
        }
        return sign * Double.parseDouble(value);
    }

    /** The deepest nesting the register loader composes, the same cap TypeScript's js-yaml applies by
     * default; PyYAML's own limit is the interpreter's recursion depth, so a deeper register is refused
     * as nested too deeply in every engine, only at a different depth. */
    private static final int DEVIATION_REGISTER_MAX_DEPTH = 100;

    /** The tags PyYAML's {@code SafeLoader} constructs (scalars resolve implicitly to these; a mapping
     * or sequence carries its default tag). Any other tag, such as {@code !!python/object:os.system},
     * is refused, as {@code SafeLoader} refuses it. */
    private static final Set<Tag> SAFE_TAGS = Set.of(
            Tag.STR, Tag.NULL, Tag.BOOL, Tag.INT, Tag.FLOAT, Tag.TIMESTAMP, Tag.MERGE, Tag.MAP, Tag.SEQ);

    /** Composes {@code node}'s raw YAML value tree. SnakeYAML's own {@code compose()} resolves each
     * scalar's tag with regexes byte-identical to PyYAML's own resolver for {@code null}/{@code
     * bool}/{@code timestamp} -- confirmed against SnakeYAML 2.3 -- so those three trust SnakeYAML's
     * resolved {@link Tag} directly. {@code int}/{@code float} do NOT agree (confirmed the same way:
     * SnakeYAML disagrees with PyYAML's YAML-1.1 grammar on several forms), so those two instead test
     * the raw scalar text against {@link #PYYAML_INT_RESOLVE_RE}/{@link #PYYAML_FLOAT_RESOLVE_RE}
     * directly, exactly like TypeScript's js-yaml custom types. A quoted scalar of any style is always
     * a plain {@link String} regardless of its text (SnakeYAML's own {@code isPlain()}, exactly
     * PyYAML's "implicit resolution applies only to the plain scalar style" rule). Never touches
     * Jackson's own YAML reader ({@link Yaml}, this engine's other loader) for this file. */
    private static Object composeValue(Node node) {
        if (!SAFE_TAGS.contains(node.getTag())) {
            throw new IllegalArgumentException(
                    "could not determine a constructor for the tag '" + node.getTag().getValue() + "'");
        }
        if (node instanceof ScalarNode scalar) {
            String text = scalar.getValue();
            if (!scalar.isPlain()) {
                return text;
            }
            Tag tag = scalar.getTag();
            if (tag.equals(Tag.NULL)) {
                return null;
            }
            if (tag.equals(Tag.BOOL)) {
                return PYYAML_BOOL_TRUE.contains(text.toLowerCase(Locale.ROOT));
            }
            if (tag.equals(Tag.TIMESTAMP)) {
                return new PyyamlTimestamp(text);
            }
            if (couldBeNumeric(text)) {
                if (PYYAML_INT_RESOLVE_RE.matcher(text).matches()) {
                    return pyyamlConstructInt(text);
                }
                if (PYYAML_FLOAT_RESOLVE_RE.matcher(text).matches()) {
                    return pyyamlConstructFloat(text);
                }
            }
            return text;
        }
        if (node instanceof SequenceNode seq) {
            List<Object> out = new ArrayList<>();
            for (Node child : seq.getValue()) {
                out.add(composeValue(child));
            }
            return out;
        }
        if (node instanceof MappingNode map) {
            Map<String, Object> out = new LinkedHashMap<>();
            for (NodeTuple tuple : flattenMapping(map)) {
                out.put(String.valueOf(composeValue(tuple.getKeyNode())), composeValue(tuple.getValueNode()));
            }
            return out;
        }
        throw new IllegalStateException("unsupported YAML node: " + node.getNodeId());
    }

    /** PyYAML's own {@code flatten_mapping} ({@code yaml/constructor.py}), ported at the {@link Node}
     * level so the ordinary compose-then-fold-into-a-{@link LinkedHashMap} step above (last {@code put}
     * wins, exactly like Python's {@code dict[key] = value} during construction) reproduces merge-key
     * precedence for free: a {@code <<: *anchor} (or {@code <<: [*a, *b, ...]}) key is replaced by its
     * source mapping's own (recursively flattened) pairs, placed <b>before</b> this mapping's own
     * explicit pairs -- so an explicit key always overrides a merged one, and for a sequence of
     * sources, an earlier source overrides a later one (mirrors {@code flatten_mapping}'s own
     * reversed-then-extended merge-list construction). A quoted {@code "<<"} scalar is not a merge key
     * -- SnakeYAML's {@code Resolver} only tags a <i>plain</i> {@code <<} scalar {@link Tag#MERGE},
     * exactly PyYAML's own "implicit resolution applies only to the plain style" rule. */
    private static List<NodeTuple> flattenMapping(MappingNode map) {
        List<NodeTuple> merged = new ArrayList<>();
        List<NodeTuple> own = new ArrayList<>();
        for (NodeTuple tuple : map.getValue()) {
            Node keyNode = tuple.getKeyNode();
            if (keyNode instanceof ScalarNode keyScalar && keyScalar.getTag().equals(Tag.MERGE)) {
                Node valueNode = tuple.getValueNode();
                if (valueNode instanceof MappingNode sourceMap) {
                    merged.addAll(flattenMapping(sourceMap));
                } else if (valueNode instanceof SequenceNode seq) {
                    List<List<NodeTuple>> submerge = new ArrayList<>();
                    for (Node subnode : seq.getValue()) {
                        if (!(subnode instanceof MappingNode subMap)) {
                            throw new IllegalStateException("while constructing a mapping: expected a mapping for merging, but found "
                                    + subnode.getNodeId());
                        }
                        submerge.add(flattenMapping(subMap));
                    }
                    for (int i = submerge.size() - 1; i >= 0; i--) {
                        merged.addAll(submerge.get(i));
                    }
                } else {
                    throw new IllegalStateException("while constructing a mapping: expected a mapping or a list of mappings "
                            + "for merging, but found " + valueNode.getNodeId());
                }
            } else {
                own.add(tuple);
            }
        }
        merged.addAll(own);
        return merged;
    }

    /** {@link #pyTruthy}, over the raw {@link #composeValue} object graph (before conversion to {@link
     * JsonNode}) -- used only by {@link #loadDeviationRegister}'s own top-level shape check. */
    private static boolean pyTruthyRaw(Object value) {
        if (value == null || Boolean.FALSE.equals(value)) {
            return false;
        }
        if (value instanceof String s) {
            return !s.isEmpty();
        }
        if (value instanceof BigInteger bi) {
            return bi.signum() != 0;
        }
        if (value instanceof Double d) {
            return d != 0.0;
        }
        if (value instanceof List<?> l) {
            return !l.isEmpty();
        }
        if (value instanceof Map<?, ?> m) {
            return !m.isEmpty();
        }
        return true;
    }

    /** {@code value} (a plain {@link String}/{@link Boolean}/{@link BigInteger}/{@link Double}/{@code
     * null}/{@link List}/{@link Map}, per {@link #composeValue}) rendered as a {@link JsonNode}. Called
     * only after {@link #normalizeDeviationDates} has rewritten every {@link PyyamlTimestamp} marker to
     * a plain string, so no marker ever reaches this method. */
    private static JsonNode toJsonNode(Object value) {
        JsonNodeFactory f = Json.nodes();
        if (value == null) {
            return f.nullNode();
        }
        if (value instanceof String s) {
            return f.textNode(s);
        }
        if (value instanceof Boolean b) {
            return f.booleanNode(b);
        }
        if (value instanceof BigInteger bi) {
            return f.numberNode(bi);
        }
        if (value instanceof Double d) {
            return f.numberNode(d);
        }
        if (value instanceof List<?> list) {
            ArrayNode arr = f.arrayNode();
            for (Object v : list) {
                arr.add(toJsonNode(v));
            }
            return arr;
        }
        if (value instanceof Map<?, ?> map) {
            ObjectNode obj = f.objectNode();
            for (Map.Entry<?, ?> e : map.entrySet()) {
                obj.set(String.valueOf(e.getKey()), toJsonNode(e.getValue()));
            }
            return obj;
        }
        throw new IllegalStateException("cannot convert " + value.getClass() + " to JSON");
    }

    /** PyYAML's own {@code Constructor.timestamp_regexp} ({@code yaml/constructor.py:310-320}), copied
     * verbatim (named groups renamed to be valid Java identifiers -- Java forbids the underscores
     * PyYAML/TypeScript use in {@code tz_sign}/{@code tz_hour}/{@code tz_minute}) -- used only to pull
     * fields out of text a {@link PyyamlTimestamp} marker already carries; never used as a resolution
     * gate (SnakeYAML's own {@code compose()} already decided this scalar is a timestamp). */
    private static final java.util.regex.Pattern PYYAML_TIMESTAMP_FIELDS_RE = java.util.regex.Pattern.compile(
            "(?<year>[0-9][0-9][0-9][0-9])-(?<month>[0-9][0-9]?)-(?<day>[0-9][0-9]?)"
                    + "(?:(?:[Tt]|[ \\t]+)(?<hour>[0-9][0-9]?):(?<minute>[0-9][0-9]):(?<second>[0-9][0-9])"
                    + "(?:\\.(?<fraction>[0-9]*))?"
                    + "(?:[ \\t]*(?<tz>Z|(?<tzSign>[-+])(?<tzHour>[0-9][0-9]?)(?::(?<tzMinute>[0-9][0-9]))?))?)?");

    /** PyYAML's {@code construct_yaml_timestamp} ({@code yaml/constructor.py:322-351}) plus Python's
     * own {@code str(date)}/{@code str(datetime)} rendering, given a {@link PyyamlTimestamp} marker's
     * raw text -- never a plain string, which {@link #normalizeDeviationDates} passes through
     * completely unchanged. Performs <b>no range validation</b>: an out-of-range field renders anyway;
     * {@link #parseDate} is the one place both a quoted and a pythonized-unquoted value are
     * range-checked, so both fail exactly the same way. */
    public static String pythonizeTimestamp(String raw) {
        java.util.regex.Matcher m = PYYAML_TIMESTAMP_FIELDS_RE.matcher(raw);
        if (!m.matches()) {
            return raw;
        }
        String year = m.group("year");
        int month = Integer.parseInt(m.group("month"));
        int day = Integer.parseInt(m.group("day"));
        String hourStr = m.group("hour");
        if (hourStr == null) {
            return String.format("%s-%02d-%02d", year, month, day);
        }
        int hour = Integer.parseInt(hourStr);
        String minute = m.group("minute");
        String second = m.group("second");
        int microsecond = 0;
        String fraction = m.group("fraction");
        if (fraction != null) {
            String f = fraction.length() > 6 ? fraction.substring(0, 6) : fraction;
            StringBuilder sb = new StringBuilder(f);
            while (sb.length() < 6) {
                sb.append('0');
            }
            microsecond = Integer.parseInt(sb.toString());
        }
        StringBuilder out = new StringBuilder(String.format("%s-%02d-%02d %02d:%s:%s", year, month, day, hour, minute, second));
        if (microsecond != 0) {
            out.append(String.format(".%06d", microsecond));
        }
        String tz = m.group("tz");
        if (tz == null) {
            // naive: no offset suffix.
        } else if (m.group("tzSign") == null) {
            // a bare Z/z.
            out.append("+00:00");
        } else {
            int tzHour = Integer.parseInt(m.group("tzHour"));
            String tzMinuteGroup = m.group("tzMinute");
            int tzMinute = tzMinuteGroup == null ? 0 : Integer.parseInt(tzMinuteGroup);
            out.append(String.format("%s%02d:%02d", m.group("tzSign"), tzHour, tzMinute));
        }
        return out.toString();
    }

    /** Rewrites every {@link PyyamlTimestamp} marker value in every entry to its {@link
     * #pythonizeTimestamp} text, in place of the marker; every other value (a plain string -- including
     * an originally-quoted timestamp-looking one, left byte-identical -- a number, a boolean, {@code
     * null}) passes straight through. This changes no Python code; Python's own {@code
     * str(date|datetime)} rendering is the reference this port matches. */
    static List<Map<String, Object>> normalizeDeviationDates(List<Map<String, Object>> deviations) {
        List<Map<String, Object>> out = new ArrayList<>();
        for (Map<String, Object> entry : deviations) {
            Map<String, Object> rewritten = new LinkedHashMap<>();
            for (Map.Entry<String, Object> e : entry.entrySet()) {
                Object v = e.getValue();
                rewritten.put(e.getKey(), v instanceof PyyamlTimestamp ts ? pythonizeTimestamp(ts.text()) : v);
            }
            out.add(rewritten);
        }
        return out;
    }

    /** Loads and validates a deviation register's <i>shape</i> (SPEC §13.3.4): a mapping with a
     * top-level {@code deviations} list of mappings, refused as {@code input.deviation_invalid} rather
     * than crashing on hostile or malformed YAML -- ports {@code _load_deviation_register}'s three
     * shape corrections exactly: a Python-falsy top-level value ({@link #pyTruthyRaw}'s falsy set, not
     * just {@code null}) is treated as "no deviations"; a {@code deviations} key present with an
     * explicit {@code null} value is a shape error, distinct from the key being absent (which silently
     * defaults to an empty list); every {@code granted}/{@code expiry} value YAML resolved as a
     * timestamp is normalized via {@link #normalizeDeviationDates} before use. */
    public static List<JsonNode> loadDeviationRegister(Path path) {
        String where = "the deviation register at " + pyRepr(path.toString());
        String text;
        try {
            // A fatal decode, as Python's `read_text(encoding="utf-8")`: a lossy one would turn a 0xff
            // byte into U+FFFD and lint the mangled control id instead of refusing the file.
            text = Verify.decodeStrict(Files.readAllBytes(path)).toString();
        } catch (CharacterCodingException e) {
            throw new InputError(
                    "input.deviation_invalid",
                    where + " is not valid UTF-8: " + e + ".",
                    "save the deviation register as UTF-8 text.");
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        }
        Object parsed;
        try {
            LoaderOptions options = new LoaderOptions();
            options.setNestingDepthLimit(DEVIATION_REGISTER_MAX_DEPTH);
            Node root = new Yaml(options).compose(new StringReader(text));
            parsed = root == null ? null : composeValue(root);
        } catch (StackOverflowError | RuntimeException e) {
            if (e instanceof StackOverflowError || String.valueOf(e.getMessage()).contains("Nesting Depth exceeded")) {
                // SnakeYAML caps nesting (and composeValue recurses per level), as PyYAML's composer
                // hits its RecursionError.
                throw new InputError(
                        "input.deviation_invalid",
                        where + " is nested too deeply to parse safely.",
                        "flatten the deviation register's structure; it exceeds the engine's safe nesting depth.");
            }
            throw new InputError(
                    "input.deviation_invalid",
                    where + " carries a YAML construct the engine refuses to load: " + e.getMessage() + ".",
                    "remove custom tags and aliases from the deviation register; only plain YAML scalars, "
                            + "mappings, and sequences are accepted.");
        }
        Object data = pyTruthyRaw(parsed) ? parsed : new LinkedHashMap<String, Object>();
        if (!(data instanceof Map<?, ?> dataMap)) {
            throw new InputError(
                    "input.deviation_invalid",
                    where + " is not a mapping.",
                    "the register must be a mapping with a top-level `deviations:` list.");
        }
        List<Object> raw;
        if (!dataMap.containsKey("deviations")) {
            raw = List.of();
        } else {
            Object devs = dataMap.get("deviations");
            if (!(devs instanceof List<?> list)) {
                throw new InputError(
                        "input.deviation_invalid",
                        where + "'s `deviations` key is not a list.",
                        "`deviations:` must be a list of deviation entries.");
            }
            raw = new ArrayList<>(list);
        }
        List<Map<String, Object>> entries = new ArrayList<>();
        for (Object entry : raw) {
            if (!(entry instanceof Map<?, ?> entryMap)) {
                throw new InputError(
                        "input.deviation_invalid",
                        where + " has a deviation entry that is not a mapping.",
                        "each entry under `deviations:` must be a mapping of the register's own fields.");
            }
            Map<String, Object> copy = new LinkedHashMap<>();
            for (Map.Entry<?, ?> e : entryMap.entrySet()) {
                copy.put(String.valueOf(e.getKey()), e.getValue());
            }
            entries.add(copy);
        }
        List<JsonNode> out = new ArrayList<>();
        for (Map<String, Object> entry : normalizeDeviationDates(entries)) {
            out.add(toJsonNode(entry));
        }
        return out;
    }

    /** The gaps-file token pattern (SPEC §13.3.4): a control id shaped {@code [A-Z]{2,4}-[0-9]{2}},
     * matched with Unicode-aware word-boundary lookarounds reproducing Python's default ({@code
     * re.UNICODE}) {@code \b} -- {@code [\p{L}\p{N}_]}, exactly Python's {@code \w} -- rather than
     * {@code Pattern.UNICODE_CHARACTER_CLASS}'s {@code \b} (which disagrees with Python's on marks,
     * connector-punctuation, and the "other number" category). */
    private static final java.util.regex.Pattern GAPS_TOKEN_RE =
            java.util.regex.Pattern.compile("(?<![\\p{L}\\p{N}_])[A-Z]{2,4}-[0-9]{2}(?![\\p{L}\\p{N}_])");

    /** The set of control ids named in a gaps file, deduplicated -- mirrors {@code
     * set(re.findall(r"\b[A-Z]{2,4}-[0-9]{2}\b", text))}. */
    public static Set<String> parseGapsFile(String text) {
        Set<String> out = new LinkedHashSet<>();
        java.util.regex.Matcher m = GAPS_TOKEN_RE.matcher(text);
        while (m.find()) {
            out.add(m.group());
        }
        return out;
    }

    // --- parseDate: a pure calendar-date record, never a java.util.Date/java.time.Instant -----------

    /** A parsed ISO-8601 calendar date -- Python's own {@code parse_date} truncates to {@code .date()}
     * before ever returning, so only the calendar date is retained here; every comparison this module
     * makes ({@code deviationLint}'s {@code > maxDays}, {@code < asOfDate}) is calendar-day arithmetic,
     * never an instant on a shared timeline. */
    record CalendarDate(int year, int month, int day) {}

    /** RFC 3339 only, the one grammar all three engines accept (2026-09-30 maintainer decision,
     * {@code TRADEOFFS.md}/inbox row 19): only the forms the deviation-register schema's own
     * {@code format: date-time} allows -- never {@code datetime.fromisoformat}'s additionally-accepted
     * basic format ({@code 20211231}), week-dates ({@code 2021-W52-5}), or hour-only/minute-only
     * reduced-precision forms; Python's own {@code parse_date} now rejects these same forms too, so
     * this is no longer a cross-engine divergence. */
    private static final java.util.regex.Pattern PARSE_DATE_RE = java.util.regex.Pattern.compile(
            "(?<year>\\d{4})-(?<month>\\d{2})-(?<day>\\d{2})"
                    + "(?:[T ](?<hour>\\d{2}):(?<minute>\\d{2}):(?<second>\\d{2})"
                    + "(?:\\.(?<fraction>\\d+))?(?<offset>Z|[+-]\\d{2}:?\\d{2})?)?");
    private static final java.util.regex.Pattern OFFSET_RE = java.util.regex.Pattern.compile("([+-])(\\d{2}):?(\\d{2})");

    private static final int[] DAYS_IN_MONTH = {31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31};

    private static boolean isLeapYear(int year) {
        return (year % 4 == 0 && year % 100 != 0) || year % 400 == 0;
    }

    private static int daysInMonth(int year, int month) {
        return month == 2 && isLeapYear(year) ? 29 : DAYS_IN_MONTH[month - 1];
    }

    /** Parses {@code value} against the grammar above and, on a match, <b>validates every field for
     * real, not just the calendar date</b> -- matching what a real {@code datetime.fromisoformat}
     * rejects: year &gt;= 1, the days-in-month/leap-year rule, hour &lt;= 23, minute &lt;= 59, second
     * &lt;= 59, offset hour &lt;= 23, offset minute &lt;= 59 when an offset is present. Matched with
     * {@link java.util.regex.Matcher#matches()}, never {@code find()} with a trailing {@code $} (which
     * can match just before a trailing newline). Returns {@code null} on any grammar or range failure --
     * never throws, mirroring {@code parse_date}'s contract exactly. */
    static CalendarDate parseDate(String value) {
        java.util.regex.Matcher m = PARSE_DATE_RE.matcher(value);
        if (!m.matches()) {
            return null;
        }
        int year = Integer.parseInt(m.group("year"));
        int month = Integer.parseInt(m.group("month"));
        int day = Integer.parseInt(m.group("day"));
        if (year < 1 || month < 1 || month > 12 || day < 1 || day > daysInMonth(year, month)) {
            return null;
        }
        String hourStr = m.group("hour");
        if (hourStr == null) {
            return new CalendarDate(year, month, day);
        }
        int hour = Integer.parseInt(hourStr);
        int minute = Integer.parseInt(m.group("minute"));
        int second = Integer.parseInt(m.group("second"));
        if (hour > 23 || minute > 59 || second > 59) {
            return null;
        }
        String offset = m.group("offset");
        if (offset != null && !"Z".equals(offset)) {
            java.util.regex.Matcher om = OFFSET_RE.matcher(offset);
            if (!om.matches()) {
                return null;
            }
            int offHour = Integer.parseInt(om.group(2));
            int offMinute = Integer.parseInt(om.group(3));
            if (offHour > 23 || offMinute > 59) {
                return null;
            }
        }
        return new CalendarDate(year, month, day);
    }

    /** The proleptic-Gregorian Julian day number of {@code date}'s calendar fields alone -- the
     * standard integer algorithm, used only so {@link #daysBetween}/{@link #compareDate} get real
     * calendar-day arithmetic (accounting for month lengths and leap years) with no {@code Date}/
     * instant anywhere in the computation. */
    private static long julianDayNumber(CalendarDate date) {
        int a = (14 - date.month()) / 12;
        int y = date.year() + 4800 - a;
        int mo = date.month() + 12 * a - 3;
        return date.day() + (153L * mo + 2) / 5 + 365L * y + y / 4 - y / 100 + y / 400 - 32045;
    }

    /** Negative when {@code a}'s calendar date is before {@code b}'s, matching Python's {@code
     * date.__lt__}. */
    static long compareDate(CalendarDate a, CalendarDate b) {
        return julianDayNumber(a) - julianDayNumber(b);
    }

    /** {@code (expiry.date() - granted.date()).days}, calendar-day arithmetic, matching Python's own
     * {@code timedelta.days} for two {@code date} objects exactly. */
    private static long daysBetween(CalendarDate from, CalendarDate to) {
        return compareDate(to, from);
    }

    // --- deviationLint -------------------------------------------------------------------------------

    /** Enforces the deviation rules of SPEC §13.3.4, ported from {@code deviation_lint}
     * ({@code readiness.py:200-276}) exactly: the control exists and its outcome was {@code
     * non-conformant} (never {@code insufficient_evidence}), no deviation touches the INT family, every
     * field is present (via {@link #pyTruthy}, so an empty list/mapping value counts as missing), the
     * approver is a person distinct from the owner (via {@link #pyEquals}, only when both are truthy),
     * and the deviation expires within the catalog's maximum lifetime. {@code outcomesByControl} carries
     * every outcome recorded for a control across every subject in this run (never a single collapsed
     * value), so the accept/reject verdict never depends on assertion iteration order. A control already
     * in {@code appliedControls} skips the outcome re-check entirely, but is instead checked, when
     * {@code asOf} is given, against its own expiry. */
    public static List<String> deviationLint(
            List<JsonNode> deviations,
            Set<String> controlIds,
            Map<String, Set<String>> outcomesByControl,
            Set<String> appliedControls,
            String asOf,
            int maxDays) {
        List<String> problems = new ArrayList<>();
        Set<String> seenControls = new LinkedHashSet<>();
        CalendarDate asOfDate = asOf != null ? parseDate(asOf) : null;
        for (JsonNode deviation : deviations) {
            String control = pyStr(pyGet(deviation, "control", Json.nodes().textNode("")));
            if (seenControls.contains(control)) {
                problems.add(control + ": duplicate deviation entry for this control");
            }
            seenControls.add(control);
            if (!controlIds.contains(control)) {
                problems.add((control.isEmpty() ? "<none>" : control) + ": control is not in the catalog");
            }
            int dash = control.indexOf('-');
            String family = dash >= 0 ? control.substring(0, dash) : control;
            if (INT_FAMILY.equals(family)) {
                problems.add(control + ": the INT family cannot be deviated (integrity and coverage)");
            }
            if (!appliedControls.contains(control)) {
                Set<String> outcomes = outcomesByControl.getOrDefault(control, Set.of());
                if (outcomes.contains("insufficient_evidence")) {
                    problems.add(control + ": insufficient_evidence is an evidence gap, not a risk acceptance");
                } else if (!outcomes.isEmpty() && !outcomes.contains("non-conformant")) {
                    List<String> sorted = new ArrayList<>(outcomes);
                    sorted.sort(Json::byteCompare);
                    problems.add(control + ": only a non-conformant outcome may be deviated (got "
                            + String.join(", ", sorted) + ")");
                }
            }
            for (String field : DEVIATION_FIELDS) {
                if (!pyTruthy(deviation.path(field))) {
                    problems.add(control + ": deviation is missing " + field);
                }
            }
            JsonNode owner = deviation.path("owner");
            JsonNode approver = deviation.path("approver");
            if (pyTruthy(owner) && pyTruthy(approver) && pyEquals(owner, approver)) {
                problems.add(control + ": the approver must be a person distinct from the owner");
            }
            JsonNode grantedRaw = deviation.path("granted");
            JsonNode expiryRaw = deviation.path("expiry");
            CalendarDate granted = grantedRaw.isTextual() ? parseDate(grantedRaw.textValue()) : null;
            CalendarDate expiry = expiryRaw.isTextual() ? parseDate(expiryRaw.textValue()) : null;
            if (pyTruthy(grantedRaw) && granted == null) {
                problems.add(control + ": granted is not a valid RFC 3339 date (" + pyRepr(grantedRaw) + ")");
            }
            if (pyTruthy(expiryRaw) && expiry == null) {
                problems.add(control + ": expiry is not a valid RFC 3339 date (" + pyRepr(expiryRaw) + ")");
            }
            if (granted != null && expiry != null && daysBetween(granted, expiry) > maxDays) {
                problems.add(control + ": deviation lifetime exceeds " + maxDays + " days");
            }
            if (asOfDate != null && expiry != null && appliedControls.contains(control)
                    && compareDate(expiry, asOfDate) < 0) {
                problems.add(control + ": applied deviation has expired (expiry " + pyStr(expiryRaw) + ")");
            }
        }
        return problems;
    }

    // --- computeReadiness ------------------------------------------------------------------------

    /** Reads {@code path} as one JSON object per line, skipping blank lines, mirroring {@code
     * Ingest.java}'s own line-reading pattern -- an empty list, never a thrown exception, when the file
     * does not exist. */
    private static List<JsonNode> readJsonl(Path path) {
        if (!Files.isRegularFile(path)) {
            return List.of();
        }
        List<String> lines;
        try {
            lines = Files.readAllLines(path, StandardCharsets.UTF_8);
        } catch (IOException e) {
            throw new IllegalStateException("cannot read " + path + ": " + e.getMessage(), e);
        }
        List<JsonNode> out = new ArrayList<>();
        for (String raw : lines) {
            String line = raw.strip();
            if (!line.isEmpty()) {
                out.add(Json.parse(line));
            }
        }
        return out;
    }

    private static JsonNode readJsonFileOr(Path path, JsonNode fallback) {
        return Files.isRegularFile(path) ? Json.parseFile(path) : fallback;
    }

    /** Returns the readiness verdict for a finished report directory (SPEC §13.3.4 stage 4), ported
     * from {@code compute_readiness} ({@code readiness.py:104-197}) exactly. {@code severities} maps
     * control id to severity (from the catalogs the report used); {@code gaps} is the set of control
     * ids recorded in the operator's gaps file with an owner and date. A high-severity {@code
     * insufficient_evidence} outcome is a limitation when recorded in {@code gaps} and a blocker
     * otherwise; integrity breaks, coverage shortfalls, applicability drift, and invalid deviations
     * always block. {@code reasons}/{@code limitations} are <b>multisets, not sets</b>: two subjects
     * each producing the identical high-severity-gap reason string yield two entries, not one, built
     * with a plain {@code ArrayList}, never a deduplicating collection, and sorted with {@link
     * Json#byteCompare}, never {@code String::compareTo}'s UTF-16 code-unit order. */
    public static Verdict computeReadiness(
            Path reportDir, Map<String, String> severities, List<JsonNode> deviations, Set<String> gaps) {
        Set<String> gapSet = gaps != null ? gaps : Set.of();
        List<String> reasons = new ArrayList<>();
        List<String> limitations = new ArrayList<>();

        for (JsonNode record : readJsonl(reportDir.resolve("integrity.jsonl"))) {
            JsonNode status = record.path("status");
            if (status.isTextual() && GAP_INTEGRITY_STATUSES.contains(status.textValue())) {
                reasons.add("integrity " + pyStr(status) + " on stream " + pyStr(record.path("stream")));
            }
        }

        JsonNode coverage = readJsonFileOr(reportDir.resolve("coverage.json"), Json.nodes().objectNode());
        JsonNode subjects = coverage.path("subjects");
        List<String> subjectNames = new ArrayList<>();
        if (subjects.isObject()) {
            subjects.fieldNames().forEachRemaining(subjectNames::add);
        }
        subjectNames.sort(Json::byteCompare);
        for (String subject : subjectNames) {
            JsonNode entry = subjects.get(subject);
            if (!entry.isObject()) {
                continue;
            }
            if ("gap".equals(entry.path("coverage_status").asText(null))) {
                reasons.add("coverage shortfall for subject " + subject);
            }
            JsonNode eventTypes = entry.path("event_types");
            if (!eventTypes.isObject()) {
                continue;
            }
            List<String> eventTypeNames = new ArrayList<>();
            eventTypes.fieldNames().forEachRemaining(eventTypeNames::add);
            eventTypeNames.sort(Json::byteCompare);
            for (String eventType : eventTypeNames) {
                JsonNode detail = eventTypes.get(eventType);
                // A declared source below the coverage threshold is a shortfall even when another
                // event type has unknown coverage and masks it in the subject-level roll-up (§13.3.4).
                if (detail.isObject() && "below_threshold".equals(detail.path("status").asText(null))) {
                    reasons.add("coverage shortfall for " + subject + " " + eventType);
                }
            }
        }

        for (JsonNode statement : readJsonl(reportDir.resolve("applicability.jsonl"))) {
            JsonNode driftRaw = pyGet(statement, "drift", Json.nodes().arrayNode());
            JsonNode drift = pyTruthy(driftRaw) && driftRaw.isArray() ? driftRaw : Json.nodes().arrayNode();
            for (JsonNode d : drift) {
                JsonNode dd = d.isObject() ? d : Json.nodes().objectNode();
                reasons.add("applicability drift: " + pyStr(dd.path("kind")) + " " + pyStr(dd.path("ref")));
            }
        }

        JsonNode assertionsRaw = readJsonFileOr(reportDir.resolve("assertions.json"), Json.nodes().arrayNode());
        JsonNode assertions = assertionsRaw.isArray() ? assertionsRaw : Json.nodes().arrayNode();
        for (JsonNode assertion : assertions) {
            if (!"insufficient_evidence".equals(assertion.path("outcome").asText(null))) {
                continue;
            }
            String control = pyStr(pyGet(assertion, "control", Json.nodes().textNode("")));
            if ("high".equals(severities.get(control))) {
                if (gapSet.contains(control)) {
                    limitations.add(control + ": high-severity evidence gap recorded");
                } else {
                    reasons.add(control + ": unrecorded high-severity insufficient_evidence");
                }
            }
        }

        if (deviations != null && !deviations.isEmpty()) {
            Map<String, Set<String>> outcomesByControl = new LinkedHashMap<>();
            Set<String> appliedControls = new LinkedHashSet<>();
            List<String> windowEnds = new ArrayList<>();
            for (JsonNode a : assertions) {
                String control = pyStr(a.path("control"));
                outcomesByControl.computeIfAbsent(control, k -> new LinkedHashSet<>()).add(pyStr(a.path("outcome")));
                if (pyTruthy(a.path("deviation"))) {
                    appliedControls.add(control);
                }
                JsonNode windowRaw = a.path("window");
                JsonNode window = pyTruthy(windowRaw) && windowRaw.isObject() ? windowRaw : Json.nodes().objectNode();
                JsonNode end = window.path("end");
                if (pyTruthy(end)) {
                    windowEnds.add(pyStr(end));
                }
            }
            String asOf = null;
            if (!windowEnds.isEmpty()) {
                List<String> sorted = new ArrayList<>(windowEnds);
                sorted.sort(Json::byteCompare);
                asOf = sorted.get(sorted.size() - 1);
            }
            List<String> problems = deviationLint(
                    deviations,
                    new LinkedHashSet<>(severities.keySet()),
                    outcomesByControl,
                    appliedControls,
                    asOf,
                    DEFAULT_MAX_DEVIATION_DAYS);
            for (String p : problems) {
                reasons.add("invalid deviation: " + p);
            }
        }

        String verdict = !reasons.isEmpty() ? NOT_READY : !limitations.isEmpty() ? READY_WITH_LIMITATIONS : READY;
        List<String> sortedReasons = new ArrayList<>(reasons);
        sortedReasons.sort(Json::byteCompare);
        List<String> sortedLimitations = new ArrayList<>(limitations);
        sortedLimitations.sort(Json::byteCompare);
        return new Verdict(verdict, sortedReasons, sortedLimitations);
    }
}
