package org.agentce;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.fasterxml.jackson.databind.JsonNode;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.stream.Stream;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.MethodSource;

/** The Java canonical form reproduces the shared vectors byte for byte (SPEC §6.7). */
class CanonicalTest {

    private static Path vectorsDir() {
        return TestPaths.repoRoot().resolve("spec/model/test-vectors");
    }

    static Stream<Path> vectors() throws IOException {
        List<Path> files = new ArrayList<>();
        try (var stream = Files.list(vectorsDir())) {
            stream.filter(p -> p.getFileName().toString().endsWith(".json")).sorted().forEach(files::add);
        }
        return files.stream();
    }

    @Test
    void theSharedVectorSetIsPresent() throws IOException {
        assertTrue(vectors().count() >= 50, "expected the full shared vector set");
    }

    @ParameterizedTest(name = "canonical vector {0}")
    @MethodSource("vectors")
    void canonicalVector(Path file) {
        JsonNode vector = Json.parseFile(file);
        JsonNode input = vector.get("input");
        if (vector.has("error")) {
            String want = vector.get("error").textValue();
            var err = assertThrows(
                    Canonical.CanonicalizationError.class,
                    () -> Canonical.canonicalString(input),
                    file.getFileName().toString());
            assertEquals(want, err.reason, file.getFileName().toString());
        } else {
            assertEquals(
                    vector.get("canonical").textValue(),
                    Canonical.canonicalString(input),
                    file.getFileName() + " canonical");
            assertEquals(
                    vector.get("sha256").textValue(),
                    Canonical.sha256Hex(input),
                    file.getFileName() + " sha256");
        }
    }
}
