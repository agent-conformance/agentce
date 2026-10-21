package org.agentce;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.math.BigInteger;
import java.nio.charset.CharacterCodingException;
import java.util.ArrayList;
import java.util.HashSet;
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

    static final String STATUS_COVERED = "covered";
    private static final String STATUS_BELOW = "below_threshold";
    static final String STATUS_UNKNOWN = "unknown";

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
    private static String ratio(int observed, long expected) {
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

    /**
     * A denominator counts only when it is independent (SPEC §6.5): never one of the subject's own declared evidence
     * sources, and never an admission attestation, whose signature and policy digest no engine verifies.
     */
    static boolean independent(Profile.CoverageDenominator denominator, Profile.Subject subject) {
        if ("admission_attestation".equals(denominator.kind)) {
            return false;
        }
        for (Profile.EvidenceSource s : subject.evidenceSources) {
            if (s.source != null && s.source.equals(denominator.source)) {
                return false;
            }
        }
        return true;
    }

    /**
     * The denominator's counts for the types it covers (every type it counts when {@code covers} is empty): both its
     * yardstick and its expected counts (SPEC §6.5).
     */
    static Map<String, Long> denominatorCounts(
            Profile.CoverageDenominator denominator,
            Profile.Subject subject,
            Map<String, Map<String, Integer>> bySource,
            Map<String, byte[]> members) {
        Map<String, Long> counts = declaredCounts(denominator, subject, bySource, members);
        if (!denominator.covers.isEmpty()) {
            counts.keySet().retainAll(denominator.covers);
        }
        return counts;
    }

    private static Map<String, Long> declaredCounts(
            Profile.CoverageDenominator denominator,
            Profile.Subject subject,
            Map<String, Map<String, Integer>> bySource,
            Map<String, byte[]> members) {
        if (!independent(denominator, subject)) {
            return new LinkedHashMap<>();
        }
        if (denominator.manifest != null && !denominator.manifest.isEmpty()) {
            byte[] raw = members.get(denominator.manifest);
            if (raw == null) {
                // A file the bundle manifest does not list is outside the bundle digest: never read (SPEC §6.6).
                return new LinkedHashMap<>();
            }
            // The bytes Bundle.load checked and hashed, never a second read of the name (18.184f).
            JsonNode data = loadManifest(denominator.manifest, raw);
            // A root that is not an object declares no counts: fall through, as Python and TypeScript do.
            if (data != null && data.isObject()) {
                Map<String, Long> counts = manifestCounts(denominator.manifest, data, "counts");
                Map<String, Long> expected = manifestCounts(denominator.manifest, data, "expected");
                return counts.isEmpty() ? expected : counts;
            }
        }
        Map<String, Long> counts = new LinkedHashMap<>();
        Map<String, Integer> inner = bySource.get(denominator.source);
        if (inner != null) {
            for (Map.Entry<String, Integer> e : inner.entrySet()) {
                counts.merge(e.getKey(), (long) e.getValue(), Long::sum);
            }
        }
        return counts;
    }

    private static InputError invalidManifest(String name, String why) {
        return new InputError(
                "input.coverage_denominator_manifest_invalid",
                "the coverage denominator manifest '" + name + "' " + why + ".",
                "write the manifest as one JSON object whose \"counts\" maps each event type to a whole number of"
                        + " records, 0 or more.");
    }

    /** The manifest's JSON, read as every engine reads it: strict UTF-8 and standard JSON only (18.184j). */
    private static JsonNode loadManifest(String name, byte[] raw) {
        try {
            return Json.parse(Verify.decodeStrict(raw).toString());
        } catch (CharacterCodingException | RuntimeException e) {
            throw invalidManifest(name, "is not JSON (" + e.getMessage() + ")");
        }
    }

    /** {@code data[key]} as counts: absent is none, anything else must be an object of whole numbers from 0 to 2^53 - 1. */
    private static Map<String, Long> manifestCounts(String name, JsonNode data, String key) {
        Map<String, Long> out = new LinkedHashMap<>();
        if (!data.has(key)) {
            return out;
        }
        JsonNode declared = data.get(key);
        if (!declared.isObject()) {
            throw invalidManifest(name, "has a '" + key + "' that is not a JSON object");
        }
        var it = declared.fields();
        while (it.hasNext()) {
            var e = it.next();
            JsonNode count = e.getValue();
            boolean whole = count.isIntegralNumber()
                    && count.canConvertToLong()
                    && count.longValue() >= 0
                    && count.longValue() <= DomainBinding.MAX_COUNT;
            if (!whole) {
                throw invalidManifest(name, "counts '" + e.getKey() + "' as " + count
                        + ", not a whole number from 0 to " + DomainBinding.MAX_COUNT);
            }
            out.put(e.getKey(), count.longValue());
        }
        return out;
    }

    private static final class ExpectedEntry {
        long count = -1;
        List<String> denominators = new ArrayList<>();
    }

    /**
     * Return the coverage.json structure for {@code events} under {@code profile}. {@code members} maps each file the
     * bundle manifest lists to the bytes {@link Bundle#load} checked: a denominator manifest is taken from there and
     * only when listed.
     */
    public static ObjectNode computeCoverage(
            List<JsonNode> events, Profile profile, Map<String, byte[]> members) {
        Map<String, Map<String, Integer>> bySource = new LinkedHashMap<>();
        for (JsonNode event : events) {
            bySource.computeIfAbsent(source(event), k -> new LinkedHashMap<>())
                    .merge(eventType(event), 1, Integer::sum);
        }

        ObjectNode subjectsOut = Json.nodes().objectNode();
        for (Profile.Subject subject : profile.subjects) {
            List<Map.Entry<Profile.CoverageDenominator, Map<String, Long>>> countsByDenominator = new ArrayList<>();
            for (Profile.CoverageDenominator d : subject.coverageDenominators) {
                countsByDenominator.add(Map.entry(d, denominatorCounts(d, subject, bySource, members)));
            }
            // A denominator's own records of a type it covers and counts are the yardstick, not captured operations.
            // One that is not independent, or does not count a type, hides none of that type's events (SPEC §6.5).
            Set<String> yardstick = new HashSet<>();
            for (Map.Entry<Profile.CoverageDenominator, Map<String, Long>> e : countsByDenominator) {
                for (String t : e.getValue().keySet()) {
                    yardstick.add(e.getKey().source + "\u0000" + t);
                }
            }
            Map<String, Integer> observed = new LinkedHashMap<>();
            Set<String> types = new LinkedHashSet<>();
            for (JsonNode event : events) {
                if (!subject(event).equals(subject.id)) {
                    continue;
                }
                String type = eventType(event);
                if (yardstick.contains(source(event) + "\u0000" + type)) {
                    continue;
                }
                observed.merge(type, 1, Integer::sum);
                types.add(type);
            }

            Map<String, ExpectedEntry> expected = new LinkedHashMap<>();
            for (Map.Entry<Profile.CoverageDenominator, Map<String, Long>> e : countsByDenominator) {
                Profile.CoverageDenominator denominator = e.getKey();
                Map<String, Long> counts = e.getValue();
                for (String type : counts.keySet()) {
                    types.add(type);
                    ExpectedEntry entry = expected.computeIfAbsent(type, k -> new ExpectedEntry());
                    long value = counts.get(type);
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
