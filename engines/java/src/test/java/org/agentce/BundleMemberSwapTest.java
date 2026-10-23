package org.agentce;

import static org.junit.jupiter.api.Assertions.assertArrayEquals;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.io.IOException;
import java.io.UncheckedIOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;

/**
 * A bundle member is read once, through the file the confinement checked (18.184f): a member or manifest.json
 * swapped for a link to an outside copy of its own bytes between the check and the read is refused, never read
 * through the swapped-in name, and ingest uses the bytes {@link Bundle#load} hashed.
 */
class BundleMemberSwapTest {

    private static final byte[] EVENT = "{\"id\":\"e1\"}\n".getBytes(StandardCharsets.UTF_8);

    @AfterEach
    void clearSeams() {
        Bundle.afterConfine = null;
        Bundle.afterCheck = null;
    }

    private static Path bundle(Path dir) throws IOException {
        Path root = dir.resolve("bundle");
        Files.createDirectories(root.resolve("events"));
        Files.write(root.resolve("events/a.jsonl"), EVENT);
        Files.writeString(
                root.resolve("manifest.json"),
                "{\"files\":[{\"path\":\"events/a.jsonl\",\"sha256\":" + Json.quote(Canonical.sha256Hex(EVENT)) + "}]}",
                StandardCharsets.UTF_8);
        return root;
    }

    private static void swap(Path member, Path outside) {
        try {
            Files.write(outside, Files.readAllBytes(member));
            Files.delete(member);
            Files.createSymbolicLink(member, outside);
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        }
    }

    @ParameterizedTest
    @ValueSource(strings = {"events/a.jsonl", "manifest.json"})
    void swappedAfterConfinementIsRefused(String target, @TempDir Path dir) throws IOException {
        Path root = bundle(dir);
        List<String> swapped = new ArrayList<>();
        Bundle.afterConfine = (rel, member) -> {
            if (rel.equals(target) && swapped.isEmpty()) {
                swap(member, dir.resolve("outside"));
                swapped.add(rel);
            }
        };
        InputError error = assertThrows(InputError.class, () -> Bundle.load(root));
        assertEquals(List.of(target), swapped);
        assertEquals("input.bundle_manifest_path", error.key);
        assertTrue(error.getMessage().contains(target + " changed while it was being read"), error.getMessage());
    }

    @Test
    void swappedOnceOpenIsNeverReadThroughTheLink(@TempDir Path dir) throws IOException {
        Path root = bundle(dir);
        Files.writeString(dir.resolve("elsewhere"), "outside bytes\n", StandardCharsets.UTF_8);
        Path member = root.resolve("events/a.jsonl");
        Bundle.afterCheck = (rel, m) -> {
            try {
                Files.delete(m);
                Files.createSymbolicLink(m, dir.resolve("elsewhere"));
            } catch (IOException e) {
                throw new UncheckedIOException(e);
            }
        };
        // Java cannot ask which file an open channel names, so it checks the path again after the read and refuses.
        InputError error = assertThrows(InputError.class, () -> Bundle.readConfined(root, "events/a.jsonl", member));
        assertEquals("input.bundle_manifest_path", error.key);
    }

    /** What ingest makes of a loaded bundle: its accepted events and quarantine records, as JSON. */
    private static String ingested(Bundle loaded) {
        Ingest.Result result = Ingest.ingest(loaded);
        List<String> out = new ArrayList<>();
        result.accepted.forEach(e -> out.add(Canonical.canonicalString(e)));
        result.quarantined.forEach(q -> out.add(Canonical.canonicalString(q.toJson())));
        return String.join("\n", out);
    }

    @Test
    void ingestUsesTheCheckedBytes(@TempDir Path dir) throws IOException {
        Path root = bundle(dir);
        Bundle loaded = Bundle.load(root);
        String before = ingested(loaded);
        Files.writeString(root.resolve("events/a.jsonl"), "not json\n", StandardCharsets.UTF_8);
        assertArrayEquals(EVENT, loaded.contents.get(loaded.eventFiles.get(0)));
        assertEquals(before, ingested(loaded));
    }

    @Test
    void coverageDenominatorRewrittenAfterLoadCountsTheCheckedBytes(@TempDir Path dir) throws IOException {
        Path root = dir.resolve("bundle");
        Files.createDirectories(root.resolve("reference"));
        byte[] checked = "{\"counts\": {\"ToolCall\": 1}}".getBytes(StandardCharsets.UTF_8);
        Files.write(root.resolve("reference/counts.json"), checked);
        Files.writeString(
                root.resolve("manifest.json"),
                "{\"files\":[{\"path\":\"reference/counts.json\",\"sha256\":"
                        + Json.quote(Canonical.sha256Hex(checked)) + "}]}",
                StandardCharsets.UTF_8);
        Bundle loaded = Bundle.load(root);
        Files.writeString(root.resolve("reference/counts.json"), "{\"counts\": {\"ToolCall\": 999}}", StandardCharsets.UTF_8);
        Profile profile = Profile.fromDict(Json.parse("{\"subjects\": [{\"id\": \"subj-A\", \"coverage_denominators\": "
                + "[{\"kind\": \"registry\", \"source\": \"urn:src:egress\", \"covers\": [\"ToolCall\"], "
                + "\"manifest\": \"reference/counts.json\"}]}]}"));
        assertEquals(1, Coverage.computeCoverage(List.of(), profile, loaded.members)
                .get("subjects").get("subj-A").get("event_types").get("ToolCall").get("expected").asInt());
    }

    @Test
    void manifestUnreadableAtTheReadIsUnreadableNotInvalid(@TempDir Path dir) throws IOException {
        // Census odd-inputs line 90 (18.184r): readable at the pre-check, refused at the read; one key per cause.
        Path root = bundle(dir);
        Bundle.afterConfine = (rel, member) -> {
            if (rel.equals("manifest.json")) {
                assertTrue(member.toFile().setReadable(false, false));
            }
        };
        try {
            InputError error = assertThrows(InputError.class, () -> Bundle.load(root));
            assertEquals("input.bundle_unreadable", error.key);
            assertTrue(error.getMessage().contains("cannot be read: manifest.json."), error.getMessage());
        } finally {
            root.resolve("manifest.json").toFile().setReadable(true, false);
        }
    }
}
