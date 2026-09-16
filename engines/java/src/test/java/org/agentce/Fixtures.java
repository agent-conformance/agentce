package org.agentce;

import com.fasterxml.jackson.databind.JsonNode;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;

/** Shared fixture helpers for the golden tests. */
final class Fixtures {
    private Fixtures() {}

    static final Path BASE = TestPaths.repoRoot().resolve("spec/catalogs/base/eu-ai-act");

    /** Read a JSON-lines file into a list of nodes (blank lines skipped). */
    static List<JsonNode> readJsonl(Path path) throws IOException {
        List<JsonNode> out = new ArrayList<>();
        for (String line : Files.readAllLines(path, StandardCharsets.UTF_8)) {
            if (!line.strip().isEmpty()) {
                out.add(Json.parse(line));
            }
        }
        return out;
    }

    static List<JsonNode> toList(JsonNode array) {
        List<JsonNode> out = new ArrayList<>();
        if (array != null) {
            array.forEach(out::add);
        }
        return out;
    }
}
