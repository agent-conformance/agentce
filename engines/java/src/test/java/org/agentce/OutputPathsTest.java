package org.agentce;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.junit.jupiter.api.Assumptions.assumeFalse;

import com.fasterxml.jackson.databind.JsonNode;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.attribute.PosixFilePermissions;
import java.security.KeyPairGenerator;
import java.security.NoSuchAlgorithmException;
import java.util.Base64;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;
import java.util.stream.Stream;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.Timeout;
import org.junit.jupiter.api.io.TempDir;

/** Every output a command writes is checked through one role and refused under its key: a target of
 * the wrong type, one that cannot be reached, or a folder that cannot be written answers exit 3 before
 * anything is written, and an existing FIFO is never opened (SPEC §8.7, §8.5; item 18.180). Mirrors
 * Python's tests/test_output_paths.py. */
@Timeout(value = 30, threadMode = Timeout.ThreadMode.SEPARATE_THREAD)
class OutputPathsTest {
    private static final Path QUICKSTART = TestPaths.repoRoot().resolve("corpus/quickstart");
    private static final Outputs.Role FILE = new Outputs.Role("input.out_file_unwritable", "--out", "f");
    private static final Outputs.Role DIR = new Outputs.Role("input.out_dir_unwritable", "--out", "f", true);
    private static final boolean ROOT = "root".equals(System.getProperty("user.name"));

    private static String refusal(Path raw, Outputs.Role role) {
        InputError err = assertThrows(InputError.class, () -> Outputs.check(raw.toString(), role));
        assertEquals(role.key(), err.key);
        return err.reason;
    }

    private static void mkfifo(Path path) throws IOException, InterruptedException {
        assertEquals(0, new ProcessBuilder("mkfifo", path.toString()).start().waitFor());
    }

    private static void assertRefused(String key, String... argv) {
        JsonNode env = Fixtures.runJson(argv);
        assertEquals(3, env.get("exit_code").asInt(), String.join(" ", argv) + " -> " + env);
        assertEquals(key, env.get("error").get("message_key").asText(), String.join(" ", argv) + " -> " + env);
    }

    @Test
    void eachReasonATargetCannotBeWrittenIsNamed(@TempDir Path dir) throws Exception {
        assertTrue(refusal(dir, FILE).contains(Outputs.NOT_A_FILE));
        Path file = Files.writeString(dir.resolve("f"), "x");
        assertTrue(refusal(file, DIR).contains(Outputs.NOT_A_DIRECTORY));
        Path fifo = dir.resolve("fifo");
        mkfifo(fifo);
        assertTrue(refusal(fifo, FILE).contains(Outputs.NOT_A_FILE));
        assertThrows(InputError.class, () -> Outputs.write(fifo.toString(), FILE, "x"));
        assertThrows(IOException.class, () -> Outputs.writeFile(fifo, "x"));
        Files.createSymbolicLink(dir.resolve("link"), dir.resolve("gone"));
        assertTrue(refusal(dir.resolve("link"), FILE).contains(Outputs.DANGLING));
        Files.createSymbolicLink(dir.resolve("loop"), Path.of("loop"));
        String longName = "n".repeat(300);
        for (String raw : new String[] {"loop", "loop/x", longName, longName + "/x"}) {
            assertTrue(refusal(dir.resolve(raw), FILE).contains(Outputs.UNREACHABLE), raw);
        }
        Path nested = dir.resolve("a/b/r.md");
        Outputs.write(nested.toString(), FILE, "report");
        assertEquals("report", Files.readString(nested));
    }

    @Test
    void aReadOnlyOrUnenterableFolderIsRefused(@TempDir Path dir) throws IOException {
        assumeFalse(ROOT, "root writes into a read-only folder");
        Path ro = Files.createDirectory(dir.resolve("ro"));
        Path shut = Files.createDirectory(dir.resolve("shut"));
        Files.setPosixFilePermissions(ro, PosixFilePermissions.fromString("r-xr-xr-x"));
        Files.setPosixFilePermissions(shut, PosixFilePermissions.fromString("---------"));
        try {
            assertTrue(refusal(ro.resolve("x.md"), FILE).contains(Outputs.UNWRITABLE));
            assertTrue(refusal(ro, DIR).contains(Outputs.UNWRITABLE));
            assertTrue(refusal(shut.resolve("x.md"), FILE).contains(Outputs.UNREACHABLE));
        } finally {
            Files.setPosixFilePermissions(ro, PosixFilePermissions.fromString("rwxr-xr-x"));
            Files.setPosixFilePermissions(shut, PosixFilePermissions.fromString("rwxr-xr-x"));
        }
    }

