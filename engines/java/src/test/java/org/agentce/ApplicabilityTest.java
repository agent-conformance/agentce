package org.agentce;

import static org.junit.jupiter.api.Assertions.assertEquals;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ArrayNode;
import java.util.ArrayList;
import java.util.List;
import org.junit.jupiter.api.Test;

/**
 * Applicability resolution is byte-identical to the reference (SPEC §6.5, §7.3). The fixture and golden
 * were emitted by the reference engine's {@code resolve} over two subjects, controls in every role
 * class, and events that drift from the declared scope.
 */
class ApplicabilityTest {

    @Test
    void applicabilityStatementsMatchGolden() {
        JsonNode fixture = Json.parseFile(TestPaths.testData().resolve("applicability-fixture.json"));
        JsonNode golden = Json.parseFile(TestPaths.testData().resolve("applicability-golden.json"));
        Profile profile = Profile.fromDict(fixture.get("profile"));
        List<Applicability.ControlMeta> controls = new ArrayList<>();
        for (JsonNode c : fixture.get("controls")) {
            List<String> roles = new ArrayList<>();
            c.get("appliesToRoles").forEach(r -> roles.add(r.asText()));
            controls.add(new Applicability.ControlMeta(
                    c.get("id").asText(), roles, c.has("family") ? c.get("family").asText() : ""));
        }
        ArrayNode statements = Applicability.resolve(profile, Fixtures.toList(fixture.get("events")), controls);
        assertEquals(Canonical.canonicalString(golden), Canonical.canonicalString(statements));
    }

    @Test
    void effectiveRolesExpandsPerIr11() {
        assertEquals(List.of("deployer", "provider"), Applicability.effectiveRoles("both"));
        assertEquals(List.of("provider"), Applicability.effectiveRoles("provider"));
        assertEquals(List.of("deployer"), Applicability.effectiveRoles("deployer"));
        assertEquals(List.of("deployer"), Applicability.effectiveRoles(null));
        assertEquals(List.of("deployer"), Applicability.effectiveRoles("unknown"));
    }
}
