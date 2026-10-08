package org.agentce;

import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.regex.Pattern;
import java.util.stream.Collectors;

/**
 * One table-driven argv scanner (SPEC §8.5; 18.109, the shape 18.50 moves every command onto): a
 * command declares its {@link Grammar} (option name to {@link Kind}, the most positionals it takes, and
 * the keys and fix texts of its refusals) and {@link #scan} reads its argv the way Python's argparse
 * ({@code allow_abbrev=False}, the reference) reads the same subparser, in argparse's order:
 *
 * <ol>
 *   <li>a short cluster ({@code -hx}: one dash, three or more characters, not an option, not a
 *       negative number) before {@code --} and not an option's value is refused first, as Python's
 *       {@code _refuse_short_clusters};
 *   <li>left to right, a token is an option when it is a known name, or {@code name=value} with a known
 *       name; else it is positional when it is {@code -}, a negative number or holds a space; else it is
 *       an unknown flag. {@code --} makes every later token positional;
 *   <li>a VALUE or APPEND option takes its {@code =} rest, or the next token unless there is none or it
 *       is option-like (then it is refused at once); HELP returns at once; a FLAG or HELP with {@code =}
 *       is refused at once;
 *   <li>unknown flags and positionals past the maximum are kept, and the first in argv order is refused
 *       once the scan ends.
 * </ol>
 */
public final class Argv {
    private Argv() {}

    /** How an option reads its argv. */
    public enum Kind {
        /** Takes one value; the last one wins (argparse {@code store}). */
        VALUE,
        /** Takes one value each time; every value is kept, in order (argparse {@code append}). */
        APPEND,
        /** Takes no value (argparse {@code store_true}). */
        FLAG,
        /** Asks for the usage text; the scan stops there (argparse {@code help}). */
        HELP
    }

    /**
     * A command's declared grammar.
     *
     * @param options option name to kind, in declaration order (the HELP names join, in this order,
     *     into argparse's label for them: {@code -h/--help})
     * @param maxPositionals the most positional arguments the command takes
     * @param unrecognizedKey the key of an unknown flag, an extra argument, or a value on a FLAG/HELP
     * @param needsValueKey the key of a VALUE/APPEND option given no value
     * @param flagFix the fix of an unknown flag (and of a value on HELP)
     * @param extraArgFix the fix of a positional past {@code maxPositionals}
     * @param valueHints per-option hint for a missing value's fix ({@code pass --emit <formats>.}); an
     *     option not named here gets {@code <value>}
     */
    public record Grammar(
            Map<String, Kind> options,
            int maxPositionals,
            String unrecognizedKey,
            String needsValueKey,
            String flagFix,
            String extraArgFix,
            Map<String, String> valueHints) {}

    /** What the scan read: VALUE options' last values, APPEND options' values, the FLAGs given, the
     * positionals in order, and whether HELP was reached. */
    public record Result(
            Map<String, String> values,
            Map<String, List<String>> lists,
            Set<String> flags,
            List<String> positionals,
            boolean help) {
        /** A VALUE option's last value, or {@code null} when it was not given. */
        public String value(String name) {
            return values.get(name);
        }

        /** An APPEND option's values, in argv order (empty when it was not given). */
        public List<String> list(String name) {
            return lists.getOrDefault(name, List.of());
        }

        /** Whether a FLAG was given. */
        public boolean flag(String name) {
            return flags.contains(name);
        }
    }

    /** argparse's {@code _negative_number_matcher} ({@code ^-\d+$|^-\d*\.\d+$}; Python's {@code $} also
     * matches before one trailing newline). */
    static final Pattern NEGATIVE_NUMBER = Pattern.compile("-\\p{Nd}+\n?|-\\p{Nd}*\\.\\p{Nd}+\n?");

