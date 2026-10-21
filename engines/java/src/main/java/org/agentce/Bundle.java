package org.agentce;

import com.fasterxml.jackson.databind.JsonNode;
import java.io.IOException;
import java.nio.channels.Channels;
import java.nio.channels.SeekableByteChannel;
import java.nio.file.AccessDeniedException;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.nio.file.attribute.BasicFileAttributes;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Objects;
import java.util.Set;
import java.util.function.BiConsumer;

/**
 * Evidence bundle loading and manifest verification (SPEC §8.1, §5.4).
 *
 * <p>A bundle is a directory with {@code events/*.jsonl}, {@code attestations/}, {@code reference/},
 * and a {@code manifest.json} listing every file with its SHA-256. Loading verifies the manifest is
 * present and every listed file hashes correctly; a missing or mismatching manifest aborts with an
 * {@link InputError}.
 */
public final class Bundle {
    public final Path root;
    public final JsonNode manifest;
    public final List<Path> eventFiles;
    /**
     * The bytes of each listed member (keyed by its path under {@code root}), exactly as {@link #load} checked and
     * hashed them: ingest uses these, never a second read of the same name (18.184f).
     */
    public final Map<Path, byte[]> contents;
    /** The same bytes keyed by each listed path as the manifest writes it (the coverage denominator's lookup). */
    public final Map<String, byte[]> members;
    public final Set<String> sources;
    public final Map<String, String> sourceClasses;
    private String digestCache;

    private Bundle(
            Path root,
            JsonNode manifest,
            List<Path> eventFiles,
            Map<Path, byte[]> contents,
            Map<String, byte[]> members,
            Set<String> sources,
            Map<String, String> sourceClasses) {
        this.root = root;
        this.manifest = manifest;
        this.eventFiles = eventFiles;
        this.contents = contents;
        this.members = members;
        this.sources = sources;
        this.sourceClasses = sourceClasses;
    }

    /**
     * The bundle digest: SHA-256 of the RFC 8785 canonical manifest (SPEC §8.1). Computed on first
     * use, not at load time: {@code verify --bundle} never reads it, and canonicalizing the manifest
     * throws on a float anywhere in it (Python's {@code Bundle.digest} is a lazy {@code @property}
     * for the same reason -- 18.65).
     */
    public String digest() {
        if (digestCache == null) {
            digestCache = "sha256:" + Canonical.sha256Hex(manifest);
        }
        return digestCache;
    }

    private static InputError bundleUnreadable(Path bundleDir, String rel) {
        return InputError.unreadable(
                "input.bundle_unreadable", "the evidence bundle", bundleDir, rel, "evidence bundle");
    }

    /** True when {@code path} cannot even be looked at because of a permission error (an unreadable
     * parent folder), as Python's {@code permission_denied} (18.68). */
    static boolean permissionDenied(Path path) {
        try {
            Files.readAttributes(path, BasicFileAttributes.class);
        } catch (AccessDeniedException e) {
            return true;
        } catch (IOException | RuntimeException e) {
            return false;
        }
        return false;
    }

    /** True when {@code path} is there but its bytes cannot be read for lack of permission. */
    static boolean cannotOpen(Path path) {
        try {
            Files.newInputStream(path).close();
            return false;
        } catch (AccessDeniedException e) {
            return true;
        } catch (IOException | RuntimeException e) {
            return false;
        }
    }

    private static String normaliseDigest(String raw) {
        return raw.startsWith("sha256:")
                ? raw.substring("sha256:".length()).toLowerCase(Locale.ROOT)
                : raw.toLowerCase(Locale.ROOT);
    }

    /**
     * {@link Files#isRegularFile} that never propagates: an OS-level hazard a confined path can still
     * hit at access time (a symlink loop, a name too long for the filesystem, an unreadable parent) is
     * treated as "not present", never left to surface as an unexpected error.
     */
    static boolean safeIsFile(Path path) {
        try {
            return Files.isRegularFile(path);
        } catch (RuntimeException e) {
            return false;
        }
    }

