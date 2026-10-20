package org.agentce;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.junit.jupiter.api.Assumptions.assumeTrue;

import com.fasterxml.jackson.databind.JsonNode;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.nio.file.attribute.PosixFilePermissions;
import java.util.List;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/**
 * An unwritable {@code --state} directory raises the keyed {@code input.state_dir_unwritable} error
 * (loophole L18.6), not an internal one -- both at the early {@link StateDir#ensureWritable} probe and
 * at {@link StateDir#save()} itself, and for a directory that already exists but lost its write bit
 * after creation (not only a missing parent).
 *
 * <p>{@code state-golden.json} was produced by the reference engine's {@code StateDir}: record a report,
 * then plan a re-assessment against a changed bundle. The port must write the same {@code state.json}
 * bytes (SPEC §9.6: a state directory is portable across engines), compute the same manifest digest
 * and supersede the same report.
 */
class StateDirTest {
    @Test
    void stateDirectoryMatchesThePythonReferenceGolden(@TempDir Path work) throws IOException {
        JsonNode golden = Json.parseFile(TestPaths.testData().resolve("state-golden.json"));
        Path manifest = work.resolve("manifest.json");
        Files.writeString(manifest, golden.get("manifest_bytes").textValue(), StandardCharsets.UTF_8);
        Path stateDir = work.resolve("s");

        String digest = StateDir.load(stateDir).record(
                golden.get("bundle_a").textValue(), manifest, golden.get("window_a").textValue());
        assertEquals(golden.get("manifest_digest").textValue(), digest);
        assertEquals(
                golden.get("state_json_after_record").textValue(),
                Files.readString(stateDir.resolve("state.json"), StandardCharsets.UTF_8));

        List<String> supersedes = StateDir.load(stateDir).plan(
                golden.get("bundle_b").textValue(),
                Fixtures.toList(golden.get("events_b")),
                golden.get("window_b").textValue());
        assertEquals(Fixtures.toList(golden.get("supersedes")).stream().map(JsonNode::textValue).toList(), supersedes);
    }

    @Test
    void aPythonWrittenStateWithPriorOutcomesLoadsAndSavesUnchanged(@TempDir Path stateDir) throws IOException {
        String python = Json.parseFile(TestPaths.testData().resolve("state-golden.json"))
                .get("state_json_with_outcomes").textValue();
        Files.writeString(stateDir.resolve("state.json"), python, StandardCharsets.UTF_8);
        StateDir.load(stateDir).save();
        assertEquals(python, Files.readString(stateDir.resolve("state.json"), StandardCharsets.UTF_8));
    }

    @Test
    void ensureWritableRaisesOnAnUnwritableParent(@TempDir Path work) throws IOException {
        Path ro = work.resolve("ro");
        Files.createDirectory(ro);
        assumeTrue(Fixtures.makeUnwritable(ro), "this user can write to a mode-555 directory");
        try {
            InputError err =
                    assertThrows(
                            InputError.class, () -> StateDir.ensureWritable(ro.resolve("state")));
            assertEquals("input.state_dir_unwritable", err.key);
            assertEquals("choose a writable --state directory.", err.fix);
        } finally {
            Files.setPosixFilePermissions(ro, PosixFilePermissions.fromString("rwxr-xr-x"));
        }
    }

    @Test
    void ensureWritableRaisesOnAPreExistingUnwritableStateDir(@TempDir Path work) throws IOException {
        Path stateDir = work.resolve("s");
        Files.createDirectory(stateDir);
        assumeTrue(Fixtures.makeUnwritable(stateDir), "this user can write to a mode-555 directory");
        try {
            InputError err =
                    assertThrows(
                            InputError.class, () -> StateDir.ensureWritable(stateDir));
            assertEquals("input.state_dir_unwritable", err.key);
        } finally {
            Files.setPosixFilePermissions(stateDir, PosixFilePermissions.fromString("rwxr-xr-x"));
        }
    }

    @Test
    void saveRaisesOnAnUnwritableStateDir(@TempDir Path work) throws IOException {
        Path stateDir = work.resolve("s");
        Files.createDirectory(stateDir);
        assumeTrue(Fixtures.makeUnwritable(stateDir), "this user can write to a mode-555 directory");
        StateDir state = StateDir.load(stateDir);
        try {
            InputError err =
                    assertThrows(InputError.class, state::save);
            assertEquals("input.state_dir_unwritable", err.key);
        } finally {
            Files.setPosixFilePermissions(stateDir, PosixFilePermissions.fromString("rwxr-xr-x"));
        }
    }