    /** Read {@code argv} (the tokens after the command name) by {@code grammar}; throws the grammar's
     * keyed {@link InputError} on the first refusal. */
    public static Result scan(String[] argv, Grammar grammar) {
        refuseShortClusters(argv, grammar);
        Map<String, String> values = new LinkedHashMap<>();
        Map<String, List<String>> lists = new LinkedHashMap<>();
        Set<String> flags = new LinkedHashSet<>();
        List<String> positionals = new ArrayList<>();
        List<String> extras = new ArrayList<>();
        boolean optionsEnded = false;
        for (int i = 0; i < argv.length; i++) {
            String token = argv[i];
            if (!optionsEnded && token.equals("--")) {
                optionsEnded = true;
                continue;
            }
            String name = token;
            String explicit = null;
            Kind kind = optionsEnded ? null : grammar.options().get(token);
            if (kind == null && !optionsEnded && token.startsWith("-") && token.indexOf('=') > 0) {
                int eq = token.indexOf('=');
                Kind joined = grammar.options().get(token.substring(0, eq));
                if (joined != null) {
                    kind = joined;
                    name = token.substring(0, eq);
                    explicit = token.substring(eq + 1);
                }
            }
            if (kind == null) {
                if (optionsEnded || !isOptional(token, grammar)) {
                    if (positionals.size() < grammar.maxPositionals()) {
                        positionals.add(token);
                    } else {
                        extras.add(token);
                    }
                } else {
                    extras.add(token);
                }
                continue;
            }
            switch (kind) {
                case VALUE, APPEND -> {
                    String value;
                    if (explicit != null) {
                        value = explicit;
                    } else if (i + 1 < argv.length && !isOptional(argv[i + 1], grammar)) {
                        value = argv[++i];
                    } else {
                        String hint = grammar.valueHints().getOrDefault(name, "<value>");
                        throw new InputError(
                                grammar.needsValueKey(),
                                "argument " + name + ": expected one argument",
                                "pass " + name + " " + hint + ".");
                    }
                    if (kind == Kind.VALUE) {
                        values.put(name, value);
                    } else {
                        lists.computeIfAbsent(name, k -> new ArrayList<>()).add(value);
                    }
                }
                case FLAG -> {
                    if (explicit != null) {
                        throw new InputError(
                                grammar.unrecognizedKey(),
                                "flag '" + name + "' takes no value.",
                                "drop the value: " + name + ".");
                    }
                    flags.add(name);
                }
                case HELP -> {
                    if (explicit != null) {
                        throw new InputError(
                                grammar.unrecognizedKey(),
                                "argument " + helpLabel(grammar) + ": ignored explicit argument "
                                        + Readiness.pyRepr(explicit) + ".",
                                grammar.flagFix());
                    }
                    return new Result(Map.of(), Map.of(), Set.of(), List.of(), true);
                }
                default -> throw new IllegalStateException("unhandled kind " + kind);
            }
        }
        if (!extras.isEmpty()) {
            throw unrecognized(extras.get(0), grammar);
        }
        Map<String, List<String>> frozenLists = new LinkedHashMap<>();
        lists.forEach((k, v) -> frozenLists.put(k, List.copyOf(v)));
        return new Result(
                Collections.unmodifiableMap(values),
                Collections.unmodifiableMap(frozenLists),
                Collections.unmodifiableSet(flags),
                List.copyOf(positionals),
                false);
    }

    /** The refusal of a token the scan could not place: an unknown flag, or an argument past the most
     * positionals the command takes (argparse's leftovers, worded by their first character). */
    private static InputError unrecognized(String token, Grammar grammar) {
        if (token.startsWith("-") && !token.equals("-")) {
            return new InputError(
                    grammar.unrecognizedKey(), "unrecognized flag '" + token + "'.", grammar.flagFix());
        }
        return new InputError(
                grammar.unrecognizedKey(), "unrecognized argument '" + token + "'.", grammar.extraArgFix());
    }

    /** argparse's label for the HELP option: its names joined by {@code /}, in declaration order. */
    private static String helpLabel(Grammar grammar) {
        return grammar.options().entrySet().stream()
                .filter(e -> e.getValue() == Kind.HELP)
                .map(Map.Entry::getKey)
                .collect(Collectors.joining("/"));
    }

    /** Whether argparse reads {@code token} as an option ({@code _parse_optional} returns non-None): a
     * known name, {@code name=value} with a known name, a short option with text joined to it, or any
     * other dash token that is not {@code -}, a negative number, or a token holding a space. */
    static boolean isOptional(String token, Grammar grammar) {
        if (!token.startsWith("-")) {
            return false;
        }
        if (grammar.options().containsKey(token)) {
            return true;
        }
        if (token.length() == 1) {
            return false;
        }
        int eq = token.indexOf('=');
        if (eq > 0 && grammar.options().containsKey(token.substring(0, eq))) {
            return true;
        }
        // With allow_abbrev=False argparse still matches a single-dash token's first two characters
        // against the short options (`-h y` is -h with a joined value).
        if (!token.startsWith("--") && grammar.options().containsKey(token.substring(0, 2))) {
            return true;
        }
        if (NEGATIVE_NUMBER.matcher(token).matches()) {
            return false;
        }
        return !token.contains(" ");
    }

    /** Python's {@code _refuse_short_clusters}: before {@code --}, a single-dash token of three or more
     * characters that is not an option and not a negative number is refused, unless it is a VALUE or
     * APPEND option's value; where such an option is followed by an option-like token the pre-scan stops,
     * so the main scan refuses the missing value first. */
    private static void refuseShortClusters(String[] argv, Grammar grammar) {
        boolean pending = false;
        for (String token : argv) {
            if (token.equals("--")) {
                return;
            }
            if (pending) {
                pending = false;
                if (!isOptional(token, grammar)) {
                    continue;
                }
                return;
            }
            Kind kind = grammar.options().get(token);
            if (kind == Kind.VALUE || kind == Kind.APPEND) {
                pending = true;
            }
            if (token.startsWith("-")
                    && !token.startsWith("--")
                    && token.length() > 2
                    && kind == null
                    && !NEGATIVE_NUMBER.matcher(token).matches()) {
                throw unrecognized(token, grammar);
            }
        }
    }
}
