package org.agentce;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * Integrity verification of evidence streams (SPEC §6.6). Each event carries {@code data.integrity}
 * {@code {hash, prev, stream, strength, sig_ref?}}. Verification recomputes each hash, checks the
 * {@code prev} chain, cross-checks source timestamps against anchor times, and reports one status per
 * stream. Broken streams are never fatal: they are findings (SPEC §8.1), and {@link #integrityCaps} turns them
 * into the caps the assessment applies (SPEC §6.6, §8.1). A faithful port of the reference.
 *
 * <p>A {@code source_signed} stream reads verified only when every event's {@code sig_ref} names a DSSE envelope in
 * the bundle that verifies against a key the run trusts and whose in-toto Statement names that event's own hash
 * (SPEC §6.6); one envelope signs a segment by naming each of its events' hashes. The trusted keys are the
 * operator's own trust root ({@code --trust-root} or {@code AGENTCE_TRUST_ROOT}), never the development root vendored
 * in the engine, whose private seed is published: with no trust root no source signature counts, and the stream reads
 * unsigned.
 */
public final class Integrity {
    private Integrity() {}

    public static final String GENESIS_PREV = "0".repeat(64);

    static final String SOURCE_SIGNED = "source_signed";
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
        /**
         * What the strength and signatures alone read (verified, verified_weak or unsigned), kept when the time check
         * has already made the status time_suspect, so a stream's weakness still caps (SPEC §6.6). Not written out.
         */
        public String strengthStatus = "";

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

    /** Any aware date-time; an anchor time comparable with it is a UTC instant (compareInstants). */
    private static final String EPOCH = "1970-01-01T00:00:00Z";

    /**
     * The latest anchor time, compared as instants (SPEC §6.7); an anchor that is not an object, or whose time is not
     * a string with a UTC offset, places no record and is left out.
     */
    private static String latestAnchor(List<JsonNode> anchors) {
        String latest = null;
        for (JsonNode anchor : anchors) {
            JsonNode node = anchor.isObject() ? anchor.get("at") : null;
            if (node == null || !node.isTextual()) {
                continue;
            }
            String at = node.textValue();
            Integer order = latest == null ? null : Structural.compareInstants(at, latest);
            if (Structural.compareInstants(at, EPOCH) != null && (latest == null || Integer.valueOf(1).equals(order))) {
                latest = at;
            }
        }
        return latest;
    }

    /**
     * Whether a record's {@code sig_ref} envelope verifies against a trusted key and names the record's own hash. A
     * later record's hash does not do: a hash leaves out {@code data.integrity}, so it commits to no earlier record.
     * Anything else, including no trust root or no bundle to read the envelope from, is unsigned (fail closed).
     */
    static boolean isSigned(
            JsonNode block,
            Path bundleRoot,
            Verify.TrustRoot trust,
            Map<String, Set<String>> envelopes) {
        if (block == null || trust == null || bundleRoot == null) {
            return false;
        }
        JsonNode sigRef = block.get("sig_ref");
        if (sigRef == null || !sigRef.isTextual() || sigRef.textValue().isEmpty()) {
            return false;
        }
        Set<String> digests = envelopes.computeIfAbsent(
                sigRef.textValue(), ref -> signedDigests(bundleRoot, ref, trust));
        JsonNode hash = block.get("hash");
        return hash != null && hash.isTextual() && digests.contains(hash.textValue());
    }

    /**
     * The SHA-256 digests the in-toto Statement of a verified {@code sig_ref} envelope names as subjects; none when the
     * file is outside the bundle or unreadable, is not a DSSE envelope over an in-toto Statement, or verifies against
     * no key in {@code trust}. A sig_ref is evidence content: it is confined to the bundle root, so a file outside it
     * (reached literally or through a symlink) never counts.
     */
    private static Set<String> signedDigests(Path bundleRoot, String sigRef, Verify.TrustRoot trust) {
        Path path = Bundle.confineToRoot(bundleRoot, sigRef);
        if (path == null || !Bundle.safeIsFile(path)) {
            return Set.of();
        }
        JsonNode statement;
        try {
            JsonNode envelope = Verify.parseVerifyInput(
                    Files.readAllBytes(path), "verify.envelope_malformed", "the signature envelope");
            Verify.VerifiedEnvelope verified = Verify.verifyEnvelope(envelope, trust);
            JsonNode payloadType = envelope.get("payloadType");
            if (!payloadType.textValue().equals(Sign.INTOTO_PAYLOAD_TYPE)) {
                return Set.of();
            }
            statement = Verify.parseVerifyInput(
                    verified.payload(), "verify.statement_unreadable", "the signed statement");
        } catch (IllegalArgumentException | IOException e) {
            return Set.of();
        }
        JsonNode subjects = statement.isObject() ? statement.get("subject") : null;
        if (subjects == null || !subjects.isArray()) {
            return Set.of();
        }
        Set<String> out = new HashSet<>();
        for (JsonNode subject : subjects) {
            JsonNode digest = subject.isObject() ? subject.get("digest") : null;
            JsonNode sha256 = digest != null && digest.isObject() ? digest.get("sha256") : null;
            if (sha256 != null && sha256.isTextual()) {
                out.add(sha256.textValue());
            }
        }
        return out;
    }

    private static Result verifyStream(
            String stream,
            List<JsonNode> events,
            List<JsonNode> anchors,
            Path bundleRoot,
            Verify.TrustRoot sourceTrust) {
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

        // An anchor whose time cannot be placed (no UTC offset, absent, or not a date-time) anchors nothing (SPEC §8.1).
        String latest = latestAnchor(anchors);
        result.strengthStatus = strengthStatus(strength, blocks, latest != null, bundleRoot, sourceTrust);

        // 3. Timestamp trust: an event later than the anchor that covers it is suspect, and so is one whose time
        // cannot be placed against it (no UTC offset, or not a date-time): it cannot be cross-checked (SPEC §6.6,
        // fail closed). A stream whose anchors are all unplaceable cannot be cross-checked at all, so it is suspect as
        // a whole.
        if (!anchors.isEmpty() && latest == null) {
            result.status = TIME_SUSPECT;
            return result;
        }
        if (latest != null) {
            for (int index = 0; index < events.size(); index++) {
                JsonNode t = events.get(index).get("time");
                Integer order = Structural.compareInstants(t != null ? t.asText() : "", latest);
                if (order == null || order == 1) {
                    result.status = TIME_SUSPECT;
                    result.firstBadIndex = index;
                    return result;
                }
            }
        }

        result.status = result.strengthStatus;
        return result;
    }

    /**
     * What a stream's strength and signatures read: source_signed is verified only when every record is signed,
     * export_anchored only with a placeable anchor, and export_chained proves only "unchanged since export".
     */
    private static String strengthStatus(
            String strength, List<JsonNode> blocks, boolean anchored, Path bundleRoot, Verify.TrustRoot trust) {
        if (strength.equals(SOURCE_SIGNED)) {
            Map<String, Set<String>> envelopes = new HashMap<>();
            for (JsonNode block : blocks) {
                if (!isSigned(block, bundleRoot, trust, envelopes)) {
                    return UNSIGNED;
                }
            }
            return VERIFIED;
        }
        return strength.equals(EXPORT_ANCHORED) && anchored ? VERIFIED : VERIFIED_WEAK;
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

    /**
     * Verify every integrity stream in {@code events}; return one result per stream, ordered by stream id, then source.
     *
     * <p>A chain is keyed on (the record's source, the stream it claims): a claimed stream id names a stream of the
     * record's own source (SPEC §6.6), so a record never joins, or breaks, another source's chain by naming it, and no
     * chain under an id two sources claim reads verified (18.151).
     */
    public static List<Result> verifyBundle(List<JsonNode> events, JsonNode manifest, Path bundleRoot) {
        return verifyBundle(events, manifest, bundleRoot, null);
    }

    /** {@link #verifyBundle}, with the keys a {@code source_signed} record's signature must verify against. */
    public static List<Result> verifyBundle(
            List<JsonNode> events, JsonNode manifest, Path bundleRoot, Verify.TrustRoot sourceTrust) {
        return new ArrayList<>(verifyStreams(events, manifest, bundleRoot, sourceTrust).values());
    }

    private static String sourceOf(JsonNode event) {
        JsonNode source = event.get("source");
        return source != null ? source.asText() : "";
    }

    /** The key of one chain in {@link #verifyStreams}' results: its stream id and its source. */
    static String chainKey(String stream, String source) {
        return stream + "\u0000" + source;
    }

    /** {@link #verifyBundle}'s results keyed by {@link #chainKey}, in its order. */
    public static Map<String, Result> verifyStreams(List<JsonNode> events, JsonNode manifest, Path bundleRoot) {
        return verifyStreams(events, manifest, bundleRoot, null);
    }

    /** {@link #verifyStreams}, with the keys a {@code source_signed} record's signature must verify against. */
    public static Map<String, Result> verifyStreams(
            List<JsonNode> events, JsonNode manifest, Path bundleRoot, Verify.TrustRoot sourceTrust) {
        Map<String, List<JsonNode>> anchors = anchorsByStream(manifest);
        Map<String, Map<String, List<JsonNode>>> grouped = new LinkedHashMap<>();
        for (JsonNode event : events) {
            grouped.computeIfAbsent(streamKey(event), k -> new LinkedHashMap<>())
                    .computeIfAbsent(sourceOf(event), k -> new ArrayList<>())
                    .add(event);
        }
        List<String> streams = new ArrayList<>(grouped.keySet());
        streams.sort(Json::byteCompare);
        Map<String, Result> results = new LinkedHashMap<>();
        for (String stream : streams) {
            Map<String, List<JsonNode>> bySource = grouped.get(stream);
            List<String> sources = new ArrayList<>(bySource.keySet());
            sources.sort(Json::byteCompare);
            for (String source : sources) {
                Result result = verifyStream(
                        stream, bySource.get(source), anchors.getOrDefault(stream, List.of()), bundleRoot, sourceTrust);
                // A stream id two sources claim: the manifest's anchors and a sig_ref name no source, so neither chain
                // under that id reads verified, which fails closed rather than let one source's chain borrow another's
                // anchors (18.151).
                if (bySource.size() > 1) {
                    if (result.status.equals(VERIFIED)) {
                        result.status = VERIFIED_WEAK;
                    }
                    if (result.strengthStatus.equals(VERIFIED)) {
                        result.strengthStatus = VERIFIED_WEAK;
                    }
                }
                results.put(chainKey(stream, source), result);
            }
        }
        return results;
    }

    /** A broken chain: every record on it is read at most as self_report (SPEC §8.1). */
    private static final Set<String> BROKEN = Set.of(FAILED, GAP, REORDERED);
    /** A chain below verified: an enforcement-point record on it caps a pass at partial (SPEC §6.6, weak_integrity). */
    private static final Set<String> WEAK = Set.of(VERIFIED_WEAK, UNSIGNED);
    private static final Set<String> CAPPED_CLASSES = Set.of("enforcement_point", "independent_system");

    /**
     * Which accepted records an integrity result caps, by event id (SPEC §6.6, §8.1): {@code broken} holds the
     * enforcement-point and independent-system records on a failed, gap or reordered stream (a record with no integrity
     * member included), {@code weak} the enforcement-point records on a verified_weak or unsigned stream, and
     * {@code timeSuspect} every record on a time_suspect stream.
     */
    public record Caps(Set<String> broken, Set<String> weak, Set<String> timeSuspect) {
        public static final Caps NONE = new Caps(Set.of(), Set.of(), Set.of());
    }

    /**
     * The caps {@code results} (from {@link #verifyStreams} over {@code rawAccepted}) put on {@code accepted}, the same
     * records index for index with their trust-corrected class (ingest).
     */
    public static Caps integrityCaps(List<JsonNode> rawAccepted, List<JsonNode> accepted, Map<String, Result> results) {
        Set<String> broken = new HashSet<>();
        Set<String> weak = new HashSet<>();
        Set<String> suspect = new HashSet<>();
        for (int index = 0; index < rawAccepted.size(); index++) {
            JsonNode raw = rawAccepted.get(index);
            JsonNode event = accepted.get(index);
            Result result = results.get(chainKey(streamKey(raw), sourceOf(raw)));
            String status = result != null ? result.status : FAILED;
            String eventId = Readiness.pyStr(event.get("id"));
            JsonNode clsNode = event.get("agentcesourceclass");
            String cls = clsNode != null && clsNode.isTextual() ? clsNode.textValue() : null;
            if (BROKEN.contains(status) && cls != null && CAPPED_CLASSES.contains(cls)) {
                broken.add(eventId);
            } else if (WEAK.contains(status) && "enforcement_point".equals(cls)) {
                weak.add(eventId);
            } else if (status.equals(TIME_SUSPECT)) {
                suspect.add(eventId);
                // The time check reads before the strength: a weak or unsigned stream stays weak whatever its times say.
                if ("enforcement_point".equals(cls) && result != null && WEAK.contains(result.strengthStatus)) {
                    weak.add(eventId);
                }
            }
        }
        return new Caps(Set.copyOf(broken), Set.copyOf(weak), Set.copyOf(suspect));
    }
}
