package org.agentce;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.fasterxml.jackson.databind.JsonNode;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.stream.Stream;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/** A path through a symlink is read where the kernel reads it: {@code lnk/..} is the parent of the
 * link's target, never the folder the link sits in (SPEC §8.7; item 18.182). Each case puts a decoy
 * at the lexical location. Mirrors Python's tests/test_kernel_paths.py. */
class KernelPathsTest {
    private static final Path QUICKSTART = TestPaths.repoRoot().resolve("corpus/quickstart");

    /** {@code w/lnk -> w/far/deep}, so the kernel reads {@code w/lnk/..} as {@code w/far}. */
    private static Path withLink(Path tmp) throws IOException {
        Path w = tmp.toRealPath();
        Files.createDirectories(w.resolve("far/deep"));
        Files.createSymbolicLink(w.resolve("lnk"), w.resolve("far/deep"));
        return w;
    }

    private static boolean holdsReadiness(Path dir) throws IOException {
        try (Stream<Path> files = Files.list(dir)) {
            return files.anyMatch(p -> p.getFileName().toString().startsWith("report-readiness-"));
        }
    }

    @Test
    void pyResolveReadsDotDotAfterTheLink(@TempDir Path tmp) throws IOException {
        Path w = withLink(tmp);
        assertEquals(w.resolve("far/x"), Conformance.pyResolve(Path.of(w + "/lnk/../x")));
    }

    @Test
    void readinessWritesIntoTheKernelFolder(@TempDir Path tmp) throws IOException {
        Path w = withLink(tmp);
        Files.createDirectory(w.resolve("target")); // the decoy: must stay empty
        String target = w + "/lnk/../target";
        JsonNode assess = Fixtures.runJson(
                "assess", "--bundle", QUICKSTART.resolve("evidence").toString(),
                "--profile", QUICKSTART.resolve("applicability.yaml").toString(),
                "--domain", QUICKSTART.resolve("domain.linkml.yaml").toString(),
                "--out", target);
        assertEquals(0, assess.get("exit_code").asInt(), assess.toString());
        JsonNode readiness = Fixtures.runJson("readiness", target);
        assertTrue(readiness.get("exit_code").asInt() <= 1, readiness.toString());
        assertTrue(holdsReadiness(w.resolve("far/target")));
        assertFalse(holdsReadiness(w.resolve("target")));
    }

    @Test
    void aSymlinkedEngineFindsTheRepositorysCatalogs(@TempDir Path tmp) throws IOException {
        Path w = withLink(tmp);
        Path engine = Files.createSymbolicLink(w.resolve("engine"), TestPaths.repoRoot().resolve("engines/java"));
        Path corpus = Files.createDirectory(w.resolve("corpus"));
        Files.writeString(corpus.resolve("corpus-manifest.json"), "{\"projects\": []}");
        Path corpusLink = Files.createSymbolicLink(w.resolve("corpus-link"), corpus);
        JsonNode report = Conformance.runEcs(engine, corpusLink, null);
        assertEquals(0, report.get("failures").size(), report.toString());
    }
}
