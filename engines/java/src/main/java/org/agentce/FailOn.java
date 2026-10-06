package org.agentce;

import java.util.ArrayList;
import java.util.List;
import java.util.Objects;
import java.util.Set;

/**
 * The {@code --fail-on} expression language of {@code agentce assess} (SPEC §8.5, §7).
 *
 * <p>A faithful port of the Python reference ({@code agentce/fail_on.py}). The grammar is comparisons
 * of the form {@code field=="literal"} joined by {@code and}/{@code or}, over a fixed field allow-list.
 * Nothing is ever evaluated as code: a hand-written tokenizer only recognizes a quoted string, the
 * {@code ==} operator and a bare identifier, so an injection payload is refused as an unexpected token at
 * parse time. With no grouping, every expression is already a disjunction of conjunctions, so
 * {@link #parse} returns a flat list of OR-groups of AND-ed comparisons and {@link Expression#matches}
 * is any/all over it: no tree, no recursion, and a chain of thousands of clauses evaluates like a short
 * one. Characters are read as code points and positions count code points, as Python's {@code str} does.
 */
public final class FailOn {
    private FailOn() {}

    /** The only fields a --fail-on expression may compare against (SPEC §9.4 assertion fields), sorted. */
    static final List<String> ALLOWED_FIELDS =
            List.of("control", "family", "mode", "outcome", "rung", "severity", "subject");

    private static final Set<String> KEYWORDS = Set.of("and", "or");

    private static final String FIX = "use comparisons of the form field==\"literal\" joined by and/or, over: "
            + String.join(", ", ALLOWED_FIELDS) + ".";

    /** One {@code field=="literal"} comparison. */
    record Comparison(String field, String literal) {}

    /** A parsed expression: OR-groups, each a list of AND-ed comparisons. */
    public record Expression(List<List<Comparison>> groups) {
        /** Whether {@code assertion} satisfies the expression: any group whose comparisons all hold. */
        public boolean matches(Assertions.Assertion assertion) {
            return groups.stream().anyMatch(group -> group.stream().allMatch(
                    c -> Objects.equals(field(assertion, c.field()), c.literal())));
        }
    }

    private static String field(Assertions.Assertion a, String field) {
        return switch (field) {
            case "control" -> a.control;
            case "subject" -> a.subject;
            case "outcome" -> a.outcome;
            case "severity" -> a.severity;
            case "family" -> a.family;
            case "rung" -> Integer.toString(a.rung);
            case "mode" -> a.mode;
            default -> throw new IllegalArgumentException(field);
        };
    }

    /** Internal syntax/semantic error; always translated to an {@link InputError} before it escapes. */
    private static final class ExprError extends RuntimeException {
        private static final long serialVersionUID = 1L;

        ExprError(String reason) {
            super(reason, null, false, false);
        }
    }

    private enum Kind { STRING, OP, IDENT }

    private record Token(Kind kind, String value) {}

    /** Python's {@code str.isspace}: exactly these 29 code points, whatever the JVM calls whitespace. */
    static boolean isPySpace(int cp) {
        return (cp >= 0x09 && cp <= 0x0D) || (cp >= 0x1C && cp <= 0x20) || cp == 0x85 || cp == 0xA0
                || cp == 0x1680 || (cp >= 0x2000 && cp <= 0x200A) || cp == 0x2028 || cp == 0x2029
                || cp == 0x202F || cp == 0x205F || cp == 0x3000;
    }

    /** Python's {@code str.isalpha} for one code point: general category L*. */
    private static boolean isPyAlpha(int cp) {
        return switch (Character.getType(cp)) {
            case Character.UPPERCASE_LETTER, Character.LOWERCASE_LETTER, Character.TITLECASE_LETTER,
                    Character.MODIFIER_LETTER, Character.OTHER_LETTER -> true;
            default -> false;
        };
    }

    /** Python's {@code str.isalnum} for one code point: general category L* or N*. */
    private static boolean isPyAlnum(int cp) {
        return switch (Character.getType(cp)) {
            case Character.DECIMAL_DIGIT_NUMBER, Character.LETTER_NUMBER, Character.OTHER_NUMBER -> true;
            default -> isPyAlpha(cp);
        };
    }

    private static String chars(int[] cps, int from, int to) {
        return new String(cps, from, to - from);
    }

