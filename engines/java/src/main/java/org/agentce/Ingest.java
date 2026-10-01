package org.agentce;

import com.fasterxml.jackson.databind.JsonNode;
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
 */
public final class Ingest {
    private Ingest() {}

    public static final int DEFAULT_MAX_EVENT_BYTES = 1_048_576;
    private static final Pattern TYPE_RE =
            Pattern.compile("^org\\.agent-conformance\\.evidence\\.(?<name>[A-Za-z0-9]+)\\.v1$");

    public static final class Result {
        public final List<JsonNode> accepted = new ArrayList<>();
        public final List<Quarantine.Record> quarantined = new ArrayList<>();
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
        Set<String> seenIds = new HashSet<>();
        Map<String, String> lastTime = new HashMap<>();

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
                    result.quarantined.add(new Quarantine.Record(Quarantine.Reason.SCHEMA_INVALID)
                            .detail("invalid UTF-8"));
                    continue;
                }
                String line = trimJsonWhitespace(raw);
                if (line.isEmpty()) {
                    continue;
                }
                if (line.getBytes(StandardCharsets.UTF_8).length > maxEventBytes) {
                    result.quarantined.add(new Quarantine.Record(Quarantine.Reason.OVERSIZE)
                            .detail("event exceeds " + maxEventBytes + " bytes"));
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
                    result.quarantined.add(new Quarantine.Record(Quarantine.Reason.SCHEMA_INVALID)
                            .detail("invalid JSON"));
                    continue;
                }
                if (event == null || !event.isObject()) {
                    result.quarantined.add(new Quarantine.Record(Quarantine.Reason.SCHEMA_INVALID)
                            .detail("event is not a JSON object"));
                    continue;
                }

                List<String> errors = Schema.validateEvent(event);
                if (!errors.isEmpty()) {
                    result.quarantined.add(new Quarantine.Record(Quarantine.Reason.SCHEMA_INVALID)
                            .eventId(opt(event, "id"))
                            .source(opt(event, "source"))
                            .type(opt(event, "type"))
                            .detail(errors.get(0)));
                    continue;
                }

                String typeValue = event.get("type").asText();
                String name = typeNameOf(typeValue);
                if (name == null || !validTypes.contains(name)) {
                    result.quarantined.add(new Quarantine.Record(Quarantine.Reason.UNKNOWN_TYPE)
                            .eventId(event.get("id").asText())
                            .source(event.get("source").asText())
                            .type(typeValue)
                            .detail("unrecognised event type '" + typeValue + "'"));
                    continue;
                }

                String source = event.get("source").asText();
                if (bundle.sources != null && !bundle.sources.contains(source)) {
                    result.quarantined.add(new Quarantine.Record(Quarantine.Reason.UNKNOWN_SOURCE)
                            .eventId(event.get("id").asText())
                            .source(source)
                            .type(typeValue)
                            .detail("source is not declared in the bundle manifest"));
                    continue;
                }

                String declaredClass = bundle.sourceClasses != null ? bundle.sourceClasses.get(source) : null;
                if (declaredClass != null) {
                    String eventClass = event.get("agentcesourceclass").asText();
                    if (!eventClass.equals(declaredClass)) {
                        result.quarantined.add(new Quarantine.Record(Quarantine.Reason.CLASS_MISMATCH)
                                .eventId(event.get("id").asText())
                                .source(source)
                                .type(typeValue)
                                .detail("event class '" + eventClass + "' differs from the declared class '"
                                        + declaredClass + "' for this source"));
                        continue;
                    }
                }

                String eventId = event.get("id").asText();
                if (seenIds.contains(eventId)) {
                    result.quarantined.add(new Quarantine.Record(Quarantine.Reason.DUPLICATE_ID)
                            .eventId(eventId)
                            .source(source)
                            .type(typeValue)
                            .detail("event id already seen in this bundle"));
                    continue;
                }

                String stream = streamOf(event);
                String timeValue = event.get("time").asText();
                String previous = lastTime.get(stream);
                if (previous != null && Json.byteCompare(timeValue, previous) < 0) {
                    result.quarantined.add(new Quarantine.Record(Quarantine.Reason.TIME_ORDER)
                            .eventId(eventId)
                            .source(source)
                            .stream(stream)
                            .type(typeValue)
                            .detail("time " + timeValue + " precedes " + previous + " in the stream"));
                    continue;
                }

                seenIds.add(eventId);
                lastTime.put(stream, timeValue);
                result.accepted.add(event);
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
