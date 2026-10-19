package org.agentce;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.FileSystemException;
import java.nio.file.Files;
import java.nio.file.InvalidPathException;
import java.nio.file.NoSuchFileException;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.nio.file.attribute.BasicFileAttributes;

/** Checked writes to the paths a command's options name (SPEC §8.7 writes only where asked, §8.5 exit
 * 3), ported from Python's {@code outputs.py}. Every output a command writes goes through one role: a
 * target that exists as the wrong type, a path that cannot be reached, or a folder that cannot be
 * written is refused under the role's key before anything is written, and a write that fails anyway
 * is refused under the same key (18.180). Java has no non-blocking open, so a FIFO is refused by its
 * attributes before the open; one put in place between the check and the open can still block. */
final class Outputs {
    static final String NOT_A_FILE = "is not a regular file";
    static final String NOT_A_DIRECTORY = "is not a directory";
    static final String DANGLING = "is a link that points nowhere";
    static final String UNREACHABLE =
            "cannot be reached (a link loop, a folder that cannot be entered, or a name too long)";
    static final String UNWRITABLE = "cannot be written";

    /** One option a command writes to: the key it is refused under, the flag shown, the fix, and
     * whether it names a file or a directory. */
    record Role(String key, String flag, String fix, boolean directory) {
        Role(String key, String flag, String fix) {
            this(key, flag, fix, false);
        }
    }

    /** The {@code --out} directory of conformance run. */
    static final Role OUT_DIR =
            new Role("input.out_dir_unwritable", "--out", "choose a writable --out directory.", true);

    /** A block of writes inside a checked folder. */
    @FunctionalInterface
    interface Writes {
        void run() throws IOException;
    }

    private Outputs() {}

    private static InputError refuse(Role role, String raw, String reason) {
        return new InputError(role.key(), role.flag() + " " + Readiness.pyRepr(raw) + " " + reason + ".", role.fix());
    }

    private static Path path(String raw, Role role) {
        try {
            return Paths.get(raw);
        } catch (InvalidPathException e) {
            throw refuse(role, raw, UNREACHABLE);
        }
    }

    /** Python's {@code Path(raw).parents}, nearest first ("." ends a relative path). */
    private static java.util.List<Path> parents(Path path) {
        java.util.List<Path> out = new java.util.ArrayList<>();
        for (Path parent = path.getParent(); parent != null; parent = parent.getParent()) {
            out.add(parent);
        }
        if (!path.isAbsolute()) {
            out.add(Paths.get("."));
        }
        return out;
    }

    /** A target that does not exist yet: its nearest existing ancestor must be a folder that can be
     * written and entered (missing folders between them are created). */
    private static void checkCreatable(Path path, Role role, String raw) {
        for (Path ancestor : parents(path)) {
            BasicFileAttributes info;
            try {
                info = Files.readAttributes(ancestor, BasicFileAttributes.class);
            } catch (NoSuchFileException e) {
                if (Files.isSymbolicLink(ancestor)) {
                    throw refuse(role, raw, UNREACHABLE);
                }
                continue;
            } catch (IOException e) {
                throw refuse(role, raw, UNREACHABLE);
            }
            if (!info.isDirectory()) {
                throw refuse(role, raw, UNREACHABLE);
            }
            if (!Files.isWritable(ancestor) || !Files.isExecutable(ancestor)) {
                throw refuse(role, raw, UNWRITABLE);
            }
            return;
        }
    }

    /** Refuse {@code raw} under the role's key unless it can be written: an existing target of the
     * role's type that can be written, or a new one whose nearest existing folder can be. Nothing is
     * opened. */
    static Path check(String raw, Role role) {
        Path path = path(raw, role);
        BasicFileAttributes info;
        try {
            info = Files.readAttributes(path, BasicFileAttributes.class);
        } catch (NoSuchFileException e) {
            if (Files.isSymbolicLink(path)) {
                throw refuse(role, raw, DANGLING);
            }
            checkCreatable(path, role, raw);
            return path;
        } catch (IOException e) {
            throw refuse(role, raw, UNREACHABLE);
        }
        if (role.directory()) {
            if (!info.isDirectory()) {
                throw refuse(role, raw, NOT_A_DIRECTORY);
            }
            if (!Files.isWritable(path) || !Files.isExecutable(path)) {
                throw refuse(role, raw, UNWRITABLE);
            }
        } else {
            if (!info.isRegularFile()) {
                throw refuse(role, raw, NOT_A_FILE);
            }
            if (!Files.isWritable(path)) {
                throw refuse(role, raw, UNWRITABLE);
            }
        }
        return path;
    }

    /** Write {@code data} to {@code path} after checking that it is absent or a regular file, so an
     * existing FIFO is never opened. Throws the I/O error; callers key it under their role. */
    static void writeFile(Path path, String data) throws IOException {
        try {
            if (!Files.readAttributes(path, BasicFileAttributes.class).isRegularFile()) {
                throw new FileSystemException(path.toString(), null, NOT_A_FILE);
            }
        } catch (NoSuchFileException e) {
            // absent: created by the write below
        }
        Files.write(path, data.getBytes(StandardCharsets.UTF_8));
    }

    /** Check {@code raw} ({@link #check}), create its missing folders and write {@code data} to it
     * ({@link #writeFile}). */
    static Path write(String raw, Role role, String data) {
        Path path = check(raw, role);
        try {
            Path parent = path.toAbsolutePath().getParent();
            if (parent != null) {
                Files.createDirectories(parent);
            }
            writeFile(path, data);
        } catch (FileSystemException e) {
            throw refuse(role, raw, NOT_A_FILE.equals(e.getReason()) ? NOT_A_FILE : UNWRITABLE);
        } catch (IOException e) {
            throw refuse(role, raw, UNWRITABLE);
        }
        return path;
    }

    /** Check the directory {@code raw} names and refuse any write inside {@code writes} that fails
     * anyway; files inside it are written with {@link #writeFile}, so a FIFO at a fixed name is refused
     * too. */
    static void writingInto(String raw, Role role, Writes writes) {
        check(raw, role);
        try {
            writes.run();
        } catch (IOException e) {
            throw refuse(role, raw, UNWRITABLE);
        }
    }
}
