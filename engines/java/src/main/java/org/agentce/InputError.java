package org.agentce;

import java.nio.file.FileSystemException;
import java.nio.file.Path;

/** A missing, unreadable, or malformed input (exit code 3). */
public final class InputError extends AgentceError {
    private static final long serialVersionUID = 1L;

    public InputError(String key, String reason, String fix) {
        super(key, reason, fix, ExitCode.INPUT_ERROR.code);
    }

    /** A target, or a file or folder inside it, that cannot be read: one key per target, naming
     * {@code rel} (the path relative to the target, empty for the target itself), as Python's
     * {@code UnreadableError} (18.68). */
    static InputError unreadable(String key, String what, Path target, String rel, String noun) {
        return new InputError(
                key,
                rel.isEmpty()
                        ? what + " " + target + " cannot be read."
                        : what + " " + target + " holds a file or folder that cannot be read: " + rel + ".",
                "make every file and folder in the " + noun + " readable, then re-run.");
    }

    /** The path a filesystem error (or the one it wraps) names, relative to {@code root} ("" for
     * {@code root} itself or no path), as Python's {@code unreadable_rel} (18.68). */
    static String unreadableRel(Path root, Throwable err) {
        Throwable cause = err instanceof FileSystemException ? err : err.getCause();
        if (!(cause instanceof FileSystemException fse) || fse.getFile() == null) {
            return "";
        }
        Path path = Path.of(fse.getFile());
        if (!path.startsWith(root)) {
            return "";
        }
        return root.relativize(path).toString().replace('\\', '/');
    }
}
