package org.agentce;

import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Deque;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.regex.Pattern;
import org.yaml.snakeyaml.nodes.MappingNode;
import org.yaml.snakeyaml.nodes.Node;
import org.yaml.snakeyaml.nodes.NodeTuple;
import org.yaml.snakeyaml.nodes.ScalarNode;
import org.yaml.snakeyaml.nodes.SequenceNode;

/**
 * The YAML profile every YAML input is checked against before it is parsed (SPEC 8.7): a port of Python's
 * {@code agentce/yaml_profile.py}, line for line, so the first violation and its line agree in every engine.
 *
 * <p>A small hand-written lexical pass, not a port of any YAML library's scanner. It finds the first construct
 * outside the profile in document order and names its rule; the caller refuses the file under its role's existing
 * {@code input.*} key. Repeated keys need the parsed tree, so {@link #duplicateKeyLine} checks them on the composed
 * nodes. The rules and their edge cases are pinned by the shared case table {@code spec/rules/yaml-profile-cases.json}.
 * Columns count code points, as Python's string indices do. Nothing here recurses.
 */
public final class YamlProfile {
    private YamlProfile() {}

    static final int MAX_DEPTH = 100;

    /** One hint per rule, the same words in every engine (the case table's {@code hints} holds the same text). */
    public static final Map<String, String> HINTS;

    static {
        Map<String, String> h = new LinkedHashMap<>();
        h.put("anchor", "write the value out in full instead of using an anchor");
        h.put("alias", "write the value out in full instead of using an alias");
        h.put("merge_key", "write the merged keys out in full instead of using <<");
        h.put("tag", "remove the tag; quote the value to keep it as text");
        h.put("directive", "remove the directive line");
        h.put("explicit_key", "write the key as a plain or quoted key followed by a colon");
        h.put("second_document", "keep one document in the file and remove the extra --- or ... line");
        h.put("tab", "replace the tab with spaces");
        h.put("non_string_key", "quote the key to make it text");
        h.put("duplicate_key", "write each key once");
        h.put("yaml11_bool", "quote the value to keep it as text");
        h.put("underscore_number", "quote the value to keep it as text, or write the number without _");
        h.put("sexagesimal", "quote the value to keep it as text");
        h.put("octal", "quote the value to keep it as text");
        h.put("exponent_no_dot", "quote the value to keep it as text, or write the number with a dot and a sign (1.0e+3)");
        h.put("leading_zero", "quote the value to keep it as text, or write the number without the leading zero");
        h.put("exponent_no_sign", "quote the value to keep it as text, or give the exponent a sign (1.0e+3)");
        h.put("flow_scalar_indicator_start", "quote the value to keep it as text");
        h.put("flow_line_not_indented", "indent the line further than the key the list or mapping belongs to");
        h.put("depth_over_100", "flatten the nesting to at most 100 levels");
        h.put("surrogate_escape", "write the character itself instead of escaping a surrogate");
        HINTS = java.util.Collections.unmodifiableMap(h);
    }

