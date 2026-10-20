package org.agentce;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.stream.Stream;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;

/** 18.184i: every catalog YAML file is one mapping read as Python's unique-key loader reads it; anything else is
 * refused as input.catalog_unreadable naming the file (Python's tests/test_catalog_yaml_files.py, the same cases). */
class CatalogYamlFileTest {

    private static final String CONTROL = "controls/DAT-01.yaml";
    private static final String SHAPE = "shapes/DAT-01.ttl";

    private static Path copy(Path tmp) throws IOException {
        Path dir = tmp.resolve("cat");
        Fixtures.copyTree(Fixtures.BASE, dir);
        Files.deleteIfExists(dir.resolve("catalog.sig.json"));
        return dir;
    }

    private static void edit(Path dir, String rel, String old, String replacement) throws IOException {
        String text = Files.readString(dir.resolve(rel));
        assertTrue(text.contains(old), old);
        Files.writeString(dir.resolve(rel), text.replaceFirst(java.util.regex.Pattern.quote(old), replacement));
    }

    private static void append(Path dir, String rel, byte[] tail) throws IOException {
        byte[] head = Files.readAllBytes(dir.resolve(rel));
        byte[] all = new byte[head.length + tail.length];
        System.arraycopy(head, 0, all, 0, head.length);
        System.arraycopy(tail, 0, all, head.length, tail.length);
        Files.write(dir.resolve(rel), all);
    }

    @ParameterizedTest
    @ValueSource(
            strings = {
                "dupcontrol", "dupmeta", "emptymeta", "listmeta", "emptycontrol", "listcontrol", "nonutf8control",
                "nonutf8shape", "syntax", "deep", "pythontag", "multidoc"
            })
    void aCatalogFileThatCannotBeLoadedIsRefusedNamingIt(String kind, @TempDir Path tmp) throws IOException {
        Path dir = copy(tmp);
        String rel = CONTROL;
        String problem = "is not YAML the engine can load";
        switch (kind) {
            case "dupcontrol" -> edit(
                    dir,
                    CONTROL,
                    "  min_source_class: self_report\n",
                    "  min_source_class: independent_system\n  min_source_class: self_report\n");
            case "dupmeta" -> {
                rel = "catalog.yaml";
                edit(dir, rel, "version: \"2026.09\"\n", "version: \"2026.09\"\nversion: \"9\"\n");
            }
            case "emptymeta" -> {
                rel = "catalog.yaml";
                problem = "does not hold one YAML mapping";
                Files.write(dir.resolve(rel), new byte[0]);
            }
            case "listmeta" -> {
                rel = "catalog.yaml";
                problem = "does not hold one YAML mapping";
                Files.writeString(dir.resolve(rel), "- id: eu-ai-act\n");
            }
            case "emptycontrol" -> {
                problem = "does not hold one YAML mapping";
                Files.write(dir.resolve(rel), new byte[0]);
            }
            case "listcontrol" -> {
                problem = "does not hold one YAML mapping";
                Files.writeString(dir.resolve(rel), "- id: DAT-01\n");
            }
            case "nonutf8control" -> {
                problem = "is not valid UTF-8";
                append(dir, rel, new byte[] {'#', ' ', (byte) 0xff, '\n'});
            }
            case "nonutf8shape" -> {
                rel = SHAPE;
                problem = "is not valid UTF-8";
                append(dir, rel, new byte[] {'#', ' ', (byte) 0xff, '\n'});
            }
            case "syntax" -> Files.writeString(dir.resolve(rel), "id: [DAT-01\n");
            case "deep" -> {
                problem = "is nested too deeply to read safely";
                Files.writeString(dir.resolve(rel), "x: " + "[".repeat(5000) + "]".repeat(5000) + "\n");
            }
            case "pythontag" -> Files.writeString(dir.resolve(rel), "id: !!python/object:object {}\n");
            default -> append(dir, rel, "---\nid: DAT-99\n".getBytes(StandardCharsets.UTF_8));
        }
        InputError e = assertThrows(InputError.class, () -> Catalog.load(dir));
        assertEquals("input.catalog_unreadable", e.key);
        assertTrue(e.reason.contains("holds a file that cannot be loaded: " + rel + " " + problem), e.reason);
    }

    /** The blast-radius fact for Java (18.184i): every catalog the repository ships, and every bundled copy, still
     * loads through the new check. */
    @Test
    void everyShippedCatalogStillLoads() throws IOException {
        Path root = TestPaths.repoRoot();
        List<Path> dirs = new ArrayList<>();
        for (Path base : List.of(root.resolve("spec/catalogs"), root.resolve("engines/java/src/main/resources/catalogs"))) {
            try (Stream<Path> walk = Files.walk(base)) {
                walk.filter(p -> p.getFileName().toString().equals("catalog.yaml"))
                        .forEach(p -> dirs.add(p.getParent()));
            }
        }
        assertFalse(dirs.isEmpty());
        for (Path dir : dirs) {
            Catalog catalog = Catalog.load(dir);
            assertFalse(catalog.version.isEmpty(), dir.toString());
        }
    }
}
