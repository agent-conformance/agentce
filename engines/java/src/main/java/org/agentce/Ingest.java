package org.agentce;

import com.fasterxml.jackson.databind.JsonNode;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
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
            List<String> lines;
            try {
                lines = Files.readAllLines(path, StandardCharsets.UTF_8);
            } catch (IOException e) {
                throw new IllegalStateException("cannot read " + path + ": " + e.getMessage(), e);
            }
            for (String raw : lines) {
                String line = raw.strip();
                if (line.isEmpty()) {
                    continue;
                }
                if (line.getBytes(StandardCharsets.UTF_8).length > maxEventBytes) {
                    result.quarantined.add(new Quarantine.Record(Quarantine.Reason.OVERSIZE)
                            .detail("event exceeds " + maxEventBytes + " bytes"));
                    continue;
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
}
