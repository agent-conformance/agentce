package org.agentce;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.junit.jupiter.api.Assertions.fail;

import com.fasterxml.jackson.databind.JsonNode;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.HexFormat;
import java.util.List;
import java.util.Set;
import java.util.TreeSet;
import org.junit.jupiter.api.DynamicTest;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.TestFactory;

/** The JSON profile (SPEC 8.7) against the shared case table {@code spec/report/json-cases.json}, the rows
 * Python's {@code test_json_profile.py} and TypeScript's {@code jsonProfile.test.ts} read too. A row whose rule
 * this engine does not run fails the last test. */
class JsonProfileTest {

    private static final List<JsonNode> CASES = new ArrayList<>();
    private static final Set<String> RUN = new TreeSet<>();

    static {
        Json.parseFile(TestPaths.repoRoot().resolve("spec/report/json-cases.json")).get("cases").forEach(CASES::add);
    }

    private static String text(JsonNode c, String field) {
        return c.get(field).asText();
    }

    private static byte[] raw(JsonNode c) {
        return c.has("hex")
                ? HexFormat.of().parseHex(text(c, "hex"))
                : (c.has("text") ? text(c, "text") : "").getBytes(StandardCharsets.UTF_8);
    }

    private static JsonNode value(String text) {
        return Json.readJson(text.getBytes(StandardCharsets.UTF_8), Json.Bom.REFUSE);
    }

    private static Json.Bom bom(JsonNode c) {
        return c.has("bom") && text(c, "bom").equals("ignore") ? Json.Bom.IGNORE : Json.Bom.REFUSE;
    }

    private static List<Object> refusal(Json.JsonProfileError exc) {
        return List.of(exc.rule(), exc.line());
    }

    private static List<Object> expected(JsonNode c) {
        return List.of(text(c, "rule"), c.get("line").asInt());
    }

    private static void reader(JsonNode c) {
        boolean accept = text(c, "expect").equals("accept");
        if (text(c, "mode").equals("json")) {
            if (accept) {
                assertEquals(text(c, "canonical"), Json.renderValue(Json.readJson(raw(c), bom(c))));
            } else {
                assertEquals(expected(c),
                        refusal(assertThrows(Json.JsonProfileError.class, () -> Json.readJson(raw(c), bom(c)))));
            }
            return;
        }
        String decoded;
        try {
            decoded = Json.decodeProfile(raw(c), bom(c));
        } catch (Json.JsonProfileError exc) {
            assertTrue(!accept, "refused " + exc.getMessage());
            assertEquals(expected(c), refusal(exc));
            return;
        }
        List<Integer> records = new ArrayList<>();
        Json.JsonProfileError bad = null;
        for (Json.ByteLine line : Json.jsonlByteRecords(decoded.getBytes(StandardCharsets.UTF_8))) {
            records.add(line.number());
            try {
                Json.readJsonlLine(new String(line.bytes(), StandardCharsets.UTF_8), line.number());
            } catch (Json.JsonProfileError exc) {
                if (bad == null) {
                    bad = exc;
                }
            }
        }
        if (accept) {
            assertEquals(null, bad);
            List<Integer> want = new ArrayList<>();
            c.get("records").forEach(n -> want.add(n.asInt()));
            assertEquals(want, records);
        } else {
            if (bad == null) {
                fail("accepted");
            }
            assertEquals(expected(c), refusal(bad));
        }
    }

    @TestFactory
    List<DynamicTest> cases() {
        List<DynamicTest> out = new ArrayList<>();
        for (JsonNode c : CASES) {
            String rule = text(c, "rule");
            String name = rule + " " + text(c, "id");
            if (c.has("mode")) {
                RUN.add(rule);
                out.add(DynamicTest.dynamicTest("reader " + name, () -> reader(c)));
            } else if (rule.equals("number_rendering")) {
                RUN.add(rule);
                out.add(DynamicTest.dynamicTest(name, () ->
                        assertEquals(text(c, "expect"), Json.renderNumber(value(text(c, "input"))))));
            } else if (rule.equals("compare_by_value") || rule.equals("compare_across_types")) {
                RUN.add(rule);
                out.add(DynamicTest.dynamicTest(name, () -> {
                    JsonNode left = value(text(c, "left"));
                    JsonNode right = value(text(c, "right"));
                    assertEquals(c.get("equal").asBoolean(), Json.jsonEqual(left, right));
                    assertEquals(c.get("equal").asBoolean(), Json.jsonEqual(right, left));
                }));
            } else if (rule.equals("integer_field_float_form")) {
                RUN.add(rule);
                out.add(DynamicTest.dynamicTest(name, () -> assertEquals(
                        text(c, "expect").equals("accept"), Json.isJsonInteger(value(text(c, "text"))))));
            } else if (rule.equals("non_integer_number")) {
                RUN.add(rule);
                out.add(DynamicTest.dynamicTest(name, () -> {
                    Canonical.CanonicalizationError exc = assertThrows(Canonical.CanonicalizationError.class,
                            () -> Canonical.canonicalString(value(text(c, "text"))));
                    assertTrue(exc.getMessage().contains("non_integer_number")
                            && exc.getMessage().contains(text(c, "rendered")), exc.getMessage());
                }));
            }
        }
        return out;
    }

    @Test
    void everyRuleInTheCaseTableIsRun() {
        cases();
        Set<String> missing = new TreeSet<>();
        CASES.forEach(c -> missing.add(text(c, "rule")));
        missing.removeAll(RUN);
        assertEquals(Set.of(), missing);
    }
}