    /**
     * Resolve {@code rel} against {@code root}; return the path only if it stays inside {@code root}
     * after symlinks resolve, else {@code null}.
     *
     * <p>Every bundle-adjacent reference an adversarial evidence bundle can carry -- the primary
     * manifest's own file list, a coverage denominator's manifest, an integrity block's {@code
     * sig_ref} -- is confined through this one function, so a symlink escape, a literal {@code
     * ..}/absolute path, a symlink loop, or an embedded NUL byte are refused the same deliberate way
     * everywhere. A path that cannot be looked at (missing, too long, under a file or an unreadable
     * folder) is not a confinement failure: it is returned unresolved so the caller's own checks
     * report it under their own message key.
     */
    static Path confineToRoot(Path root, String rel) {
        if (rel == null || rel.isEmpty() || rel.startsWith("/") || Arrays.asList(rel.split("/")).contains("..")) {
            return null;
        }
        try {
            Path candidate = root.resolve(rel);
            Path resolvedRoot = root.toRealPath();
            // The member resolves the way Python's does, so a link is judged by where it points even
            // when what it points at cannot be read.
            String resolved = walkLoose(resolvedRoot.toString(), rel, new HashMap<>());
            if (resolved == null) {
                return null;
            }
            Path inside = Path.of(resolved);
            if (!inside.equals(resolvedRoot) && !inside.startsWith(resolvedRoot)
                    // The same folder spelled another way (letter case), judged by identity (18.184l).
                    && !underByIdentity(resolvedRoot, inside)) {
                return null;
            }
            return candidate;
        } catch (IOException | RuntimeException e) {
            return null; // fail closed: anything unresolvable is unsafe.
        }
    }

    /** Whether the already resolved {@code path} is {@code root} or lies under it by folder identity:
     * {@code path} or one of its existing ancestors is the same file as {@code root}; a folder that is
     * not there is never {@code root} (18.184l). */
    static boolean underByIdentity(Path root, Path path) {
        for (Path at = path; at != null; at = at.getParent()) {
            try {
                if (Files.isSameFile(at, root)) {
                    return true;
                }
            } catch (IOException | RuntimeException e) {
                // not there, or cannot be looked at: never the root
            }
        }
        return false;
    }

    /**
     * {@code path} with every symlink it can follow resolved, as Python's {@code
     * Path.resolve(strict=False)} does ({@code posixpath._joinrealpath}): a component that cannot be
     * looked at (missing, not a folder, a name too long, inside a folder that cannot be listed) is
     * kept as written, with the rest of the path, so a link that leaves the root is seen to leave it
     * even when its target cannot be read. {@code null} where Python's {@code resolve()} raises: a
     * symlink loop, or a link that cannot be read (18.68).
     */
    static String resolveLoose(Path path) {
        return walkLoose("/", path.toAbsolutePath().toString(), new HashMap<>());
    }

    /** {@code rest} resolved from the already resolved folder {@code start}, for {@link
     * #resolveLoose}; {@code seen} maps each link to what it resolved to, or null while it is being
     * resolved (a loop). */
    private static String walkLoose(String start, String rest, Map<String, String> seen) {
        String current = rest.startsWith("/") ? "/" : start;
        for (String name : rest.split("/")) {
            if (name.isEmpty() || name.equals(".")) {
                continue;
            }
            if (name.equals("..")) {
                int slash = current.lastIndexOf('/');
                current = slash <= 0 ? "/" : current.substring(0, slash);
                continue;
            }
            String next = current.endsWith("/") ? current + name : current + "/" + name;
            boolean isLink = false;
            try {
                isLink = Files.readAttributes(Path.of(next), BasicFileAttributes.class, LinkOption.NOFOLLOW_LINKS)
                        .isSymbolicLink();
            } catch (IOException e) {
                // kept as written, as Python keeps a component lstat cannot look at
            }
            if (!isLink) {
                current = next;
                continue;
            }
            if (seen.containsKey(next)) {
                String cached = seen.get(next);
                if (cached == null) {
                    return null; // a symlink loop
                }
                current = cached;
                continue;
            }
            seen.put(next, null);
            String inner;
            try {
                inner = walkLoose(current, Files.readSymbolicLink(Path.of(next)).toString(), seen);
            } catch (IOException e) {
                return null;
            }
            if (inner == null) {
                return null;
            }
            seen.put(next, inner);
            current = inner;
        }
        return current;
    }

    /**
     * Test seams for 18.184f: {@code afterConfine} runs once a member's confinement check has passed and before it
     * is opened, {@code afterCheck} once the opened file has been checked and before it is read.
     */
    static BiConsumer<String, Path> afterConfine;
    static BiConsumer<String, Path> afterCheck;

    private static InputError changedWhileRead(String rel) {
        return new InputError(
                "input.bundle_manifest_path",
                rel + " changed while it was being read: the file opened is not the one checked to be inside "
                        + "the bundle root.",
                "keep the bundle folder unchanged while the command reads it, then run the command again.");
    }

