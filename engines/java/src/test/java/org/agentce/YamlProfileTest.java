package org.agentce;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertThrows;

import com.fasterxml.jackson.databind.JsonNode;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeSet;
import java.util.function.Consumer;
import java.util.stream.Stream;
import org.junit.jupiter.api.DynamicTest;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.TestFactory;
import org.junit.jupiter.api.io.TempDir;

/** Every case of the shared YAML profile table ({@code spec/rules/yaml-profile-cases.json}, SPEC 8.7; 18.184u) through
 * the real loaders: a refuse case is refused under each file role's key with a message naming the rule and line, an
 * accept case reads. The Java side of Python's {@code tests/test_yaml_profile.py}. */
class YamlProfileTest {

    private static final JsonNode TABLE =
            Json.parseFile(TestPaths.repoRoot().resolve("spec/rules/yaml-profile-cases.json"));

    private record Role(String key, String what, Consumer<Path> load) {}

    private static final List<Role> ROLES = List.of(
            new Role("input.profile_invalid", "the applicability profile", Profile::load),
            new Role("input.domain_binding_invalid", "the domain binding", DomainBinding::load),
            new Role("input.deviation_invalid", "the deviation register",
                    p -> Readiness.loadPyyaml(p, "input.deviation_invalid", "the deviation register")));

    @TempDir
    Path tmp;

    /** The case's YAML, built from its {@code generate} description when it has one (the table's
     * {@code generators}). */
    static String caseText(JsonNode c) {
        if (c.has("yaml")) {
            return c.get("yaml").asText();
        }
        return generate(c.get("generate").get("kind").asText(), c.get("generate").get("depth").asInt());
    }

    private static String generate(String kind, int depth) {
        StringBuilder out = new StringBuilder();
        switch (kind) {
            case "block_map" -> {
                for (int i = 0; i < depth - 1; i++) {
                    out.append("  ".repeat(i)).append("k:\n");
                }
                out.append("  ".repeat(depth - 1)).append("k: v\n");
            }
            case "block_seq" -> {
                for (int i = 0; i < depth - 1; i++) {
                    out.append("  ".repeat(i)).append("-\n");
                }
                out.append("  ".repeat(depth - 1)).append("- v\n");
            }
            case "flow_seq" -> out.append("[".repeat(depth)).append("]".repeat(depth)).append("\n");
            case "flow_map" -> out.append("{a: ".repeat(depth - 1)).append("{}").append("}".repeat(depth - 1)).append("\n");
            case "mixed" -> {
                int half = depth / 2;
                int rest = depth - half;
                String map = generate("block_map", half);
                out.append(map, 0, map.length() - 2).append("[".repeat(rest)).append("]".repeat(rest)).append("\n");
            }
            case "indentless" -> {
                String map = generate("block_map", depth - 1);
                out.append(map, 0, map.length() - 6).append("k:\n").append("  ".repeat(depth - 2)).append("- v\n");
            }
            default -> throw new IllegalArgumentException(kind);
        }
        return out.toString();
    }

    @Test
    void hintsMatchTheTable() {
        Map<String, String> table = new LinkedHashMap<>();
        TABLE.get("hints").fields().forEachRemaining(e -> table.put(e.getKey(), e.getValue().asText()));
        assertEquals(table, YamlProfile.HINTS);
    }

    @Test
    void everyRuleKeepsARefuseCase() {
        Set<String> refused = new TreeSet<>();
        for (JsonNode c : TABLE.get("cases")) {
            if (c.get("expect").asText().equals("refuse")) {
                refused.add(c.get("rule").asText());
            }
        }
        assertEquals(new TreeSet<>(YamlProfile.HINTS.keySet()), refused);
    }

    @TestFactory
    Stream<DynamicTest> everyCase() {
        List<DynamicTest> tests = new ArrayList<>();
        for (JsonNode c : TABLE.get("cases")) {
            tests.add(DynamicTest.dynamicTest(c.get("id").asText(), () -> runCase(c)));
        }
        return tests.stream();
    }

    private void runCase(JsonNode c) throws IOException {
        String text = caseText(c);
        Path dir = Files.createTempDirectory(tmp, "case");
        Path path = dir.resolve("input.yaml");
        Files.write(path, text.getBytes(StandardCharsets.UTF_8));
        String id = c.get("id").asText();
        if (c.get("expect").asText().equals("accept")) {
            assertNotNull(Readiness.composeCatalogPyyaml(text), id);
            for (Role role : ROLES) {
                role.load().accept(path);
            }
            return;
        }
        String rule = c.get("rule").asText();
        String tail = "is outside the YAML profile: " + rule + " at line " + c.get("line").asInt() + " (SPEC 8.7)";
        YamlProfile.ProfileException caught =
                assertThrows(YamlProfile.ProfileException.class, () -> Readiness.composeCatalogPyyaml(text), id);
        assertEquals(tail, caught.violation().message(), id);
        for (Role role : ROLES) {
            InputError refused = assertThrows(InputError.class, () -> role.load().accept(path), id);
            assertEquals(role.key(), refused.key, id);
            assertEquals(role.what() + " at " + Readiness.pyRepr(path.toString()) + " " + tail + ".", refused.reason, id);
            assertEquals(YamlProfile.HINTS.get(rule) + ".", refused.fix, id);
        }
    }

    @Test
    void aCatalogFileOutsideTheProfileIsRefusedNamingTheFile() throws IOException {
        Path dir = Files.createDirectories(tmp.resolve("cat"));
        Files.writeString(dir.resolve("catalog.yaml"), "id: a\nversion: '1'\nid: b\n");
        InputError e = assertThrows(InputError.class, () -> Catalog.load(dir));
        assertEquals("input.catalog_unreadable", e.key);
        assertEquals("the catalog directory " + dir + " holds a file that cannot be loaded: catalog.yaml "
                + "is outside the YAML profile: duplicate_key at line 3 (SPEC 8.7).", e.reason);
        assertEquals("write each key once.", e.fix);
    }

    @Test
    void aTenThousandDeepFileIsRefusedWithoutOverflow() {
        String text = "[".repeat(10_000) + "]".repeat(10_000) + "\n";
        assertEquals(new YamlProfile.Violation("depth_over_100", 1), YamlProfile.violation(text));
        StringBuilder block = new StringBuilder();
        for (int i = 0; i < 300; i++) {
            block.append("  ".repeat(i)).append("k:\n");
        }
        assertEquals("depth_over_100", YamlProfile.violation(block.toString()).rule());
    }
}