    // A double-quoted escape of a UTF-16 surrogate (D800-DFFF), lone or in a pair: UTF-8 cannot carry one, and the engines'
    // YAML libraries keep, replace or join them differently.
    private static final Pattern SURROGATE_ESCAPE =
            Pattern.compile("\\\\(u[dD][89a-fA-F][0-9a-fA-F]{2}|U0000[dD][89a-fA-F][0-9a-fA-F]{2})");
    private static final Pattern YAML11_BOOL = Pattern.compile("(?i)y|n|yes|no|on|off");
    private static final Pattern UNDERSCORE_NUMBER = Pattern.compile(
            "[-+]?(0b[01_]+|0x[0-9a-fA-F_]+|0[0-7_]+|[0-9][0-9_]*(\\.[0-9_]*)?([eE][-+][0-9]+)?|\\.[0-9_]+([eE][-+][0-9]+)?)");
    private static final Pattern SEXAGESIMAL = Pattern.compile("[-+]?[0-9][0-9_]*(:[0-5]?[0-9])+(\\.[0-9_]*)?");
    private static final Pattern OCTAL = Pattern.compile("[-+]?0[0-7]+|0o[0-7]+");
    private static final Pattern EXPONENT_NO_DOT = Pattern.compile("[-+]?[0-9]+[eE][-+]?[0-9]+");
    private static final Pattern LEADING_ZERO = Pattern.compile("[-+]?0[0-9]+");
    private static final Pattern EXPONENT_NO_SIGN = Pattern.compile("[-+]?([0-9]+\\.[0-9]*|\\.[0-9]+)[eE][0-9]+");
    /** A plain key in this list does not read as a string (the written list of SPEC 8.7's "every mapping key is a
     * string"). */
    private static final Pattern NON_STRING_KEY = Pattern.compile(
            "|~|null|Null|NULL|true|True|TRUE|false|False|FALSE"
                    + "|[-+]?(0|[1-9][0-9_]*)|[-+]?0b[01_]+|[-+]?0x[0-9a-fA-F_]+"
                    + "|[-+]?[0-9][0-9_]*\\.[0-9_]*([eE][-+][0-9]+)?|\\.[0-9][0-9_]*([eE][-+][0-9]+)?"
                    + "|[-+]?\\.(inf|Inf|INF)|\\.(nan|NaN|NAN)"
                    + "|[0-9]{4}-[0-9]{2}-[0-9]{2}"
                    + "|[0-9]{4}-[0-9]{1,2}-[0-9]{1,2}([Tt]|[ \\t]+)[0-9]{1,2}:[0-9]{2}:[0-9]{2}(\\.[0-9]*)?"
                    + "([ \\t]*(Z|[-+][0-9]{1,2}(:[0-9]{2})?))?");

    private static final String FLOW_INDICATORS = ",[]{}";

    /** A construct outside the profile: its rule and its 1-based line. */
    public record Violation(String rule, int line) {
        /** The message tail every engine prints after naming the file, byte for byte. */
        public String message() {
            return "is outside the YAML profile: " + rule + " at line " + line + " (SPEC 8.7)";
        }

        public String hint() {
            return HINTS.get(rule);
        }
    }

    /** A YAML file outside the profile; the caller refuses it under the file role's {@code input.*} key. An
     * {@link IllegalArgumentException}, as every other YAML refusal of the compose path is. */
    public static final class ProfileException extends IllegalArgumentException {
        private static final long serialVersionUID = 1L;
        private final transient Violation violation;

        ProfileException(Violation violation) {
            super(violation.message());
            this.violation = violation;
        }

        public Violation violation() {
            return violation;
        }
    }

    /** The scanner's own unwinding signal (Python's {@code _Violation}). */
    private static final class Found extends RuntimeException {
        private static final long serialVersionUID = 1L;
        private final transient Violation found;

        Found(String rule, int line) {
            super(rule, null, false, false);
            this.found = new Violation(rule, line);
        }
    }

    /** The rule a plain scalar's text breaks, or {@code null}. */
    static String scalarRule(String text) {
        if (YAML11_BOOL.matcher(text).matches()) {
            return "yaml11_bool";
        }
        if (UNDERSCORE_NUMBER.matcher(text).matches() && text.indexOf('_') >= 0) {
            return "underscore_number";
        }
        if (SEXAGESIMAL.matcher(text).matches()) {
            return "sexagesimal";
        }
        if (OCTAL.matcher(text).matches()) {
            return "octal";
        }
        if (EXPONENT_NO_DOT.matcher(text).matches()) {
            return "exponent_no_dot";
        }
        if (LEADING_ZERO.matcher(text).matches()) {
            return "leading_zero";
        }
        if (EXPONENT_NO_SIGN.matcher(text).matches()) {
            return "exponent_no_sign";
        }
        return null;
    }

    /** The first construct in {@code text} outside the SPEC 8.7 YAML profile, or {@code null} (repeated keys
     * excepted, see {@link #duplicateKeyLine}). */
    public static Violation violation(String text) {
        return new Scanner(text).run();
    }

    /** Throws {@link ProfileException} for the first construct in {@code text} outside the profile. */
    static void check(String text) {
        Violation found = violation(text);
        if (found != null) {
            throw new ProfileException(found);
        }
    }

    /** Throws {@link ProfileException} ({@code duplicate_key}) when a mapping under {@code node} repeats a key. */
    static void checkDuplicateKeys(Node node) {
        Integer line = duplicateKeyLine(node);
        if (line != null) {
            throw new ProfileException(new Violation("duplicate_key", line));
        }
    }