    /** A state directory with one recorded report; returns its state.json path. */
    private static Path saved(Path work) throws IOException {
        Path manifest = work.resolve("manifest.json");
        Files.writeString(manifest, "{}\n");
        StateDir.load(work.resolve("state")).record("sha256:bbb", manifest, "2026-02-01T00:00:00Z");
        return work.resolve("state").resolve("state.json");
    }

    private static void assertRefused(Path state, String causeEnd) {
        InputError err = assertThrows(InputError.class, () -> StateDir.ensureWritable(state));
        assertEquals("input.state_dir_unwritable", err.key);
        assertTrue(err.getMessage().contains(causeEnd), err.getMessage());
    }

    @Test
    void ensureWritableRefusesAFifoStateJsonWithoutOpeningIt(@TempDir Path work) throws Exception {
        Path state = Files.createDirectory(work.resolve("state"));
        assertEquals(0, new ProcessBuilder("mkfifo", state.resolve("state.json").toString()).start().waitFor());
        assertRefused(state, "state.json' is not a regular file.");
    }

    @Test
    void ensureWritableRefusesAnUnreadableStateJson(@TempDir Path work) throws IOException {
        Path sj = saved(work);
        Files.setPosixFilePermissions(sj, PosixFilePermissions.fromString("---------"));
        try {
            assertRefused(work.resolve("state"), "state.json': Permission denied");
        } finally {
            Files.setPosixFilePermissions(sj, PosixFilePermissions.fromString("rw-r--r--"));
        }
    }

    @Test
    void aDanglingStateJsonIsAFreshSeriesAndSaveReplacesTheLink(@TempDir Path work) throws IOException {
        Path state = Files.createDirectory(work.resolve("state"));
        Files.createSymbolicLink(state.resolve("state.json"), work.resolve("away").resolve("state.json"));
        StateDir.ensureWritable(state);
        assertNull(StateDir.load(state).lastReportDigest);
        Path sj = saved(work);
        assertTrue(Files.isRegularFile(sj, LinkOption.NOFOLLOW_LINKS));
        assertFalse(Files.exists(work.resolve("away")));
    }

    @Test
    void saveKeepsAHardLinkOutsideStateAndTheMode(@TempDir Path work) throws IOException {
        Path sj = saved(work);
        Files.setPosixFilePermissions(sj, PosixFilePermissions.fromString("rw-------"));
        Path outside = Files.createLink(work.resolve("outside.json"), sj);
        String before = Files.readString(outside);
        StateDir state = StateDir.load(work.resolve("state"));
        state.lastWindowEnd = "2026-03-01T00:00:00Z";
        state.save();
        assertEquals(before, Files.readString(outside));
        assertEquals(1, (int) Files.getAttribute(outside, "unix:nlink"));
        assertEquals("rw-------", PosixFilePermissions.toString(Files.getPosixFilePermissions(sj)));
        assertEquals("2026-03-01T00:00:00Z", Json.parseFile(sj).get("last_window_end").textValue());
    }

    @Test
    void aFifoAtAProjectReportUnderOutRefusesTheConformanceRun(@TempDir Path work) throws Exception {
        Path corpus = work.resolve("corpus");
        Path project = corpus.resolve("projects").resolve("q");
        Path quickstart = TestPaths.repoRoot().resolve("corpus/quickstart");
        try (var walk = Files.walk(quickstart)) {
            for (Path p : (Iterable<Path>) walk::iterator) {
                Path to = project.resolve(quickstart.relativize(p).toString());
                if (Files.isDirectory(p)) {
                    Files.createDirectories(to);
                } else {
                    Files.copy(p, to);
                }
            }
        }
        Files.writeString(corpus.resolve("corpus-manifest.json"), "{\"corpus_version\": \"t\", \"projects\": [{\"id\": \"q\"}]}");
        Path out = work.resolve("out");
        Path report = Files.createDirectories(out.resolve("projects").resolve("q")).resolve("report.md");
        assertEquals(0, new ProcessBuilder("mkfifo", report.toString()).start().waitFor());
        InputError err = assertThrows(InputError.class,
                () -> Conformance.runEcs(TestPaths.repoRoot().resolve("engines/java"), corpus, out));
        assertEquals("input.out_dir_unwritable", err.key);
        assertTrue(err.getMessage().contains("cannot be written"), err.getMessage());
    }
}
