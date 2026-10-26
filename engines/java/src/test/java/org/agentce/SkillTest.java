package org.agentce;

import static org.junit.jupiter.api.Assertions.assertEquals;

import com.fasterxml.jackson.databind.JsonNode;
import java.io.IOException;
import java.nio.file.Files;
import java.util.ArrayList;
import java.util.List;
import java.util.TreeSet;
import org.junit.jupiter.api.Test;

/** The skill folder's functions render every case of skill_cases.json as Python does (18.16f). */
class SkillTest {
    private static JsonNode fixture(String name) throws IOException {
        return Json.readBundled(Files.readAllBytes(
                TestPaths.repoRoot().resolve("tools/fixtures/report_formats").resolve(name)));
    }

    @Test
    void everyCaseRendersPythonsText() throws IOException {
        JsonNode expected = fixture("skill_expected.json");
        TreeSet<String> names = new TreeSet<>();
        List<String> mismatches = new ArrayList<>();
        for (JsonNode c : fixture("skill_cases.json").get("cases")) {
            String name = c.get("name").asText();
            names.add(name);
            if (!expected.path(name).asText().equals(Seams.skillCase(c))) {
                mismatches.add(name);
            }
        }
        TreeSet<String> want = new TreeSet<>();
        expected.fieldNames().forEachRemaining(want::add);
        assertEquals(want, names);
        assertEquals(List.of(), mismatches);
    }

    @Test
    void doubleQuotedEscapesAsPyYamlAllowUnicode() {
        assertEquals("\"\"", Skill.doubleQuoted(""));
        assertEquals("\"a\\\"b\\\\c\"", Skill.doubleQuoted("a\"b\\c"));
        assertEquals("\"\\N\\L\\P\\uFEFF\\0\\e\"", Skill.doubleQuoted("\u0085  ﻿\u0000\u001b"));
        assertEquals("\" ퟿�\\uFFFE\\uFFFF\"", Skill.doubleQuoted(" ퟿�￾￿"));
        assertEquals("\"\\U0001F600\\x7F\"", Skill.doubleQuoted("😀\u007f"));
    }

    @Test
    void safeKeepsWhatPythonsIsalnumKeeps() {
        assertEquals("é日本_²Ⅷx", Report.safe("é日本́²Ⅷx"));
        assertEquals("𝐀𐍈_", Report.safe("𝐀𐍈😀"));
    }
}
