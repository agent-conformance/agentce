package org.agentce;

import java.util.ArrayList;
import java.util.List;

/**
 * The PyYAML scanner rules SnakeYAML does not keep (18.184p): where a tab may sit, a {@code ?} ending a plain scalar in
 * a flow collection, and an empty flow key. {@link #problem} walks the text as PyYAML's scanner does (its indent stack,
 * simple keys, plain, quoted and block scalar extents, directives, anchors, tags and aliases) and checks each top-level
 * flow collection against PyYAML's flow grammar. It reports only a refusal PyYAML makes; a document PyYAML refuses for
 * another reason may pass, and the composer refuses it.
 */
final class PyyamlScanner {
    private static final String BREAKS = "\r\n\u0085  ";
    private static final String WS = " \t" + BREAKS;
    private static final String END = "\0" + WS;
    private static final String FLOW_IND = ",[]{}";
    private static final String NOT_BREAK_END = "\0" + BREAKS;

    private static final class Problem extends RuntimeException {
        private static final long serialVersionUID = 1L;

        Problem(String message) {
            super(message, null, false, false);
        }
    }

    private record Tok(String kind, int line) {}

    private final int[] t;
    private final int n;
    private int i;
    private int lineStart;
    private int line;
    private final List<Integer> indents = new ArrayList<>();
    private int indent = -1;
    private int depth;
    private List<Tok> flow = new ArrayList<>();
    private boolean allowKey = true;
    private int keyLine = -1;
    private int keyCol;

    private PyyamlScanner(String text) {
        t = text.codePoints().toArray();
        n = t.length;
        i = n > 0 && t[0] == 0xFEFF ? 1 : 0;
        lineStart = i;
    }

    /** {@code null} when PyYAML's scanner and flow parser get past every tab, {@code ?}, {@code :} and {@code -} in
     * {@code text}, else PyYAML's reason. */
    static String problem(String text) {
        try {
            new PyyamlScanner(text).run();
        } catch (Problem e) {
            return e.getMessage();
        }
        return null;
    }

    private static boolean in(int c, String set) {
        return set.indexOf(c) >= 0;
    }

    private int peek(int k) {
        int j = i + k;
        return j < n ? t[j] : 0;
    }

    private int peek() {
        return peek(0);
    }

    private int col() {
        return i - lineStart;
    }

    private boolean startsWith(String s) {
        if (i + s.length() > n) {
            return false;
        }
        for (int k = 0; k < s.length(); k++) {
            if (t[i + k] != s.charAt(k)) {
                return false;
            }
        }
        return true;
    }

    private boolean documentMarker() {
        return col() == 0 && (startsWith("---") || startsWith("...")) && in(peek(3), END);
    }

    private static boolean nameChar(int c) {
        return c < 128 && (Character.isLetterOrDigit(c) || c == '-' || c == '_');
    }

    private void newline() {
        i += peek() == '\r' && peek(1) == '\n' ? 2 : 1;
        lineStart = i;
        line++;
        if (depth == 0) {
            allowKey = true;
        }
    }

    private void tab() {
        throw new Problem("found character '\\t' that cannot start any token (line " + (line + 1) + ")");
    }

    private void addIndent(int column) {
        if (indent < column) {
            indents.add(indent);
            indent = column;
        }
    }

    private void emit(String kind, int atLine) {
        flow.add(new Tok(kind, atLine));
    }

    private void emit(String kind) {
        emit(kind, line);
    }

    private void nodeStart() {
        if (depth == 0 && allowKey) {
            keyLine = line;
            keyCol = col();
        }
    }

    private void run() {
        while (true) {
            while (true) {
                int c = peek();
                if (c == ' ') {
                    i++;
                } else if (c == '#') {
                    while (!in(peek(), NOT_BREAK_END)) {
                        i++;
                    }
                } else if (in(c, BREAKS)) {
                    newline();
                } else {
                    break;
                }
            }
            if (i >= n) {
                break;
            }
            if (keyLine >= 0 && keyLine != line) {
                keyLine = -1;
            }
            if (depth == 0) {
                while (indent > col()) {
                    indent = indents.remove(indents.size() - 1);
                }
            }
            token();
        }
        if (depth > 0) {
            throw new Problem("a flow collection is not closed");
        }
    }

