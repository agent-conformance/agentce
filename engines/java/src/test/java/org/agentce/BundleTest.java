package org.agentce;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
import org.junit.jupiter.api.io.TempDir;

/**
 * Path confinement for evidence bundles (SPEC §8.1): a manifest-listed path is confined to the bundle
 * root after symlinks resolve, not only string-checked for a leading {@code /} or a {@code ..}
 * segment. A path that escapes the root -- by a literal {@code ..} or by a symlink whose target lies
 * outside -- is refused under {@code input.bundle_manifest_path}; a symlink that stays inside the root
 * is ordinary and still loads.
 */
class BundleTest {

    private static void writeManifest(Path root, String rel, byte[] contents) throws IOException {
        String manifest = "{\"files\":[{\"path\":" + Json.quote(rel) + ",\"sha256\":"
                + Json.quote(Canonical.sha256Hex(contents)) + "}]}";
        Files.writeString(root.resolve("manifest.json"), manifest, StandardCharsets.UTF_8);
    }

    @Test
    void symlinkEscapingTheBundleRootIsRefused(@TempDir Path tempDir) throws IOException {
        Path root = tempDir.resolve("bundle");
        Files.createDirectories(root.resolve("events"));
        Path outside = tempDir.resolve("outside.txt");
        Files.writeString(outside, "outside the bundle root\n", StandardCharsets.UTF_8);
        Files.createSymbolicLink(root.resolve("events").resolve("evil.jsonl"), outside);
        writeManifest(root, "events/evil.jsonl", Files.readAllBytes(outside));

        InputError error = assertThrows(InputError.class, () -> Bundle.load(root));
        assertEquals("input.bundle_manifest_path", error.key);
    }

    @Test
    void literalDotDotPathIsStillRefused(@TempDir Path tempDir) throws IOException {
        Path root = tempDir.resolve("bundle");
        Files.createDirectories(root);
        Path outside = tempDir.resolve("outside.txt");
        Files.writeString(outside, "x\n", StandardCharsets.UTF_8);
        writeManifest(root, "../outside.txt", Files.readAllBytes(outside));

        InputError error = assertThrows(InputError.class, () -> Bundle.load(root));
        assertEquals("input.bundle_manifest_path", error.key);
    }

    @Test
    void symlinkResolvingInsideTheBundleRootIsAccepted(@TempDir Path tempDir) throws IOException {
        // Positive control: confinement checks the resolved target, not "is it a symlink".
        Path root = tempDir.resolve("bundle");
        Files.createDirectories(root.resolve("events"));
        Path real = root.resolve("events").resolve("real.jsonl");
        Files.writeString(real, "{}\n", StandardCharsets.UTF_8);
        Path link = root.resolve("events").resolve("alias.jsonl");
        Files.createSymbolicLink(link, real);
        writeManifest(root, "events/alias.jsonl", Files.readAllBytes(real));

        Bundle bundle = Bundle.load(root);
        assertEquals(List.of(link), bundle.eventFiles);
    }

    @Test
    void confineToRootRefusesAbsoluteAndEmptyAndNullPaths(@TempDir Path tempDir) {
        assertNull(Bundle.confineToRoot(tempDir, null));
        assertNull(Bundle.confineToRoot(tempDir, ""));
        assertNull(Bundle.confineToRoot(tempDir, "/etc/passwd"));
        assertNull(Bundle.confineToRoot(tempDir, "events/../../outside.txt"));
    }

    @Test
    void confineToRootAcceptsAnInRootPathAndOneThatDoesNotExistYet(@TempDir Path tempDir)
            throws IOException {
        Files.createDirectories(tempDir.resolve("reference"));
        Path inside = tempDir.resolve("reference").resolve("counts.json");
        Files.writeString(inside, "{}\n", StandardCharsets.UTF_8);

        Path confined = Bundle.confineToRoot(tempDir, "reference/counts.json");
        assertEquals(inside, confined);
        assertTrue(Bundle.safeIsFile(confined));

        // A path that simply is not there is not a confinement failure: it comes back unresolved so
        // the caller reports "missing from the bundle" under its own key.
        Path missing = Bundle.confineToRoot(tempDir, "reference/absent.json");
        assertNotNull(missing);
        assertEquals(tempDir.resolve("reference").resolve("absent.json"), missing);
        assertFalse(Bundle.safeIsFile(missing));
    }

    @Test
    void confineToRootIsNotFooledBySiblingRootPrefix(@TempDir Path tempDir) throws IOException {
        // "/…/bundle-evil" shares a string prefix with "/…/bundle" but is not inside it.
        Path root = tempDir.resolve("bundle");
        Files.createDirectories(root);
        Path sibling = tempDir.resolve("bundle-evil");
        Files.createDirectories(sibling);
        Files.writeString(sibling.resolve("secret.json"), "{}\n", StandardCharsets.UTF_8);
        Files.createSymbolicLink(root.resolve("alias.json"), sibling.resolve("secret.json"));

        assertNull(Bundle.confineToRoot(root, "alias.json"));
    }

    @Test
    void confineToRootHandlesASymlinkLoopWithoutRaising(@TempDir Path tempDir) throws IOException {
        Path loopA = tempDir.resolve("a");
        Path loopB = tempDir.resolve("b");
        Files.createSymbolicLink(loopA, loopB);
        Files.createSymbolicLink(loopB, loopA);

        assertNull(Bundle.confineToRoot(tempDir, "a"));
        assertFalse(Bundle.safeIsFile(loopA));
    }

