package org.agentce;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;
import java.util.TreeSet;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * The error-key registry (SPEC §13.4 AX-6), a port of Python's {@code agentce.error_catalogue}: every
 * {@code errors.<key>.cause|fix} pair in the vendored catalogue ({@code src/main/resources/i18n/},
 * sourced from {@code spec/i18n/}) becomes one entry, and {@link #catalogueGaps} names any key a raise
 * site uses that the catalogue lacks. Raise sites keep their own run-time cause and fix text, as
 * Python's do; the catalogue holds the generic, documented text {@code docs/errors.md} is rendered
 * from. Mirrors {@code engines/typescript/src/errorCatalogue.ts}.
 */
public final class ErrorCatalogue {
    private ErrorCatalogue() {}

    /** One key's documented text. */
    public record Entry(String cause, String fix) {}

    private static final Pattern CATALOGUE_KEY = Pattern.compile("^errors\\.(.+)\\.(cause|fix)$");

    /** A literal key passed as the first argument of an error constructor or factory. Keys composed at
     * run time ({@code "input." + key + "_missing"}) do not end in a comma after the literal and are not
     * matched, as in Python. */
    private static final Pattern RAISED =
            Pattern.compile("(?:new\\s+(?:InputError|AgentceError)|InputError\\.unreadable|refusal)\\(\\s*\"([^\"]+)\"\\s*[,)]");

    private static volatile Map<String, Entry> cached;

    private static Map<String, Entry> load() {
        Map<String, String> catalog = Messages.loadCatalog(Messages.DEFAULT_LANGUAGE);
        Map<String, Entry> entries = new TreeMap<>();
        for (String name : catalog.keySet()) {
            Matcher m = CATALOGUE_KEY.matcher(name);
            if (m.matches()) {
                String key = m.group(1);
                entries.putIfAbsent(key, new Entry(
                        catalog.getOrDefault("errors." + key + ".cause", ""),
                        catalog.getOrDefault("errors." + key + ".fix", "")));
            }
        }
        return Collections.unmodifiableMap(entries);
    }

    /** key -> {cause, fix}, built from the vendored catalogue on first use. */
    public static Map<String, Entry> messageKeys() {
        Map<String, Entry> result = cached;
        if (result == null) {
            result = load();
            cached = result;
        }
        return result;
    }

    /** The English cause text the catalogue holds for error {@code key}, the same text Python's {@code
     * error_catalogue.MESSAGE_KEYS[key].cause} reads. Throws if the key has none, so a missing entry
     * fails loudly rather than printing an empty reason. */
    public static String errorCause(String key) {
        Entry entry = messageKeys().get(key);
        if (entry == null || entry.cause().isEmpty()) {
            throw new IllegalStateException("message catalogue has no errors." + key + ".cause");
        }
        return entry.cause();
    }

    /** The English fix text the catalogue holds for error {@code key}; throws if missing. */
    public static String errorFix(String key) {
        Entry entry = messageKeys().get(key);
        if (entry == null || entry.fix().isEmpty()) {
            throw new IllegalStateException("message catalogue has no errors." + key + ".fix");
        }
        return entry.fix();
    }

    /** The completeness check: a key with an empty cause or fix, and any key raised in {@code sources}
     * (the engine's own source texts) that the catalogue lacks. Empty when the catalogue covers them. */
    public static List<String> catalogueGaps(Iterable<String> sources) {
        Map<String, Entry> keys = messageKeys();
        List<String> problems = new ArrayList<>();
        for (Map.Entry<String, Entry> e : keys.entrySet()) {
            if (e.getValue().cause().isBlank()) {
                problems.add(e.getKey() + ": no cause");
            }
            if (e.getValue().fix().isBlank()) {
                problems.add(e.getKey() + ": no fix");
            }
        }
        Set<String> raised = new TreeSet<>();
        for (String text : sources) {
            Matcher m = RAISED.matcher(text);
            while (m.find()) {
                raised.add(m.group(1));
            }
        }
        for (String key : raised) {
            if (!keys.containsKey(key)) {
                problems.add(key + ": raised but not in the catalogue");
            }
        }
        return problems;
    }
}
