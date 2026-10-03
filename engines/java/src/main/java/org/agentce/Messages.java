package org.agentce;

import com.fasterxml.jackson.databind.JsonNode;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.util.Iterator;
import java.util.LinkedHashMap;
import java.util.Map;

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

    private static Map<String, String> loadCatalog(String language) {
        String text = readClasspathResource("/i18n/messages." + language + ".json");
        if (text == null) {
            return Map.of();
        }
        Map<String, String> out = new LinkedHashMap<>();
        JsonNode node = Json.parse(text);
        for (Iterator<String> it = node.fieldNames(); it.hasNext(); ) {
            String key = it.next();
            out.put(key, node.get(key).asText());
        }
        return out;
    }

    private static Map<String, String> reportKeys(String language) {
        Map<String, String> out = new LinkedHashMap<>();
        for (Map.Entry<String, String> entry : loadCatalog(language).entrySet()) {
            for (String prefix : REPORT_KEY_PREFIXES) {
                if (entry.getKey().startsWith(prefix)) {
                    out.put(entry.getKey(), entry.getValue());
                    break;
                }
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
