package org.agentce;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;

/**
 * A minimal RDF term model, triple store, and Turtle parser for the bounded Portable Shape Profile
 * subset (SPEC §7.2, ADR-0002).
 *
 * <p>The reference parses shapes with rdflib and the TypeScript engine with N3.js; the PSP is a
 * strict, finite subset of SHACL Core (node shapes with {@code sh:targetClass}/{@code sh:targetNode}/
 * {@code agentce:targetWhere} and property shapes over predicate, inverse, sequence, or alternative
 * paths), so a focused parser suffices and keeps the engine free of a heavy RDF dependency. Statements
 * are stored in file order, so multi-valued predicates (such as a shape's several {@code sh:property})
 * are returned in the order the reference sees them, keeping violation order byte-identical.
 */
public final class Rdf {
    private Rdf() {}

    static final String RDF_NS = "http://www.w3.org/1999/02/22-rdf-syntax-ns#";
    static final String RDF_TYPE = RDF_NS + "type";
    static final String RDF_FIRST = RDF_NS + "first";
    static final String RDF_REST = RDF_NS + "rest";
    static final String RDF_NIL = RDF_NS + "nil";
    static final String XSD = "http://www.w3.org/2001/XMLSchema#";

    public enum Kind {
        IRI,
        BLANK,
        LITERAL
    }

    /** An RDF term: a named node (full IRI), a blank node, or a literal (lexical value + datatype). */
    public static final class Term {
        public final Kind kind;
        public final String value;
        public final String datatype;

        private Term(Kind kind, String value, String datatype) {
            this.kind = kind;
            this.value = value;
            this.datatype = datatype;
        }

        static Term iri(String value) {
            return new Term(Kind.IRI, value, null);
        }

        static Term blank(String id) {
            return new Term(Kind.BLANK, id, null);
        }

        static Term literal(String value, String datatype) {
            return new Term(Kind.LITERAL, value, datatype);
        }

        public boolean isIri() {
            return kind == Kind.IRI;
        }

        public boolean isBlank() {
            return kind == Kind.BLANK;
        }

        public boolean isLiteral() {
            return kind == Kind.LITERAL;
        }

        @Override
        public boolean equals(Object o) {
            if (!(o instanceof Term t)) {
                return false;
            }
            return kind == t.kind && value.equals(t.value) && Objects.equals(datatype, t.datatype);
        }

        @Override
        public int hashCode() {
            return Objects.hash(kind, value, datatype);
        }
    }

    /** A triple store preserving insertion (parse) order for deterministic reads. */
    public static final class Store {
        private final List<Term[]> quads = new ArrayList<>();

        void add(Term s, Term p, Term o) {
            quads.add(new Term[] {s, p, o});
        }

        /** Objects of {@code (subject, predicate)} in insertion order. */
        public List<Term> objects(Term subject, Term predicate) {
            List<Term> out = new ArrayList<>();
            for (Term[] q : quads) {
                if (q[0].equals(subject) && q[1].equals(predicate)) {
                    out.add(q[2]);
                }
            }
            return out;
        }

        /** Subjects of {@code (predicate, object)} in insertion order. */
        public List<Term> subjects(Term predicate, Term object) {
            List<Term> out = new ArrayList<>();
            for (Term[] q : quads) {
                if (q[1].equals(predicate) && q[2].equals(object)) {
                    out.add(q[0]);
                }
            }
            return out;
        }

        /** Every quad with the given subject, in insertion order. */
        public List<Term[]> quadsOf(Term subject) {
            List<Term[]> out = new ArrayList<>();
            for (Term[] q : quads) {
                if (q[0].equals(subject)) {
                    out.add(q);
                }
            }
            return out;
        }

        /** The first object of {@code (subject, predicate)}, or null. */
        public Term value(Term subject, Term predicate) {
            List<Term> objs = objects(subject, predicate);
            return objs.isEmpty() ? null : objs.get(0);
        }
    }

    /** Parse Turtle text into a {@link Store}. */
    public static Store parse(String text) {
        return new Parser(text).parseDocument();
    }

    // --- tokenizer + parser ---

