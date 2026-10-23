package org.agentce;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.JsonNodeFactory;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.fasterxml.jackson.databind.node.POJONode;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.TreeMap;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/**
 * The domain binding's YAML node forms read as PyYAML's SafeLoader reads them (SPEC §7.4; 18.184k). {@code
 * yaml-node-forms-golden.json} is written by {@code generate_goldens.py} from the Python reference: each case's YAML
 * (tags x scalar styles x values, key kinds, merges, nesting inside task_scopes and budgets) and Python's reading of
 * it through {@code load_untrusted_yaml}, by type, or the key it is refused under. {@link
 * Readiness#loadPyyaml} must give the same reading for every case.
 */
class YamlNodeFormsTest {

    private static final JsonNodeFactory F = JsonNodeFactory.instance;

    /** Numbers equal by value (the golden writes 10 as 10.0), everything else as Jackson compares it. */
    private static final Comparator<JsonNode> BY_VALUE = (a, b) -> a.isNumber() && b.isNumber()
            ? Double.compare(a.doubleValue(), b.doubleValue())
            : a.equals(b) ? 0 : 1;

    /** A reading encoded as the golden encodes Python's: numbers by value, keys in code point order. */
    private static JsonNode reading(JsonNode value) {
        ObjectNode out = F.objectNode();
        if (value.isNull()) {
            return F.nullNode();
        } else if (value.isBoolean()) {
            out.put("b", value.booleanValue());
        } else if (value.isNumber()) {
            double d = value.doubleValue();
            if (Double.isNaN(d)) {
                out.put("n", "nan");
            } else if (Double.isInfinite(d)) {
                out.put("n", d > 0 ? "inf" : "-inf");
            } else {
                out.put("n", d);
            }
        } else if (value.isTextual()) {
            out.put("s", value.textValue());
        } else if (value.isPojo()) {
            out.put("ts", String.valueOf(((POJONode) value).getPojo()));
        } else if (value.isArray()) {
            ArrayNode seq = out.putArray("seq");
            value.forEach(item -> seq.add(reading(item)));
        } else {
            TreeMap<String, JsonNode> sorted = new TreeMap<>(Json::byteCompare);
            value.fields().forEachRemaining(e -> sorted.put(e.getKey(), e.getValue()));
            ArrayNode map = out.putArray("map");
            sorted.forEach((k, v) -> map.addArray().add(k).add(reading(v)));
        }
        return out;
    }

    @Test
    void everyNodeFormReadsAsPythonReadsIt(@TempDir Path tmp) throws IOException {
        JsonNode cases = Json.parseFile(TestPaths.testData().resolve("yaml-node-forms-golden.json"));
        Path path = tmp.resolve("domain.yaml");
        List<String> differ = new ArrayList<>();
        for (JsonNode c : cases) {
            Files.writeString(path, c.get("yaml").textValue(), StandardCharsets.UTF_8);
            JsonNode got;
            try {
                got = reading(Readiness.toJsonNode(
                        Readiness.loadPyyaml(path, "input.domain_binding_invalid", "the domain binding")));
            } catch (InputError e) {
                got = F.objectNode().put("refused", e.key);
            } catch (RuntimeException e) {
                got = F.objectNode().put("crash", String.valueOf(e));
            }
            if (!got.equals(BY_VALUE, c.get("reads"))) {
                differ.add(c.get("name").textValue() + ": " + Json.nodes().textNode(c.get("yaml").textValue())
                        + " Python " + c.get("reads") + ", Java " + got);
            }
        }
        assertTrue(cases.size() > 1000, "the table holds " + cases.size() + " cases");
        assertEquals(List.of(), differ, differ.size() + " of " + cases.size() + " node forms differ");
    }
}
