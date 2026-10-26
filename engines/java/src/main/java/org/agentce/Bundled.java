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
import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
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

    /** The cross-standard baseline a run evaluates when nothing names a catalog (Python's
     * {@code bundled.DEFAULT_LENS}). */
    static final String DEFAULT_LENS = "baseline@2026.09";

    private static volatile Path cachedRoot;
    private static final Map<String, String> TEMPLATES = new java.util.concurrent.ConcurrentHashMap<>();

    /** A vendored report template's text, read once (Python's {@code bundled.*_template()}; each original is
     * {@code spec/report/templates/<name>}, vendored under {@code resources/templates/}). */
    static String template(String name) {
        return TEMPLATES.computeIfAbsent(name, n -> {
            String resource = "/templates/" + n;
            try (java.io.InputStream in = Bundled.class.getResourceAsStream(resource)) {
                if (in == null) {
                    throw new IllegalStateException("the engine's bundled " + resource + " is missing");
                }
                return new String(in.readAllBytes(), java.nio.charset.StandardCharsets.UTF_8);
            } catch (IOException e) {
                throw new java.io.UncheckedIOException(e);
            }
        });
    }

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

    /** Every catalog the engine ships (base and sector overlays), keyed {@code id@version}. */
    static Map<String, Path> vendoredCatalogs() {
        Map<String, Path> found = new LinkedHashMap<>();
        Path root = catalogsDir();
        for (String kind : new String[] {"base", "overlays"}) {
            Path dir = root.resolve(kind);
            List<String> names = new ArrayList<>();
            if (Files.isDirectory(dir)) {
                try (Stream<Path> stream = Files.list(dir)) {
                    stream.map(p -> p.getFileName().toString()).forEach(names::add);
                } catch (IOException ignored) {
                    // no vendored catalogs of this kind
                }
            }
            names.sort(Json::byteCompare);
            for (String name : names) {
                Path catalogYaml = dir.resolve(name).resolve("catalog.yaml");
                if (!Files.isRegularFile(catalogYaml)) {
                    continue;
                }
                try {
                    com.fasterxml.jackson.databind.JsonNode meta = Yaml.parseFile(catalogYaml);
                    if (meta != null && meta.isObject() && meta.has("id") && meta.has("version")
                            && meta.get("id").isTextual() && meta.get("version").isTextual()) {
                        found.put(meta.get("id").asText() + "@" + meta.get("version").asText(), dir.resolve(name));
                    }
                } catch (RuntimeException ignored) {
                    // a malformed vendored catalog is skipped, never fatal to discovery
                }
            }
        }
        return found;
    }

    /** The {@code id@version} of every vendored base catalog, in byte order: the lenses a run can
     * choose (Python's {@code bundled.base_lenses}). */
    static List<String> baseLenses() {
        List<String> lenses = cachedBaseLenses;
        if (lenses == null) {
            Path base = catalogsDir().resolve("base");
            List<String> found = new ArrayList<>();
            vendoredCatalogs().forEach((label, directory) -> {
                if (base.equals(directory.getParent())) {
                    found.add(label);
                }
            });
            found.sort(Json::byteCompare);
            lenses = List.copyOf(found);
            cachedBaseLenses = lenses;
        }
        return lenses;
    }

    /** The vendored catalogs are fixed for the process, so the lens list is read once. */
    private static volatile List<String> cachedBaseLenses;

    /** The vendored quickstart project: an evidence bundle, an applicability profile, and a domain. */
    public static Path quickstartDir() {
        return dataRoot().resolve("corpus").resolve("quickstart");
    }
}
