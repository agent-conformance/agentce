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
 * YAML loading as a {@link JsonNode}, so catalogs, profiles, and domain bindings share the JSON model.
 *
 * <p>SnakeYAML (via Jackson's {@code YAMLFactory}) parses YAML 1.1, the same version PyYAML uses in the
 * reference, so integers, booleans, and strings type identically.
 */
public final class Yaml {
    private Yaml() {}

    private static final ObjectMapper MAPPER = new ObjectMapper(new YAMLFactory())
            .enable(DeserializationFeature.USE_BIG_INTEGER_FOR_INTS)
            .enable(DeserializationFeature.USE_BIG_DECIMAL_FOR_FLOATS);

    /** Parse a YAML file to a {@link JsonNode}; a missing file throws. Returns {@code null} for empty input. */
    public static JsonNode parseFile(Path path) {
        try {
            return MAPPER.readTree(Files.readString(path, StandardCharsets.UTF_8));
        } catch (IOException e) {
            throw new IllegalArgumentException("cannot read YAML " + path + ": " + e.getMessage(), e);
        }
    }
}
