package org.agentce;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertInstanceOf;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.junit.jupiter.api.Assumptions.assumeFalse;

import java.io.IOException;
import java.nio.file.AccessDeniedException;
import java.nio.file.FileSystemException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.attribute.PosixFilePermission;
import java.nio.file.attribute.PosixFilePermissions;
import java.util.List;
import java.util.Set;
import java.util.function.Function;
import java.util.function.Supplier;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/** 18.170: the catalog walk refuses an entry it cannot stat, as Python and TypeScript do, and skips only
 * what is not there (a dangling or looping link). */
class CatalogUnreadableWalkTest {

    private static final Set<String> SIG = Set.of(Verify.CATALOG_SIGNATURE_NAME);

    @BeforeEach
    void notRoot() {
        // Root enters any folder, so a folder that cannot be entered cannot be made.
        assumeFalse("root".equals(System.getProperty("user.name")));
    }

    /** Run {@code body} with {@code folder} listable but not enterable, restoring its mode afterwards. */
    private static <T> T withUnenterable(Path folder, Supplier<T> body) throws IOException {
        Set<PosixFilePermission> mode = Files.getPosixFilePermissions(folder);
        Files.setPosixFilePermissions(folder, PosixFilePermissions.fromString("r--------"));
        try {
            return body.get();
        } finally {
            Files.setPosixFilePermissions(folder, mode);
        }
    }

    /** {@code tmp/tree/a/x.ttl}: a tree with one folder holding one file. */
    private static Path treeWithOneFile(Path tmp) throws IOException {
        Path root = tmp.resolve("tree");
        Files.createDirectories(root.resolve("a"));
        Files.writeString(root.resolve("a/x.ttl"), "x");
        return root;
    }

    @Test
    void theWalkRefusesAnEntryInAFolderItCannotEnterNamingIt(@TempDir Path tmp) throws IOException {
        Path root = treeWithOneFile(tmp);
        for (Function<Path, Object> walk : List.<Function<Path, Object>>of(
                r -> Catalog.coveredFiles(r, SIG), r -> Catalog.digestTree(r, SIG))) {
            IllegalStateException e = withUnenterable(
                    root.resolve("a"), () -> assertThrows(IllegalStateException.class, () -> walk.apply(root)));
            FileSystemException cause = assertInstanceOf(AccessDeniedException.class, e.getCause());
            assertEquals(root.resolve("a/x.ttl").toString(), cause.getFile());
        }
    }

    @Test
    void loadAndVerifyRefuseACatalogHoldingAFolderTheyCannotEnter(@TempDir Path tmp) throws IOException {
        Path dir = tmp.resolve("cat");
        Fixtures.copyTree(Fixtures.BASE, dir);
        Files.createDirectories(dir.resolve("controls/extra"));
        Files.writeString(dir.resolve("controls/extra/x.txt"), "x");
        for (Function<Path, Object> command :
                List.<Function<Path, Object>>of(Catalog::load, d -> Verify.verifyCatalog(d, null))) {
            InputError e = withUnenterable(
                    dir.resolve("controls/extra"), () -> assertThrows(InputError.class, () -> command.apply(dir)));
            assertEquals("input.catalog_unreadable", e.key);
            assertTrue(e.reason.endsWith("cannot be read: controls/extra/x.txt."), e.reason);
        }
    }

    @Test
    void aDanglingOrLoopingLinkIsSkipped(@TempDir Path tmp) throws IOException {
        Path root = treeWithOneFile(tmp);
        List<String> before = Catalog.coveredFiles(root, SIG);
        String digest = Catalog.digestTree(root, SIG);
        Files.createSymbolicLink(root.resolve("a/gone.ttl"), tmp.resolve("no-such-file"));
        Files.createSymbolicLink(root.resolve("a/loop"), root.resolve("a/loop"));
        assertEquals(before, Catalog.coveredFiles(root, SIG));
        assertEquals(digest, Catalog.digestTree(root, SIG));
    }
}
