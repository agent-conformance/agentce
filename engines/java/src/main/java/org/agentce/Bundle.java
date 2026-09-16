package org.agentce;

import com.fasterxml.jackson.databind.JsonNode;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;

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
    public final Set<String> sources;
    public final Map<String, String> sourceClasses;
    public final String digest;

    private Bundle(
            Path root,
            JsonNode manifest,
            List<Path> eventFiles,
            Set<String> sources,
            Map<String, String> sourceClasses,
            String digest) {
        this.root = root;
        this.manifest = manifest;
        this.eventFiles = eventFiles;
        this.sources = sources;
        this.sourceClasses = sourceClasses;
        this.digest = digest;
    }

    private static String fileSha256Hex(Path path) {
        try {
            return Canonical.sha256Hex(Files.readAllBytes(path));
        } catch (IOException e) {
            throw new IllegalStateException("cannot read " + path + ": " + e.getMessage(), e);
        }
    }

    private static String normaliseDigest(String raw) {
        return raw.startsWith("sha256:")
                ? raw.substring("sha256:".length()).toLowerCase(Locale.ROOT)
                : raw.toLowerCase(Locale.ROOT);
    }

    private static Path safeMember(Path root, String rel) {
        if (rel.startsWith("/") || Arrays.asList(rel.split("/")).contains("..")) {
            throw new InputError(
                    "input.bundle_manifest_path",
                    "manifest lists an unsafe path " + Json.quote(rel) + ".",
                    "the manifest must list only paths inside the bundle.");
        }
        return root.resolve(rel);
    }

    public static Bundle load(Path bundleDir) {
        Path manifestPath = bundleDir.resolve("manifest.json");
        if (!Files.isRegularFile(manifestPath)) {
            throw new InputError(
                    "input.bundle_manifest_missing",
                    "the bundle at " + Json.quote(bundleDir.toString()) + " has no manifest.json.",
                    "add a manifest.json listing every file with its SHA-256.");
        }
        JsonNode manifest;
        try {
            manifest = Json.parseFile(manifestPath);
        } catch (RuntimeException exc) {
            throw new InputError(
                    "input.bundle_manifest_invalid",
                    "manifest.json is not valid JSON: " + exc.getMessage() + ".",
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
        for (JsonNode entry : files) {
            if (!entry.isObject() || !entry.has("path") || !entry.has("sha256")) {
                throw new InputError(
                        "input.bundle_manifest_entry",
                        "a 'files' entry is missing 'path' or 'sha256'.",
                        "each entry needs {\"path\": ..., \"sha256\": ...}.");
            }
            String rel = entry.get("path").asText();
            Path member = safeMember(bundleDir, rel);
            if (!Files.isRegularFile(member)) {
                throw new InputError(
                        "input.bundle_manifest_mismatch",
                        "manifest lists " + Json.quote(rel) + ", which is missing from the bundle.",
                        "regenerate the bundle so its files match the manifest.");
            }
            if (!fileSha256Hex(member).equals(normaliseDigest(entry.get("sha256").asText()))) {
                throw new InputError(
                        "input.bundle_manifest_mismatch",
                        Json.quote(rel) + " does not match its manifest SHA-256.",
                        "regenerate the bundle so its files match the manifest.");
            }
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
                if (!item.isObject() || !item.has("id")) {
                    continue;
                }
                String sourceId = item.get("id").asText();
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
                sources,
                sourceClasses.isEmpty() ? null : sourceClasses,
                "sha256:" + Canonical.sha256Hex(manifest));
    }
}