    /** The line (1-based) of the earliest key that repeats an earlier key of its mapping, or {@code null}. */
    static Integer duplicateKeyLine(Node node) {
        Integer lowest = null;
        Deque<Node> pending = new ArrayDeque<>();
        if (node != null) {
            pending.push(node);
        }
        while (!pending.isEmpty()) {
            Node current = pending.pop();
            if (current instanceof MappingNode map) {
                Set<String> seen = new HashSet<>();
                for (NodeTuple tuple : map.getValue()) {
                    if (tuple.getKeyNode() instanceof ScalarNode key) {
                        if (!seen.add(key.getValue())) {
                            int line = key.getStartMark().getLine() + 1;
                            lowest = lowest == null ? line : Math.min(lowest, line);
                        }
                    }
                    pending.push(tuple.getValueNode());
                }
            } else if (current instanceof SequenceNode seq) {
                for (Node child : seq.getValue()) {
                    pending.push(child);
                }
            }
        }
        return lowest;
    }

    private static boolean spaceOrEnd(int[] line, int i) {
        return i >= line.length || line[i] == ' ' || line[i] == '\t';
    }

    private static boolean startsWith(int[] line, String prefix) {
        if (line.length < prefix.length()) {
            return false;
        }
        for (int k = 0; k < prefix.length(); k++) {
            if (line[k] != prefix.charAt(k)) {
                return false;
            }
        }
        return true;
    }

    private static boolean isFlowIndicator(int ch) {
        return ch < 0x80 && FLOW_INDICATORS.indexOf(ch) >= 0;
    }

    /** {@code line[start:end]} as text with trailing spaces removed (Python's {@code rstrip(" ")}). */
    private static String rstripped(int[] line, int start, int end) {
        while (end > start && line[end - 1] == ' ') {
            end--;
        }
        return new String(line, start, end - start);
    }

    private record Pending(String text, int line, int owner) {}

    private record Block(int col, String kind) {}

    private static final class Scanner {
        private final String text;
        private int ln = 0;
        private boolean contentSeen = false;
        private boolean docStartSeen = false;
        /** Open block collections: (column, "map" | "seq"). */
        private final List<Block> stack = new ArrayList<>();
        /** Open flow collections. */
        private final List<Integer> flow = new ArrayList<>();
        /** The enclosing block's indentation when the outermost flow collection opened. */
        private int flowIndent = -1;
        /** A quoted scalar left open at the end of a line (0 when none). */
        private int quote = 0;
        /** The parent indentation while inside a block scalar body ({@code null} when none). */
        private Integer blockScalar = null;
        /** A block plain scalar that may continue: text, line, owner. */
        private Pending pending = null;
        private boolean pendingMulti = false;

        Scanner(String text) {
            if (text.startsWith("﻿")) {
                text = text.substring(1);
            }
            this.text = text;
        }

        private Found fail(String rule) {
            return new Found(rule, ln);
        }

        private Found fail(String rule, int line) {
            return new Found(rule, line);
        }

        Violation run() {
            try {
                // The lines split on \r\n, \r or \n (Python's re.split), each read as code points when reached, so a
                // huge file refused early is never copied whole.
                int pos = 0;
                int n = text.length();
                while (true) {
                    int end = pos;
                    while (end < n && text.charAt(end) != '\r' && text.charAt(end) != '\n') {
                        end++;
                    }
                    ln++;
                    line(text.substring(pos, end).codePoints().toArray());
                    if (end >= n) {
                        break;
                    }
                    pos = text.charAt(end) == '\r' && end + 1 < n && text.charAt(end + 1) == '\n' ? end + 2 : end + 1;
                }
                finishPending();
            } catch (Found found) {
                return found.found;
            }
            return null;
        }

        // -- depth ------------------------------------------------------------------------------------------------

        private void checkDepth() {
            if (stack.size() + flow.size() > MAX_DEPTH) {
                throw fail("depth_over_100");
            }
        }

        private void blockEntry(int col, String kind) {
            if (!stack.isEmpty() && last().col() == col) {
                String top = last().kind();
                if (top.equals(kind)) {
                    return;
                }
                if (top.equals("seq") && kind.equals("map")) {
                    stack.remove(stack.size() - 1); // an indentless sequence ends at the next key of its mapping
                    if (!stack.isEmpty() && last().equals(new Block(col, "map"))) {
                        return;
                    }
                }
            }
            stack.add(new Block(col, kind));
            checkDepth();
        }