    private enum Type {
        IRIREF,
        PNAME,
        A,
        STRING,
        NUMBER,
        BOOLEAN,
        DOT,
        SEMI,
        COMMA,
        LBRACKET,
        RBRACKET,
        LPAREN,
        RPAREN,
        PREFIX_KW,
        EOF
    }

    private record Token(Type type, String value) {}

    private static final class Parser {
        private final String src;
        private int pos;
        private final Map<String, String> prefixes = new LinkedHashMap<>();
        private final Store store = new Store();
        private int blankCounter;
        private Token lookahead;

        Parser(String src) {
            this.src = src;
        }

        Store parseDocument() {
            while (peek().type() != Type.EOF) {
                if (peek().type() == Type.PREFIX_KW) {
                    parsePrefix();
                } else {
                    parseTriples();
                }
            }
            return store;
        }

        private void parsePrefix() {
            expect(Type.PREFIX_KW);
            Token pname = expect(Type.PNAME); // "sh:" -> prefix "sh", empty local
            String prefix = pname.value().substring(0, pname.value().indexOf(':'));
            Token iri = expect(Type.IRIREF);
            expect(Type.DOT);
            prefixes.put(prefix, iri.value());
        }

        private void parseTriples() {
            Term subject = parseTerm(next());
            parsePredicateObjectList(subject);
            expect(Type.DOT);
        }

        private void parsePredicateObjectList(Term subject) {
            while (true) {
                Token p = next();
                Term predicate = p.type() == Type.A ? Term.iri(RDF_TYPE) : parseTerm(p);
                parseObjectList(subject, predicate);
                if (peek().type() == Type.SEMI) {
                    next();
                    // allow a trailing ";" before "]" or "."
                    if (peek().type() == Type.RBRACKET || peek().type() == Type.DOT) {
                        return;
                    }
                    continue;
                }
                return;
            }
        }

        private void parseObjectList(Term subject, Term predicate) {
            while (true) {
                Term object = parseTerm(next());
                store.add(subject, predicate, object);
                if (peek().type() == Type.COMMA) {
                    next();
                    continue;
                }
                return;
            }
        }

        private Term parseTerm(Token token) {
            switch (token.type()) {
                case IRIREF:
                    return Term.iri(token.value());
                case PNAME:
                    return Term.iri(expandPname(token.value()));
                case STRING:
                    return Term.literal(token.value(), XSD + "string");
                case NUMBER:
                    return Term.literal(token.value(), XSD + "integer");
                case BOOLEAN:
                    return Term.literal(token.value(), XSD + "boolean");
                case LBRACKET:
                    return parseBlank();
                case LPAREN:
                    return parseCollection();
                default:
                    throw new IllegalArgumentException("unexpected token " + token.type() + " parsing a term");
            }
        }

        private Term parseBlank() {
            Term bnode = Term.blank("_:b" + (blankCounter++));
            if (peek().type() != Type.RBRACKET) {
                parsePredicateObjectList(bnode);
            }
            expect(Type.RBRACKET);
            return bnode;
        }

        private Term parseCollection() {
            List<Term> items = new ArrayList<>();
            while (peek().type() != Type.RPAREN) {
                items.add(parseTerm(next()));
            }
            expect(Type.RPAREN);
            if (items.isEmpty()) {
                return Term.iri(RDF_NIL);
            }
            Term head = Term.blank("_:b" + (blankCounter++));
            Term current = head;
            for (int i = 0; i < items.size(); i++) {
                store.add(current, Term.iri(RDF_FIRST), items.get(i));
                Term rest = i == items.size() - 1 ? Term.iri(RDF_NIL) : Term.blank("_:b" + (blankCounter++));
                store.add(current, Term.iri(RDF_REST), rest);
                current = rest;
            }
            return head;
        }

        private String expandPname(String pname) {
            int colon = pname.indexOf(':');
            String prefix = pname.substring(0, colon);
            String local = pname.substring(colon + 1);
            String ns = prefixes.get(prefix);
            if (ns == null) {
                throw new IllegalArgumentException("unknown prefix " + prefix + ":");
            }
            return ns + local;
        }

