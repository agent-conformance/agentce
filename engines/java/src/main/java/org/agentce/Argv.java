package org.agentce;

import com.fasterxml.jackson.databind.JsonNode;
import java.io.IOException;
import java.io.InputStream;
import java.util.ArrayList;
import java.util.Collection;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;
import java.util.function.Supplier;

/**
 * The command-line grammar scanner (SPEC §8.5, "Command-line grammar"; 18.184w).
 *
 * <p>Every engine reads argv under one table, {@code spec/cli/argv-grammar.json} (vendored byte for byte
 * to {@code src/main/resources/cli/}), with the same one-pass scan: no prefix matching, no short-flag
 * clusters, no option given twice unless the table marks it repeatable, a value only after {@code =} or
 * in the next token when that token does not start with {@code -}, and {@code --} ending the options.
 * {@code -h}/{@code --help} in an option position wins over any refusal; otherwise the first refusal in
 * argv order is thrown, keyed by the command. The scan never reads the file system. Mirrors Python's
 * {@code agentce/argv.py} and TypeScript's {@code src/argv.ts}.
 */
public final class Argv {
    private Argv() {}

    /** The help spellings, valid in every option position. */
    public static final List<String> HELP = List.of("-h", "--help");

    /**
     * One option of the table.
     *
     * @param names its spellings; the last one names it ({@link #name})
     * @param kind {@code flag} or {@code value}
     * @param repeatable whether it may be given more than once (its values kept in order)
     * @param needsValueKey the key of a missing value, in place of the command's {@code keys.needs_value}
     * @param valueHint the value hint in a missing value's fix ({@code <value>} when the table names none)
     * @param choices the values it takes, or {@code null} for any
     */
    public record Option(
            List<String> names, String kind, boolean repeatable, String needsValueKey, String valueHint,
            List<String> choices) {
        public String name() {
            return names.get(names.size() - 1);
        }
    }

    /** One command, or one action under it: its options by every spelling, its positional bound
     * ({@code null}: unbounded), and its actions. */
    public record Level(Map<String, Option> options, Integer maxPositionals, Map<String, Level> actions) {}

    /** One command of the table: its top level, refusal keys and fixes, and the engines that build it. */
    public record Command(
            String name, Level level, String unrecognizedKey, String needsValueKey, String flagFix,
            String extraFix, List<String> implementedIn) {}

    /** What a scan read. {@code help} is set when the line asks for a usage; {@code action} then names
     * whose ({@code null}: the command's). {@code values} holds single values, {@code lists} a repeatable
     * option's values in argv order, {@code flags} the flags given, each by the option's last spelling. */
    public static final class Scan {
        public boolean help;
        public String action;
        public final Map<String, String> values = new LinkedHashMap<>();
        public final Map<String, List<String>> lists = new LinkedHashMap<>();
        public final Set<String> flags = new LinkedHashSet<>();
        public final List<String> positionals = new ArrayList<>();

        /** A single-valued option's value, or {@code null} when it was not given. */
        public String value(String name) {
            return values.get(name);
        }

        /** A repeatable option's values, in argv order (empty when it was not given). */
        public List<String> list(String name) {
            return lists.getOrDefault(name, List.of());
        }

        /** Whether a flag was given. */
        public boolean flag(String name) {
            return flags.contains(name);
        }
    }

    /** Words a value outside an option's {@code choices}, given the option's name and the value. */
    @FunctionalInterface
    public interface ChoiceError {
        InputError apply(String option, String value);
    }

    private static volatile JsonNode grammar;
    private static final Map<String, Command> COMMANDS = new ConcurrentHashMap<>();

    /** The vendored grammar file, as data. */
    public static JsonNode grammar() {
        JsonNode loaded = grammar;
        if (loaded == null) {
            String path = "/cli/argv-grammar.json";
            try (InputStream in = Argv.class.getResourceAsStream(path)) {
                if (in == null) {
                    throw new IllegalStateException("missing classpath resource " + path);
                }
                loaded = Json.readBundled(in.readAllBytes());
            } catch (IOException e) {
                throw new IllegalStateException("cannot read classpath resource " + path + ": " + e.getMessage(), e);
            }
            grammar = loaded;
        }
        return loaded;
    }

