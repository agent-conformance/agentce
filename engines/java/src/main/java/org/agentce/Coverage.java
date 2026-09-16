package org.agentce;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.math.BigInteger;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * Coverage and reconciliation with independent denominators (SPEC §6.5, §8.3 rung 0). For each subject
 * and event type the engine compares captured events against the independent denominator's count using
 * integer arithmetic and reports the ratio and a status. A source's own counters are never its own
 * denominator. A faithful port of the reference; the ratio is four places, banker's rounding, exact
 * integer arithmetic.
 */
public final class Coverage {
    private Coverage() {}

    private static final String STATUS_COVERED = "covered";
    private static final String STATUS_BELOW = "below_threshold";
    private static final String STATUS_UNKNOWN = "unknown";

    private static String eventType(JsonNode event) {
        JsonNode data = event.get("data");
        if (data != null && data.isObject() && data.get("@type") != null && data.get("@type").isTextual()) {
            return data.get("@type").textValue();
        }
        return "Unknown";
    }

    private static String source(JsonNode event) {
        JsonNode s = event.get("source");
        return s != null ? s.asText() : "";
    }

    private static String subject(JsonNode event) {
        JsonNode s = event.get("subject");
        return s != null ? s.asText() : "";
    }

    /** {@code observed / expected} to four decimals, banker's rounding, or null when {@code expected <= 0}. */
    private static String ratio(int observed, int expected) {
        if (expected <= 0) {
            return null;
        }
        BigInteger exp = BigInteger.valueOf(expected);
        BigInteger scaled = BigInteger.valueOf((long) observed * 10000L);
        BigInteger[] qr = scaled.divideAndRemainder(exp);
        BigInteger q = qr[0];
        BigInteger twice = qr[1].multiply(BigInteger.TWO);
        int cmp = twice.compareTo(exp);
        if (cmp > 0) {
            q = q.add(BigInteger.ONE);
        } else if (cmp == 0 && q.testBit(0)) {
            q = q.add(BigInteger.ONE); // half to even
        }
        BigInteger[] whole = q.divideAndRemainder(BigInteger.valueOf(10000));
        StringBuilder frac = new StringBuilder(whole[1].toString());
        while (frac.length() < 4) {
            frac.insert(0, '0');
        }
        return whole[0].toString() + "." + frac;
    }

    private static Map<String, Integer> denominatorCounts(
            Profile.CoverageDenominator denominator, Map<String, Map<String, Integer>> bySource, Path bundleRoot) {
        if (denominator.manifest != null && !denominator.manifest.isEmpty() && bundleRoot != null) {
            Path path = bundleRoot.resolve(denominator.manifest);
            if (Files.isRegularFile(path)) {
                JsonNode data = Json.parseFile(path);
                if (data != null && data.isObject()) {
                    JsonNode declared = firstNonEmpty(data.get("counts"), data.get("expected"));
                    Map<String, Integer> out = new LinkedHashMap<>();
                    var it = declared.fields();
                    while (it.hasNext()) {
                        var e = it.next();
                        out.put(e.getKey(), (int) e.getValue().asDouble());
                    }
                    return out;
                }
            }
        }
        Map<String, Integer> counts = new LinkedHashMap<>();
        Map<String, Integer> inner = bySource.get(denominator.source);
        if (inner != null) {
            for (Map.Entry<String, Integer> e : inner.entrySet()) {
                counts.merge(e.getKey(), e.getValue(), Integer::sum);
            }
        }
        return counts;
    }

    private static JsonNode firstNonEmpty(JsonNode... candidates) {
        for (JsonNode candidate : candidates) {
            if (candidate != null && candidate.isObject() && !candidate.isEmpty()) {
                return candidate;
            }
        }
        return Json.nodes().objectNode();
    }

    private static final class ExpectedEntry {
        int count = -1;
        List<String> denominators = new ArrayList<>();
    }

