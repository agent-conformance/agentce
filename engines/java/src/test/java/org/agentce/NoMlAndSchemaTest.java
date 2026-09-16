package org.agentce;

import static org.junit.jupiter.api.Assertions.assertArrayEquals;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.io.IOException;
import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import org.junit.jupiter.api.Test;

/**
 * The no-learned-components scan passes on the engine's own lockfile, and the vendored evidence schema
 * stays byte-identical to the generated artifact under {@code spec/} (the vendoring sync guard).
 */
class NoMlAndSchemaTest {

    @Test
    void noMlScanPassesOnTheEngineLockfile() {
        Path repoRoot = TestPaths.repoRoot();
        Path lock = repoRoot.resolve("engines/java/gradle.lockfile");
        NoMl.Result result = NoMl.evaluate(repoRoot, lock);
        assertEquals("pass", result.result, "engine dependencies must carry no learned component");
        assertTrue(result.violations.isEmpty());
        assertTrue(result.packages > 0);
    }

    @Test
    void vendoredSchemaIsByteIdenticalToSpec() throws IOException {
        byte[] spec = Files.readAllBytes(
                TestPaths.repoRoot().resolve("spec/model/generated/json-schema/agentce-evidence.schema.json"));
        byte[] vendored;
        try (InputStream in = NoMlAndSchemaTest.class.getResourceAsStream("/agentce-evidence.schema.json")) {
            assertTrue(in != null, "the vendored schema must be on the classpath");
            vendored = in.readAllBytes();
        }
        assertArrayEquals(spec, vendored, "the vendored evidence schema must match spec/ byte for byte");
    }
}
