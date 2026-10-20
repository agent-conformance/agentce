package org.agentce;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assumptions.assumeFalse;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.attribute.PosixFilePermissions;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;

/**
 * A path the user names that cannot be read, or whose name is too long, is refused with the role's key, never
 * internal.unexpected; conformance run reads no_ml from this engine's own install, never from the folder --engine
 * names (item 18.179). Mirrors Python's tests/test_unreadable_inputs.py.
 */
class UnreadableInputsTest {
    private static final Path REPO = TestPaths.repoRoot();
    private static final Path QUICKSTART = REPO.resolve("corpus/quickstart");
    private static final String BUNDLE = QUICKSTART.resolve("evidence").toString();
    private static final String PROFILE = QUICKSTART.resolve("applicability.yaml").toString();
    private static final boolean ROOT = "root".equals(System.getProperty("user.name"));

    private static void assertRefused(String key, String... argv) {
        JsonNode env = Fixtures.runJson(argv);
        String shown = String.join(" ", argv);
        assertEquals(3, env.get("exit_code").asInt(), shown + " -> " + env);
        assertEquals(key, env.get("error").get("message_key").asText(), shown + " -> " + env);
    }

    @Test
    void anUnreadableFileIsRefusedWithTheRoleUnreadableKey(@TempDir Path dir) throws IOException {
        assumeFalse(ROOT, "root reads a mode 000 file");
        Path locked = Files.writeString(dir.resolve("locked.json"), "[]");
        Files.setPosixFilePermissions(locked, PosixFilePermissions.fromString("---------"));
        String f = locked.toString();
        String r = Files.createDirectory(dir.resolve("r")).toString();
        try {
            assertRefused("input.profile_unreadable", "assess", "--bundle", BUNDLE, "--profile", f);
            assertRefused("input.trust_root_unreadable",
                    "assess", "--bundle", BUNDLE, "--profile", PROFILE, "--trust-root", f);
            assertRefused("input.gaps_unreadable", "readiness", r, "--gaps", f);
            assertRefused("input.report_a_unreadable", "diff", f, f);
            assertRefused("input.from_unreadable", "report", "--from", f);
        } finally {
            Files.setPosixFilePermissions(locked, PosixFilePermissions.fromString("rw-------"));
        }
    }

    @Test
    void aThreeHundredByteNameIsNotAFile(@TempDir Path dir) {
        assertRefused("input.profile_not_a_file",
                "assess", "--bundle", BUNDLE, "--profile", dir.resolve("a".repeat(300)).toString());
    }

    /** {@code name/} resolves only to a directory (POSIX ENOTDIR), so it is refused, never read as the file
     * {@code name} that {@code Paths.get} normalises it to (18.184b). */
    @Test
    void aFileNamedWithATrailingSlashIsNotAFile(@TempDir Path dir) throws IOException {
        String f = Files.writeString(dir.resolve("file.json"), "[]").toString();
        String r = Files.createDirectory(dir.resolve("r")).toString();
        assertRefused("input.profile_not_a_file", "assess", "--bundle", BUNDLE, "--profile", f + "/");
        assertRefused("input.trust_root_not_a_file",
                "assess", "--bundle", BUNDLE, "--profile", PROFILE, "--trust-root", f + "/");
        assertRefused("input.gaps_not_a_file", "readiness", r, "--gaps", f + "/");
        assertRefused("input.report_a_not_a_file", "diff", f + "/", f);
        assertRefused("input.report_b_not_a_file", "diff", f, f + "/");
        assertRefused("input.from_not_a_file", "report", "--from", f + "/");
        assertRefused("input.from_not_a_file", "report", "--from", f + "/.");
    }

    @Test
    void anAdaptersFolderThatCannotBeListedOrEnteredIsRefused(@TempDir Path dir) throws IOException {
        assumeFalse(ROOT, "root enters a mode 600 folder");
        Path adapters = Files.createDirectory(dir.resolve("adapters"));
        Files.setPosixFilePermissions(adapters, PosixFilePermissions.fromString("rw-------"));
        try {
            assertRefused("input.adapters_unreadable", "conformance", "run",
                    "--engine", REPO.resolve("engines/java").toString(),
                    "--corpus", REPO.resolve("corpus").toString(),
                    "--adapters", adapters.toString());
        } finally {
            Files.setPosixFilePermissions(adapters, PosixFilePermissions.fromString("rwx------"));
        }
    }

    @ParameterizedTest
    @ValueSource(booleans = {false, true})
    void aCorpusManifestThatCannotBeReachedIsRefusedNotReadAsAbsent(boolean withGenerator, @TempDir Path dir)
            throws IOException {
        assumeFalse(ROOT, "root enters a mode 600 folder");
        Path corpus = Files.createDirectory(dir.resolve("corpus"));
        Path locked = Files.createDirectory(dir.resolve("L"));
        Files.writeString(locked.resolve("f"), "{}");
        Files.createSymbolicLink(corpus.resolve("corpus-manifest.json"), locked.resolve("f"));
        if (withGenerator) {
            Files.createSymbolicLink(corpus.resolve("generator"), REPO.resolve("corpus/generator"));
        }
        String engine = REPO.resolve("engines/java").toString();
        Files.setPosixFilePermissions(locked, PosixFilePermissions.fromString("rw-------"));
        try {
            assertRefused("input.corpus_unreadable",
                    "conformance", "run", "--engine", engine, "--corpus", corpus.toString());
        } finally {
            Files.setPosixFilePermissions(locked, PosixFilePermissions.fromString("rwx------"));
        }
        assertRefused("input.corpus_not_found",
                "conformance", "run", "--engine", engine, "--corpus", locked.toString());
    }

    @Test
    void conformanceNoMlDoesNotFollowEngineToAnotherEnginesFolder(@TempDir Path dir) throws IOException {
        Path corpus = dir.resolve("corpus");
        Fixtures.copyTree(QUICKSTART, corpus.resolve("projects/q"));
        Files.writeString(corpus.resolve("corpus-manifest.json"),
                "{\"corpus_version\": \"t\", \"projects\": [{\"id\": \"q\"}]}");
        // engines/python has no gradle.lockfile: reading the lock under --engine crashed here.
        ObjectNode report = Conformance.runEcs(REPO.resolve("engines/python"), corpus, null);
        assertEquals("pass", report.get("no_ml").asText());
        assertEquals("full", report.get("claim").asText(), report.toString());
    }
}
