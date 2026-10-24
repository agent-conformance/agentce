package org.agentce;

import java.nio.charset.StandardCharsets;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Deque;
import java.util.HashSet;
import java.util.List;
import java.util.Set;

/**
 * The report-file reader contract (SPEC 8.7) for the files {@code report --validate} reads that are not
 * JSON: {@code report.csv}, {@code report.junit.xml}, {@code oscal-ar.xml}, {@code report.md} and {@code
 * report.html}. Bytes are strict UTF-8 with no byte-order mark; CSV is RFC 4180; XML is well-formed XML
 * 1.0 with no DTD, no entity beyond the five predefined, nothing after the root, no control character and
 * every prefix bound; the empty check uses space, tab, CR and LF. Hand-written so no library decides an
 * outcome; the same design as Python's {@code report_reader.py} and TypeScript's {@code reportReader.ts}
 * ({@code harness/remediation/evidence/P18-18.60/design.md}). A file outside the contract throws {@link
 * ReaderException} naming the first fault's rule and line.
 */
public final class ReportReader {
    private ReportReader() {}

    /** A file outside the reader contract: {@code rule} is a census class token, {@code line} is 1 + the
     * LFs before the fault. */
    public static final class ReaderException extends IllegalArgumentException {
        private static final long serialVersionUID = 1L;
        private final String rule;
        private final int line;