        private Token expect(Type type) {
            Token token = next();
            if (token.type() != type) {
                throw new IllegalArgumentException("expected " + type + " but found " + token.type());
            }
            return token;
        }

        private Token peek() {
            if (lookahead == null) {
                lookahead = scan();
            }
            return lookahead;
        }

        private Token next() {
            Token token = peek();
            lookahead = null;
            return token;
        }

        private Token scan() {
            skipTrivia();
            if (pos >= src.length()) {
                return new Token(Type.EOF, "");
            }
            char c = src.charAt(pos);
            switch (c) {
                case '.':
                    pos++;
                    return new Token(Type.DOT, ".");
                case ';':
                    pos++;
                    return new Token(Type.SEMI, ";");
                case ',':
                    pos++;
                    return new Token(Type.COMMA, ",");
                case '[':
                    pos++;
                    return new Token(Type.LBRACKET, "[");
                case ']':
                    pos++;
                    return new Token(Type.RBRACKET, "]");
                case '(':
                    pos++;
                    return new Token(Type.LPAREN, "(");
                case ')':
                    pos++;
                    return new Token(Type.RPAREN, ")");
                case '<':
                    return scanIriRef();
                case '"':
                    return scanString();
                case '@':
                    return scanAt();
                default:
                    return scanBareword();
            }
        }

        private void skipTrivia() {
            while (pos < src.length()) {
                char c = src.charAt(pos);
                if (Character.isWhitespace(c)) {
                    pos++;
                } else if (c == '#') {
                    while (pos < src.length() && src.charAt(pos) != '\n') {
                        pos++;
                    }
                } else {
                    return;
                }
            }
        }

        private Token scanIriRef() {
            int start = ++pos; // skip '<'
            while (pos < src.length() && src.charAt(pos) != '>') {
                pos++;
            }
            String iri = src.substring(start, pos);
            pos++; // skip '>'
            return new Token(Type.IRIREF, iri);
        }

        private Token scanString() {
            pos++; // skip opening quote
            StringBuilder sb = new StringBuilder();
            while (pos < src.length()) {
                char c = src.charAt(pos++);
                if (c == '"') {
                    return new Token(Type.STRING, sb.toString());
                }
                if (c == '\\' && pos < src.length()) {
                    char e = src.charAt(pos++);
                    switch (e) {
                        case 'n' -> sb.append('\n');
                        case 't' -> sb.append('\t');
                        case 'r' -> sb.append('\r');
                        case '"' -> sb.append('"');
                        case '\\' -> sb.append('\\');
                        case 'u' -> {
                            sb.append((char) Integer.parseInt(src.substring(pos, pos + 4), 16));
                            pos += 4;
                        }
                        default -> sb.append(e);
                    }
                } else {
                    sb.append(c);
                }
            }
            throw new IllegalArgumentException("unterminated string literal");
        }

        private Token scanAt() {
            int start = pos++; // skip '@'
            while (pos < src.length() && Character.isLetter(src.charAt(pos))) {
                pos++;
            }
            String word = src.substring(start, pos);
            if (word.equals("@prefix")) {
                return new Token(Type.PREFIX_KW, word);
            }
            throw new IllegalArgumentException("unsupported directive " + word);
        }

        private Token scanBareword() {
            int start = pos;
            while (pos < src.length() && isNameChar(src.charAt(pos))) {
                pos++;
            }
            String word = src.substring(start, pos);
            if (word.isEmpty()) {
                throw new IllegalArgumentException("unexpected character '" + src.charAt(pos) + "'");
            }
            if (word.equals("a")) {
                return new Token(Type.A, word);
            }
            if (word.equals("true") || word.equals("false")) {
                return new Token(Type.BOOLEAN, word);
            }
            if (word.indexOf(':') >= 0) {
                return new Token(Type.PNAME, word);
            }
            if (word.matches("[+-]?[0-9]+")) {
                return new Token(Type.NUMBER, word);
            }
            throw new IllegalArgumentException("unrecognised token '" + word + "'");
        }

        private static boolean isNameChar(char c) {
            return Character.isLetterOrDigit(c) || c == ':' || c == '_' || c == '-' || c == '+';
        }
    }
}