    /**
     * The bytes of {@code member}, the path {@link #confineToRoot} returned for {@code rel}, read through one open
     * file of the path it resolves to, and only when that path is still inside {@code root} and still the same file
     * (file key) once the read is done, as Python's {@code read_confined} (18.184f). Java cannot ask which file an
     * open channel names, so it opens the resolved path without following a final link and checks the path again
     * after the read: a member swapped between the confinement check and the read (for a link outside the bundle,
     * or for any other file) is refused with {@code input.bundle_manifest_path} and its bytes are never used. An
     * {@link IOException} from opening or reading ({@link AccessDeniedException} included) propagates.
     */
    static byte[] readConfined(Path root, String rel, Path member) throws IOException {
        if (afterConfine != null) {
            afterConfine.accept(rel, member);
        }
        Path real;
        Object key;
        try {
            real = member.toRealPath();
            BasicFileAttributes attrs =
                    Files.readAttributes(real, BasicFileAttributes.class, LinkOption.NOFOLLOW_LINKS);
            if (!attrs.isRegularFile()
                    || confineToRoot(root, rel) == null
                    || !real.startsWith(root.toRealPath())) {
                throw changedWhileRead(rel);
            }
            key = attrs.fileKey();
        } catch (AccessDeniedException e) {
            throw e;
        } catch (IOException e) {
            throw changedWhileRead(rel);
        }
        byte[] data;
        try (SeekableByteChannel channel =
                Files.newByteChannel(real, StandardOpenOption.READ, LinkOption.NOFOLLOW_LINKS)) {
            if (afterCheck != null) {
                afterCheck.accept(rel, member);
            }
            data = Channels.newInputStream(channel).readAllBytes();
        } catch (AccessDeniedException e) {
            throw e;
        } catch (IOException e) {
            // A final link swapped in before the open (NOFOLLOW_LINKS refuses it) or a file that went away.
            throw changedWhileRead(rel);
        }
        try {
            Object after = Files.readAttributes(real, BasicFileAttributes.class, LinkOption.NOFOLLOW_LINKS).fileKey();
            if (!Objects.equals(after, key) || !member.toRealPath().equals(real) || confineToRoot(root, rel) == null) {
                throw changedWhileRead(rel);
            }
        } catch (IOException e) {
            throw changedWhileRead(rel);
        }
        return data;
    }

    private static Path safeMember(Path root, String rel) {
        Path member = confineToRoot(root, rel);
        if (member == null) {
            throw new InputError(
                    "input.bundle_manifest_path",
                    "manifest lists an unsafe path " + rel + ": it is absolute, contains '..', "
                            + "resolves outside the bundle root (a symlink or junction escapes it), "
                            + "or cannot be safely resolved.",
                    "the manifest must list only paths that stay inside the bundle after symlinks "
                            + "resolve.");
        }
        return member;
    }