        public ReaderException(String rule, int line) {
            super("outside the report-file reader contract: " + rule + " at line " + line + " (SPEC 8.7)");
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

    /** {@code data} as text: a leading byte-order mark is {@code bom}, then any invalid or overlong
     * sequence, encoded surrogate, code point past U+10FFFF or truncated sequence is {@code invalid_utf8}
     * at the line of its first byte. */
    public static String decodeReportText(byte[] data) {
        if (Json.hasBom(data)) {
            throw new ReaderException("bom", 1);
        }
        int bad = Json.invalidUtf8At(data, 0);
        if (bad >= 0) {
            int line = 1;
            for (int k = 0; k < bad; k++) {
                if (data[k] == '\n') {
                    line += 1;
                }
            }
            throw new ReaderException("invalid_utf8", line);
        }
        return new String(data, StandardCharsets.UTF_8);
    }

    /** Whether {@code text} holds only space, tab, CR and LF (none at all included). */
    public static boolean isBlank(String text) {
        for (int i = 0; i < text.length(); i++) {
            char c = text.charAt(i);
            if (c != ' ' && c != '\t' && c != '\r' && c != '\n') {
                return false;
            }
        }
        return true;
    }

    private static int lineAt(String text, int index) {
        int line = 1;
        for (int k = 0; k < index && k < text.length(); k++) {
            if (text.charAt(k) == '\n') {
                line += 1;
            }
        }
        return line;
    }

    private static ReaderException fault(String rule, String text, int index) {
        return new ReaderException(rule, lineAt(text, index));
    }

    // --- CSV (RFC 4180) ----------------------------------------------------------------------------

    /** The records of {@code text}, each a list of fields: a record ends at LF or CR LF outside quotes; a
     * quoted field may hold line ends, commas and doubled quotes; an empty line is a record with no
     * fields, even at the very end (SPEC 8.7: the final line end is optional, an empty line after it is
     * refused). A bare CR outside
     * quotes, text after a closing quote, a quote inside an unquoted field or an unclosed quote throws. */
    public static List<List<String>> readCsv(String text) {
        List<List<String>> records = new ArrayList<>();
        int n = text.length();
        int i = 0;
        while (i < n) {
            List<String> record = new ArrayList<>();
            records.add(record);
            int end = recordEnd(text, i);
            if (end > 0) {
                i = end; // an empty line: a record with no fields
                continue;
            }
            while (true) {
                StringBuilder field = new StringBuilder();
                if (i < n && text.charAt(i) == '"') {
                    int open = i;
                    i += 1;
                    while (true) {
                        if (i >= n) {
                            throw fault("csv_unclosed_quote", text, open);
                        }
                        char c = text.charAt(i);
                        if (c == '"') {
                            if (i + 1 < n && text.charAt(i + 1) == '"') {
                                field.append('"');
                                i += 2;
                                continue;
                            }
                            i += 1;
                            break;
                        }
                        field.append(c);
                        i += 1;
                    }
                    // After the closing quote: a comma, LF, CR LF or the end; anything else, a bare CR
                    // included, is text after the quote.
                    if (i < n && text.charAt(i) != ',' && text.charAt(i) != '\n'
                            && !text.startsWith("\r\n", i)) {
                        throw fault("csv_text_after_quote", text, i);
                    }
                } else {
                    while (i < n) {
                        char c = text.charAt(i);
                        if (c == ',' || c == '\n') {
                            break;
                        }
                        if (c == '\r') {
                            if (i + 1 < n && text.charAt(i + 1) == '\n') {
                                break;
                            }
                            throw fault("csv_bare_cr", text, i);
                        }
                        if (c == '"') {
                            throw fault("csv_quote_in_field", text, i);
                        }
                        field.append(c);
                        i += 1;
                    }
                }
                record.add(field.toString());
                if (i >= n) {
                    break;
                }
                if (text.charAt(i) == ',') {
                    i += 1;
                    continue;
                }
                i = recordEnd(text, i);
                break;
            }
        }
        return records;
    }

    /** The index just past a record end (LF or CR LF) at {@code i}, or -1 when there is none there; a
     * bare CR (not followed by LF) is {@code csv_bare_cr}. */
    private static int recordEnd(String text, int i) {
        if (i >= text.length()) {
            return -1;
        }
        char c = text.charAt(i);
        if (c == '\n') {
            return i + 1;
        }
        if (c == '\r') {
            if (i + 1 < text.length() && text.charAt(i + 1) == '\n') {
                return i + 2;
            }
            throw fault("csv_bare_cr", text, i);
        }
        return -1;
    }

    // --- XML 1.0 well-formedness ---------------------------------------------------------------------

    private static final Set<String> PREDEFINED_ENTITIES = Set.of("amp", "lt", "gt", "quot", "apos");

    /** Whether {@code cp} is an XML 1.0 {@code Char}. */
    static boolean isXmlChar(long cp) {
        return cp == 0x9 || cp == 0xA || cp == 0xD
                || (cp >= 0x20 && cp <= 0xD7FF)
                || (cp >= 0xE000 && cp <= 0xFFFD)
                || (cp >= 0x10000 && cp <= 0x10FFFF);
    }

    /** XML 1.0 Fifth Edition {@code NameStartChar}. */
    static boolean isNameStartChar(int cp) {
        return cp == ':' || (cp >= 'A' && cp <= 'Z') || cp == '_' || (cp >= 'a' && cp <= 'z')
                || (cp >= 0xC0 && cp <= 0xD6) || (cp >= 0xD8 && cp <= 0xF6) || (cp >= 0xF8 && cp <= 0x2FF)
                || (cp >= 0x370 && cp <= 0x37D) || (cp >= 0x37F && cp <= 0x1FFF) || (cp >= 0x200C && cp <= 0x200D)
                || (cp >= 0x2070 && cp <= 0x218F) || (cp >= 0x2C00 && cp <= 0x2FEF) || (cp >= 0x3001 && cp <= 0xD7FF)
                || (cp >= 0xF900 && cp <= 0xFDCF) || (cp >= 0xFDF0 && cp <= 0xFFFD) || (cp >= 0x10000 && cp <= 0xEFFFF);
    }

    /** XML 1.0 Fifth Edition {@code NameChar}. */
    static boolean isNameChar(int cp) {
        return isNameStartChar(cp) || cp == '-' || cp == '.' || (cp >= '0' && cp <= '9') || cp == 0xB7
                || (cp >= 0x300 && cp <= 0x36F) || (cp >= 0x203F && cp <= 0x2040);
    }

    private static boolean isXmlSpace(char c) {
        return c == ' ' || c == '\t' || c == '\r' || c == '\n';
    }

    /** Throws {@link ReaderException} when {@code text} is not a well-formed XML 1.0 document under the
     * contract; the first fault in text order wins. */
    public static void checkXml(String text) {
        int badChar = -1;
        for (int i = 0; i < text.length(); ) {
            int cp = text.codePointAt(i);
            if (!isXmlChar(cp)) {
                badChar = i;
                break;
            }
            i += Character.charCount(cp);
        }
        int faultAt = Integer.MAX_VALUE;
        ReaderException found = null;
        try {
            new XmlScanner(text).document();
        } catch (ScanFault e) {
            faultAt = e.index;
            found = fault(e.rule, text, e.index);
        }
        // The scan reads the text in order, so a character outside Char before its own fault came first.
        if (badChar >= 0 && badChar < faultAt) {
            throw fault("xml_control_char", text, badChar);
        }
        if (found != null) {
            throw found;
        }
    }

    /** A scanner fault at a character index; turned into a {@link ReaderException} with its line. */
    private static final class ScanFault extends RuntimeException {
        private static final long serialVersionUID = 1L;
        final String rule;
        final int index;

        ScanFault(String rule, int index) {
            super(rule, null, false, false);
            this.rule = rule;
            this.index = index;
        }
    }

    private static final class XmlScanner {
        private final String s;
        private final int n;
        private int i;
        /** The prefixes bound on each open element, innermost first. */
        private final Deque<Set<String>> scopes = new ArrayDeque<>();

        XmlScanner(String text) {
            this.s = text;
            this.n = text.length();
        }

        private ScanFault syntax(int at) {
            return new ScanFault("xml_syntax", at);
        }

        private boolean at(String token) {
            return s.startsWith(token, i);
        }

        private boolean skipSpace() {
            int start = i;
            while (i < n && isXmlSpace(s.charAt(i))) {
                i += 1;
            }
            return i > start;
        }

        void document() {
            if (at("<?xml") && n > 5 && isXmlSpace(s.charAt(5))) {
                xmlDeclaration();
            }
            misc(false);
            if (i >= n || s.charAt(i) != '<') {
                throw syntax(i); // no root element, or text before it
            }
            element();
            misc(true);
        }

        /** {@code <?xml} at offset 0: {@code version}, then optionally {@code encoding} (UTF-8, any case)
         * and {@code standalone} ({@code yes} or {@code no}), in that order, then {@code ?>}. */
        private void xmlDeclaration() {
            List<String> order = List.of("version", "encoding", "standalone");
            int next = 0;
            i += 5;
            while (true) {
                boolean space = skipSpace();
                if (at("?>") && next > 0) {
                    i += 2;
                    return;
                }
                if (!space || i >= n) {
                    throw syntax(i);
                }
                int nameAt = i;
                String name = name();
                int position = order.indexOf(name);
                if (position < next || (next == 0 && position != 0)) {
                    throw syntax(nameAt);
                }
                next = position + 1;
                skipSpace();
                if (i >= n || s.charAt(i) != '=') {
                    throw syntax(i);
                }
                i += 1;
                skipSpace();
                String value = quotedPlain();
                boolean valid = switch (name) {
                    case "version" -> value.matches("1\\.[0-9]+");
                    case "encoding" -> value.equalsIgnoreCase("UTF-8");
                    default -> value.equals("yes") || value.equals("no");
                };
                if (!valid) {
                    throw syntax(nameAt);
                }
            }
        }

        /** A quoted value in the XML declaration (no references). */
        private String quotedPlain() {
            if (i >= n || (s.charAt(i) != '"' && s.charAt(i) != '\'')) {
                throw syntax(i);
            }
            char quote = s.charAt(i);
            int close = s.indexOf(quote, i + 1);
            if (close < 0) {
                throw syntax(i);
            }
            String value = s.substring(i + 1, close);
            i = close + 1;
            return value;
        }

        /** Misc*: whitespace, comments and processing instructions; after the root, anything else is
         * {@code xml_after_root}. A DOCTYPE anywhere is {@code xml_dtd}. */
        private void misc(boolean afterRoot) {
            while (true) {
                skipSpace();
                if (i >= n) {
                    return;
                }
                if (at("<!--")) {
                    comment();
                } else if (at("<!DOCTYPE")) {
                    throw new ScanFault("xml_dtd", i);
                } else if (at("<?")) {
                    processingInstruction();
                } else if (afterRoot) {
                    throw new ScanFault("xml_after_root", i);
                } else {
                    return;
                }
            }
        }

        private void comment() {
            int start = i;
            int dashes = s.indexOf("--", i + 4);
            if (dashes < 0) {
                throw syntax(start);
            }
            if (!s.startsWith("-->", dashes)) {
                throw syntax(dashes);
            }
            i = dashes + 3;
        }

        private void processingInstruction() {
            int start = i;
            i += 2;
            int nameAt = i;
            String target = name();
            if (target.equalsIgnoreCase("xml")) {
                throw syntax(nameAt); // an XML declaration not at offset 0, or a reserved target
            }
            if (at("?>")) {
                i += 2;
                return;
            }
            if (!skipSpace()) {
                throw syntax(i);
            }
            int close = s.indexOf("?>", i);
            if (close < 0) {
                throw syntax(start);
            }
            i = close + 2;
        }

        /** An XML Name at {@code i}, or {@code xml_syntax}. */
        private String name() {
            int start = i;
            if (i >= n || !isNameStartChar(s.codePointAt(i))) {
                throw syntax(i);
            }
            i += Character.charCount(s.codePointAt(i));
            while (i < n && isNameChar(s.codePointAt(i))) {
                i += Character.charCount(s.codePointAt(i));
            }
            return s.substring(start, i);
        }

        /** The prefix of a qualified name ({@code null} when it has none); more than one colon or an empty
         * side is {@code xml_syntax}. */
        private String prefix(String qname, int at) {
            int colon = qname.indexOf(':');
            if (colon < 0) {
                return null;
            }
            if (colon == 0 || colon == qname.length() - 1 || qname.indexOf(':', colon + 1) >= 0) {
                throw syntax(at);
            }
            return qname.substring(0, colon);
        }

        private boolean bound(String prefix, Set<String> own) {
            if (prefix.equals("xml") || own.contains(prefix)) {
                return true;
            }
            for (Set<String> scope : scopes) {
                if (scope.contains(prefix)) {
                    return true;
                }
            }
            return false;
        }

        private void element() {
            int start = i;
            i += 1;
            String tag = name();
            List<String> attributeNames = new ArrayList<>();
            List<Integer> attributeAt = new ArrayList<>();
            Set<String> own = new HashSet<>();
            boolean empty;
            while (true) {
                boolean space = skipSpace();
                if (at("/>")) {
                    i += 2;
                    empty = true;
                    break;
                }
                if (i < n && s.charAt(i) == '>') {
                    i += 1;
                    empty = false;
                    break;
                }
                if (!space || i >= n) {
                    throw syntax(i);
                }
                int nameAt = i;
                String attribute = name();
                if (attributeNames.contains(attribute)) {
                    throw syntax(nameAt);
                }
                skipSpace();
                if (i >= n || s.charAt(i) != '=') {
                    throw syntax(i);
                }
                i += 1;
                skipSpace();
                String value = attributeValue();
                attributeNames.add(attribute);
                attributeAt.add(nameAt);
                String attributePrefix = prefix(attribute, nameAt);
                if ("xmlns".equals(attributePrefix)) {
                    if (value.isEmpty()) {
                        throw syntax(nameAt);
                    }
                    own.add(attribute.substring(6));
                }
            }
            String tagPrefix = prefix(tag, start + 1);
            if ("xmlns".equals(tagPrefix)) {
                throw syntax(start + 1);
            }
            if (tagPrefix != null && !bound(tagPrefix, own)) {
                throw new ScanFault("xml_unbound_prefix", start + 1);
            }
            for (int k = 0; k < attributeNames.size(); k++) {
                String p = prefix(attributeNames.get(k), attributeAt.get(k));
                if (p != null && !p.equals("xmlns") && !bound(p, own)) {
                    throw new ScanFault("xml_unbound_prefix", attributeAt.get(k));
                }
            }
            if (empty) {
                return;
            }
            scopes.push(own);
            content(tag);
            scopes.pop();
        }

        /** A quoted attribute value; {@code <} in it is {@code xml_syntax}, references are checked. The
         * returned text is the raw value (only its emptiness is read). */
        private String attributeValue() {
            if (i >= n || (s.charAt(i) != '"' && s.charAt(i) != '\'')) {
                throw syntax(i);
            }
            char quote = s.charAt(i);
            i += 1;
            int start = i;
            while (true) {
                if (i >= n) {
                    throw syntax(i);
                }
                char c = s.charAt(i);
                if (c == quote) {
                    String value = s.substring(start, i);
                    i += 1;
                    return value;
                }
                if (c == '<') {
                    throw syntax(i);
                }
                if (c == '&') {
                    reference();
                } else {
                    i += 1;
                }
            }
        }

        /** A reference at {@code &}: the five predefined entities and character references to a Char. */
        private void reference() {
            int start = i;
            if (at("&#")) {
                boolean hex = at("&#x");
                i += hex ? 3 : 2;
                int digitsAt = i;
                long value = 0;
                while (i < n) {
                    char c = s.charAt(i);
                    int digit = hex ? Character.digit(c, 16) : (c >= '0' && c <= '9' ? c - '0' : -1);
                    if (digit < 0 || (c > 0x7F)) {
                        break;
                    }
                    value = Math.min(value * (hex ? 16 : 10) + digit, 0x110000L);
                    i += 1;
                }
                if (i == digitsAt || i >= n || s.charAt(i) != ';') {
                    throw syntax(start);
                }
                i += 1;
                if (!isXmlChar(value)) {
                    throw new ScanFault("xml_control_char", start);
                }
                return;
            }
            i += 1;
            if (i >= n || !isNameStartChar(s.codePointAt(i))) {
                throw syntax(start);
            }
            String entity = name();
            if (i >= n || s.charAt(i) != ';') {
                throw syntax(start);
            }
            i += 1;
            if (!PREDEFINED_ENTITIES.contains(entity)) {
                throw new ScanFault("xml_entity", start);
            }
        }

        private void content(String tag) {
            while (true) {
                if (i >= n) {
                    throw syntax(i); // an unclosed element
                }
                char c = s.charAt(i);
                if (c == '<') {
                    if (at("</")) {
                        int nameAt = i + 2;
                        i += 2;
                        String closing = name();
                        if (!closing.equals(tag)) {
                            throw syntax(nameAt);
                        }
                        skipSpace();
                        if (i >= n || s.charAt(i) != '>') {
                            throw syntax(i);
                        }
                        i += 1;
                        return;
                    } else if (at("<!--")) {
                        comment();
                    } else if (at("<![CDATA[")) {
                        int close = s.indexOf("]]>", i + 9);
                        if (close < 0) {
                            throw syntax(i);
                        }
                        i = close + 3;
                    } else if (at("<!DOCTYPE")) {
                        throw new ScanFault("xml_dtd", i);
                    } else if (at("<?")) {
                        processingInstruction();
                    } else if (at("<!")) {
                        throw syntax(i);
                    } else {
                        element();
                    }
                } else if (c == '&') {
                    reference();
                } else if (c == ']' && at("]]>")) {
                    throw syntax(i);
                } else {
                    i += 1;
                }
            }
        }
    }
}