    /** Whether the table names {@code name} as a command. */
    public static boolean isCommand(String name) {
        return grammar().get("commands").has(name);
    }

    /** The command {@code name} of the table. */
    public static Command command(String name) {
        return COMMANDS.computeIfAbsent(name, n -> {
            JsonNode raw = grammar().get("commands").get(n);
            List<Option> globals = new ArrayList<>();
            grammar().get("globals").forEach(o -> globals.add(option(o)));
            List<String> implementedIn = new ArrayList<>();
            raw.get("implemented_in").forEach(e -> implementedIn.add(e.asText()));
            return new Command(
                    n,
                    level(raw, globals),
                    raw.get("keys").get("unrecognized").asText(),
                    raw.get("keys").get("needs_value").asText(),
                    raw.get("fixes").get("flag").asText(),
                    raw.get("fixes").get("extra").asText(),
                    List.copyOf(implementedIn));
        });
    }

    private static Option option(JsonNode raw) {
        List<String> names = new ArrayList<>();
        raw.get("names").forEach(e -> names.add(e.asText()));
        List<String> choices = null;
        if (raw.hasNonNull("choices")) {
            choices = new ArrayList<>();
            for (JsonNode c : raw.get("choices")) {
                choices.add(c.asText());
            }
            choices = List.copyOf(choices);
        }
        return new Option(
                List.copyOf(names),
                raw.get("kind").asText(),
                raw.get("repeatable").asBoolean(),
                raw.hasNonNull("needs_value_key") ? raw.get("needs_value_key").asText() : null,
                raw.hasNonNull("value_hint") ? raw.get("value_hint").asText() : "<value>",
                choices);
    }

    private static Level level(JsonNode raw, List<Option> globals) {
        Map<String, Option> options = new LinkedHashMap<>();
        List<Option> all = new ArrayList<>(globals);
        raw.get("options").forEach(o -> all.add(option(o)));
        for (Option o : all) {
            for (String n : o.names()) {
                options.put(n, o);
            }
        }
        Map<String, Level> actions = new LinkedHashMap<>();
        JsonNode rawActions = raw.get("actions");
        if (rawActions != null) {
            rawActions.fields().forEachRemaining(e -> actions.put(e.getKey(), level(e.getValue(), globals)));
        }
        JsonNode max = raw.get("positionals").get("max");
        return new Level(
                Collections.unmodifiableMap(options),
                max == null || max.isNull() ? null : max.asInt(),
                Collections.unmodifiableMap(actions));
    }

    /**
     * Read {@code tokens} (the line after the command word) under {@code cmd}'s grammar. {@code seen}
     * holds the global flags already given before the command word (a second one is a repeat). {@code
     * choiceError} words a value outside an option's choices; {@code actionError} words an action the
     * command does not have. Throws the first refusal in argv order, unless the line asks for help.
     */
    public static Scan scan(
            Command cmd, List<String> tokens, Collection<String> seen, ChoiceError choiceError,
            Supplier<InputError> actionError) {
        return new Scanner(cmd, choiceError, actionError, seen).run(tokens);
    }

    /** {@link #scan} with nothing seen before the command word and no choice or action wording. */
    public static Scan scan(Command cmd, List<String> tokens) {
        return scan(cmd, tokens, List.of(), null, null);
    }

    /** One scan's state: the level read so far, the options given, and the first refusal. */
    private static final class Scanner {
        private final Command cmd;
        private final ChoiceError choiceError;
        private final Supplier<InputError> actionError;
        private final Set<String> given;
        private final Scan out = new Scan();
        private Level level;
        private InputError first;

        Scanner(Command cmd, ChoiceError choiceError, Supplier<InputError> actionError, Collection<String> seen) {
            this.cmd = cmd;
            this.choiceError = choiceError;
            this.actionError = actionError;
            this.given = new LinkedHashSet<>(seen);
            this.level = cmd.level();
        }

