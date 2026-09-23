package org.agentce;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Integrity verification of evidence streams (SPEC §6.6). Each event carries {@code data.integrity}
 * {@code {hash, prev, stream, strength, sig_ref?}}. Verification recomputes each hash, checks the
 * {@code prev} chain, cross-checks source timestamps against anchor times, and reports one status per
 * stream. Broken streams are never fatal: they are findings, not assertions (SPEC §8.1). A faithful
 * port of the reference; its results inform findings and do not feed assertions.
 */
public final class Integrity {
    private Integrity() {}

    public static final String GENESIS_PREV = "0".repeat(64);

    private static final String SOURCE_SIGNED = "source_signed";
    private static final String EXPORT_ANCHORED = "export_anchored";
    private static final String EXPORT_CHAINED = "export_chained";
    private static final String VERIFIED = "verified";
    private static final String VERIFIED_WEAK = "verified_weak";
    private static final String GAP = "gap";
    private static final String REORDERED = "reordered";
    private static final String UNSIGNED = "unsigned";
    private static final String TIME_SUSPECT = "time_suspect";
    private static final String FAILED = "failed";

    public static final class Result {
        public final String stream;
        public String strength;
        public String status = "";
        public Integer firstBadIndex;
        public List<JsonNode> anchors;

        Result(String stream, String strength, List<JsonNode> anchors) {
            this.stream = stream;
            this.strength = strength;
            this.anchors = anchors;
        }

        public ObjectNode toJson() {
            ObjectNode record = Json.nodes().objectNode();
            record.put("stream", stream);
            record.put("strength", strength);
            record.put("status", status);
            if (firstBadIndex != null) {
                record.put("first_bad_index", firstBadIndex);
            }
            if (!anchors.isEmpty()) {
                ArrayNode a = record.putArray("anchors");
                anchors.forEach(a::add);
            }
            return record;
        }
    }

    private static JsonNode integrityBlock(JsonNode event) {
        JsonNode data = event.get("data");
        if (data != null && data.isObject()) {
            JsonNode block = data.get("integrity");
            if (block != null && block.isObject()) {
                return block;
            }
        }
        return null;
    }

    private static String streamKey(JsonNode event) {
        JsonNode block = integrityBlock(event);
        if (block != null && block.get("stream") != null && block.get("stream").isTextual()) {
            return block.get("stream").textValue();
        }
        JsonNode source = event.get("source");
        JsonNode subject = event.get("subject");
        return (source != null ? source.asText() : "") + "|" + (subject != null ? subject.asText() : "");
    }

    static String recomputeHash(JsonNode event) {
        ObjectNode without = ((ObjectNode) event).deepCopy();
        JsonNode data = without.get("data");
        if (data != null && data.isObject()) {
            ObjectNode stripped = Json.nodes().objectNode();
            data.fields().forEachRemaining(e -> {
                if (!e.getKey().equals("integrity")) {
                    stripped.set(e.getKey(), e.getValue());
                }
            });
            without.set("data", stripped);
        }
        return Canonical.sha256Hex(without);
    }

    private static Long parseTime(String value) {
        if (value == null || value.isEmpty()) {
            return null;
        }
        try {
            return java.time.Instant.parse(value).toEpochMilli();
        } catch (RuntimeException ignored) {
            // fall through
        }
        try {
            return java.time.OffsetDateTime.parse(value).toInstant().toEpochMilli();
        } catch (RuntimeException ignored) {
            return null;
        }
    }

    private static List<Long> anchorTimes(List<JsonNode> anchors) {
        List<Long> times = new ArrayList<>();
        for (JsonNode anchor : anchors) {
            JsonNode at = anchor.get("at");
            Long parsed = parseTime(at != null ? at.asText() : "");
            if (parsed != null) {
                times.add(parsed);
            }
        }
        return times;
    }

    static boolean isSigned(JsonNode block, Path bundleRoot) {
        if (block == null) {
            return false;
        }
        JsonNode sigRef = block.get("sig_ref");
        if (sigRef == null || !sigRef.isTextual() || sigRef.textValue().isEmpty()) {
            return false;
        }
        if (bundleRoot == null) {
            return true;
        }
        // A sig_ref is evidence content: confine it to the bundle root so a hostile event cannot have
        // an attacker-chosen file outside the bundle stand in as this stream's signature.
        Path confined = Bundle.confineToRoot(bundleRoot, sigRef.textValue());
        return confined != null && Bundle.safeIsFile(confined);
    }

