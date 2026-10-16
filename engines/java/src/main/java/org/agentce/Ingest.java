package org.agentce;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.io.IOException;
import java.nio.charset.CharacterCodingException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Ingest and validate an evidence bundle (SPEC §8.1 first module). Parse every event in
 * {@code events/*.jsonl}, validate it against the JSON Schema, and quarantine anything the engine
 * refuses with a stable reason (SPEC App. F). Quarantine is an output, never a silent drop. A faithful
 * port of the reference.
 *
 * <p>A source the manifest declares with no trust class (or a bundle with no manifest classes at all)
 * is "undeclared": its events keep flowing, but {@code accepted} never trusts the event's own
 * self-asserted {@code agentcesourceclass} at face value -- it is corrected down to the weakest class,
 * {@code self_report}, for every downstream reader (SPEC §6.4). That correction never reaches {@code
 * rawAccepted}, which stays byte-identical to what the source emitted, because SPEC §6.6 defines each
 * event's {@code integrity.hash} over the whole CloudEvent ({@code agentcesourceclass} included) as
 * received -- callers verifying integrity must hash what was actually signed, not the engine's
 * trust-corrected copy.
 */
public final class Ingest {
    private Ingest() {}

    public static final int DEFAULT_MAX_EVENT_BYTES = 1_048_576;

    /** A record that passed every per-record check, waiting for the id and time-order checks. */
    private record Candidate(JsonNode event, JsonNode rawEvent, String source, String typeValue, int rank) {}
    private static final Pattern TYPE_RE =
            Pattern.compile("^org\\.agent-conformance\\.evidence\\.(?<name>[A-Za-z0-9]+)\\.v1$");

    public static final class Result {
        /** What assessment, activity, and graph building read: an undeclared source's event carries
         * {@code agentcesourceclass: "self_report"} here, never its own unproven self-assertion. */
        public final List<JsonNode> accepted = new ArrayList<>();
        /** The same events exactly as received. Pass this, never {@code accepted}, to integrity
         * verification (SPEC §6.6), which must hash what the source actually signed. */
        public final List<JsonNode> rawAccepted = new ArrayList<>();
        public final List<Quarantine.Record> quarantined = new ArrayList<>();
        public final List<Withheld> withheld = new ArrayList<>();
    }

    /**
     * A quarantined record that may hold evidence a control needs (18.119): quarantine never gives a better
     * outcome. {@code event} is the record as readers would see it (class corrected as for an accepted event)
     * when ingest could read it but refused it for where it sits (time_order, a duplicate_id that is not an
     * identical redelivery, class_mismatch, unknown_source); it is null when ingest could not read it at all
     * (schema_invalid, oversize). {@code subject} is the record's own subject, null when unknown (then the
     * record may belong to any subject). An unknown_type record is not withheld: the engine reads nothing from
     * a type it does not model.
     */
    public record Withheld(
            Quarantine.Record record, JsonNode event, String subject, String digest, UnreadableWrite write) {
        Withheld(Quarantine.Record record, JsonNode event, String subject, String digest) {
            this(record, event, subject, digest, null);
        }
    }

    /**
     * What ingest can still see of an unreadable record that may be a MemoryWrite (SPEC §7.4, 18.172): its raw
     * {@code data.record_ref} and {@code data.store} when each is a string, else null. {@link Withheld#write} is set
     * on an unreadable record whose raw type names MemoryWrite, or whose type cannot be read at all.
     */
    public record UnreadableWrite(String recordRef, String store) {}

    /** The record's canonical digest, null when it has no canonical form (a schema-invalid record may hold a
     * number the canonical form refuses). */
    /**
     * The record's canonical digest, null when it has no canonical form: a schema-invalid record may hold a number the
     * canonical form refuses, or a lone surrogate, which UTF-8 cannot carry (Python's {@code _digest} refuses it as well).
     */
    static String digestOf(JsonNode event) {
        try {
            String text = Canonical.canonicalString(event);
            return LONE_SURROGATE.matcher(text).find()
                    ? null
                    : Canonical.sha256Hex(text.getBytes(java.nio.charset.StandardCharsets.UTF_8));
        } catch (Canonical.CanonicalizationError e) {
            return null;
        }
    }

    /** Matches only an unpaired surrogate (a valid pair is one code point to the regex engine). */
    private static final Pattern LONE_SURROGATE = Pattern.compile("[\\x{D800}-\\x{DFFF}]");

    private static Withheld readable(Quarantine.Record record, JsonNode event) {
        return new Withheld(record, event, opt(event, "subject"), digestOf(event));
    }

    private static Withheld unreadable(Quarantine.Record record, JsonNode event) {
        return new Withheld(
                record,
                null,
                event != null ? opt(event, "subject") : null,
                event != null ? digestOf(event) : null,
                event != null ? unreadableWrite(event) : null);
    }

    /** The raw MemoryWrite facts of an unreadable {@code event}: a MemoryWrite unless its envelope or data names
     * another known type, so a record whose type cannot be read, or names a type the engine does not know, counts as
     * one (fail closed, 18.172). */
    private static UnreadableWrite unreadableWrite(JsonNode event) {
        JsonNode type = event.get("type");
        String name = type != null && type.isTextual() ? typeNameOf(type.asText()) : null;
        JsonNode data = event.get("data");
        JsonNode declared = data != null && data.isObject() ? data.get("@type") : null;
        boolean declaredText = declared != null && declared.isTextual();
        if (!"MemoryWrite".equals(name)
                && !(declaredText && "MemoryWrite".equals(declared.asText()))
                && ((name != null && Schema.eventTypes().contains(name))
                        || (declaredText && Schema.eventTypes().contains(declared.asText())))) {
            return null;
        }
        return new UnreadableWrite(textField(data, "record_ref"), textField(data, "store"));
    }

    private static String textField(JsonNode data, String key) {
        JsonNode value = data != null && data.isObject() ? data.get(key) : null;
        return value != null && value.isTextual() ? value.asText() : null;
    }

    private static String typeNameOf(String typeValue) {
        Matcher m = TYPE_RE.matcher(typeValue);
        return m.matches() ? m.group("name") : null;
    }

    private static String streamOf(JsonNode event) {
        JsonNode data = event.get("data");
        if (data != null && data.isObject()) {
            JsonNode integrity = data.get("integrity");
            if (integrity != null && integrity.isObject()) {
                JsonNode stream = integrity.get("stream");
                if (stream != null && stream.isTextual() && !stream.textValue().isEmpty()) {
                    return stream.textValue();
                }
            }
        }
        JsonNode source = event.get("source");
        return source != null ? source.asText() : "";
    }

    /**
     * Whether {@code time} is before {@code previous}, compared as instants (SPEC §6.7), so a UTC offset or a fraction
     * of a second never puts a later record before an earlier one (18.145). Two times that are not comparable instants
     * (not date-times, or one naive and one aware) keep the byte order ingest has always used.
     */
    private static boolean precedes(String time, String previous) {
        Integer order = Structural.compareInstants(time, previous);
        return order == null ? Json.byteCompare(time, previous) < 0 : order < 0;
    }

    private static String opt(JsonNode event, String key) {
        JsonNode v = event.get(key);
        return v != null && v.isTextual() ? v.textValue() : null;
    }

    public static Result ingest(Bundle bundle) {
        return ingest(bundle, DEFAULT_MAX_EVENT_BYTES);
    }

    public static Result ingest(Bundle bundle, int maxEventBytes) {
        Result result = new Result();
        Set<String> validTypes = Schema.eventTypes();
        // Pass 1 checks each record on its own; `records` keeps, in file order, each quarantine record and each
        // candidate. `ownerRank` is the highest trusted class among the candidates that carry each id: a record of a
        // lower class never displaces it (18.143).
        List<Object> records = new ArrayList<>();
        Map<String, Integer> ownerRank = new HashMap<>();

        for (Path path : bundle.eventFiles) {
            byte[] content;
            try {
                content = Files.readAllBytes(path);
            } catch (IOException e) {
                throw new IllegalStateException("cannot read " + path + ": " + e.getMessage(), e);
            }
            for (byte[] rawLine : splitLines(content)) {
                String raw;
                try {
                    raw = Verify.decodeStrict(rawLine).toString();
                } catch (CharacterCodingException e) {
                    records.add(unreadable(new Quarantine.Record(Quarantine.Reason.SCHEMA_INVALID)
                            .detail("invalid UTF-8"), null));
                    continue;
                }
                String line = trimJsonWhitespace(raw);
                if (line.isEmpty()) {
                    continue;
                }
                if (line.getBytes(StandardCharsets.UTF_8).length > maxEventBytes) {
                    records.add(unreadable(new Quarantine.Record(Quarantine.Reason.OVERSIZE)
                            .detail("event exceeds " + maxEventBytes + " bytes"), null));
                    continue;
                }
                // The bracket count bounds the nesting, so most lines skip the character scan.
                if (bracketCount(line) > Verify.MAX_JSON_DEPTH
                        && maxNesting(line) > Verify.MAX_JSON_DEPTH) {
                    throw new InputError(
                            "input.event_structure_too_deep",
                            "an evidence event line is nested too deeply to parse safely.",
                            "flatten the event's structure; reference deeply nested content by an "
                                    + "opaque locator instead (SPEC R12).");
                }
                JsonNode event;
                try {
                    event = Json.parse(line);
                } catch (RuntimeException exc) {
                    records.add(unreadable(new Quarantine.Record(Quarantine.Reason.SCHEMA_INVALID)
                            .detail("invalid JSON"), null));
                    continue;
                }
                if (event == null || !event.isObject()) {
                    records.add(unreadable(new Quarantine.Record(Quarantine.Reason.SCHEMA_INVALID)
                            .detail("event is not a JSON object"), null));
                    continue;
                }

                List<String> errors = Schema.validateEvent(event);
                if (!errors.isEmpty()) {
                    records.add(unreadable(new Quarantine.Record(Quarantine.Reason.SCHEMA_INVALID)
                            .eventId(opt(event, "id"))
                            .source(opt(event, "source"))
                            .type(opt(event, "type"))
                            .detail(errors.get(0)), event));
                    continue;
                }

                String typeValue = event.get("type").asText();
                String name = typeNameOf(typeValue);
                if (name == null || !validTypes.contains(name)) {
                    records.add(new Quarantine.Record(Quarantine.Reason.UNKNOWN_TYPE)
                            .eventId(event.get("id").asText())
                            .source(event.get("source").asText())
                            .type(typeValue)
                            .detail("unrecognised event type '" + typeValue + "'"));
                    continue;
                }

                String source = event.get("source").asText();
                if (bundle.sources != null && !bundle.sources.contains(source)) {
                    records.add(readable(new Quarantine.Record(Quarantine.Reason.UNKNOWN_SOURCE)
                            .eventId(event.get("id").asText())
                            .source(source)
                            .type(typeValue)
                            .detail("source is not declared in the bundle manifest"),
                            ((ObjectNode) event).deepCopy().put("agentcesourceclass", "self_report")));
                    continue;
                }

                JsonNode rawEvent = event;
                String declaredClass = bundle.sourceClasses != null ? bundle.sourceClasses.get(source) : null;
                if (declaredClass != null) {
                    String eventClass = event.get("agentcesourceclass").asText();
                    if (!eventClass.equals(declaredClass)) {
                        records.add(readable(new Quarantine.Record(Quarantine.Reason.CLASS_MISMATCH)
                                .eventId(event.get("id").asText())
                                .source(source)
                                .type(typeValue)
                                .detail("event class '" + eventClass + "' differs from the declared class '"
                                        + declaredClass + "' for this source"),
                                ((ObjectNode) event).deepCopy().put("agentcesourceclass", declaredClass)));
                        continue;
                    }
                } else if (!"self_report".equals(event.get("agentcesourceclass").asText())) {
                    // SPEC §6.4: the manifest declares no trust class for this source, so the event's
                    // own self-assertion is never trusted as-is. `rawEvent` (added below, unmodified)
                    // keeps the original for integrity hashing (SPEC §6.6); this copy is what every
                    // assessment, activity, and graph reader sees.
                    event = ((ObjectNode) event).deepCopy().put("agentcesourceclass", "self_report");
                }

                String eventId = event.get("id").asText();
                // The evidence ladder (SPEC §6.4): of two records with one id, the lower class is quarantined.
                int rank = SourceClass.RANK.get(event.get("agentcesourceclass").asText());
                ownerRank.merge(eventId, rank, Math::max);
                records.add(new Candidate(event, rawEvent, source, typeValue, rank));
            }
        }

        Set<String> seenIds = new HashSet<>();
        // Time order is checked per (source, stream): the stream a record claims names a stream of its own source, so
        // a record never joins another source's ordering group by naming that source's stream (18.147).
        Map<List<String>, String> lastTime = new HashMap<>();
        // Each duplicate_id record with the raw record it was read from: withheld unless it is an identical
        // redelivery of the record held under its id, which is known only once every record is placed.
        List<Map.Entry<Withheld, JsonNode>> duplicates = new ArrayList<>();
        for (Object record : records) {
            if (record instanceof Withheld withheld) {
                result.quarantined.add(withheld.record());
                result.withheld.add(withheld);
                continue;
            }
            if (!(record instanceof Candidate candidate)) {
                result.quarantined.add((Quarantine.Record) record);
                continue;
            }
            JsonNode event = candidate.event();
            String eventId = event.get("id").asText();
            if (seenIds.contains(eventId) || candidate.rank() < ownerRank.get(eventId)) {
                Withheld duplicate = readable(new Quarantine.Record(Quarantine.Reason.DUPLICATE_ID)
                        .eventId(eventId)
                        .source(candidate.source())
                        .type(candidate.typeValue())
                        .detail(seenIds.contains(eventId)
                                ? "event id already seen in this bundle"
                                : "a record of a higher source class has this event id"), event);
                result.quarantined.add(duplicate.record());
                duplicates.add(Map.entry(duplicate, candidate.rawEvent()));
                continue;
            }

            String stream = streamOf(event);
            List<String> orderKey = List.of(candidate.source(), stream);
            String timeValue = event.get("time").asText();
            String previous = lastTime.get(orderKey);
            if (previous != null && precedes(timeValue, previous)) {
                Withheld late = readable(new Quarantine.Record(Quarantine.Reason.TIME_ORDER)
                        .eventId(eventId)
                        .source(candidate.source())
                        .stream(stream)
                        .type(candidate.typeValue())
                        .detail("time " + timeValue + " precedes " + previous + " in the stream"), event);
                result.quarantined.add(late.record());
                result.withheld.add(late);
                continue;
            }

            seenIds.add(eventId);
            lastTime.put(orderKey, timeValue);
            result.accepted.add(event);
            result.rawAccepted.add(candidate.rawEvent());
        }
        // Only ids that were delivered twice are compared, so only their held records are hashed.
        Set<String> ids = new HashSet<>();
        for (Map.Entry<Withheld, JsonNode> entry : duplicates) {
            ids.add(entry.getValue().get("id").asText());
        }
        Map<String, String> held = new HashMap<>();
        for (JsonNode e : result.rawAccepted) {
            if (ids.contains(e.get("id").asText())) {
                held.put(e.get("id").asText(), digestOf(e));
            }
        }
        for (Map.Entry<Withheld, JsonNode> entry : duplicates) {
            String digest = digestOf(entry.getValue());
            if (digest == null || !digest.equals(held.get(entry.getValue().get("id").asText()))) {
                result.withheld.add(entry.getKey());
            }
        }
        return result;
    }

    /**
     * One line rule the three engines share (18.65, Python's {@code bytes.splitlines}): a line ends at
     * \n, \r\n or a lone \r. Split on bytes, before decoding, so each line is decoded (and refused) on
     * its own; \r and \n never occur inside a multi-byte UTF-8 sequence.
     */
    static List<byte[]> splitLines(byte[] raw) {
        List<byte[]> lines = new ArrayList<>();
        int start = 0;
        for (int i = 0; i < raw.length; i++) {
            if (raw[i] == '\n' || raw[i] == '\r') {
                lines.add(Arrays.copyOfRange(raw, start, i));
                if (raw[i] == '\r' && i + 1 < raw.length && raw[i + 1] == '\n') {
                    i++;
                }
                start = i + 1;
            }
        }
        if (start < raw.length) {
            lines.add(Arrays.copyOfRange(raw, start, raw.length));
        }
        return lines;
    }

    /**
     * Mirrors {@code ingest._max_nesting}: the deepest {@code [}/{@code {} nesting in {@code line},
     * counted lexically (brackets inside strings ignored) before any parse, so the limit is the same
     * number in all three engines.
     */
    /** Every {@code [} and {@code {} in {@code line}: an upper bound on its nesting. */
    private static long bracketCount(String line) {
        return line.chars().filter(ch -> ch == '[' || ch == '{').count();
    }

    static int maxNesting(String line) {
        int depth = 0;
        int deepest = 0;
        boolean inString = false;
        boolean escaped = false;
        for (int i = 0; i < line.length(); i++) {
            char ch = line.charAt(i);
            if (inString) {
                if (escaped) {
                    escaped = false;
                } else if (ch == '\\') {
                    escaped = true;
                } else if (ch == '"') {
                    inString = false;
                }
            } else if (ch == '"') {
                inString = true;
            } else if (ch == '[' || ch == '{') {
                depth++;
                deepest = Math.max(deepest, depth);
            } else if (ch == ']' || ch == '}') {
                depth--;
            }
        }
        return deepest;
    }

    /** Trims only the whitespace JSON itself allows around a value (RFC 8259 §2). */
    private static String trimJsonWhitespace(String text) {
        int start = 0;
        int end = text.length();
        while (start < end && isJsonWhitespace(text.charAt(start))) {
            start++;
        }
        while (end > start && isJsonWhitespace(text.charAt(end - 1))) {
            end--;
        }
        return text.substring(start, end);
    }

    private static boolean isJsonWhitespace(char ch) {
        return ch == ' ' || ch == '\t' || ch == '\r' || ch == '\n';
    }
}
