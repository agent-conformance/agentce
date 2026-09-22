package org.agentce;

import java.io.IOException;
import java.net.URI;
import java.net.URISyntaxException;
import java.net.URL;
import java.nio.file.FileSystem;
import java.nio.file.FileSystems;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.util.Comparator;
import java.util.Map;
import java.util.stream.Stream;

/**
 * Where the engine finds the data it ships (SPEC §13.4 AX-1).
 *
 * <p>The base and overlay catalogs and the quickstart project are vendored under {@code
 * src/main/resources/} and resolved through the classpath, so an installed distribution resolves them
 * from wherever it is installed and a source checkout resolves the same bytes. From an exploded
 * classpath (tests, {@code gradlew run}) the resource URL is a plain {@code file:} path; from a packaged
 * jar it is extracted once to a temporary directory, mirroring the Python reference's {@code
 * importlib.resources.as_file}. {@code BundledDataTest} holds the vendored tree byte-identical to its
 * {@code spec/}/{@code corpus/} original.
 */
public final class Bundled {
    private Bundled() {}

    private static volatile Path cachedRoot;

    private static Path dataRoot() {
        if (cachedRoot != null) {
            return cachedRoot;
        }
        synchronized (Bundled.class) {
            if (cachedRoot != null) {
                return cachedRoot;
            }
            URL url = Bundled.class.getResource("/catalogs");
            if (url == null) {
                throw new IllegalStateException("the engine's bundled data is missing from the classpath");
            }
            try {
                cachedRoot = "jar".equals(url.getProtocol())
                        ? extractFromJar(url.toURI())
                        : Path.of(url.toURI()).getParent();
            } catch (URISyntaxException e) {
                throw new IllegalStateException("cannot resolve the engine's bundled data: " + e.getMessage(), e);
            }
            return cachedRoot;
        }
    }

    private static Path extractFromJar(URI resourceUri) {
        try {
            Path tmp = Files.createTempDirectory("agentce-data-");
            Runtime.getRuntime().addShutdownHook(new Thread(() -> deleteRecursively(tmp)));
            try (FileSystem jarFs = FileSystems.newFileSystem(resourceUri, Map.of())) {
                for (String top : new String[] {"catalogs", "corpus"}) {
                    Path source = jarFs.getPath("/" + top);
                    if (Files.isDirectory(source)) {
                        copyTree(source, tmp.resolve(top));
                    }
                }
            }
            return tmp;
        } catch (IOException e) {
            throw new IllegalStateException("cannot extract the engine's bundled data from its jar: " + e.getMessage(), e);
        }
    }

    private static void copyTree(Path source, Path target) throws IOException {
        try (Stream<Path> walk = Files.walk(source)) {
            for (Path path : (Iterable<Path>) walk::iterator) {
                Path dest = target.resolve(source.relativize(path).toString());
                if (Files.isDirectory(path)) {
                    Files.createDirectories(dest);
                } else {
                    Files.createDirectories(dest.getParent());
                    Files.copy(path, dest, StandardCopyOption.REPLACE_EXISTING);
                }
            }
        }
    }

    private static void deleteRecursively(Path root) {
        try (Stream<Path> walk = Files.walk(root)) {
            walk.sorted(Comparator.reverseOrder()).forEach(p -> {
                try {
                    Files.deleteIfExists(p);
                } catch (IOException ignored) {
                    // best effort at shutdown
                }
            });
        } catch (IOException ignored) {
            // best effort at shutdown
        }
    }

    /** The vendored catalogs, laid out as {@code base/<catalog>/} and {@code overlays/<catalog>/}. */
    public static Path catalogsDir() {
        return dataRoot().resolve("catalogs");
    }

    /** The vendored quickstart project: an evidence bundle, an applicability profile, and a domain. */
    public static Path quickstartDir() {
        return dataRoot().resolve("corpus").resolve("quickstart");
    }
}