    private static Result verifyStream(String stream, List<JsonNode> events, List<JsonNode> anchors, Path bundleRoot) {
        List<JsonNode> blocks = new ArrayList<>();
        for (JsonNode event : events) {
            blocks.add(integrityBlock(event));
        }
        String strength = EXPORT_CHAINED;
        for (JsonNode block : blocks) {
            if (block != null && block.get("strength") != null && block.get("strength").isTextual()) {
                strength = block.get("strength").textValue();
                break;
            }
        }
        Result result = new Result(stream, strength, anchors);

        // 1. Tamper: a recomputed hash that does not match the stored one.
        for (int index = 0; index < events.size(); index++) {
            JsonNode block = blocks.get(index);
            String stored = block != null && block.get("hash") != null ? block.get("hash").asText(null) : null;
            if (block == null || stored == null || !stored.equals(recomputeHash(events.get(index)))) {
                result.status = FAILED;
                result.firstBadIndex = index;
                return result;
            }
        }

        // 2. Chain linkage in arrival order.
        java.util.Set<String> allHashes = new java.util.HashSet<>();
        for (JsonNode block : blocks) {
            if (block != null && block.has("hash")) {
                allHashes.add(block.get("hash").asText());
            }
        }
        for (int index = 0; index < blocks.size(); index++) {
            JsonNode block = blocks.get(index);
            String prev = block.get("prev") != null ? block.get("prev").asText() : "";
            String want = index == 0 ? GENESIS_PREV : blocks.get(index - 1).get("hash").asText();
            if (!prev.equals(want)) {
                boolean gap = !prev.equals(GENESIS_PREV) && !allHashes.contains(prev);
                result.status = gap ? GAP : REORDERED;
                result.firstBadIndex = index;
                return result;
            }
        }

        // 3. Timestamp trust: an event later than the anchor that covers it is suspect.
        List<Long> times = anchorTimes(anchors);
        if (!times.isEmpty()) {
            long latestAnchor = times.stream().mapToLong(Long::longValue).max().getAsLong();
            for (int index = 0; index < events.size(); index++) {
                JsonNode t = events.get(index).get("time");
                Long eventTime = parseTime(t != null ? t.asText() : "");
                if (eventTime != null && eventTime > latestAnchor) {
                    result.status = TIME_SUSPECT;
                    result.firstBadIndex = index;
                    return result;
                }
            }
        }

        // 4. Strength and signatures.
        if (strength.equals(SOURCE_SIGNED)) {
            boolean allSigned = true;
            for (JsonNode block : blocks) {
                if (!isSigned(block, bundleRoot)) {
                    allSigned = false;
                    break;
                }
            }
            result.status = allSigned ? VERIFIED : UNSIGNED;
        } else if (strength.equals(EXPORT_ANCHORED)) {
            result.status = !anchors.isEmpty() ? VERIFIED : VERIFIED_WEAK;
        } else {
            result.status = VERIFIED_WEAK;
        }
        return result;
    }

    private static Map<String, List<JsonNode>> anchorsByStream(JsonNode manifest) {
        Map<String, List<JsonNode>> out = new LinkedHashMap<>();
        JsonNode streams = manifest.get("streams");
        if (streams != null && streams.isArray()) {
            for (JsonNode entry : streams) {
                if (entry.isObject() && entry.get("stream") != null && entry.get("stream").isTextual()) {
                    List<JsonNode> anchors = new ArrayList<>();
                    JsonNode a = entry.get("anchors");
                    if (a != null && a.isArray()) {
                        a.forEach(anchors::add);
                    }
                    out.put(entry.get("stream").textValue(), anchors);
                }
            }
        }
        return out;
    }

    /** Verify every integrity stream in {@code events}; return one result per stream, ordered by stream id. */
    public static List<Result> verifyBundle(List<JsonNode> events, JsonNode manifest, Path bundleRoot) {
        Map<String, List<JsonNode>> anchors = anchorsByStream(manifest);
        Map<String, List<JsonNode>> grouped = new LinkedHashMap<>();
        for (JsonNode event : events) {
            grouped.computeIfAbsent(streamKey(event), k -> new ArrayList<>()).add(event);
        }
        List<String> streams = new ArrayList<>(grouped.keySet());
        streams.sort(Json::byteCompare);
        List<Result> results = new ArrayList<>();
        for (String stream : streams) {
            results.add(verifyStream(stream, grouped.get(stream), anchors.getOrDefault(stream, List.of()), bundleRoot));
        }
        return results;
    }
}
