package org.agentce;

import static org.junit.jupiter.api.Assertions.assertArrayEquals;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.io.IOException;
import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Set;
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

    // --- The installed-artifact no_ml self-report (item 18.22): `agentce version --json` in a
    // packaged jar has no repo checkout to read `gradle.lockfile` from, so it intersects the vendored
    // denylist against the runtime classpath's real, resolved dependency coordinates instead (baked
    // into the jar at build time by the `noMlRuntimeDeps` Gradle task). ---

    @Test
    void evaluateInstalledFailsWhenASyntheticDenylistNamesARealBundledRuntimeDependency() {
        // jackson-databind is a real, always-bundled runtime dependency (build.gradle.kts).
        NoMl.InstalledResult result = NoMl.evaluateInstalled(Set.of("jackson-databind"), NoMl.loadRuntimeDeps());
        assertEquals("fail", result.result);
        assertEquals(List.of("jackson-databind"), result.denylistedPresent);
    }

    @Test
    void evaluateInstalledPassesForTheRealVendoredDenylistAgainstTheRealRuntimeDependencies() {
        NoMl.InstalledResult result = NoMl.evaluateInstalled(NoMl.loadVendoredDenylist(), NoMl.loadRuntimeDeps());
        assertEquals("pass", result.result, "the engine's runtime dependencies must carry no learned component");
        assertTrue(result.denylistedPresent.isEmpty());
    }

    @Test
    void vendoredDenylistIsByteIdenticalToSpec() throws IOException {
        byte[] spec = Files.readAllBytes(TestPaths.repoRoot().resolve("spec/rules/no-ml-denylist.txt"));
        byte[] vendored;
        try (InputStream in = NoMlAndSchemaTest.class.getResourceAsStream("/no-ml-denylist.txt")) {
            assertTrue(in != null, "the vendored denylist must be on the classpath");
            vendored = in.readAllBytes();
        }
        assertArrayEquals(spec, vendored, "the vendored no_ml denylist must match spec/ byte for byte");
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
