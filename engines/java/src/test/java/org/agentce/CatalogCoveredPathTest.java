package org.agentce;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.Set;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/** 18.168: the catalog loaders read only files the catalog digest covers, by exact name. */
class CatalogCoveredPathTest {

    private static void copyTree(Path source, Path target) throws IOException {
        try (java.util.stream.Stream<Path> walk = Files.walk(source)) {
            for (Path path : (Iterable<Path>) walk::iterator) {
                Path dest = target.resolve(source.relativize(path).toString());
                if (Files.isDirectory(path)) {
                    Files.createDirectories(dest);
                } else {
                    Files.createDirectories(dest.getParent());
                    Files.copy(path, dest);
                }
            }
        }
    }

    /** A copy of the base catalog whose {@code control} names {@code value} as {@code evaluation.<field>},
     * with a copy of the original file at {@code place} (relative to the catalog, or absolute) when given. */
    private static Path withControlValue(Path tmp, String control, String field, String value, String place)
            throws IOException {
        Path dir = tmp.resolve("cat");
        copyTree(Fixtures.BASE, dir);
        Path doc = dir.resolve("controls").resolve(control + ".yaml");
        String text = Files.readString(doc);
        String original = text.lines()
                .filter(l -> l.strip().startsWith(field + ":"))
                .findFirst()
                .orElseThrow()
                .split(":", 2)[1]
                .strip();
        Files.writeString(doc, text.replace(field + ": " + original, field + ": " + value));
        if (place != null) {
            Path target = place.startsWith("/") ? Path.of(place) : dir.resolve(place);
            Files.createDirectories(target.getParent());
            Files.copy(dir.resolve(original), target);
        }
        return dir;
    }

    @Test
    void coveredFilesIsWhatTheDigestHashes(@TempDir Path tmp) throws IOException {
        Path root = tmp.resolve("tree");
        Files.createDirectories(root.resolve("a/.hidden"));
        Files.createDirectories(root.resolve("a/__pycache__"));
        Files.createDirectories(tmp.resolve("real"));
        Files.writeString(root.resolve("a/x.ttl"), "x");
        Files.writeString(root.resolve("a/.x.ttl"), "x");
        Files.writeString(root.resolve("a/.hidden/y.ttl"), "y");
        Files.writeString(root.resolve("a/__pycache__/z.ttl"), "z");
        Files.writeString(tmp.resolve("real/w.ttl"), "w");
        Files.createSymbolicLink(root.resolve("linked"), tmp.resolve("real"));
        Files.createSymbolicLink(root.resolve("a/v.ttl"), tmp.resolve("real/w.ttl"));
        Files.writeString(root.resolve("catalog.sig.json"), "{}");
        Set<String> exclude = Set.of("catalog.sig.json");
        List<String> covered = Catalog.coveredFiles(root, exclude);
        assertEquals(List.of("a/v.ttl", "a/x.ttl", "linked/w.ttl"), covered);
        List<String> lines = new ArrayList<>();
        for (String rel : covered) {
            lines.add(rel + "\0" + Canonical.sha256Hex(Files.readAllBytes(root.resolve(rel))));
        }
        assertEquals(
                "sha256:" + Canonical.sha256Hex(String.join("\n", lines).getBytes(StandardCharsets.UTF_8)),
                Catalog.digestTree(root, exclude));
    }

    @Test
    void aDotNamedOrAppleDoubleControlFileIsNeverRead(@TempDir Path tmp) throws IOException {
        List<String> before = Catalog.load(Fixtures.BASE).controls.stream().map(c -> c.id).toList();
        for (String name : List.of(".DOC-99.yaml", "._DOC-01.yaml")) {
            Path dir = tmp.resolve(name);
            copyTree(Fixtures.BASE, dir);
            // Not a control at all: a loader that read it would fail, not merely add a control.
            Files.writeString(dir.resolve("controls").resolve(name), "{not: [a control\n");
            assertEquals(before, Catalog.load(dir).controls.stream().map(c -> c.id).toList(), name);
        }
    }

    @Test
    void aShapeOrEvidenceShapeOutsideTheDigestIsRefusedNamingControlAndPath(@TempDir Path tmp) throws IOException {
        String[][] cases = {
            {"shapes/.X.ttl", "shapes/.X.ttl"},
            {"shapes/._X.ttl", "shapes/._X.ttl"},
            {".hidden/X.ttl", ".hidden/X.ttl"},
            {"shapes/__pycache__/X.ttl", "shapes/__pycache__/X.ttl"},
            {"shapes/__PYCACHE__/X.ttl", "shapes/__pycache__/X.ttl"},
            {"../outside.ttl", "../outside.ttl"},
            {"shapes/../shapes/X.ttl", "shapes/X.ttl"},
            {"./shapes/X.ttl", "shapes/X.ttl"},
            {"shapes//X.ttl", "shapes/X.ttl"},
            {"ABS", "ABS"},
            {"shapes/MISSING.ttl", null},
            {"\"\"", null},
            {"5", null},
        };
        String[][] fields = {
            {"shape", "DOC-01", "catalog.shape.unresolved"},
            {"evidence_shape", "ROB-02", "catalog.evidence_shape.unresolved"},
        };
        int n = 0;
        for (String[] f : fields) {
            for (String[] c : cases) {
                Path caseDir = tmp.resolve("c" + n++);
                Path abs = caseDir.resolve("abs/X.ttl");
                String value = c[0].equals("ABS") ? abs.toString() : c[0];
                String place = "ABS".equals(c[1]) ? abs.toString() : c[1];
                Path dir = withControlValue(caseDir, f[1], f[0], value, place);
                InputError e = assertThrows(InputError.class, () -> Catalog.load(dir), f[0] + " " + value);
                assertEquals(f[2], e.key, f[0] + " " + value);
                if (!value.equals("\"\"") && !value.equals("5")) {
                    assertTrue(e.reason.contains(f[1]) && e.reason.contains(value), e.reason);
                }
            }
        }
    }

    @Test
    void aSymlinkedShapeInsideTheCatalogLoadsAndANullShapeIsNoShape(@TempDir Path tmp) throws IOException {
        Path dir = withControlValue(tmp.resolve("a"), "DOC-01", "shape", "shapes/linked.ttl", null);
        Files.createSymbolicLink(dir.resolve("shapes/linked.ttl"), dir.resolve("shapes/DOC-01.ttl"));
        Catalog catalog = Catalog.load(dir);
        Catalog.ControlSpec doc = catalog.controls.stream().filter(c -> c.id.equals("DOC-01")).findFirst().orElseThrow();
        assertNotNull(Catalog.shapeFor(catalog, doc));
        Path nulled = withControlValue(tmp.resolve("b"), "DOC-01", "shape", "null", null);
        assertNull(Catalog.load(nulled).controls.stream()
                .filter(c -> c.id.equals("DOC-01")).findFirst().orElseThrow().shapePath);
    }
}
