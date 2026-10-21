package org.agentce;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.util.List;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;

/** The vendored evidence schema loads and validates two-step (envelope + payload) via networknt. */
class SchemaTest {

    @Test
    void eventTypesEnumLoads() {
        assertEquals(24, Schema.eventTypes().size());
        assertTrue(Schema.eventTypes().contains("Decision"));
    }

    @Test
    void anEmptyObjectFailsEnvelopeValidation() {
        List<String> errors = Schema.validateEvent(Json.parse("{}"));
        assertFalse(errors.isEmpty(), "an empty object is missing required envelope fields");
    }

    @Test
    void unknownPayloadTypeIsReported() {
        // A well-formed envelope but an unknown @type should be flagged after the envelope passes.
        List<String> errors = Schema.validateEvent(Json.parse(
                "{\"id\":\"e1\",\"source\":\"s\",\"type\":\"org.agent-conformance.evidence.Nope.v1\","
                        + "\"time\":\"2026-01-01T00:00:00Z\",\"subject\":\"x\",\"specversion\":\"1.0\","
                        + "\"agentcesourceclass\":\"self_report\",\"data\":{\"@type\":\"Nope\"}}"));
        assertTrue(errors.stream().anyMatch(e -> e.contains("Nope")), "unknown payload type should be named");
    }

    // --- 18.184j: a usage count that is not a whole-number token is invalid, as TypeScript reads it, so a record
    // holding one is quarantined schema_invalid in every engine.

    private static final String MODEL_CALL = "{\"agentcesourceclass\": \"self_report\", \"data\": {\"@context\": "
            + "\"https://agent-conformance.org/contexts/evidence/v1\", \"@type\": \"ModelCall\", "
            + "\"agent\": {\"id\": \"spiffe://corp/agents/a\"}, \"model\": {\"name\": \"m\", \"provider\": \"p\", "
            + "\"version_or_digest\": \"1\"}, \"operation\": \"chat\", "
            + "\"usage\": {\"input_tokens\": USAGE, \"output_tokens\": 10}}, "
            + "\"datacontenttype\": \"application/ld+json\", \"id\": \"mc1\", \"source\": \"urn:src:agent\", "
            + "\"specversion\": \"1.0\", \"subject\": \"spiffe://corp/agents/a\", \"time\": \"2026-01-02T00:00:01Z\", "
            + "\"type\": \"org.agent-conformance.evidence.ModelCall.v1\"}";

    @ParameterizedTest
    @ValueSource(
            strings = {"5000.0", "1.0", "5e3", "5E+3", "5000.5", "-0.0", "1e400", "9007199254740992", "-9007199254740992"})
    void usageCountThatIsNotAWholeNumberTokenIsInvalid(String usage) {
        assertFalse(Schema.validateEvent(Json.parse(MODEL_CALL.replace("USAGE", usage))).isEmpty());
    }

    @ParameterizedTest
    @ValueSource(strings = {"5000", "0", "-0", "null", "9007199254740991"})
    void usageCountWrittenAsAWholeNumberIsValid(String usage) {
        assertEquals(List.of(), Schema.validateEvent(Json.parse(MODEL_CALL.replace("USAGE", usage))));
    }

    @ParameterizedTest
    @ValueSource(strings = {"1.5", "5e3", "9007199254740992"})
    void stringFieldHoldingANonCanonicalNumberIsInvalid(String value) {
        // The number reads as an object, as TypeScript reads it, never as text that a string field would accept.
        String event = MODEL_CALL.replace("USAGE", "1").replace("\"operation\": \"chat\"", "\"operation\": " + value);
        assertFalse(Schema.validateEvent(Json.parse(event)).isEmpty());
    }
}
