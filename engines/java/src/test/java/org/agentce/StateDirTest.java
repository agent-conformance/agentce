package org.agentce;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assumptions.assumeTrue;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.attribute.PosixFilePermissions;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/**
 * An unwritable {@code --state} directory raises the keyed {@code input.state_dir_unwritable} error
 * (loophole L18.6), not an internal one -- both at the early {@link StateDir#ensureWritable} probe and
 * at {@link StateDir#save()} itself, and for a directory that already exists but lost its write bit
 * after creation (not only a missing parent).
 */
class StateDirTest {
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
}
