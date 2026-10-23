package org.agentce;

import com.fasterxml.jackson.databind.DeserializationFeature;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.dataformat.yaml.YAMLFactory;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;

/**
 * YAML loading as a {@link JsonNode}, for the inputs not yet read through {@link Readiness#composePyyaml} (catalog
 * and domain-binding values are read from that tree).
 *
 * <p>SnakeYAML (via Jackson's {@code YAMLFactory}) parses YAML 1.1, the same version PyYAML uses in the
 * reference, so integers, booleans, and strings type identically.
 */
public final class Yaml {
    private Yaml() {}

    private static final ObjectMapper MAPPER = new ObjectMapper(new YAMLFactory())
            .enable(DeserializationFeature.USE_BIG_INTEGER_FOR_INTS)
            .enable(DeserializationFeature.USE_BIG_DECIMAL_FOR_FLOATS);

    /** Parse YAML text read from {@code shown} to a {@link JsonNode}; text that does not parse throws as
     * {@link #parseFile} does. Returns {@code null} for empty input. */
    private static JsonNode parse(String text, Path shown) {
        try {
            return Json.readTreeWith(MAPPER, text);
        } catch (IOException e) {
            throw new IllegalArgumentException("cannot read YAML " + shown + ": " + e.getMessage(), e);
        }
    }

    /** Parse a YAML file to a {@link JsonNode}; a missing file throws. Returns {@code null} for empty input. */
    public static JsonNode parseFile(Path path) {
        String text;
        try {
            text = Files.readString(path, StandardCharsets.UTF_8);
        } catch (IOException e) {
            throw new IllegalArgumentException("cannot read YAML " + path + ": " + e.getMessage(), e);
        }
        return parse(text, path);
    }
}