    public static Bundle load(Path bundleDir) {
        Path manifestPath = bundleDir.resolve("manifest.json");
        if (!safeIsFile(manifestPath)) {
            if (permissionDenied(manifestPath)) {
                throw bundleUnreadable(bundleDir, "manifest.json");
            }
            throw new InputError(
                    "input.bundle_manifest_missing",
                    "the bundle at " + bundleDir + " has no manifest.json.",
                    "an agent writes a bundle by running with the agentce_emit emitter on: set "
                            + "`AGENTCE_EMIT=1 AGENTCE_EMIT_OUT=<dir>` and see docs/integrate.md; to watch one "
                            + "built, run `examples/custom-loop/run.sh <dir>` from a checkout, then `agentce "
                            + "validate --bundle <dir>`.");
        }
        if (confineToRoot(bundleDir, "manifest.json") == null) {
            throw new InputError(
                    "input.bundle_manifest_path",
                    "manifest.json resolves outside the bundle root (a symlink or junction escapes it), "
                            + "so the bundle's list of files does not travel with the bundle.",
                    "replace the manifest.json link with the manifest file itself, inside the bundle folder.");
        }
        if (cannotOpen(manifestPath)) {
            throw bundleUnreadable(bundleDir, "manifest.json");
        }
        JsonNode manifest;
        try {
            manifest = Verify.parseUntrustedJson(readConfined(bundleDir, "manifest.json", manifestPath));
        } catch (InputError exc) {
            throw exc;
        } catch (IOException | RuntimeException exc) {
            // A fixed message, not the parser's own text: Jackson's exception text would make this
            // refusal's cause diverge from the other two engines for the same malformed input, for no
            // reason a reader could use (F3, 18.65).
            throw new InputError(
                    "input.bundle_manifest_invalid",
                    "manifest.json is not valid JSON.",
                    "regenerate the bundle so its manifest.json is well-formed.");
        }
        if (manifest == null || !manifest.isObject()) {
            throw new InputError(
                    "input.bundle_manifest_invalid",
                    "manifest.json is not an object.",
                    "regenerate the bundle so its manifest.json is well-formed.");
        }

        JsonNode files = manifest.get("files");
        if (files == null || !files.isArray() || files.isEmpty()) {
            throw new InputError(
                    "input.bundle_manifest_files",
                    "manifest.json has no non-empty 'files' array.",
                    "the manifest must list every file with its path and sha256.");
        }

        List<Path> eventFiles = new ArrayList<>();
        Map<Path, byte[]> contents = new HashMap<>();
        Map<String, byte[]> members = new HashMap<>();
        for (JsonNode entry : files) {
            // A non-string path/sha256 folds into the same refusal as a missing one: Jackson's
            // asText(), Python's str() and JS's String() disagree on how to render an array,
            // object or null, which would make the following messages diverge across engines for
            // no reason a reader could use.
            if (!entry.isObject()
                    || !entry.has("path")
                    || !entry.has("sha256")
                    || !entry.get("path").isTextual()
                    || !entry.get("sha256").isTextual()) {
                throw new InputError(
                        "input.bundle_manifest_entry",
                        "a 'files' entry is missing 'path' or 'sha256'.",
                        "each entry needs {\"path\": ..., \"sha256\": ...}.");
            }
            String rel = entry.get("path").asText();
            Path member = safeMember(bundleDir, rel);
            if (!safeIsFile(member)) {
                if (permissionDenied(member)) {
                    throw bundleUnreadable(bundleDir, rel);
                }
                throw new InputError(
                        "input.bundle_manifest_mismatch",
                        "manifest lists " + rel + ", which is missing from the bundle.",
                        "regenerate the bundle so its files match the manifest.");
            }
            if (cannotOpen(member)) {
                throw bundleUnreadable(bundleDir, rel);
            }
            byte[] data;
            try {
                data = readConfined(bundleDir, rel, member);
            } catch (AccessDeniedException e) {
                throw bundleUnreadable(bundleDir, rel);
            } catch (IOException e) {
                throw new IllegalStateException("cannot read " + member + ": " + e.getMessage(), e);
            }
            if (!Canonical.sha256Hex(data).equals(normaliseDigest(entry.get("sha256").asText()))) {
                throw new InputError(
                        "input.bundle_manifest_mismatch",
                        rel + " does not match its manifest SHA-256.",
                        "regenerate the bundle so its files match the manifest.");
            }
            contents.put(member, data);
            members.put(rel, data);
            if (rel.startsWith("events/") && rel.endsWith(".jsonl")) {
                eventFiles.add(member);
            }
        }

        Set<String> sources = null;
        Map<String, String> sourceClasses = new LinkedHashMap<>();
        JsonNode declared = manifest.get("sources");
        if (declared != null && declared.isArray()) {
            Set<String> ids = new LinkedHashSet<>();
            for (JsonNode item : declared) {
                // A non-string `id` (an array, object, number, null) folds into the same "not
                // declared" skip as a missing one, rather than coercing with `asText()`: Jackson's
                // `asText()`, Python's `str()` and TypeScript's `String()` each render a non-string
                // JSON value differently, which would make a tampered source's trust classification
                // diverge across engines for no reason a reader could use (verifier round 1, 18.65).
                JsonNode idNode = item.isObject() ? item.get("id") : null;
                if (idNode == null || !idNode.isTextual()) {
                    continue;
                }
                String sourceId = idNode.asText();
                ids.add(sourceId);
                JsonNode cls = item.get("class");
                if (cls != null && cls.isTextual() && !cls.asText().isEmpty()) {
                    sourceClasses.put(sourceId, cls.asText());
                }
            }
            if (!ids.isEmpty()) {
                sources = ids;
            }
        }

        eventFiles.sort(java.util.Comparator.comparing(Path::toString));
        return new Bundle(
                bundleDir,
                manifest,
                eventFiles,
                contents,
                members,
                sources,
                sourceClasses.isEmpty() ? null : sourceClasses);
    }
}
