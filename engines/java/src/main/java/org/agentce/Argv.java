package org.agentce;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.function.Function;
import java.util.function.Supplier;
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
 *       an unknown flag. {@code --} makes every later token positional (and, for a command that takes
 *       no positional, is itself an extra argument, as argparse leaves it);
 *   <li>a VALUE or APPEND option takes its {@code =} rest, or the next token unless there is none or it
 *       is option-like (then it is refused at once, with the option's own needs-value key where it has
 *       one); a value outside the option's {@link Choices} is refused at once, by its own error, the
 *       moment it is read (argparse checks a choice as it consumes the value, so before a later unknown
 *       flag or missing value); HELP returns at once; a FLAG or HELP with {@code =} is refused at once;
 *   <li>unknown flags and positionals past the maximum are kept, and the first in argv order is refused
 *       once the scan ends.
 * </ol>
 *
 * <p>A grammar with an {@link Action} (argparse's subparsers: {@code conformance run}) reads two levels
 * (18.111): its short-cluster pre-scan stops at the first token that does not start with {@code -}; left
 * to right, the first positional token, or {@code --}, is the action; an action the grammar does not name
 * is refused at once, by the action's own error, before any earlier unknown flag; a named action's rest
 * is scanned by its grammar (its own cluster pre-scan first); HELP in either level returns at once and
 * {@link Result#action} names whose usage to print ({@code null}: the command's); unknown tokens are
 * refused once the whole scan ends, the command level's first (argparse appends the subparser's leftovers
 * after its own).
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
     * An option's allowed values (argparse {@code choices}) and the keyed refusal of any other value.
     *
     * @param values the values the option takes
     * @param error the refusal of a value outside {@code values}, given that value
     */
    public record Choices(List<String> values, Function<String, InputError> error) {}

    /**
     * A command's actions (argparse {@code add_subparsers}): the first positional token names one.
     *
     * @param grammars action name to the grammar its rest is scanned by
     * @param error the refusal of an action not named in {@code grammars} (and of {@code --} in its place)
     */
    public record Action(Map<String, Grammar> grammars, Supplier<InputError> error) {}

    /**
     * A command's declared grammar.
     *
     * @param options option name to kind, in declaration order (the HELP names join, in this order,
     *     into argparse's label for them: {@code -h/--help})
     * @param maxPositionals the most positional arguments the command takes
     * @param unrecognizedKey the key of an unknown flag, an extra argument, or a value on a FLAG/HELP
     * @param needsValueKey the key of a VALUE/APPEND option given no value, unless {@code needsValueKeys}
     *     names the option
     * @param flagFix the fix of an unknown flag (and of a value on HELP)
     * @param extraArgFix the fix of a positional past {@code maxPositionals}
     * @param valueHints per-option hint for a missing value's fix ({@code pass --emit <formats>.}); an
     *     option not named here gets {@code <value>}
     * @param needsValueKeys per-option key of a missing value, in place of {@code needsValueKey} (Python's
     *     {@code _ArgvErrors.value_keys}: report's {@code --from} gives {@code input.from_missing})
     * @param choices per-option allowed values and their refusal (argparse {@code choices})
     * @param argparseValueCause whether a missing value's cause is argparse's own sentence ({@code
     *     argument --x: expected one argument}, as assess keeps) or {@code flag '--x' needs a value.}
     * @param action the command's actions, or {@code null} for a command with none
     */
    public record Grammar(
            Map<String, Kind> options,
            int maxPositionals,
            String unrecognizedKey,
            String needsValueKey,
            String flagFix,
            String extraArgFix,
            Map<String, String> valueHints,
            Map<String, String> needsValueKeys,
            Map<String, Choices> choices,
            boolean argparseValueCause,
            Action action) {
        /** A grammar with no per-option needs-value key and no choices, whose missing-value cause is
         * argparse's own sentence (assess's). */
        public Grammar(
                Map<String, Kind> options,
                int maxPositionals,
                String unrecognizedKey,
                String needsValueKey,
                String flagFix,
                String extraArgFix,
                Map<String, String> valueHints) {
            this(options, maxPositionals, unrecognizedKey, needsValueKey, flagFix, extraArgFix, valueHints,
                    Map.of(), Map.of(), true, null);
        }
    }

    /** What the scan read: VALUE options' last values, APPEND options' values, the FLAGs given, the
     * positionals in order, whether HELP was reached, and the action named ({@code null} when none was;
     * with {@code help}, the action whose usage to print). Both levels' options are merged. */
    public record Result(
            Map<String, String> values,
            Map<String, List<String>> lists,
            Set<String> flags,
            List<String> positionals,
            boolean help,
            String action) {
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
        Level command = scanLevel(argv, grammar);
        if (command.help()) {
            return helpResult(null);
        }
        String name = null;
        Grammar actionGrammar = null;
        Level action = null;
        if (command.actionAt() >= 0) {
            name = argv[command.actionAt()];
            actionGrammar = grammar.action().grammars().get(name);
            if (actionGrammar == null) {
                throw grammar.action().error().get();
            }
            action = scanLevel(Arrays.copyOfRange(argv, command.actionAt() + 1, argv.length), actionGrammar);
            if (action.help()) {
                return helpResult(name);
            }
        }
        if (!command.extras().isEmpty()) {
            throw unrecognized(command.extras().get(0), grammar);
        }
        if (action == null) {
            return command.result(null, null);
        }
        if (!action.extras().isEmpty()) {
            throw unrecognized(action.extras().get(0), actionGrammar);
        }
        return action.result(command, name);
    }

    private static Result helpResult(String action) {
        return new Result(Map.of(), Map.of(), Set.of(), List.of(), true, action);
    }

    /** One level's read: its options and positionals, the tokens it could not place, whether HELP was
     * reached, and the index of the action token ({@code -1}: none, or a grammar with no action). */
    private record Level(
            Map<String, String> values,
            Map<String, List<String>> lists,
            Set<String> flags,
            List<String> positionals,
            List<String> extras,
            boolean help,
            int actionAt) {
        /** This level's read as a {@link Result}, after {@code outer}'s options when there is one. */
        Result result(Level outer, String action) {
            Map<String, String> allValues = new LinkedHashMap<>();
            Map<String, List<String>> allLists = new LinkedHashMap<>();
            Set<String> allFlags = new LinkedHashSet<>();
            for (Level level : outer == null ? List.of(this) : List.of(outer, this)) {
                allValues.putAll(level.values);
                level.lists.forEach((k, v) -> allLists.computeIfAbsent(k, x -> new ArrayList<>()).addAll(v));
                allFlags.addAll(level.flags);
            }
            Map<String, List<String>> frozenLists = new LinkedHashMap<>();
            allLists.forEach((k, v) -> frozenLists.put(k, List.copyOf(v)));
            return new Result(
                    Collections.unmodifiableMap(allValues),
                    Collections.unmodifiableMap(frozenLists),
                    Collections.unmodifiableSet(allFlags),
                    List.copyOf(positionals),
                    false,
                    action);
        }
    }

    private static Level scanLevel(String[] argv, Grammar grammar) {
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
                if (grammar.action() != null) {
                    // argparse hands `--` to the subparsers action as its name, so it is refused there.
                    return new Level(values, lists, flags, positionals, extras, false, i);
                }
                optionsEnded = true;
                if (grammar.maxPositionals() == 0) {
                    // No positional can take it, so argparse leaves `--` itself among the extras.
                    extras.add(token);
                }
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
                if (grammar.action() != null && !isOptional(token, grammar)) {
                    return new Level(values, lists, flags, positionals, extras, false, i);
                }
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
                                grammar.needsValueKeys().getOrDefault(name, grammar.needsValueKey()),
                                grammar.argparseValueCause()
                                        ? "argument " + name + ": expected one argument"
                                        : "flag '" + name + "' needs a value.",
                                "pass " + name + " " + hint + ".");
                    }
                    Choices choices = grammar.choices().get(name);
                    if (choices != null && !choices.values().contains(value)) {
                        throw choices.error().apply(value);
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
                    return new Level(Map.of(), Map.of(), Set.of(), List.of(), List.of(), true, -1);
                }
                default -> throw new IllegalStateException("unhandled kind " + kind);
            }
        }
        return new Level(values, lists, flags, positionals, extras, false, -1);
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
     * so the main scan refuses the missing value first. A grammar with an action stops at the first token
     * that does not start with {@code -} (the action's rest has its own pre-scan). */
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
            if (grammar.action() != null && !token.startsWith("-")) {
                return;
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
