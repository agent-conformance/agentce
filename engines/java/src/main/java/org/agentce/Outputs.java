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
 * is refused under the same key (18.180). Java has no non-blocking open, so a file is written to a
 * fresh sibling and moved over its target: no path that can be a FIFO is ever opened. */
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

    /** The open target is not a regular file (a FIFO, a device or a socket). */
    static final class NotRegularFile extends FileSystemException {
        private static final long serialVersionUID = 1L;

        NotRegularFile(Path path) {
            super(path.toString(), null, NOT_A_FILE);
        }
    }

    private static final java.security.SecureRandom RANDOM = new java.security.SecureRandom();

    private Outputs() {}

    static InputError refuse(Role role, String raw, String reason) {
        return new InputError(role.key(), role.flag() + " " + Readiness.pyRepr(raw) + " " + reason + ".", role.fix());
    }

    /** The refusal for a write under {@code role} that failed after its check passed. */
    static InputError unwritable(String raw, Role role) {
        return refuse(role, raw, UNWRITABLE);
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

    /** {@code path} has a {@code ..} right after a folder that does not exist ({@code nosub/../name}).
     * The OS cannot resolve it, so it is refused rather than created, the same in every engine
     * (18.184c). */
    static boolean throughMissingFolder(String raw) {
        try {
            return throughMissingFolder(Paths.get(raw));
        } catch (InvalidPathException e) {
            return false;
        }
    }

    static boolean throughMissingFolder(Path path) {
        for (Path p = path; p != null; p = p.getParent()) {
            Path name = p.getFileName();
            if (name != null && name.toString().equals("..")) {
                Path parent = p.getParent();
                if (!Files.isDirectory(parent != null ? parent : Paths.get("."))) {
                    return true;
                }
            }
        }
        return false;
    }

    /** A target that does not exist yet: its nearest existing ancestor must be a folder that can be
     * written and entered (missing folders between them are created), and the path must not climb out
     * of a folder that does not exist ({@link #throughMissingFolder}). */
    private static void checkCreatable(Path path, Role role, String raw) {
        if (throughMissingFolder(path)) {
            throw refuse(role, raw, UNREACHABLE);
        }
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

    /** Write {@code data} to {@code path} without ever opening a path that can be a FIFO: an existing
     * target must be a regular file that can be written, and the bytes go to a fresh sibling that is
     * then moved over it. Java has no non-blocking open, so this is how a FIFO put in place after the
     * check is replaced rather than waited on. A link to a regular file is followed, as an open would.
     * Throws the I/O error; callers key it under their role. */
    static void writeFile(Path path, byte[] data) throws IOException {
        Path target = path;
        java.util.Set<java.nio.file.attribute.PosixFilePermission> mode = null;
        try {
            if (!Files.readAttributes(path, BasicFileAttributes.class).isRegularFile()) {
                throw new NotRegularFile(path);
            }
            if (!Files.isWritable(path)) {
                throw new java.nio.file.AccessDeniedException(path.toString());
            }
            target = path.toRealPath();
            if (target.getFileSystem().supportedFileAttributeViews().contains("posix")) {
                mode = Files.getPosixFilePermissions(target);
            }
        } catch (NoSuchFileException e) {
            // absent: the move below creates it
        }
        Path parent = target.toAbsolutePath().getParent();
        // A bounded name: a 255-byte target still gets a sibling (18.184c).
        Path sibling = parent.resolve(".agentce-" + Long.toHexString(RANDOM.nextLong() | Long.MIN_VALUE) + ".tmp");
        Files.createFile(sibling);
        try {
            if (mode != null) {
                Files.setPosixFilePermissions(sibling, mode);
            }
            Files.write(sibling, data);
            Files.move(sibling, target, java.nio.file.StandardCopyOption.REPLACE_EXISTING,
                    java.nio.file.StandardCopyOption.ATOMIC_MOVE);
        } catch (IOException e) {
            Files.deleteIfExists(sibling);
            throw e;
        }
    }

    static void writeFile(Path path, String data) throws IOException {
        writeFile(path, data.getBytes(StandardCharsets.UTF_8));
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
        } catch (NotRegularFile e) {
            throw refuse(role, raw, NOT_A_FILE);
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
        } catch (IOException | java.io.UncheckedIOException e) {
            throw refuse(role, raw, UNWRITABLE);
        }
    }
}