        private void refuse(String key, String cause, String fix) {
            if (first == null) {
                first = new InputError(key == null ? cmd.unrecognizedKey() : key, cause, fix);
            }
        }

        private void unrecognized(String token) {
            refuse(null, "unrecognized flag '" + token + "'.", cmd.flagFix());
        }

        private void refuseAction() {
            if (actionError == null) {
                throw new IllegalStateException("a command with actions words its unknown action");
            }
            InputError err = actionError.get();
            refuse(err.key, err.reason, err.fix);
        }

        private void positional(String token) {
            if (level.maxPositionals() != null && out.positionals.size() >= level.maxPositionals()) {
                refuse(null, "unrecognized argument '" + token + "'.", cmd.extraFix());
            } else {
                out.positionals.add(token);
            }
        }

        Scan run(List<String> tokens) {
            boolean helpSeen = false;
            String helpAction = null;
            boolean ended = false;
            int i = 0;
            while (i < tokens.size()) {
                String token = tokens.get(i);
                i++;
                if (ended) {
                    positional(token);
                    continue;
                }
                if (token.equals("--")) {
                    if (!level.actions().isEmpty() && out.action == null) {
                        refuseAction();
                        break;
                    }
                    if (Integer.valueOf(0).equals(level.maxPositionals()) || i == tokens.size()) {
                        refuse(null, "unrecognized flag '--'.", cmd.flagFix());
                    }
                    ended = true;
                    continue;
                }
                if (HELP.contains(token)) {
                    if (!helpSeen) {
                        helpSeen = true;
                        helpAction = out.action;
                    }
                    continue;
                }
                if (!token.startsWith("-")) {
                    if (!level.actions().isEmpty() && out.action == null) {
                        Level action = level.actions().get(token);
                        if (action != null) {
                            out.action = token;
                            level = action;
                            continue;
                        }
                        refuseAction();
                        break;
                    }
                    positional(token);
                    continue;
                }
                int eq = token.indexOf('=');
                String name = eq < 0 ? token : token.substring(0, eq);
                Option opt = level.options().get(name);
                if (opt == null) {
                    if (HELP.contains(name)) {
                        refuse(null, "flag '" + name + "' takes no value.", "drop the value: " + name + ".");
                    } else {
                        // exact_spelling, short_cluster and dash_token_not_option share one wording.
                        unrecognized(token);
                    }
                    continue;
                }
                if (given.contains(opt.name()) && !opt.repeatable()) {
                    refuse(null, "flag '" + name + "' was given more than once.", "give " + name + " once.");
                }
                given.add(opt.name());
                if (opt.kind().equals("flag")) {
                    if (eq >= 0) {
                        refuse(null, "flag '" + name + "' takes no value.", "drop the value: " + name + ".");
                    }
                    out.flags.add(opt.name());
                    continue;
                }
                String value;
                if (eq >= 0) {
                    value = token.substring(eq + 1);
                } else if (i < tokens.size() && !tokens.get(i).startsWith("-")) {
                    value = tokens.get(i);
                    i++;
                } else {
                    String next = i < tokens.size() ? tokens.get(i) : null;
                    refuse(
                            opt.needsValueKey() != null ? opt.needsValueKey() : cmd.needsValueKey(),
                            "flag '" + name + "' needs a value.",
                            "pass " + name + " " + opt.valueHint() + ".");
                    if (next != null && HELP.contains(next)) {
                        i++; // help in a value slot is no help (SPEC 8.5)
                    }
                    continue;
                }
                if (opt.choices() != null && !opt.choices().contains(value) && choiceError != null) {
                    InputError err = choiceError.apply(opt.name(), value);
                    refuse(err.key, err.reason, err.fix);
                }
                if (opt.repeatable()) {
                    out.lists.computeIfAbsent(opt.name(), k -> new ArrayList<>()).add(value);
                } else {
                    out.values.put(opt.name(), value);
                }
            }
            if (helpSeen) {
                Scan help = new Scan();
                help.help = true;
                help.action = helpAction;
                return help;
            }
            if (first != null) {
                throw first;
            }
            return out;
        }
    }
}
