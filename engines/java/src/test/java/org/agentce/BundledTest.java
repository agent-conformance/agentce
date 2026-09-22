package org.agentce;

import static org.junit.jupiter.api.Assertions.assertTrue;

import java.nio.file.Files;
import java.nio.file.Path;
import org.junit.jupiter.api.Test;

/** {@link Bundled} resolves the engine's own catalogs and quickstart project through its public API
 * (the same classpath resolution {@code Cli} uses); {@code BundledDataTest} holds their bytes. */
class BundledTest {
    @Test
    void catalogsDirResolvesTheVendoredBaseAndOverlayCatalogs() {
        Path dir = Bundled.catalogsDir();
        assertTrue(Files.isDirectory(dir.resolve("base").resolve("eu-ai-act")));
        assertTrue(Files.isRegularFile(dir.resolve("base").resolve("eu-ai-act").resolve("catalog.yaml")));
        assertTrue(Files.isDirectory(dir.resolve("overlays")));
    }

    @Test
    void quickstartDirResolvesTheVendoredQuickstartProject() {
        Path dir = Bundled.quickstartDir();
        assertTrue(Files.isDirectory(dir.resolve("evidence")));
        assertTrue(Files.isRegularFile(dir.resolve("applicability.yaml")));
        assertTrue(Files.isRegularFile(dir.resolve("domain.linkml.yaml")));
    }

    @Test
    void repeatedCallsResolveTheSameCachedRoot() {
        assertTrue(Bundled.catalogsDir().equals(Bundled.catalogsDir()));
        assertTrue(Bundled.quickstartDir().getParent().getParent().equals(Bundled.catalogsDir().getParent()));
    }
}