    /** Return the coverage.json structure for {@code events} under {@code profile}. */
    public static ObjectNode computeCoverage(List<JsonNode> events, Profile profile, Path bundleRoot) {
        Map<String, Map<String, Integer>> bySource = new LinkedHashMap<>();
        for (JsonNode event : events) {
            bySource.computeIfAbsent(source(event), k -> new LinkedHashMap<>())
                    .merge(eventType(event), 1, Integer::sum);
        }

        ObjectNode subjectsOut = Json.nodes().objectNode();
        for (Profile.Subject subject : profile.subjects) {
            Set<String> denomIds = Profile.denominatorIds(subject);
            Map<String, Integer> observed = new LinkedHashMap<>();
            Set<String> types = new LinkedHashSet<>();
            for (JsonNode event : events) {
                if (!subject(event).equals(subject.id)) {
                    continue;
                }
                if (denomIds.contains(source(event))) {
                    continue;
                }
                String type = eventType(event);
                observed.merge(type, 1, Integer::sum);
                types.add(type);
            }

            Map<String, ExpectedEntry> expected = new LinkedHashMap<>();
            for (Profile.CoverageDenominator denominator : subject.coverageDenominators) {
                Map<String, Integer> counts = denominatorCounts(denominator, bySource, bundleRoot);
                List<String> covered = !denominator.covers.isEmpty()
                        ? denominator.covers
                        : new ArrayList<>(counts.keySet());
                for (String type : covered) {
                    if (!counts.containsKey(type)) {
                        continue;
                    }
                    types.add(type);
                    ExpectedEntry entry = expected.computeIfAbsent(type, k -> new ExpectedEntry());
                    int value = counts.get(type);
                    if (value > entry.count) {
                        entry.count = value;
                        entry.denominators = new ArrayList<>(List.of(denominator.source));
                    } else if (value == entry.count) {
                        entry.denominators.add(denominator.source);
                    }
                }
            }

            ObjectNode byType = Json.nodes().objectNode();
            List<String> sortedTypes = new ArrayList<>(types);
            sortedTypes.sort(Json::byteCompare);
            for (String type : sortedTypes) {
                int obs = observed.getOrDefault(type, 0);
                ExpectedEntry entry = expected.get(type);
                ObjectNode row = byType.putObject(type);
                if (entry == null) {
                    row.put("observed", obs);
                    row.putNull("expected");
                    row.putNull("ratio");
                    row.put("status", STATUS_UNKNOWN);
                    row.putArray("denominators");
                } else {
                    row.put("observed", obs);
                    row.put("expected", entry.count);
                    String r = ratio(obs, entry.count);
                    if (r == null) {
                        row.putNull("ratio");
                    } else {
                        row.put("ratio", r);
                    }
                    row.put("status", obs >= entry.count ? STATUS_COVERED : STATUS_BELOW);
                    ArrayNode denoms = row.putArray("denominators");
                    List<String> sortedDenoms = new ArrayList<>(entry.denominators);
                    sortedDenoms.sort(Json::byteCompare);
                    sortedDenoms.forEach(denoms::add);
                }
            }

            ObjectNode subjectOut = subjectsOut.putObject(subject.id);
            subjectOut.set("event_types", byType);
            subjectOut.put("coverage_status", rollup(subject.coverageDenominators, byType));
            subjectOut.put("has_independent_denominator", !subject.coverageDenominators.isEmpty());
        }

        ObjectNode out = Json.nodes().objectNode();
        out.set("subjects", subjectsOut);
        return out;
    }

    private static String rollup(List<Profile.CoverageDenominator> denominators, ObjectNode byType) {
        if (denominators.isEmpty()) {
            return STATUS_UNKNOWN;
        }
        Set<String> statuses = new LinkedHashSet<>();
        byType.forEach(entry -> statuses.add(entry.get("status").asText()));
        if (statuses.contains(STATUS_UNKNOWN)) {
            return STATUS_UNKNOWN;
        }
        if (statuses.contains(STATUS_BELOW)) {
            return "gap";
        }
        return "ok";
    }
}