        private Block last() {
            return stack.get(stack.size() - 1);
        }

        private int owner() {
            return stack.isEmpty() ? -1 : last().col();
        }

        // -- plain scalars ----------------------------------------------------------------------------------------

        private void checkPlain(String text, int line, boolean isKey) {
            if (text.equals("<<")) {
                throw fail("merge_key", line);
            }
            String rule = scalarRule(text);
            if (rule != null) {
                throw fail(rule, line);
            }
            if (isKey && NON_STRING_KEY.matcher(text).matches()) {
                throw fail("non_string_key", line);
            }
        }

        private void finishPending() {
            if (pending != null) {
                Pending p = pending;
                boolean multi = pendingMulti;
                pending = null;
                pendingMulti = false;
                if (!multi) {
                    checkPlain(p.text(), p.line(), false);
                }
            }
        }

        // -- lines ------------------------------------------------------------------------------------------------

        private void line(int[] line) {
            if ((startsWith(line, "---") || startsWith(line, "...")) && spaceOrEnd(line, 3)) {
                if (blockScalar != null) {
                    blockScalar = null;
                }
                if (startsWith(line, "---") && !contentSeen && !docStartSeen && quote == 0) {
                    docStartSeen = true;
                    scan(line, 3);
                    return;
                }
                finishPending();
                throw fail("second_document");
            }
            if (line.length > 0 && line[0] == '%' && quote == 0 && blockScalar == null) {
                finishPending();
                throw fail("directive");
            }
            int col = 0;
            while (col < line.length && line[col] == ' ') {
                col++;
            }
            boolean blank = col == line.length;
            if (blockScalar != null) {
                if (blank || col > blockScalar) {
                    return; // the block scalar's body: text, tabs included
                }
                blockScalar = null;
            }
            if (blank) {
                return;
            }
            if (!flow.isEmpty() && col <= flowIndent) {
                throw fail("flow_line_not_indented");
            }
            if (quote != 0) {
                int end = quotedEnd(line, 0, quote);
                if (end < 0) {
                    return;
                }
                quote = 0;
                end = afterScalar(line, end, null, false);
                if (end >= 0) {
                    scan(line, end);
                }
                return;
            }
            if (pending != null) {
                if (line[col] != '#' && col > pending.owner()) {
                    pendingMulti = true;
                    continuation(line, col);
                    return;
                }
                finishPending();
            }
            if (flow.isEmpty()) {
                while (!stack.isEmpty() && last().col() > col) {
                    stack.remove(stack.size() - 1);
                }
            }
            scan(line, col);
        }

        /** A block plain scalar continued on a more indented line: text up to a comment. */
        private void continuation(int[] line, int i) {
            while (i < line.length) {
                int ch = line[i];
                if (ch == '\t') {
                    throw fail("tab");
                }
                if (ch == '#' && line[i - 1] == ' ') {
                    comment(line, i);
                    finishPending();
                    return;
                }
                i++;
            }
        }

        private void comment(int[] line, int i) {
            for (int k = i; k < line.length; k++) {
                if (line[k] == '\t') {
                    throw fail("tab");
                }
            }
        }

        private int skipSpaces(int[] line, int i) {
            while (i < line.length && (line[i] == ' ' || line[i] == '\t')) {
                if (line[i] == '\t') {
                    throw fail("tab");
                }
                i++;
            }
            return i;
        }

        /** The index after the closing {@code quote} from {@code i} on, or -1 when the scalar runs past the line. */
        private int quotedEnd(int[] line, int i, int quote) {
            while (i < line.length) {
                int ch = line[i];
                if (quote == '"' && ch == '\\') {
                    String escape = new String(line, i, Math.min(10, line.length - i));
                    if (SURROGATE_ESCAPE.matcher(escape).lookingAt()) {
                        throw fail("surrogate_escape");
                    }
                    i += 2;
                    continue;
                }
                if (ch == quote) {
                    if (quote == '\'' && i + 1 < line.length && line[i + 1] == '\'') {
                        i += 2;
                        continue;
                    }
                    return i + 1;
                }
                i++;
            }
            return -1;
        }