    private void token() {
        int c = peek();
        boolean nextEnds = in(peek(1), END);
        if (c == '\t') {
            tab();
        }
        if (c == '%' && col() == 0) {
            directive();
            return;
        }
        if (documentMarker()) {
            if (depth > 0) {
                throw new Problem("a document marker inside a flow collection");
            }
            i += 3;
            indents.clear();
            indent = -1;
            keyLine = -1;
            return;
        }
        if (c == '[' || c == '{') {
            nodeStart();
            depth++;
            emit(Character.toString(c));
            i++;
            allowKey = true;
            return;
        }
        if (c == ']' || c == '}') {
            emit(Character.toString(c));
            i++;
            allowKey = false;
            if (depth > 0) {
                depth--;
                if (depth == 0) {
                    List<Tok> done = flow;
                    flow = new ArrayList<>();
                    checkFlow(done);
                }
            }
            return;
        }
        if (c == ',' && depth > 0) {
            emit(",");
            i++;
            allowKey = true;
            return;
        }
        if (c == '-' && nextEnds) {
            if (depth > 0) {
                throw new Problem("a block entry inside a flow collection");
            }
            addIndent(col());
            i++;
            allowKey = true;
            return;
        }
        if (c == '?' && (depth > 0 || nextEnds)) {
            if (depth > 0) {
                emit("?");
                allowKey = false;
            } else {
                addIndent(col());
                allowKey = true;
            }
            i++;
            return;
        }
        if (c == ':' && (depth > 0 || nextEnds)) {
            if (depth > 0) {
                emit(":");
            } else {
                if (keyLine >= 0) {
                    addIndent(keyCol);
                    keyLine = -1;
                } else {
                    addIndent(col());
                }
                allowKey = true;
            }
            i++;
            return;
        }
        if (c == '&' || c == '!' || c == '*') {
            nodeStart();
            i++;
            if (c == '!') {
                while (!in(peek(), END)) {
                    i++;
                }
            } else {
                while (nameChar(peek())) {
                    i++;
                }
            }
            if (depth > 0) {
                emit(c == '&' ? "anchor" : c == '!' ? "tag" : "alias");
            }
            allowKey = false;
            return;
        }
        if ((c == '|' || c == '>') && depth == 0) {
            allowKey = true;
            blockScalar();
            return;
        }
        nodeStart();
        allowKey = false;
        int atLine = line;
        if (c == '\'' || c == '"') {
            quoted(c);
            allowKey = false;
        } else {
            plain(); // a line break inside allows a simple key again, as scan_plain_spaces does
        }
        if (depth > 0) {
            emit("node", atLine);
        }
    }

    /** scan_directive: a name, then for YAML and TAG values and spaces (a tab refused); any other directive's line is
     * skipped whole. */
    private void directive() {
        i++;
        int start = i;
        while (nameChar(peek())) {
            i++;
        }
        if (peek() == '\t') {
            tab();
        }
        String name = new String(t, start, i - start);
        boolean known = name.equals("YAML") || name.equals("TAG");
        while (!in(peek(), NOT_BREAK_END)) {
            if (known && peek() == '\t') {
                tab();
            }
            if (known && peek() == '#' && t[i - 1] == ' ') {
                break;
            }
            i++;
        }
        while (!in(peek(), NOT_BREAK_END)) {
            i++;
        }
    }

    private void quoted(int q) {
        i++;
        while (true) {
            int c = peek();
            if (i >= n) {
                throw new Problem("a quoted scalar is not closed");
            }
            if (q == '\'' && c == '\'') {
                if (peek(1) == '\'') {
                    i += 2;
                    continue;
                }
                i++;
                return;
            }
            if (q == '"' && c == '\\') {
                i++;
                if (in(peek(), BREAKS)) {
                    newline();
                } else {
                    i++;
                }
                continue;
            }
            if (q == '"' && c == '"') {
                i++;
                return;
            }
            if (in(c, BREAKS)) {
                newline();
                continue;
            }
            i++;
        }
    }

    private void plain() {
        boolean inFlow = depth > 0;
        int minIndent = indent + 1;
        while (true) {
            if (peek() == '#') {
                return;
            }
            int length = 0;
            while (true) {
                int ch = peek(length);
                if (in(ch, END)
                        || (ch == ':' && in(peek(length + 1), inFlow ? END + FLOW_IND : END))
                        || (inFlow && in(ch, ",?[]{}"))) {
                    break;
                }
                length++;
            }
            if (length == 0) {
                return;
            }
            i += length;
            int markI = i;
            int markLineStart = lineStart;
            int markLine = line;
            while (peek() == ' ') {
                i++;
            }
            if (in(peek(), BREAKS)) {
                newline();
                while (true) {
                    if (documentMarker()) {
                        i = markI;
                        lineStart = markLineStart;
                        line = markLine;
                        return;
                    }
                    if (peek() == ' ') {
                        i++;
                    } else if (in(peek(), BREAKS)) {
                        newline();
                    } else {
                        break;
                    }
                }
            } else if (i == markI) {
                return;
            }
            if (peek() == '#' || i >= n || (!inFlow && col() < minIndent)) {
                return;
            }
        }
    }

    private void blockScalar() {
        int minIndent = Math.max(indent + 1, 1);
        i++;
        int increment = -1;
        for (int k = 0; k < 2; k++) {
            int c = peek();
            if (c == '+' || c == '-') {
                i++;
            } else if (c >= '1' && c <= '9' && increment < 0) {
                increment = c - '0';
                i++;
            }
        }
        while (peek() == ' ') {
            i++;
        }
        if (peek() == '#') {
            while (!in(peek(), NOT_BREAK_END)) {
                i++;
            }
        }
        if (!in(peek(), NOT_BREAK_END)) {
            if (peek() == '\t') {
                tab();
            }
            throw new Problem("expected a comment or a line break");
        }
        if (i >= n) {
            return;
        }
        newline();
        int blockIndent;
        if (increment < 0) {
            int maxIndent = 0;
            while ((peek() == ' ' || in(peek(), BREAKS)) && i < n) {
                if (peek() == ' ') {
                    i++;
                    maxIndent = Math.max(maxIndent, col());
                } else {
                    newline();
                }
            }
            blockIndent = Math.max(minIndent, maxIndent);
        } else {
            blockIndent = minIndent + increment - 1;
            breaks(blockIndent);
        }
        while (col() == blockIndent && i < n) {
            while (!in(peek(), NOT_BREAK_END)) {
                i++;
            }
            if (i >= n) {
                return;
            }
            newline();
            breaks(blockIndent);
        }
    }