    @Test
    void reportOutOnAFifoIsRefused(@TempDir Path dir) throws Exception {
        Path fifo = dir.resolve("fifo");
        mkfifo(fifo);
        Path from = Files.writeString(dir.resolve("assertions.json"), "[]");
        assertRefused("input.out_file_unwritable", "report", "--from", from.toString(), "--out", fifo.toString());
    }

    @Test
    void quickstartOutWithAFifoAtAFixedReportNameIsRefused(@TempDir Path dir) throws Exception {
        for (String name : List.of("report.md", "claim.json", "quarantine.jsonl")) {
            Path out = Files.createDirectory(dir.resolve("q-" + name));
            mkfifo(out.resolve(name));
            assertRefused("input.out_dir_unwritable", "quickstart", "--out", out.toString());
        }
    }

    @Test
    void writeFileReplacesAFifoRatherThanOpeningIt(@TempDir Path dir) throws Exception {
        Path fifo = dir.resolve("fifo");
        mkfifo(fifo);
        assertThrows(Outputs.NotRegularFile.class, () -> Outputs.writeFile(fifo, "x"));
        Path file = Files.writeString(dir.resolve("file"), "old");
        Object before = Files.readAttributes(file, java.nio.file.attribute.BasicFileAttributes.class).fileKey();
        Outputs.writeFile(file, "new");
        assertEquals("new", Files.readString(file));
        // A new inode: the bytes went to a sibling that was moved into place, not into the target (KD-465).
        assertTrue(!before.equals(
                Files.readAttributes(file, java.nio.file.attribute.BasicFileAttributes.class).fileKey()));
        try (Stream<Path> left = Files.list(dir)) {
            assertEquals(2, left.count(), "no sibling left behind");
        }
    }

    private static Path assessed(Path dir) {
        Path out = dir.resolve("report");
        JsonNode env = Fixtures.runJson("assess",
                "--bundle", QUICKSTART.resolve("evidence").toString(),
                "--profile", QUICKSTART.resolve("applicability.yaml").toString(),
                "--domain", QUICKSTART.resolve("domain.linkml.yaml").toString(),
                "--out", out.toString());
        assertTrue(env.get("exit_code").asInt() <= 1, env.toString());
        return out;
    }

    private static String keyFile(Path dir) throws IOException, NoSuchAlgorithmException {
        byte[] der = KeyPairGenerator.getInstance("Ed25519").generateKeyPair().getPrivate().getEncoded();
        String pem = "-----BEGIN PRIVATE KEY-----\n"
                + Base64.getMimeEncoder(64, "\n".getBytes(StandardCharsets.US_ASCII)).encodeToString(der)
                + "\n-----END PRIVATE KEY-----\n";
        return Files.writeString(dir.resolve("k.pem"), pem).toString();
    }

    private static Map<String, String> snapshot(Path root) throws IOException {
        Map<String, String> out = new TreeMap<>();
        try (Stream<Path> walk = Files.walk(root)) {
            for (Path p : walk.filter(Files::isRegularFile).toList()) {
                out.put(root.relativize(p).toString(), Base64.getEncoder().encodeToString(Files.readAllBytes(p)));
            }
        }
        return out;
    }

