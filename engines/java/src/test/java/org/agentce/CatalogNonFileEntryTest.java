package org.agentce;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.fasterxml.jackson.databind.node.ObjectNode;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import org.junit.jupiter.api.Assumptions;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;

/** 18.181: a catalog entry that is not a file, a folder named like a control, and a covered file that
 * links outside the catalog (Python's tests/test_catalog_signature.py, the same cases). */
class CatalogNonFileEntryTest {

    private static Path copy(Path source, Path tmp) throws IOException {
        Path dir = tmp.resolve("c");
        Fixtures.copyTree(source, dir);
        Files.deleteIfExists(dir.resolve("catalog.sig.json"));
        return dir;
    }

    private static void mkfifo(Path path) throws IOException, InterruptedException {
        assertEquals(0, new ProcessBuilder("mkfifo", path.toString()).start().waitFor());
    }

    @ParameterizedTest
    @ValueSource(strings = {"fifo", "dangling", "loop", "folder"})
    void aControlThatIsNotAFileIsRefusedNamingIt(String kind, @TempDir Path tmp) throws Exception {
        Path dir = copy(Fixtures.BASE, tmp);
        Path control = dir.resolve("controls/DOC-01.yaml");
        Files.delete(control);
        switch (kind) {
            case "fifo" -> mkfifo(control);
            case "dangling" -> Files.createSymbolicLink(control, tmp.resolve("nowhere"));
            case "loop" -> Files.createSymbolicLink(control, control);
            default -> Files.createDirectory(control);
        }
        InputError e = assertThrows(InputError.class, () -> Catalog.load(dir));
        assertEquals("input.catalog_unreadable", e.key);
        assertTrue(e.reason.endsWith("cannot be read: controls/DOC-01.yaml."), e.reason);
    }

    @Test
    void aNonFileEntryThatIsNotCoveredIsLeftAlone(@TempDir Path tmp) throws Exception {
        Path dir = copy(Fixtures.BASE, tmp);
        Files.createDirectories(dir.resolve(".git"));
        Files.createDirectories(dir.resolve("__pycache__"));
        mkfifo(dir.resolve(".git/x"));
        mkfifo(dir.resolve("__pycache__/y"));
        mkfifo(dir.resolve("catalog.sig.json"));
        List<String> covered = Catalog.coveredFiles(dir, Set.of(Verify.CATALOG_SIGNATURE_NAME));
        assertTrue(covered.contains("controls/DOC-01.yaml"));
    }

    @Test
    void aCatalogYamlThatIsAFifoIsRefusedUnopened(@TempDir Path tmp) throws Exception {
        Path dir = copy(Fixtures.BASE, tmp);
        Files.delete(dir.resolve("catalog.yaml"));
        mkfifo(dir.resolve("catalog.yaml"));
        InputError e = assertThrows(InputError.class, () -> Catalog.load(dir));
        assertEquals("input.catalog_unreadable", e.key);
        assertTrue(e.reason.endsWith("cannot be read: catalog.yaml."), e.reason);
    }

    @Test
    void linksOutOfTheCatalogAreListedAndALinkInsideIsNot(@TempDir Path tmp) throws IOException {
        Path dir = copy(Fixtures.BASE, tmp);
        Files.move(dir.resolve("controls/DOC-01.yaml"), tmp.resolve("moved.yaml"));
        Files.createSymbolicLink(dir.resolve("controls/DOC-01.yaml"), tmp.resolve("moved.yaml"));
        Files.move(dir.resolve("shapes/DOC-01.ttl"), dir.resolve("test/DOC-01.ttl.txt"));
        Files.createSymbolicLink(dir.resolve("shapes/DOC-01.ttl"), Path.of("../test/DOC-01.ttl.txt"));
        Catalog.CatalogFiles files = Catalog.readFiles(dir, java.util.Map.of());
        assertEquals(Set.of("controls/DOC-01.yaml"), files.linksOut());
        Files.writeString(dir.resolve("catalog.sig.json"), "{}");
        ObjectNode result = Verify.verifyCatalog(dir, Verify.vendoredTrust());
        assertEquals(false, result.get("verified").asBoolean());
        assertEquals("catalog.link_outside", result.get("reason_keys").get(0).asText());
    }

    @Test
    void anEvidenceShapeLinkedOutsideIsRefusedByTheLoad(@TempDir Path tmp) throws IOException {
        Path dir = copy(Fixtures.CONDUCT, tmp);
        Files.move(dir.resolve("shapes/CND-02-evidence.ttl"), tmp.resolve("moved.ttl"));
        Files.createSymbolicLink(dir.resolve("shapes/CND-02-evidence.ttl"), tmp.resolve("moved.ttl"));
        InputError e = assertThrows(InputError.class, () -> Catalog.load(dir));
        assertEquals("catalog.evidence_shape.unresolved", e.key);
    }

    @Test
    void anAbsoluteInsideLinkReachedThroughAnotherSpellingIsNotOutside(@TempDir Path tmp) throws IOException {
        Path real = tmp.toRealPath();
        List<Path> spellings = new ArrayList<>();
        Path folded = Path.of(real.toString().toUpperCase(Locale.ROOT));
        if (!folded.equals(real) && Files.isDirectory(folded)) {
            spellings.add(folded);
        }
        Path firmlink = Path.of("/System/Volumes/Data" + real);
        if (Files.isDirectory(firmlink)) {
            spellings.add(firmlink);
        }
        Assumptions.assumeFalse(spellings.isEmpty(), "needs a second spelling of one folder");
        Path dir = copy(Fixtures.BASE, real);
        Files.move(dir.resolve("controls/DOC-01.yaml"), dir.resolve("test/DOC-01.yaml.txt"));
        for (Path other : spellings) {
            Files.deleteIfExists(dir.resolve("controls/DOC-01.yaml"));
            Files.createSymbolicLink(
                    dir.resolve("controls/DOC-01.yaml"), other.resolve("c/test/DOC-01.yaml.txt"));
            assertEquals(Set.of(), Catalog.readFiles(dir, Map.of()).linksOut());
            assertEquals(Set.of(), Catalog.readFiles(other.resolve("c"), Map.of()).linksOut());
            assertTrue(Catalog.insideFolder(other.resolve("c"), dir.resolve("controls/DOC-01.yaml")));
        }
        assertFalse(Catalog.insideFolder(dir.resolve("controls"), dir.resolve("catalog.yaml")));
    }
}
