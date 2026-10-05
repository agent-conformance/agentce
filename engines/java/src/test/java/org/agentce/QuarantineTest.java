package org.agentce;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assumptions.assumeTrue;

import com.fasterxml.jackson.databind.JsonNode;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.attribute.PosixFilePermissions;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/**
 * An unwritable {@code --out} directory raises the keyed {@code input.out_dir_unwritable} error
 * (loophole L18.6) through the real CLI dispatch -- {@code writeQuarantineJsonl} (private to
 * {@link Cli}) is exercised the same way {@code CliTest} exercises every other command, not through a
 * package-private test entrypoint. Covers both a directory whose parent cannot be created and a
 * directory that already exists but lost its write bit after creation (not only a missing parent).
 */
class QuarantineTest {
    @Test
    void quickstartRaisesOnAnUnwritableOutParent(@TempDir Path work) throws IOException {
        Path ro = work.resolve("ro");
        Files.createDirectory(ro);
        assumeTrue(Fixtures.makeUnwritable(ro), "this user can write to a mode-555 directory");
        try {
            JsonNode env = Fixtures.runJson("quickstart", "--out", ro.resolve("out").toString());
            assertEquals(3, env.get("exit_code").asInt());
            assertEquals("input.out_dir_unwritable", env.get("error").get("message_key").asText());
            assertFalse(env.toString().contains("internal.unexpected"));
        } finally {
            Files.setPosixFilePermissions(ro, PosixFilePermissions.fromString("rwxr-xr-x"));
        }
    }

    @Test
    void quickstartRaisesOnAPreExistingUnwritableOutDir(@TempDir Path work) throws IOException {
        Path out = work.resolve("out");
        Files.createDirectory(out);
        assumeTrue(Fixtures.makeUnwritable(out), "this user can write to a mode-555 directory");
        try {
            JsonNode env = Fixtures.runJson("quickstart", "--out", out.toString());
            assertEquals(3, env.get("exit_code").asInt());
            assertEquals("input.out_dir_unwritable", env.get("error").get("message_key").asText());
            assertFalse(env.toString().contains("internal.unexpected"));
        } finally {
            Files.setPosixFilePermissions(out, PosixFilePermissions.fromString("rwxr-xr-x"));
        }
    }
}
