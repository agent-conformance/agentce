package org.agentce;

import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;

/** Locate repository resources from a test regardless of the working directory. */
final class TestPaths {
    private TestPaths() {}

    /** The repository root: the nearest ancestor holding {@code spec/model/test-vectors}. */
    static Path repoRoot() {
        Path dir = Paths.get(System.getProperty("user.dir")).toAbsolutePath();
        while (dir != null) {
            if (Files.isDirectory(dir.resolve("spec/model/test-vectors"))) {
                return dir;
            }
            dir = dir.getParent();
        }
        throw new IllegalStateException("could not locate the repository root from " + System.getProperty("user.dir"));
    }

    static Path testData() {
        return Paths.get(System.getProperty("user.dir")).toAbsolutePath().resolve("src/test/resources/testdata");
    }
}