    @Test
    void signRefusesABadWriteTargetBeforeRewritingTheClaim(@TempDir Path dir) throws Exception {
        Path base = assessed(dir);
        String key = keyFile(dir);
        for (String kind : new String[] {"fifo", "folder", "signatures_file"}) {
            Path report = dir.resolve(kind);
            Fixtures.copyTree(base, report);
            Path sigs = report.resolve("signatures");
            if (Files.isDirectory(sigs)) {
                try (Stream<Path> walk = Files.walk(sigs)) {
                    for (Path p : walk.sorted(java.util.Comparator.reverseOrder()).toList()) {
                        Files.delete(p);
                    }
                }
            }
            if (kind.equals("signatures_file")) {
                Files.writeString(sigs, "x");
            } else {
                Files.createDirectory(sigs);
                Path target = sigs.resolve("claimant-kms.dsse.json");
                if (kind.equals("fifo")) {
                    mkfifo(target);
                } else {
                    Files.createDirectory(target);
                }
            }
            Map<String, String> before = snapshot(report);
            assertRefused("input.report_dir_unwritable",
                    "sign", report.toString(), "--as", "claimant", "--profile", "kms", "--key", key);
            assertEquals(before, snapshot(report), kind);
        }
        JsonNode env = Fixtures.runJson("sign", base.toString(), "--as", "claimant", "--profile", "kms", "--key", key);
        assertEquals(0, env.get("exit_code").asInt(), env.toString());
        assertTrue(Files.isRegularFile(base.resolve("signatures/claimant-kms.dsse.json")));
    }

    @Test
    void readinessOnAReadOnlyReportWarnsUnderItsKey(@TempDir Path dir) throws IOException {
        assumeFalse(ROOT, "root writes into a read-only folder");
        Path report = assessed(dir);
        Files.setPosixFilePermissions(report, PosixFilePermissions.fromString("r-xr-xr-x"));
        try {
            JsonNode env = Fixtures.runJson("readiness", report.toString());
            assertTrue(env.get("exit_code").asInt() <= 1, env.toString());
            assertTrue(env.get("report").isNull(), env.toString());
            assertEquals("warning.readiness_file_unwritten", env.get("report_warning").asText());
        } finally {
            Files.setPosixFilePermissions(report, PosixFilePermissions.fromString("rwxr-xr-x"));
        }
    }

    // 18.184c: one write strategy in every engine, a bounded-name sibling moved over the target.

    @Test
    void aHardLinkKeepsItsOtherNameAndADanglingLinkIsReplaced(@TempDir Path dir) throws IOException {
        Path outside = Files.writeString(dir.resolve("outside.md"), "old\n");
        Path linked = Files.createLink(dir.resolve("r.md"), outside);
        Outputs.writeFile(linked, "new\n");
        assertEquals("new\n", Files.readString(linked));
        assertEquals("old\n", Files.readString(outside));
        Files.createDirectory(dir.resolve("away"));
        Path dangling = Files.createSymbolicLink(dir.resolve("d.md"), dir.resolve("away/d.md"));
        Outputs.writeFile(dangling, "new\n");
        assertTrue(!Files.isSymbolicLink(dangling) && Files.notExists(dir.resolve("away/d.md")));
    }

    @Test
    void aLongNameIsWrittenAndTheModeIsKept(@TempDir Path dir) throws IOException {
        Path longName = dir.resolve("n".repeat(252) + ".md");
        Outputs.writeFile(longName, "new\n");
        assertEquals("new\n", Files.readString(longName));
        Path kept = Files.writeString(dir.resolve("r.md"), "old\n");
        Files.setPosixFilePermissions(kept, PosixFilePermissions.fromString("rw-r-----"));
        Outputs.writeFile(kept, "new\n");
        assertEquals("rw-r-----", PosixFilePermissions.toString(Files.getPosixFilePermissions(kept)));
        try (var names = Files.list(dir)) {
            assertEquals(2, names.count());
        }
    }

    @Test
    void aPathThroughAMissingFolderIsRefusedAndNothingIsMade(@TempDir Path dir) throws IOException {
        for (String rel : new String[] {"nosub/../r.md", "nosub/..", "a/nosub/../b/r.md"}) {
            assertTrue(refusal(dir.resolve(rel), FILE).contains(Outputs.UNREACHABLE), rel);
        }
        try (var names = Files.list(dir)) {
            assertEquals(0, names.count());
        }
        Files.createDirectory(dir.resolve("sub"));
        Outputs.write(dir.resolve("sub/../r.md").toString(), FILE, "new\n");
        assertEquals("new\n", Files.readString(dir.resolve("r.md")));
    }
}