        /** Read tokens from {@code i} to the end of the line; every position reached after spaces is a token start. */
        private void scan(int[] line, int i) {
            while (true) {
                i = skipSpaces(line, i);
                if (i >= line.length) {
                    return;
                }
                int ch = line[i];
                int nxt = i + 1 < line.length ? line[i + 1] : -1;
                if (ch == '#') {
                    comment(line, i);
                    return;
                }
                if (ch == '&') {
                    throw fail("anchor");
                }
                if (ch == '*') {
                    throw fail("alias");
                }
                if (ch == '!') {
                    throw fail("tag");
                }
                contentSeen = true;
                if (ch == '?') {
                    if (spaceOrEnd(line, i + 1) || (!flow.isEmpty() && isFlowIndicator(nxt))) {
                        throw fail("explicit_key");
                    }
                    if (!flow.isEmpty()) {
                        throw fail("flow_scalar_indicator_start");
                    }
                }
                if (ch == ':') {
                    if (spaceOrEnd(line, i + 1) || (!flow.isEmpty() && isFlowIndicator(nxt))) {
                        throw fail("non_string_key"); // a value with no key: the key is empty
                    }
                    if (!flow.isEmpty()) {
                        throw fail("flow_scalar_indicator_start");
                    }
                }
                if (!flow.isEmpty()) {
                    if (ch == '[' || ch == '{') {
                        openFlow(ch);
                        i++;
                        continue;
                    }
                    if (ch == ']' || ch == '}') {
                        flow.remove(flow.size() - 1);
                        i = afterScalar(line, i + 1, null, true);
                        if (i < 0) {
                            return;
                        }
                        continue;
                    }
                    if (ch == ',') {
                        i++;
                        continue;
                    }
                } else {
                    if (ch == '-' && spaceOrEnd(line, i + 1)) {
                        blockEntry(i, "seq");
                        i++;
                        continue;
                    }
                    if (ch == '|' || ch == '>') {
                        blockScalar = owner();
                        comment(line, i);
                        return;
                    }
                    if (ch == '[' || ch == '{') {
                        flowIndent = owner();
                        openFlow(ch);
                        i++;
                        continue;
                    }
                }
                if (ch == '\'' || ch == '"') {
                    int end = quotedEnd(line, i + 1, ch);
                    if (end < 0) {
                        quote = ch;
                        return;
                    }
                    i = afterScalar(line, end, i, false);
                    if (i < 0) {
                        return;
                    }
                    continue;
                }
                i = plain(line, i);
                if (i < 0) {
                    return;
                }
            }
        }

        private void openFlow(int ch) {
            flow.add(ch);
            checkDepth();
        }

        /** After a quoted scalar or a closed flow collection: a {@code :} makes it a key. Returns where to go on, or
         * -1. */
        private int afterScalar(int[] line, int i, Integer keyCol, boolean collection) {
            int j = skipSpaces(line, i);
            if (j < line.length && line[j] == ':' && (!flow.isEmpty() || spaceOrEnd(line, j + 1))) {
                if (collection) {
                    throw fail("non_string_key");
                }
                if (flow.isEmpty() && keyCol != null) {
                    blockEntry(keyCol, "map");
                }
                return j + 1;
            }
            if (j < line.length && line[j] == '#') {
                comment(line, j);
                return -1;
            }
            return j;
        }

        /** A plain scalar from {@code start}: checked when it ends (a key at once). Returns where to go on, or -1. */
        private int plain(int[] line, int start) {
            int i = start;
            int n = line.length;
            while (i < n) {
                int ch = line[i];
                if (ch == '\t') {
                    throw fail("tab");
                }
                if (ch == '#' && line[i - 1] == ' ') {
                    break;
                }
                if (ch == ':' && (spaceOrEnd(line, i + 1) || (!flow.isEmpty() && isFlowIndicator(line[i + 1])))) {
                    String text = rstripped(line, start, i);
                    checkPlain(text, ln, true);
                    if (flow.isEmpty()) {
                        blockEntry(start, "map");
                    }
                    return i + 1;
                }
                if (!flow.isEmpty() && (ch == ',' || ch == '?' || isFlowIndicator(ch))) {
                    break;
                }
                i++;
            }
            String text = rstripped(line, start, i);
            if (!flow.isEmpty()) {
                checkPlain(text, ln, false);
                return i;
            }
            if (i < n) { // ended by a comment
                checkPlain(text, ln, false);
                comment(line, i);
                return -1;
            }
            pending = new Pending(text, ln, owner());
            pendingMulti = false;
            return -1;
        }
    }
}
