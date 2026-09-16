package org.agentce;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.util.List;
import org.junit.jupiter.api.Test;

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
}
