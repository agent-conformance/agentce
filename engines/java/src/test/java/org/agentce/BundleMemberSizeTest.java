package org.agentce;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

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

/**
 * A manifest member's size is decided from the stat before it is read, in Python's order (census odd-inputs line
 * 91, 18.184r): at the per-file limit it is read, over it it is refused input.bundle_manifest_file_too_large and
 * never read, and a file that grows past the limit during the read is refused with the same key.
 */
class BundleMemberSizeTest {

    private final long savedLimit = Bundle.maxManifestFileBytes;

    @AfterEach
    void restore() {
        Bundle.maxManifestFileBytes = savedLimit;
        Bundle.afterConfine = null;
    }

    private static Path bundle(Path dir, String contents) throws IOException {
        byte[] bytes = contents.getBytes(StandardCharsets.UTF_8);
        Path root = dir.resolve("b");
        Files.createDirectories(root.resolve("events"));
        Files.write(root.resolve("events/a.jsonl"), bytes);
        Files.writeString(
                root.resolve("manifest.json"),
                "{\"files\":[{\"path\":\"events/a.jsonl\",\"sha256\":" + Json.quote(Canonical.sha256Hex(bytes)) + "}]}",
                StandardCharsets.UTF_8);
        return root;
    }

    @Test
    void memberExactlyAtTheLimitIsRead(@TempDir Path dir) throws IOException {
        Path root = bundle(dir, "0123456\n");
        Bundle.maxManifestFileBytes = 8;
        assertEquals(1, Bundle.load(root).eventFiles.size());
    }

    @Test
    void memberOverTheLimitIsRefusedFromTheStatNeverRead(@TempDir Path dir) throws IOException {
        Path root = bundle(dir, "0123456789\n");
        Bundle.maxManifestFileBytes = 8;
        List<String> read = new ArrayList<>();
        Bundle.afterConfine = (rel, member) -> read.add(rel);
        InputError error = assertThrows(InputError.class, () -> Bundle.load(root));
        assertEquals("input.bundle_manifest_file_too_large", error.key);
        assertEquals(List.of("manifest.json"), read);
        assertEquals(
                "events/a.jsonl is 11 bytes, over the 8-byte per-file limit.",
                error.getMessage().substring(error.getMessage().indexOf("events/a.jsonl")));
    }

    @Test
    void memberThatGrowsPastTheLimitDuringTheReadIsRefused(@TempDir Path dir) throws IOException {
        Path root = bundle(dir, "0123\n");
        Bundle.maxManifestFileBytes = 8;
        Bundle.afterConfine = (rel, member) -> {
            if (rel.equals("events/a.jsonl")) {
                try {
                    Files.writeString(member, "0123456789abcdef\n", StandardCharsets.UTF_8);
                } catch (IOException e) {
                    throw new UncheckedIOException(e);
                }
            }
        };
        InputError error = assertThrows(InputError.class, () -> Bundle.load(root));
        assertEquals("input.bundle_manifest_file_too_large", error.key);
    }
}
