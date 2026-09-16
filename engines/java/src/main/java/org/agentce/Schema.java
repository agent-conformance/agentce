package org.agentce;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.networknt.schema.JsonSchema;
import com.networknt.schema.JsonSchemaFactory;
import com.networknt.schema.SpecVersion;
import com.networknt.schema.ValidationMessage;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;

/**
 * Access to the evidence JSON Schema generated from the LinkML model (SPEC §6, item 0.3).
 *
 * <p>The engine vendors a copy of {@code agentce-evidence.schema.json}. Events are validated in two
 * steps, mirroring the model: the CloudEvents envelope against {@code EvidenceEvent} with {@code data}
 * emptied (its range is the generic {@code Payload}), and the JSON-LD payload against the specific
 * {@code <@type>Payload} definition. The {@code EventType} enum is read from the same schema. The draft
 * is 2019-09.
 */
public final class Schema {
    private Schema() {}

    private static final JsonSchemaFactory FACTORY =
            JsonSchemaFactory.getInstance(SpecVersion.VersionFlag.V201909);
    private static final Map<String, JsonSchema> VALIDATORS = new ConcurrentHashMap<>();
    private static volatile JsonNode schema;

    private static JsonNode schema() {
        JsonNode local = schema;
        if (local == null) {
            try (InputStream in = Schema.class.getResourceAsStream("/agentce-evidence.schema.json")) {
                if (in == null) {
                    throw new IllegalStateException("vendored evidence schema not on classpath");
                }
                local = Json.parse(new String(in.readAllBytes(), StandardCharsets.UTF_8));
                schema = local;
            } catch (IOException e) {
                throw new IllegalStateException("cannot read vendored evidence schema", e);
            }
        }
        return local;
    }

    private static JsonNode defs() {
        return schema().get("$defs");
    }

    /** The set of valid {@code EventType} names (SPEC §6.2.3). */
    public static Set<String> eventTypes() {
        Set<String> out = new LinkedHashSet<>();
        for (JsonNode name : defs().get("EventType").get("enum")) {
            out.add(name.asText());
        }
        return out;
    }

    private static JsonSchema validatorForDef(String ref) {
        return VALIDATORS.computeIfAbsent(ref, r -> {
            ObjectNode sub = Json.nodes().objectNode();
            sub.set("$defs", defs());
            sub.put("$ref", "#/$defs/" + r);
            return FACTORY.getSchema(sub);
        });
    }

    private static List<String> messagesOf(Set<ValidationMessage> errors) {
        List<String> out = new ArrayList<>();
        for (ValidationMessage m : errors) {
            out.add(m.getMessage());
        }
        return out;
    }

    /** Schema-validation error messages for {@code event} (empty when it is valid). */
    public static List<String> validateEvent(JsonNode event) {
        if (event == null || !event.isObject()) {
            return List.of("event is not a JSON object");
        }
        List<String> errors = new ArrayList<>();
        ObjectNode envelope = ((ObjectNode) event).deepCopy();
        envelope.set("data", Json.nodes().objectNode());
        errors.addAll(messagesOf(validatorForDef("EvidenceEvent").validate(envelope)));

        JsonNode data = event.get("data");
        if (data == null || !data.isObject()) {
            errors.add("data is missing or not a JSON object");
            return errors;
        }
        JsonNode payloadType = data.get("@type");
        if (payloadType == null || !payloadType.isTextual()) {
            errors.add("data.@type is missing or not a string");
            return errors;
        }
        String definition = payloadType.textValue() + "Payload";
        if (!defs().has(definition)) {
            errors.add("unknown payload type '" + payloadType.textValue() + "'");
            return errors;
        }
        ObjectNode payload = Json.nodes().objectNode();
        var it = data.fields();
        while (it.hasNext()) {
            Map.Entry<String, JsonNode> e = it.next();
            if (!e.getKey().equals("@context") && !e.getKey().equals("@type")) {
                payload.set(e.getKey(), e.getValue());
            }
        }
        errors.addAll(messagesOf(validatorForDef(definition).validate(payload)));
        return errors;
    }
}