    @Test
    void confineToRootHandlesAnEmbeddedNulWithoutRaising(@TempDir Path tempDir) {
        String nul = String.valueOf((char) 0);
        assertNull(Bundle.confineToRoot(tempDir, "evil" + nul + "name"));
    }

    /** A valid bundle at {@code <dir>/b} whose manifest.json is moved and linked back per {@code kind}
     * (18.183); returns the folder to load. */
    private static Path placeManifest(Path dir, String kind) throws IOException {
        Path root = dir.resolve("b");
        Files.createDirectories(root.resolve("events"));
        byte[] events = "{}\n".getBytes(StandardCharsets.UTF_8);
        Files.write(root.resolve("events").resolve("s.jsonl"), events);
        writeManifest(root, "events/s.jsonl", events);
        Path manifest = root.resolve("manifest.json");
        Path out = Files.createDirectories(dir.resolve("out"));
        if (kind.equals("plain")) {
            return root;
        }
        if (kind.equals("root_link")) {
            return Files.createSymbolicLink(dir.resolve("rl"), root);
        }
        if (kind.equals("dangling")) {
            Files.delete(manifest);
            Files.createSymbolicLink(manifest, out.resolve("none.json"));
            return root;
        }
        String link;
        Path target;
        switch (kind) {
            case "outside" -> { link = "../out/manifest.json"; target = out.resolve("manifest.json"); }
            case "inside" -> { link = "meta/m.json"; target = root.resolve("meta/m.json"); }
            case "folder_link_out" -> { link = "meta/m.json"; target = out.resolve("m.json"); }
            case "link_to_link_out" -> { link = "inner"; target = out.resolve("m.json"); }
            case "out_and_back" -> { link = "../b/m.json"; target = root.resolve("m.json"); }
            case "sibling_prefix" -> { link = "../b-evil/manifest.json"; target = dir.resolve("b-evil/manifest.json"); }
            default -> throw new IllegalArgumentException(kind);
        }
        Files.createDirectories(target.getParent());
        Files.move(manifest, target);
        if (kind.equals("folder_link_out")) {
            Files.createSymbolicLink(root.resolve("meta"), out);
        }
        if (kind.equals("link_to_link_out")) {
            Files.createSymbolicLink(root.resolve("inner"), target);
        }
        Files.createSymbolicLink(manifest, Path.of(link));
        return root;
    }

    @ParameterizedTest
    @ValueSource(strings = {"outside", "folder_link_out", "link_to_link_out", "sibling_prefix"})
    void manifestResolvingOutsideTheBundleIsRefused(String kind, @TempDir Path tempDir) throws IOException {
        Path bundle = placeManifest(tempDir, kind);
        InputError error = assertThrows(InputError.class, () -> Bundle.load(bundle));
        assertEquals("input.bundle_manifest_path", error.key);
    }

    @ParameterizedTest
    @ValueSource(strings = {"plain", "inside", "root_link", "out_and_back"})
    void manifestResolvingInsideTheBundleLoads(String kind, @TempDir Path tempDir) throws IOException {
        assertNotNull(Bundle.load(placeManifest(tempDir, kind)).manifest.get("files"));
    }

    @Test
    void manifestDanglingLinkIsStillMissing(@TempDir Path tempDir) throws IOException {
        Path bundle = placeManifest(tempDir, "dangling");
        InputError error = assertThrows(InputError.class, () -> Bundle.load(bundle));
        assertEquals("input.bundle_manifest_missing", error.key);
    }

    /** The bundle's own folder spelled in upper case, or null where the filesystem tells names apart
     * by case (KD-441: these cases discriminate only on a case-insensitive filesystem). */
    private static Path folded(Path dir) {
        Path upper = Path.of(dir.toString().toUpperCase(java.util.Locale.ROOT));
        return !upper.equals(dir) && Files.exists(upper) ? upper : null;
    }

    @Test
    void aLinkSpelledWithTheBundleInAnotherCaseIsJudgedByFolderIdentity(@TempDir Path tempDir)
            throws IOException {
        Path bundle = Files.createDirectories(tempDir.resolve("bundle").resolve("moved-inside"))
                .getParent();
        Files.createDirectories(tempDir.resolve("bundle2"));
        Files.writeString(bundle.resolve("moved-inside/x.jsonl"), "{}\n");
        Files.writeString(tempDir.resolve("bundle2/x.jsonl"), "{}\n");
        Files.writeString(tempDir.resolve("up.jsonl"), "{}\n");
        Path upper = folded(bundle);
        org.junit.jupiter.api.Assumptions.assumeTrue(upper != null, "needs a case-insensitive filesystem");
        Files.createSymbolicLink(bundle.resolve("inside.jsonl"), upper.resolve("moved-inside/x.jsonl"));
        Files.createSymbolicLink(bundle.resolve("sibling.jsonl"),
                folded(tempDir.resolve("bundle2")).resolve("x.jsonl"));
        Files.createSymbolicLink(bundle.resolve("parent.jsonl"), folded(tempDir).resolve("up.jsonl"));
        assertEquals(bundle.resolve("inside.jsonl"), Bundle.confineToRoot(bundle, "inside.jsonl"));
        assertNull(Bundle.confineToRoot(bundle, "sibling.jsonl"));
        assertNull(Bundle.confineToRoot(bundle, "parent.jsonl"));
    }
}
