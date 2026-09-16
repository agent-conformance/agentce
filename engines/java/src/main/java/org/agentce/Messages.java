package org.agentce;

import java.util.LinkedHashMap;
import java.util.Map;

/**
 * Message-key catalogue for report rendering (SPEC §9.3, §8.4). All human-readable text in
 * {@code report.md} and {@code report.html} comes from message keys, so a translation changes only the
 * report — never {@code assertions.json}, the manifest digests, or the claim. A partial {@code de}
 * catalogue demonstrates the mechanism; missing keys fall back to {@code en}.
 */
public final class Messages {
    private Messages() {}

    public static final String DEFAULT_LANGUAGE = "en";

    private static final Map<String, String> EN = en();
    private static final Map<String, Map<String, String>> CATALOGUES = catalogues();

    private static Map<String, String> en() {
        Map<String, String> m = new LinkedHashMap<>();
        m.put("report.title", "AgentCE conformance report");
        m.put("report.summary_heading", "Outcome summary");
        m.put("report.assertions_heading", "Assertions");
        m.put("report.no_controls", "No controls were evaluated.");
        m.put("report.affected_persons",
                "Affected persons may obtain an explanation and raise concerns through the deployer's "
                        + "published contact channel (EU AI Act Arts. 26(11), 85, 86).");
        m.put("outcome.conformant", "conformant");
        m.put("outcome.non-conformant", "non-conformant");
        m.put("outcome.partial", "partial");
        m.put("outcome.not_applicable", "not applicable");
        m.put("outcome.not_assessed", "not assessed");
        m.put("outcome.insufficient_evidence", "insufficient evidence");
        return m;
    }

    private static Map<String, Map<String, String>> catalogues() {
        Map<String, String> de = new LinkedHashMap<>();
        de.put("report.title", "AgentCE-Konformitätsbericht");
        de.put("report.summary_heading", "Ergebnisübersicht");
        de.put("report.assertions_heading", "Aussagen");
        de.put("report.no_controls", "Es wurden keine Kontrollen bewertet.");
        Map<String, Map<String, String>> c = new LinkedHashMap<>();
        c.put("en", EN);
        c.put("de", de);
        return c;
    }

    /** The message catalogue for {@code language}, backed by {@code en} for any missing key. */
    public static Map<String, String> catalogue(String language) {
        Map<String, String> merged = new LinkedHashMap<>(EN);
        merged.putAll(CATALOGUES.getOrDefault(language, Map.of()));
        return merged;
    }

    public static Map<String, String> catalogue() {
        return catalogue(DEFAULT_LANGUAGE);
    }
}