    private void breaks(int blockIndent) {
        while (col() < blockIndent && peek() == ' ') {
            i++;
        }
        while (in(peek(), BREAKS) && i < n) {
            newline();
            while (col() < blockIndent && peek() == ' ') {
                i++;
            }
        }
    }

    private static void checkFlow(List<Tok> toks) {
        List<String> keyed = withKeys(toks);
        if (!keyed.isEmpty() && keyed.get(0).equals("?")) {
            keyed = keyed.subList(1, keyed.size());
        }
        new Flow(keyed).collection();
    }

    private static boolean isOpen(String kind) {
        return kind.equals("[") || kind.equals("{");
    }

    private static boolean isClose(String kind) {
        return kind.equals("]") || kind.equals("}");
    }

    private static boolean isProperty(String kind) {
        return kind.equals("anchor") || kind.equals("tag");
    }

    /** The flow tokens with PyYAML's KEY token inserted before a node that a {@code :} on its own line follows. */
    private static List<String> withKeys(List<Tok> toks) {
        List<String> out = new ArrayList<>();
        int k = 0;
        while (k < toks.size()) {
            Tok tok = toks.get(k);
            String kind = tok.kind();
            if (!(kind.equals("node") || kind.equals("alias") || isOpen(kind) || isProperty(kind))) {
                out.add(kind);
                k++;
                continue;
            }
            int start = k;
            while (k < toks.size() && isProperty(toks.get(k).kind())) {
                k++;
            }
            List<String> body = new ArrayList<>();
            for (Tok p : toks.subList(start, k)) {
                body.add(p.kind());
            }
            if (k < toks.size() && isOpen(toks.get(k).kind())) {
                int lvl = 0;
                int j = k;
                while (j < toks.size()) {
                    if (isOpen(toks.get(j).kind())) {
                        lvl++;
                    } else if (isClose(toks.get(j).kind())) {
                        lvl--;
                        if (lvl == 0) {
                            break;
                        }
                    }
                    j++;
                }
                body.add(toks.get(k).kind());
                body.addAll(withKeys(toks.subList(k + 1, Math.min(j, toks.size()))));
                if (j < toks.size()) {
                    body.add(toks.get(j).kind());
                }
                k = j + 1;
            } else if (k < toks.size()
                    && (toks.get(k).kind().equals("node") || (toks.get(k).kind().equals("alias") && k == start))) {
                body.add(toks.get(k).kind());
                k++;
            }
            if (k < toks.size()
                    && toks.get(k).kind().equals(":")
                    && toks.get(k).line() == tok.line()
                    && !(!out.isEmpty() && out.get(out.size() - 1).equals("?"))) {
                out.add("?");
            }
            out.addAll(body);
        }
        return out;
    }

    /** PyYAML's flow grammar over the keyed tokens. */
    private static final class Flow {
        private final List<String> t;
        private int k;

        Flow(List<String> toks) {
            t = toks;
        }

        String peek() {
            return k < t.size() ? t.get(k) : "eof";
        }

        void take() {
            k++;
        }

        void fail(String why) {
            throw new Problem("while parsing a flow collection: " + why + ", but found " + Readiness.pyRepr(peek()));
        }

        /** parse_node: an alias, or at most one anchor and one tag (either order) before the content, which may be
         * empty after them. */
        void node() {
            if (peek().equals("alias")) {
                take();
                return;
            }
            List<String> props = new ArrayList<>();
            while (isProperty(peek()) && !props.contains(peek())) {
                props.add(peek());
                take();
            }
            if (peek().equals("node")) {
                take();
            } else if (isOpen(peek())) {
                collection();
            } else if (props.isEmpty()) {
                fail("expected the node content");
            }
        }

        void collection() {
            String end = peek().equals("[") ? "]" : "}";
            take();
            boolean first = true;
            while (!peek().equals(end)) {
                if (!first) {
                    if (!peek().equals(",")) {
                        fail("expected ',' or '" + end + "'");
                    }
                    take();
                    if (peek().equals(end)) {
                        break;
                    }
                }
                first = false;
                if (peek().equals("?")) {
                    take();
                    if (!(peek().equals(":") || peek().equals(",") || peek().equals(end))) {
                        node();
                    }
                    if (peek().equals(":")) {
                        take();
                        if (!(peek().equals(",") || peek().equals(end))) {
                            node();
                        }
                    }
                } else {
                    node();
                }
            }
            take();
        }
    }
}
