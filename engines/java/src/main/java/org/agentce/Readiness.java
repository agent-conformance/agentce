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
import java.nio.file.LinkOption;
import java.nio.file.NoSuchFileException;
import java.nio.file.Path;
import java.nio.file.attribute.BasicFileAttributes;
import java.util.ArrayList;
import java.util.Collections;
import java.util.HashSet;
import java.util.IdentityHashMap;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.function.Function;
import org.yaml.snakeyaml.DumperOptions;
import org.yaml.snakeyaml.LoaderOptions;
import org.yaml.snakeyaml.Yaml;
import org.yaml.snakeyaml.constructor.SafeConstructor;
import org.yaml.snakeyaml.nodes.MappingNode;
import org.yaml.snakeyaml.nodes.Node;
import org.yaml.snakeyaml.nodes.NodeId;
import org.yaml.snakeyaml.nodes.NodeTuple;
import org.yaml.snakeyaml.nodes.ScalarNode;
import org.yaml.snakeyaml.nodes.SequenceNode;
import org.yaml.snakeyaml.nodes.Tag;
import org.yaml.snakeyaml.representer.Representer;
import org.yaml.snakeyaml.resolver.Resolver;

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
     * False} spelled Python's way, a list or mapping as its items' reprs, else the plain text. */
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
        if (value.isArray()) {
            // Python's str(list): each item's repr, so a register's `control: [DOC-01]` prints ['DOC-01'].
            java.util.StringJoiner out = new java.util.StringJoiner(", ", "[", "]");
            value.forEach(item -> out.add(pyRepr(item)));
            return out.toString();
        }
        if (value.isObject()) {
            java.util.StringJoiner out = new java.util.StringJoiner(", ", "{", "}");
            value.fields().forEachRemaining(e -> out.add(pyRepr(e.getKey()) + ": " + pyRepr(e.getValue())));
            return out.toString();
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
    private static final Set<String> PYYAML_BOOL_FALSE = Set.of("no", "false", "off");

    /** Marks the tag SnakeYAML resolves for an untagged plain scalar, so {@link #composeValue} can tell it from
     * a tag the file writes ({@code !!str 7}, {@code !!bool "yes"}): PyYAML resolves only the first and
     * constructs the second by its tag. */
    private static final String IMPLICIT_TAG_PREFIX = "tag:agentce.org,implicit:";

    /** YAML 1.1's {@code =} value type, which PyYAML's resolver tags and {@code SafeLoader} cannot construct. */
    private static final Tag PYYAML_VALUE_TAG = new Tag("tag:yaml.org,2002:value");

    private static final class ImplicitMarkingResolver extends Resolver {
        @Override
        public Tag resolve(NodeId kind, String value, boolean implicit) {
            if (kind != NodeId.scalar || !implicit) {
                return super.resolve(kind, value, implicit);
            }
            // PyYAML's resolver regexes end in Python's `$`, which also matches before one trailing newline (a
            // `! |` block scalar), and it resolves a plain `=` as the value type.
            String resolved = withoutTrailingNewline(value);
            Tag tag = resolved.equals("=") ? PYYAML_VALUE_TAG : super.resolve(kind, resolved, true);
            return new Tag(IMPLICIT_TAG_PREFIX + tag.getValue());
        }
    }

    /** {@code text} without one trailing newline: the text Python's {@code $} lets a resolver regex match. */
    private static String withoutTrailingNewline(String text) {
        return text.endsWith("\n") ? text.substring(0, text.length() - 1) : text;
    }

    /** {@code node}'s tag with the implicit mark removed. */
    private static Tag pyyamlTag(Node node) {
        String text = node.getTag().getValue();
        return text.startsWith(IMPLICIT_TAG_PREFIX) ? new Tag(text.substring(IMPLICIT_TAG_PREFIX.length())) : node.getTag();
    }

    private static Yaml pyyamlComposer(LoaderOptions options) {
        DumperOptions dumper = new DumperOptions();
        return new Yaml(new SafeConstructor(options), new Representer(dumper), dumper, options, new ImplicitMarkingResolver());
    }

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

    /** The whitespace Python's {@code int()} and {@code float()} ignore around a number ({@code str.isspace}). */
    private static final String PY_SPACE =
            "[\\t\\n\\x0b\\x0c\\r\\x1c-\\x1f \\x85\\xa0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000]*";

    /** Python's {@code int(text, base)} by base, after {@link #asciiDigits}: an optional sign, the base's own
     * optional prefix, then digits. */
    private static final Map<Integer, java.util.regex.Pattern> PY_INT_RE = Map.of(
            2, java.util.regex.Pattern.compile("^" + PY_SPACE + "([-+]?)(?:0[bB])?([01]+)" + PY_SPACE + "$"),
            8, java.util.regex.Pattern.compile("^" + PY_SPACE + "([-+]?)(?:0[oO])?([0-7]+)" + PY_SPACE + "$"),
            10, java.util.regex.Pattern.compile("^" + PY_SPACE + "([-+]?)([0-9]+)" + PY_SPACE + "$"),
            16, java.util.regex.Pattern.compile("^" + PY_SPACE + "([-+]?)(?:0[xX])?([0-9a-fA-F]+)" + PY_SPACE + "$"));

    /** Python's {@code float(text)}, after {@link #asciiDigits}: decimal with an optional exponent, {@code inf},
     * {@code infinity} or {@code nan}, any case, after an optional sign. */
    private static final java.util.regex.Pattern PY_FLOAT_RE = java.util.regex.Pattern.compile(
            "^" + PY_SPACE + "([-+]?)(?:((?:[0-9]+\\.?[0-9]*|\\.[0-9]+)(?:e[-+]?[0-9]+)?)|(inf|infinity)|(nan))" + PY_SPACE + "$",
            java.util.regex.Pattern.CASE_INSENSITIVE);

    /** {@code text} with each Unicode 15.0 decimal digit ({@code ١}, {@code １}, {@code 𝟏}) written as its ASCII digit, as
     * Python's {@code int()} and {@code float()} read it first; other characters, {@code ²} among them, are kept. */
    /** Each Unicode 15.0 decimal digit as inclusive code point ranges, the table Python's {@code safe_yaml} carries
     * ({@code UNICODE_15_DIGITS}, from Python 3.12's {@code unicodedata}): a digit outside it is not read as a digit
     * on any Python or JDK (census odd-inputs line 101). */
    private static final int[][] UNICODE_15_DIGITS = {
        {0x0030, 0x0039},
        {0x0660, 0x0669},
        {0x06F0, 0x06F9},
        {0x07C0, 0x07C9},
        {0x0966, 0x096F},
        {0x09E6, 0x09EF},
        {0x0A66, 0x0A6F},
        {0x0AE6, 0x0AEF},
        {0x0B66, 0x0B6F},
        {0x0BE6, 0x0BEF},
        {0x0C66, 0x0C6F},
        {0x0CE6, 0x0CEF},
        {0x0D66, 0x0D6F},
        {0x0DE6, 0x0DEF},
        {0x0E50, 0x0E59},
        {0x0ED0, 0x0ED9},
        {0x0F20, 0x0F29},
        {0x1040, 0x1049},
        {0x1090, 0x1099},
        {0x17E0, 0x17E9},
        {0x1810, 0x1819},
        {0x1946, 0x194F},
        {0x19D0, 0x19D9},
        {0x1A80, 0x1A89},
        {0x1A90, 0x1A99},
        {0x1B50, 0x1B59},
        {0x1BB0, 0x1BB9},
        {0x1C40, 0x1C49},
        {0x1C50, 0x1C59},
        {0xA620, 0xA629},
        {0xA8D0, 0xA8D9},
        {0xA900, 0xA909},
        {0xA9D0, 0xA9D9},
        {0xA9F0, 0xA9F9},
        {0xAA50, 0xAA59},
        {0xABF0, 0xABF9},
        {0xFF10, 0xFF19},
        {0x104A0, 0x104A9},
        {0x10D30, 0x10D39},
        {0x11066, 0x1106F},
        {0x110F0, 0x110F9},
        {0x11136, 0x1113F},
        {0x111D0, 0x111D9},
        {0x112F0, 0x112F9},
        {0x11450, 0x11459},
        {0x114D0, 0x114D9},
        {0x11650, 0x11659},
        {0x116C0, 0x116C9},
        {0x11730, 0x11739},
        {0x118E0, 0x118E9},
        {0x11950, 0x11959},
        {0x11C50, 0x11C59},
        {0x11D50, 0x11D59},
        {0x11DA0, 0x11DA9},
        {0x11F50, 0x11F59},
        {0x16A60, 0x16A69},
        {0x16AC0, 0x16AC9},
        {0x16B50, 0x16B59},
        {0x1D7CE, 0x1D7FF},
        {0x1E140, 0x1E149},
        {0x1E2F0, 0x1E2F9},
        {0x1E4F0, 0x1E4F9},
        {0x1E950, 0x1E959},
        {0x1FBF0, 0x1FBF9}
    };

    /** The value of {@code cp} if it is a {@link #UNICODE_15_DIGITS} digit, else -1; each script's digits run from 0
     * to 9, so the value is the distance from the range's start, mod 10. */
    private static int unicode15Digit(int cp) {
        for (int[] range : UNICODE_15_DIGITS) {
            if (range[0] <= cp && cp <= range[1]) {
                return (cp - range[0]) % 10;
            }
        }
        return -1;
    }

    private static String asciiDigits(String text) {
        if (text.chars().allMatch(c -> c < 0x80)) {
            return text;
        }
        StringBuilder out = new StringBuilder(text.length());
        text.codePoints().forEach(cp -> {
            int digit = unicode15Digit(cp);
            if (digit >= 0) {
                out.append((char) ('0' + digit));
            } else {
                out.appendCodePoint(cp);
            }
        });
        return out.toString();
    }

    private static BigInteger pythonInt(String text, int base) {
        java.util.regex.Matcher m = PY_INT_RE.get(base).matcher(asciiDigits(text));
        if (!m.matches()) {
            throw new IllegalArgumentException("invalid literal for int() with base " + base + ": " + pyRepr(text));
        }
        BigInteger n = new BigInteger(m.group(2), base);
        return m.group(1).equals("-") ? n.negate() : n;
    }

    private static double pythonFloat(String text) {
        java.util.regex.Matcher m = PY_FLOAT_RE.matcher(asciiDigits(text));
        if (!m.matches()) {
            throw new IllegalArgumentException("could not convert string to float: " + pyRepr(text));
        }
        double n = m.group(2) != null ? Double.parseDouble(m.group(2))
                : m.group(3) != null ? Double.POSITIVE_INFINITY : Double.NaN;
        return m.group(1).equals("-") ? -n : n;
    }

    /** PyYAML's sign handling: the sign of {@code value}'s first character, and {@code value} without
     * it. Empty text is refused, where PyYAML's {@code value[0]} raises. */
    private static String pyyamlUnsigned(String value, int[] sign) {
        if (value.isEmpty()) {
            throw new IllegalArgumentException("string index out of range");
        }
        sign[0] = value.charAt(0) == '-' ? -1 : 1;
        return value.charAt(0) == '+' || value.charAt(0) == '-' ? value.substring(1) : value;
    }

    /** PyYAML's {@code construct_yaml_int} ({@code yaml/constructor.py:237-263}), copied verbatim:
     * strip {@code _}, take the sign, then dispatch on the {@code 0b}/{@code 0x}/leading-{@code
     * 0}/colon-sexagesimal/plain-decimal forms in that exact order (order matters, since these
     * prefixes overlap textually -- {@code 0b101} starts with {@code 0} too), each read as Python's
     * {@code int()} reads it. Throws {@link IllegalArgumentException} where PyYAML raises. */
    private static BigInteger pyyamlConstructInt(String data) {
        int[] sign = new int[1];
        String value = pyyamlUnsigned(data.replace("_", ""), sign);
        BigInteger magnitude;
        if (value.equals("0")) {
            return BigInteger.ZERO;
        } else if (value.startsWith("0b")) {
            magnitude = pythonInt(value.substring(2), 2);
        } else if (value.startsWith("0x")) {
            magnitude = pythonInt(value.substring(2), 16);
        } else if (value.charAt(0) == '0') {
            magnitude = pythonInt(value, 8);
        } else if (value.contains(":")) {
            String[] parts = value.split(":", -1);
            BigInteger out = BigInteger.ZERO;
            BigInteger base = BigInteger.ONE;
            for (int i = parts.length - 1; i >= 0; i--) {
                out = out.add(pythonInt(parts[i], 10).multiply(base));
                base = base.multiply(BigInteger.valueOf(60));
            }
            magnitude = out;
        } else {
            magnitude = pythonInt(value, 10);
        }
        return sign[0] < 0 ? magnitude.negate() : magnitude;
    }

    /** PyYAML's {@code construct_yaml_float} ({@code yaml/constructor.py:270-292}), copied verbatim:
     * lower-case, strip {@code _}, take the sign, then dispatch on {@code .inf}/{@code .nan}/
     * colon-sexagesimal/plain-decimal, each read as Python's {@code float()} reads it. Throws {@link
     * IllegalArgumentException} where PyYAML raises. */
    private static double pyyamlConstructFloat(String data) {
        int[] sign = new int[1];
        String value = pyyamlUnsigned(data.replace("_", "").toLowerCase(Locale.ROOT), sign);
        if (value.equals(".inf")) {
            return sign[0] * Double.POSITIVE_INFINITY;
        }
        if (value.equals(".nan")) {
            return Double.NaN;
        }
        if (value.contains(":")) {
            String[] parts = value.split(":", -1);
            double out = 0;
            double base = 1;
            for (int i = parts.length - 1; i >= 0; i--) {
                out += pythonFloat(parts[i]) * base;
                base *= 60;
            }
            return sign[0] * out;
        }
        return sign[0] * pythonFloat(value);
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
        boolean implicit = node.getTag().getValue().startsWith(IMPLICIT_TAG_PREFIX);
        Tag tag = safeTag(node);
        boolean collectionTag = tag.equals(Tag.SEQ) || tag.equals(Tag.MAP);
        if (tag.equals(Tag.MERGE) || collectionTag == node instanceof ScalarNode
                || tag.equals(Tag.SEQ) && !(node instanceof SequenceNode)
                || tag.equals(Tag.MAP) && !(node instanceof MappingNode)) {
            // SafeLoader has no constructor for a merge key read as a value, and each constructor takes
            // one kind of node: !!str [1] and !!seq x are refused.
            throw new IllegalArgumentException(
                    "could not construct the tag '" + tag.getValue() + "' from a " + node.getNodeId() + " node");
        }
        if (node instanceof ScalarNode scalar) {
            String text = scalar.getValue();
            if (tag.equals(Tag.NULL)) {
                return null;
            }
            if (tag.equals(Tag.TIMESTAMP)) {
                // A date construct_yaml_timestamp cannot build stays text, as load_untrusted_yaml keeps it; its regex
                // ends in `$`, so a `! |` block's trailing newline is not part of the date.
                String stamp = withoutTrailingNewline(text);
                return isPyyamlCalendarTimestamp(stamp) ? new PyyamlTimestamp(stamp) : pythonizeTimestamp(text);
            }
            String lower = text.toLowerCase(Locale.ROOT);
            if (tag.equals(Tag.BOOL)) {
                if (!PYYAML_BOOL_TRUE.contains(lower) && !PYYAML_BOOL_FALSE.contains(lower)) {
                    throw new IllegalArgumentException("'" + text + "' is not a YAML boolean");
                }
                return PYYAML_BOOL_TRUE.contains(lower);
            }
            if (implicit) {
                // SnakeYAML's int/float resolution differs from PyYAML's; its null/bool/timestamp agree.
                String resolved = withoutTrailingNewline(text);
                if (couldBeNumeric(resolved)) {
                    if (PYYAML_INT_RESOLVE_RE.matcher(resolved).matches()) {
                        return pyyamlConstructInt(text);
                    }
                    if (PYYAML_FLOAT_RESOLVE_RE.matcher(resolved).matches()) {
                        return pyyamlConstructFloat(text);
                    }
                }
                return text;
            }
            // A tag the file writes, or str for a quoted scalar: constructed by that tag, as SafeLoader does.
            if (tag.equals(Tag.INT)) {
                return pyyamlConstructInt(text);
            }
            if (tag.equals(Tag.FLOAT)) {
                return pyyamlConstructFloat(text);
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
                out.put(String.valueOf(composeValue(scalarKey(tuple))), composeValue(tuple.getValueNode()));
            }
            return out;
        }
        throw new IllegalStateException("unsupported YAML node: " + node.getNodeId());
    }

    /** {@code node}'s tag, refused unless it is one of {@link #SAFE_TAGS}, as {@code SafeLoader} refuses a tag it has no
     * constructor for. */
    private static Tag safeTag(Node node) {
        Tag tag = pyyamlTag(node);
        if (!SAFE_TAGS.contains(tag)) {
            throw new IllegalArgumentException(
                    "could not determine a constructor for the tag '" + tag.getValue() + "'");
        }
        return tag;
    }

    /** {@code tuple}'s key node, refused unless it is a scalar: PyYAML cannot hash a sequence or mapping key (written
     * {@code [a]: 1}, {@code ? [a]}, or as an alias to one), where SnakeYAML would read it as its text. */
    private static Node scalarKey(NodeTuple tuple) {
        Node key = tuple.getKeyNode();
        if (!(key instanceof ScalarNode)) {
            throw new IllegalArgumentException("while constructing a mapping: found unhashable key");
        }
        return key;
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
            if (keyNode instanceof ScalarNode keyScalar && pyyamlTag(keyScalar).equals(Tag.MERGE)) {
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
     * null}/{@link List}/{@link Map}/{@link PyyamlTimestamp}, per {@link #composeValue}) rendered as a
     * {@link JsonNode}. A timestamp becomes a POJO node holding Python's {@code str(date)}: like PyYAML's
     * {@code date} it is neither text nor a number to {@code isTextual}/{@code isNumber}, and {@code asText}
     * renders it as Python's {@code str} does (a mapping key the same way). */
    static JsonNode toJsonNode(Object value) {
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
        if (value instanceof PyyamlTimestamp ts) {
            return f.pojoNode(pythonizeTimestamp(ts.text()));
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
                Object k = e.getKey();
                obj.set(
                        k instanceof PyyamlTimestamp ts ? pythonizeTimestamp(ts.text()) : String.valueOf(k),
                        toJsonNode(e.getValue()));
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

    /** Whether {@code construct_yaml_timestamp} builds a {@code date}/{@code datetime} from {@code raw}:
     * its fields match and lie in the calendar, the clock and a UTC offset under 24 hours. */
    private static boolean isPyyamlCalendarTimestamp(String raw) {
        java.util.regex.Matcher m = PYYAML_TIMESTAMP_FIELDS_RE.matcher(raw);
        if (!m.matches()) {
            return false;
        }
        int year = Integer.parseInt(m.group("year"));
        int month = Integer.parseInt(m.group("month"));
        int day = Integer.parseInt(m.group("day"));
        if (year < 1 || month < 1 || month > 12 || day < 1 || day > daysInMonth(year, month)) {
            return false;
        }
        if (m.group("hour") == null) {
            return true;
        }
        if (Integer.parseInt(m.group("hour")) > 23 || Integer.parseInt(m.group("minute")) > 59
                || Integer.parseInt(m.group("second")) > 59) {
            return false;
        }
        String tzMinute = m.group("tzMinute");
        return m.group("tzSign") == null
                || Integer.parseInt(m.group("tzHour")) * 60 + (tzMinute == null ? 0 : Integer.parseInt(tzMinute)) < 24 * 60;
    }

    /** PyYAML's {@code construct_yaml_timestamp} ({@code yaml/constructor.py:322-351}) plus Python's
     * own {@code str(date)}/{@code str(datetime)} rendering, given a {@link PyyamlTimestamp} marker's
     * raw text -- never a plain string, which {@link #normalizeDeviationDates} passes through
     * completely unchanged. Performs <b>no range validation</b>: an out-of-range field renders anyway;
     * the loader keeps a date no calendar holds as text, and {@link #parseDate} range-checks a quoted
     * and a pythonized-unquoted value alike. */
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

    /** The YAML at {@code path} as Python's {@code load_untrusted_yaml} reads it (PyYAML's {@code SafeLoader} typing,
     * the compose path above), refusing a file that is not UTF-8, nests too deeply, or carries YAML it cannot load
     * under {@code key}. With {@code uniqueKeys}, a mapping that writes one key twice is refused too (a key a
     * {@code <<} merge brings in may be written again); without it the last value wins, as {@code SafeLoader} keeps it. */
    static Object loadPyyaml(Path path, String key, String what, boolean uniqueKeys) {
        // Python passes the loader a Path, so its errors name ./r.yaml as r.yaml.
        String where = what + " at " + pyRepr(Diff.normalizePosixPath(path.toString()));
        String text;
        try {
            // A fatal decode, as Python's `read_text(encoding="utf-8")`: a lossy one would turn a 0xff
            // byte into U+FFFD and read the mangled text instead of refusing the file.
            text = Verify.decodeStrict(Files.readAllBytes(path)).toString();
        } catch (CharacterCodingException e) {
            throw new InputError(key, where + " is not valid UTF-8: " + e + ".", "save " + what + " as UTF-8 text.");
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        }
        try {
            return composePyyaml(text, uniqueKeys);
        } catch (StackOverflowError | RuntimeException e) {
            if (nestedTooDeeply(e)) {
                // SnakeYAML caps nesting (and composeValue recurses per level), as PyYAML's composer
                // hits its RecursionError.
                throw new InputError(
                        key,
                        where + " is nested too deeply to parse safely.",
                        "flatten " + what + "'s structure; it exceeds the engine's safe nesting depth.");
            }
            throw new InputError(
                    key,
                    where + " carries a YAML construct the engine refuses to load: " + e.getMessage() + ".",
                    "remove custom tags and aliases from " + what + "; only plain YAML scalars, "
                            + "mappings, and sequences are accepted.");
        }
    }

    /** {@code text} read as {@link #loadPyyaml} reads a file, its errors left to the caller: a {@link
     * StackOverflowError} or a {@link RuntimeException} ({@link #nestedTooDeeply} tells the depth cap apart). */
    static Object composePyyaml(String text, boolean uniqueKeys) {
        LoaderOptions options = new LoaderOptions();
        options.setNestingDepthLimit(DEVIATION_REGISTER_MAX_DEPTH);
        Node root = pyyamlComposer(options).compose(new StringReader(text));
        if (root == null) {
            return null;
        }
        checkTree(root, uniqueKeys, Collections.newSetFromMap(new IdentityHashMap<>()));
        return composeValue(root);
    }

    /** {@code text} read as a catalog file: {@link #composePyyaml} with unique keys (18.184o). */
    static Object composeCatalogPyyaml(String text) {
        return composePyyaml(text, true);
    }

    /** Whether {@code e}, thrown by {@link #composePyyaml}, is SnakeYAML's nesting cap or the recursion it
     * guards (composeValue recurses per level), as PyYAML's composer hits its RecursionError. */
    static boolean nestedTooDeeply(Throwable e) {
        return e instanceof StackOverflowError || String.valueOf(e.getMessage()).contains("Nesting Depth exceeded");
    }

    /** Refuses a merge source (or merge-list item) anywhere under {@code node} whose tag is not one of {@link #SAFE_TAGS}
     * other than the merge tag, as Python's untrusted loaders refuse it in every file (18.184p); with {@code uniqueKeys},
     * also a mapping that writes one key twice. Each node is walked once ({@code visited}), so an alias is not. */
    private static void checkTree(Node node, boolean uniqueKeys, Set<Node> visited) {
        if (!visited.add(node)) {
            return;
        }
        if (node instanceof SequenceNode seq) {
            for (Node child : seq.getValue()) {
                checkTree(child, uniqueKeys, visited);
            }
        } else if (node instanceof MappingNode map) {
            Set<String> seen = new HashSet<>();
            boolean merged = false;
            for (NodeTuple tuple : map.getValue()) {
                if (Tag.MERGE.equals(pyyamlTag(tuple.getKeyNode()))) {
                    // `<<` is a key too: which of two merges wins is never read (18.184k D1).
                    if (merged && uniqueKeys) {
                        throw new IllegalArgumentException("found the merge key '<<' twice");
                    }
                    merged = true;
                    rejectMergeSourceTags(tuple.getValueNode());
                } else if (uniqueKeys) {
                    String k = String.valueOf(composeValue(scalarKey(tuple)));
                    if (!seen.add(k)) {
                        throw new IllegalArgumentException("found the key " + k + " twice");
                    }
                }
                checkTree(tuple.getValueNode(), uniqueKeys, visited);
            }
        }
    }

    /** {@link #flattenMapping} never composes a merge source, so {@link #composeValue}'s tag check does not reach it. */
    private static void rejectMergeSourceTags(Node value) {
        List<Node> sources = new ArrayList<>(List.of(value));
        if (value instanceof SequenceNode seq) {
            sources.addAll(seq.getValue());
        }
        for (Node source : sources) {
            if (safeTag(source).equals(Tag.MERGE)) {
                throw new IllegalArgumentException("could not determine a constructor for the tag '" + Tag.MERGE.getValue() + "'");
            }
        }
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
        String where = "the deviation register at " + pyRepr(Diff.normalizePosixPath(path.toString()));
        Object parsed = loadPyyaml(path, "input.deviation_invalid", "the deviation register", false);
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

    /** A stage-4 input every report has, parsed: an absent one is a blocking reason, never an empty
     * file, so deleting the record of a gap or a break cannot clear it (SPEC §13.3.4). One that is not
     * UTF-8 JSON of its shape blocks the same way, never a crash and never read as empty; either way
     * {@code empty} is returned. */
    private static <T> T reportFile(
            Path reportDir, String name, Function<String, T> parse, T empty, List<String> reasons) {
        try {
            String text = readReportFile(reportDir.resolve(name));
            if (text != null) {
                return parse.apply(text);
            }
        } catch (IOException | Unreadable e) {
            reasons.add("cannot read report file " + name);
            return empty;
        }
        reasons.add("missing report file " + name);
        return empty;
    }

    /** The text of one report file, or {@code null} when the report folder has no entry by that name,
     * ported from {@code _read_report_file}. An entry that is there but cannot be read as a regular file
     * (a folder, a dangling link, a link into a folder that can be listed but not entered, a file
     * without read permission) throws {@link IOException}: readiness reports it as a blocking reason
     * rather than reading it as absent, which would let a coverage gap or an integrity break stop
     * blocking. Text that is not UTF-8 is unreadable the same way (as Python's strict decode makes it). */
    private static String readReportFile(Path path) throws IOException {
        try {
            Files.readAttributes(path, BasicFileAttributes.class, LinkOption.NOFOLLOW_LINKS);
        } catch (NoSuchFileException e) {
            return null;
        }
        if (!Files.isRegularFile(path)) {
            throw new IOException(path + " is not a regular file");
        }
        return Files.readString(path, StandardCharsets.UTF_8);
    }

    /** A report file whose content is not JSON of the shape readiness reads. */
    private static final class Unreadable extends RuntimeException {
        private static final long serialVersionUID = 1L;

        Unreadable(String message) {
            super(message);
        }
    }

    private static JsonNode parseJson(String text) {
        try {
            return Json.parse(text);
        } catch (IllegalArgumentException e) {
            throw new Unreadable(e.getMessage());
        }
    }

    /** Every entry an object, or the file is unreadable (a report file of the wrong shape). */
    private static List<JsonNode> objects(Iterable<JsonNode> values) {
        List<JsonNode> out = new ArrayList<>();
        for (JsonNode value : values) {
            if (!value.isObject()) {
                throw new Unreadable("an entry is not a JSON object");
            }
            out.add(value);
        }
        return out;
    }

    /** Each named field the verdict is decided on is a JSON string when present (null included), or the
     * file is unreadable: a wrong-typed field read loosely hides a gap or a break (SPEC §13.3.4). */
    private static JsonNode strings(JsonNode record, String... fields) {
        for (String field : fields) {
            if (record.has(field) && !record.get(field).isTextual()) {
                throw new Unreadable("a field of " + String.join(", ", fields) + " is not a JSON string");
            }
        }
        return record;
    }

    private static List<JsonNode> parseJsonl(String text) {
        return objects(Json.jsonlLines(text).stream().map(line -> parseJson(line.text())).toList());
    }

    /** integrity.jsonl: one object per line, whose {@code status} (when present) is a string. */
    private static List<JsonNode> parseIntegrity(String text) {
        return parseJsonl(text).stream().map(record -> strings(record, "status")).toList();
    }

    private static JsonNode parseJsonObject(String text) {
        JsonNode value = parseJson(text);
        if (!value.isObject()) {
            throw new Unreadable("not a JSON object");
        }
        return value;
    }

    private static List<JsonNode> parseJsonObjects(String text) {
        JsonNode value = parseJson(text);
        if (!value.isArray()) {
            throw new Unreadable("not a JSON array");
        }
        return objects(value);
    }

    private static void objectOfObjects(JsonNode value) {
        if (!value.isObject()) {
            throw new Unreadable("not a JSON object");
        }
        objects(value::elements);
    }

    /** coverage.json: an object whose {@code subjects} (when present) maps each subject to an object,
     * and each subject's {@code event_types} (when present) maps each type to an object; a subject's
     * {@code coverage_status} and an event type's {@code status} are strings when present. An entry of
     * another shape makes the file unreadable, never skipped: skipping it loses what deleting it would
     * (SPEC §13.3.4). */
    private static JsonNode parseCoverage(String text) {
        JsonNode coverage = parseJsonObject(text);
        if (coverage.has("subjects")) {
            objectOfObjects(coverage.get("subjects"));
            for (JsonNode entry : coverage.get("subjects")) {
                strings(entry, "coverage_status");
                if (entry.has("event_types")) {
                    objectOfObjects(entry.get("event_types"));
                    for (JsonNode detail : entry.get("event_types")) {
                        strings(detail, "status");
                    }
                }
            }
        }
        return coverage;
    }

    /** assertions.json: an array of objects whose {@code control} and {@code outcome} are strings,
     * {@code window} an object whose {@code end} is a string, and {@code deviation} a string or null,
     * each when present. Another type makes the file unreadable, never read loosely (SPEC §13.3.4). */
    private static List<JsonNode> parseAssertions(String text) {
        List<JsonNode> assertions = parseJsonObjects(text);
        for (JsonNode assertion : assertions) {
            strings(assertion, "control", "outcome");
            if (assertion.has("window")) {
                if (!assertion.get("window").isObject()) {
                    throw new Unreadable("window is not a JSON object");
                }
                strings(assertion.get("window"), "end");
            }
            if (assertion.has("deviation") && !assertion.get("deviation").isNull()) {
                strings(assertion, "deviation");
            }
        }
        return assertions;
    }

    /** applicability.jsonl: one object per line, whose {@code drift} (when present) is an array of
     * objects. A drift of another shape makes the file unreadable, never skipped (SPEC §13.3.4). */
    private static List<JsonNode> parseApplicability(String text) {
        List<JsonNode> statements = parseJsonl(text);
        for (JsonNode statement : statements) {
            if (statement.has("drift")) {
                JsonNode drift = statement.get("drift");
                if (!drift.isArray()) {
                    throw new Unreadable("drift is not a JSON array");
                }
                objects(drift);
            }
        }
        return statements;
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
        return computeReadiness(reportDir, severities, deviations, gaps, List.of());
    }

    /** As {@link #computeReadiness(Path, Map, List, Set)}, starting from {@code given} limitations (a
     * catalog read under {@code --allow-unverified-catalog}). */
    public static Verdict computeReadiness(
            Path reportDir, Map<String, String> severities, List<JsonNode> deviations, Set<String> gaps,
            List<String> given) {
        Set<String> gapSet = gaps != null ? gaps : Set.of();
        List<String> reasons = new ArrayList<>();
        List<String> limitations = new ArrayList<>(given);

        for (JsonNode record :
                Readiness.<List<JsonNode>>reportFile(reportDir, "integrity.jsonl", Readiness::parseIntegrity, List.of(), reasons)) {
            JsonNode status = record.path("status");
            if (status.isTextual() && GAP_INTEGRITY_STATUSES.contains(status.textValue())) {
                reasons.add("integrity " + pyStr(status) + " on stream " + pyStr(record.path("stream")));
            }
        }

        JsonNode coverage =
                reportFile(reportDir, "coverage.json", Readiness::parseCoverage, Json.nodes().objectNode(), reasons);
        JsonNode subjects = coverage.path("subjects");
        List<String> subjectNames = new ArrayList<>();
        subjects.fieldNames().forEachRemaining(subjectNames::add);
        subjectNames.sort(Json::byteCompare);
        for (String subject : subjectNames) {
            JsonNode entry = subjects.get(subject);
            if ("gap".equals(entry.path("coverage_status").asText(null))) {
                reasons.add("coverage shortfall for subject " + subject);
            } else if ("unknown".equals(entry.path("coverage_status").asText(null))) {
                // No independent denominator reconciles the subject's records (SPEC §6.5, §13.3.4).
                reasons.add("coverage unknown for subject " + subject + ": no independent denominator");
            }
            JsonNode eventTypes = entry.path("event_types");
            List<String> eventTypeNames = new ArrayList<>();
            eventTypes.fieldNames().forEachRemaining(eventTypeNames::add);
            eventTypeNames.sort(Json::byteCompare);
            for (String eventType : eventTypeNames) {
                JsonNode detail = eventTypes.get(eventType);
                // A declared source below the coverage threshold is a shortfall even when another
                // event type has unknown coverage and masks it in the subject-level roll-up (§13.3.4).
                if ("below_threshold".equals(detail.path("status").asText(null))) {
                    reasons.add("coverage shortfall for " + subject + " " + eventType);
                }
            }
        }

        for (JsonNode statement :
                Readiness.<List<JsonNode>>reportFile(
                        reportDir, "applicability.jsonl", Readiness::parseApplicability, List.of(), reasons)) {
            for (JsonNode d : statement.path("drift")) {
                reasons.add("applicability drift: " + pyStr(d.path("kind")) + " " + pyStr(d.path("ref")));
            }
        }

        List<JsonNode> assertions =
                reportFile(reportDir, "assertions.json", Readiness::parseAssertions, List.of(), reasons);
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
