package org.agentce;

import com.fasterxml.jackson.databind.JsonNode;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.util.Arrays;
import java.util.Iterator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

/**
 * Message-key catalogue for report rendering (SPEC §9.3, §8.4). All human-readable text in {@code
 * report.md} and {@code report.html} comes from message keys, read from the vendored, language-neutral
 * catalogue ({@code src/main/resources/i18n/}, sourced from {@code spec/i18n/}, {@code BundledDataTest}
 * holds them in sync) that also backs Python's {@code agentce.error_catalogue} CLI strings, so a
 * translation changes only the report -- never {@code assertions.json}, the manifest digests, or the
 * claim. A partial {@code de} catalogue demonstrates the mechanism; missing keys fall back to {@code
 * en}. A faithful port of the Python reference ({@code engines/python/agentce/messages.py},
 * {@code i18n_format.py}); mirrors {@code engines/typescript/src/messages.ts}.
 */
public final class Messages {
    private Messages() {}

    public static final String DEFAULT_LANGUAGE = "en";

    /** The languages a report can be rendered in: one per vendored {@code messages.<lang>.json}, sorted,
     * as Python's {@code messages.available_languages()} globs them ({@code CliTest} holds this list to
     * the vendored files). */
    public static final List<String> AVAILABLE_LANGUAGES = List.of("de", "en");

    /** Which of the catalogue's keys this module renders. Their text lives in the vendored catalogue,
     * never hardcoded in this module. */
    private static final String[] REPORT_KEY_PREFIXES = {
        "report.", "verdict.", "next.", "outcome.", "readiness."
    };

    private static String readClasspathResource(String path) {
        try (InputStream in = Messages.class.getResourceAsStream(path)) {
            if (in == null) {
                return null;
            }
            return new String(in.readAllBytes(), StandardCharsets.UTF_8);
        } catch (IOException e) {
            throw new IllegalStateException("cannot read classpath resource " + path + ": " + e.getMessage(), e);
        }
    }

    /** The vendored catalogue for {@code language}, as Python's {@code i18n_format.load_catalog}: empty
     * when that language has no file at all; a file that is not valid JSON, or whose top level is not an
     * object, throws (a corrupt catalogue must never read as silently empty strings). */
    static Map<String, String> loadCatalog(String language) {
        String path = "/i18n/messages." + language + ".json";
        return parseCatalog(path, readClasspathResource(path));
    }

    /** {@link #loadCatalog}'s parse of one catalogue's text ({@code null} for a missing file), package
     * visible for its unit test. */
    static Map<String, String> parseCatalog(String path, String text) {
        if (text == null) {
            return Map.of();
        }
        Map<String, String> out = new LinkedHashMap<>();
        JsonNode node = Json.parse(text);
        if (node == null || !node.isObject()) {
            throw new IllegalArgumentException(path + " does not hold a message catalogue object");
        }
        for (Iterator<String> it = node.fieldNames(); it.hasNext(); ) {
            String key = it.next();
            out.put(key, node.get(key).asText());
        }
        return out;
    }

    /** `loadCatalog` is read-and-filter per language; a report render calls {@code catalogue()}
     * several times, so the parsed-and-filtered result is cached per language rather than re-reading
     * the classpath resource each time. */
    private static final Map<String, Map<String, String>> REPORT_KEY_CACHE = new ConcurrentHashMap<>();

    private static Map<String, String> reportKeys(String language) {
        // ConcurrentHashMap refuses a null key; a null language (falls back to en, same as an
        // unrecognised one) is rare enough to skip the cache rather than normalise it into one.
        if (language == null) {
            return filterReportKeys(null);
        }
        return REPORT_KEY_CACHE.computeIfAbsent(language, Messages::filterReportKeys);
    }

    private static Map<String, String> filterReportKeys(String language) {
        Map<String, String> out = new LinkedHashMap<>();
        for (Map.Entry<String, String> entry : loadCatalog(language).entrySet()) {
            if (Arrays.stream(REPORT_KEY_PREFIXES).anyMatch(entry.getKey()::startsWith)) {
                out.put(entry.getKey(), entry.getValue());
            }
        }
        return out;
    }

    /** The message catalogue for {@code language}, backed by {@code en} for any missing key. */
    public static Map<String, String> catalogue(String language) {
        Map<String, String> merged = new LinkedHashMap<>(reportKeys("en"));
        merged.putAll(reportKeys(language));
        return merged;
    }

    public static Map<String, String> catalogue() {
        return catalogue(DEFAULT_LANGUAGE);
    }
}
