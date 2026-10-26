package org.agentce;

import static org.junit.jupiter.api.Assertions.assertEquals;

import com.fasterxml.jackson.databind.JsonNode;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import org.junit.jupiter.api.Test;

/** renderRemediationMd renders every package of the shared case table to the Python reference's bytes
 * (tools/fixtures/report_formats/remediation_md_expected.json, written by report_format_parity_check.py
 * --write-expected; 18.16e). */
class RemediationTest {
    private static JsonNode read(String name) throws Exception {
        Path path = TestPaths.repoRoot().resolve("tools/fixtures/report_formats").resolve(name);
        return Json.readBundled(Files.readAllBytes(path));
    }

    @Test
    void rendersTheCaseTableAsPython() throws Exception {
        JsonNode cases = read("remediation_md_cases.json").get("cases");
        JsonNode expected = read("remediation_md_expected.json");
        List<String> names = new ArrayList<>();
        List<String> expectedNames = new ArrayList<>();
        cases.forEach(c -> names.add(c.get("name").asText()));
        expected.fieldNames().forEachRemaining(expectedNames::add);
        names.sort(null);
        expectedNames.sort(null);
        assertEquals(expectedNames, names);
        for (JsonNode c : cases) {
            String name = c.get("name").asText();
            assertEquals(expected.get(name).asText(), Remediation.renderRemediationMd(c.get("package")), name);
        }
    }
}