    /** Lex {@code expr} into string/op/ident tokens. Any other character is a syntax error: there is no
     * "skip and continue" branch, so a backtick, a parenthesis or a dot fails the whole expression. */
    private static List<Token> tokenize(int[] expr) {
        List<Token> tokens = new ArrayList<>();
        int i = 0;
        int n = expr.length;
        while (i < n) {
            int c = expr[i];
            if (isPySpace(c)) {
                i++;
                continue;
            }
            if (c == '"' || c == '\'') {
                int j = i + 1;
                StringBuilder buf = new StringBuilder();
                while (j < n && expr[j] != c) {
                    if (expr[j] == '\\' && j + 1 < n) {
                        buf.appendCodePoint(expr[j + 1]);
                        j += 2;
                        continue;
                    }
                    buf.appendCodePoint(expr[j]);
                    j++;
                }
                if (j >= n) {
                    throw new ExprError("unterminated string literal starting at position " + i);
                }
                tokens.add(new Token(Kind.STRING, buf.toString()));
                i = j + 1;
                continue;
            }
            if (c == '=' && i + 1 < n && expr[i + 1] == '=') {
                tokens.add(new Token(Kind.OP, "=="));
                i += 2;
                continue;
            }
            if (isPyAlpha(c) || c == '_') {
                int j = i;
                while (j < n && (isPyAlnum(expr[j]) || expr[j] == '_')) {
                    j++;
                }
                tokens.add(new Token(Kind.IDENT, chars(expr, i, j)));
                i = j;
                continue;
            }
            throw new ExprError(
                    "unexpected character " + Readiness.pyRepr(chars(expr, i, i + 1)) + " at position " + i);
        }
        return tokens;
    }

    /** expr := and_expr ("or" and_expr)* ; and_expr := comparison ("and" comparison)* ;
     * comparison := FIELD "==" STRING. No parentheses, no other operators: the whole grammar. */
    private static final class Parser {
        private final List<Token> tokens;
        private int pos;

        Parser(List<Token> tokens) {
            this.tokens = tokens;
        }

        private Token peek() {
            return pos < tokens.size() ? tokens.get(pos) : null;
        }

        private Token advance() {
            Token tok = peek();
            pos++;
            return tok;
        }

        List<List<Comparison>> parse() {
            List<List<Comparison>> groups = new ArrayList<>();
            groups.add(andExpr());
            while (atKeyword("or")) {
                advance();
                groups.add(andExpr());
            }
            Token trailing = peek();
            if (trailing != null) {
                throw new ExprError(
                        "unexpected token " + Readiness.pyRepr(trailing.value()) + " after a complete expression");
            }
            return groups;
        }

        private boolean atKeyword(String word) {
            Token tok = peek();
            return tok != null && tok.kind() == Kind.IDENT && tok.value().equals(word);
        }

        private List<Comparison> andExpr() {
            List<Comparison> group = new ArrayList<>();
            group.add(comparison());
            while (atKeyword("and")) {
                advance();
                group.add(comparison());
            }
            return group;
        }

        private Comparison comparison() {
            Token fieldTok = advance();
            if (fieldTok == null || fieldTok.kind() != Kind.IDENT) {
                throw new ExprError("expected a field name");
            }
            String field = fieldTok.value();
            if (KEYWORDS.contains(field)) {
                throw new ExprError("expected a field name, got the reserved word " + Readiness.pyRepr(field));
            }
            if (!ALLOWED_FIELDS.contains(field)) {
                throw new ExprError("unknown field " + Readiness.pyRepr(field) + "; choose from: "
                        + String.join(", ", ALLOWED_FIELDS));
            }
            Token opTok = advance();
            if (opTok == null || opTok.kind() != Kind.OP) {
                throw new ExprError("expected '==' after field name " + Readiness.pyRepr(field));
            }
            Token valTok = advance();
            if (valTok == null || valTok.kind() != Kind.STRING) {
                throw new ExprError("expected a quoted string literal after '=='");
            }
            return new Comparison(field, valTok.value());
        }
    }

    /** Parse a {@code --fail-on} expression. Throws {@link InputError} with the stable
     * {@code input.fail_on_invalid_expression} key on any syntax error, unknown field or disallowed
     * token, before any assertion is evaluated against it (SPEC §7: never eval). */
    public static Expression parse(String expr) {
        try {
            List<Token> tokens = tokenize(expr.codePoints().toArray());
            if (tokens.isEmpty()) {
                throw new ExprError("the --fail-on expression is empty");
            }
            return new Expression(new Parser(tokens).parse());
        } catch (ExprError exc) {
            throw new InputError(
                    "input.fail_on_invalid_expression",
                    "--fail-on " + Readiness.pyRepr(expr) + " is not a valid expression: " + exc.getMessage(),
                    FIX);
        }
    }
}
