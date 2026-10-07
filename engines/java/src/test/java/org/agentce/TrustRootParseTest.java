package org.agentce;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Map;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/**
 * Trust-root parse parity (18.81): {@link Verify#loadTrustRoot} parses with {@link
 * Verify#readUntrustedJsonFile}, the rule Python's {@code signing.load_trust_root} and TypeScript's
 * {@code loadTrustRoot} share, so a trust root nested past {@code MAX_JSON_DEPTH} gets one fixed text
 * and the three engines accept the same files. Python's {@code tests/test_trust_root_parse.py} and
 * TypeScript's {@code src/trustRootParse.test.ts} pin the same behaviour; {@code
 * VG-TRUST-ROOT-PARSE-PARITY} runs all three.
 */
class TrustRootParseTest {
    private static final String TOO_DEEP = "is not readable JSON: it nests containers more than 1000 levels deep";

    @TempDir
    Path work;

    private String refusal(String name, byte[] raw) throws IOException {
        Path path = work.resolve(name + ".json");
        Files.write(path, raw);
        IllegalArgumentException e = assertThrows(IllegalArgumentException.class, () -> Verify.loadTrustRoot(path));
        return e.getMessage().replace(path.toString(), "<path>");
    }

    private static byte[] ascii(String text) {
        return text.getBytes(StandardCharsets.US_ASCII);
    }

    private static byte[] concat(byte[]... parts) {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        for (byte[] part : parts) {
            out.writeBytes(part);
        }
        return out.toByteArray();
    }

    @Test
    void pastTheLimitIsOneFixedText() throws IOException {
        Map<String, byte[]> cases = Map.of(
                "deep-array-10000", ascii("[".repeat(10000) + "]".repeat(10000)),
                "deep-object-10000", ascii("{\"a\":".repeat(10000) + "1" + "}".repeat(10000)),
                "keys-1001", ascii("{\"keys\":" + "[".repeat(1000) + "]".repeat(1000) + "}"),
                "truncated-1001", ascii("[".repeat(1001)));
        for (Map.Entry<String, byte[]> c : cases.entrySet()) {
            assertEquals("<path> " + TOO_DEEP, refusal(c.getKey(), c.getValue()), c.getKey());
        }
    }

    @Test
    void atTheLimitReachesTheShapeStage() throws IOException {
        byte[] raw = ascii("{\"keys\":" + "[".repeat(999) + "]".repeat(999) + "}");
        assertEquals("<path> is not a usable trust root: keys is not a mapping of key id to key entry",
                refusal("keys-999", raw));
    }

    @Test
    void otherParseRefusalsNameTheFile() throws IOException {
        Map<String, byte[]> cases = Map.of(
                "nan", ascii("{\"keys\":{},\"x\":NaN}"),
                "lone-surrogate", ascii("{\"keys\":{},\"x\":\"\\ud800\"}"),
                "invalid-utf8", concat(ascii("{\"keys\":{},\"x\":\""), new byte[] {(byte) 0xff}, ascii("\"}")),
                "bom", concat(new byte[] {(byte) 0xef, (byte) 0xbb, (byte) 0xbf}, ascii("{\"keys\":{}}")),
                "not-json", ascii("{not json"));
        for (Map.Entry<String, byte[]> c : cases.entrySet()) {
            String text = refusal(c.getKey(), c.getValue());
            assertTrue(text.startsWith("<path> is not readable JSON"), c.getKey() + ": " + text);
            assertFalse(text.contains("not readable JSON: not readable JSON"), c.getKey() + ": " + text);
            assertFalse(text.contains("1000 levels"), c.getKey() + ": " + text);
        }
    }

    @Test
    void aLoneSurrogateHasNoParserSuffix() throws IOException {
        assertEquals("<path> is not readable JSON", refusal("s", ascii("{\"keys\":{},\"x\":\"\\ud800\"}")));
    }
}
