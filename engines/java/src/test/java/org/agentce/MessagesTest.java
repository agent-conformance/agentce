package org.agentce;

import static org.junit.jupiter.api.Assertions.assertEquals;

import com.fasterxml.jackson.databind.JsonNode;
import java.nio.file.Path;
import java.util.Iterator;
import java.util.LinkedHashMap;
import java.util.Map;
import org.junit.jupiter.api.Test;

/**
 * {@code Messages.catalogue()} loads the vendored {@code spec/i18n} catalogue rather than a
 * hand-copied dictionary, so these tests compare against a live read of the spec file at test time --
 * not a copy-pasted literal -- so a reverted loader (one that returns a hardcoded map again) cannot
 * pass even if it still hard-codes a few correct-looking spot values (18.35).
 */
class MessagesTest {
    private static final String[] PREFIXES = {"report.", "verdict.", "next.", "outcome.", "readiness."};

    private static Map<String, String> specReportKeys(String language) {
        Path path = TestPaths.repoRoot().resolve("spec/i18n/messages." + language + ".json");
        if (!path.toFile().isFile()) {
            return Map.of();
        }
        JsonNode node = Json.parseFile(path);
        Map<String, String> out = new LinkedHashMap<>();
        for (Iterator<String> it = node.fieldNames(); it.hasNext(); ) {
            String key = it.next();
            for (String prefix : PREFIXES) {
                if (key.startsWith(prefix)) {
                    out.put(key, node.get(key).asText());
                    break;
                }
            }
        }
        return out;
    }

    @Test
    void catalogueEnDeepEqualsALiveFilteredReadOfSpecI18nMessagesEnJson() {
        assertEquals(specReportKeys("en"), Messages.catalogue("en"));
    }

    @Test
    void catalogueDeDeepEqualsEnFilteredAndMergedWithDeFiltered() {
        Map<String, String> want = new LinkedHashMap<>(specReportKeys("en"));
        want.putAll(specReportKeys("de"));
        assertEquals(want, Messages.catalogue("de"));
    }

    @Test
    void catalogueWithNoArgumentDefaultsToEn() {
        assertEquals(Messages.catalogue("en"), Messages.catalogue());
    }

    @Test
    void activityNoneUndeclaredMatchesTheSpecText() {
        assertEquals(
                "every agent, tool, and model your records show is declared",
                Messages.catalogue("en").get("report.activity_none_undeclared"));
    }

    @Test
    void activityUndeclaredAgentsLabelIsPresentAndMatchesTheSpecText() {
        assertEquals("Agents", Messages.catalogue("en").get("report.activity_undeclared_agents_label"));
    }

    @Test
    void catalogueWithNullLanguageFallsBackToEnWithoutThrowing() {
        assertEquals(Messages.catalogue("en"), Messages.catalogue(null));
    }
}
