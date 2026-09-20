package org.agentce;

import static org.junit.jupiter.api.Assertions.assertArrayEquals;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;

import java.io.IOException;
import java.net.URISyntaxException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;
import java.util.stream.Stream;
import org.junit.jupiter.api.Test;

/**
 * The catalogs and the quickstart project the engine ships on its classpath stay byte-identical to
 * their {@code spec/} and {@code corpus/} originals and to the Python engine's copy.
 */
class BundledDataTest {
    private static Map<String, byte[]> files(Path root, boolean skipRootReadme) throws IOException {
        Map<String, byte[]> found = new TreeMap<>();
        try (Stream<Path> walk = Files.walk(root)) {
            for (Path path : (Iterable<Path>) walk.filter(Files::isRegularFile)::iterator) {
                String name = root.relativize(path).toString().replace('\\', '/');
                if (name.equals(".DS_Store") || name.endsWith("/.DS_Store") || (skipRootReadme && name.equals("README.md"))) {
                    continue;
                }
                found.put(name, Files.readAllBytes(path));
            }
        }
        return found;
    }

    private static Path onClasspath(String resource) throws URISyntaxException {
        var url = BundledDataTest.class.getResource(resource);
        assertNotNull(url, resource + " must be on the classpath");
        return Path.of(url.toURI());
    }

    private static void assertSameTree(Path vendored, Path original, boolean skipRootReadme) throws IOException {
        Map<String, byte[]> want = files(original, skipRootReadme);
        Map<String, byte[]> got = files(vendored, false);
        assertEquals(want.keySet(), got.keySet(), vendored + " and " + original + " list different files");
        for (var entry : want.entrySet()) {
            assertArrayEquals(entry.getValue(), got.get(entry.getKey()), "drifted from the original: " + entry.getKey());
        }
    }

    @Test
    void bundledCatalogsAndQuickstartAreByteIdenticalToTheirOriginals() throws IOException, URISyntaxException {
        Path repo = TestPaths.repoRoot();
        assertSameTree(onClasspath("/catalogs/base"), repo.resolve("spec/catalogs/base"), false);
        assertSameTree(onClasspath("/catalogs/overlays"), repo.resolve("spec/catalogs/overlays"), true);
        assertSameTree(onClasspath("/corpus/quickstart"), repo.resolve("corpus/quickstart"), false);
    }

    @Test
    void bundledDataIsByteIdenticalToThePythonEnginesCopy() throws IOException, URISyntaxException {
        Path python = TestPaths.repoRoot().resolve("engines/python/agentce/data");
        for (List<String> pair : List.of(List.of("/catalogs", "catalogs"), List.of("/corpus", "corpus"))) {
            assertSameTree(onClasspath(pair.get(0)), python.resolve(pair.get(1)), false);
        }
    }
}
