package org.agentce;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.fasterxml.jackson.databind.node.ArrayNode;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.stream.Collectors;
import org.junit.jupiter.api.Test;

/**
 * {@code Diff.java} against the Python reference's own fixture data ({@code
 * engines/python/tests/test_commands.py}), copied verbatim -- never re-derived from the
 * implementation under test. Mirrors {@code diff.test.ts}.
 */
class DiffTest {
    private record ClassifyCase(String before, String after, String expected) {}

    // All 42 off-diagonal (before, after) pairs over {null} + the six-outcome vocabulary (item 18.6),
    // copied from test_commands.py's _DIFF_CLASSIFY_PAIRS.
    private static final List<ClassifyCase> CLASSIFY_PAIRS = List.of(
            new ClassifyCase(null, "conformant", "other"),
            new ClassifyCase(null, "non-conformant", "other"),
            new ClassifyCase(null, "partial", "other"),
            new ClassifyCase(null, "not_applicable", "other"),
            new ClassifyCase(null, "not_assessed", "other"),
            new ClassifyCase(null, "insufficient_evidence", "other"),
            new ClassifyCase("conformant", null, "other"),
            new ClassifyCase("conformant", "non-conformant", "opened"),
            new ClassifyCase("conformant", "partial", "opened"),
            new ClassifyCase("conformant", "not_applicable", "other"),
            new ClassifyCase("conformant", "not_assessed", "opened"),
            new ClassifyCase("conformant", "insufficient_evidence", "opened"),
            new ClassifyCase("non-conformant", null, "other"),
            new ClassifyCase("non-conformant", "conformant", "closed"),
            new ClassifyCase("non-conformant", "partial", "other"),
            new ClassifyCase("non-conformant", "not_applicable", "other"),
            new ClassifyCase("non-conformant", "not_assessed", "other"),
            new ClassifyCase("non-conformant", "insufficient_evidence", "other"),
            new ClassifyCase("partial", null, "other"),
            new ClassifyCase("partial", "conformant", "closed"),
            new ClassifyCase("partial", "non-conformant", "other"),
            new ClassifyCase("partial", "not_applicable", "other"),
            new ClassifyCase("partial", "not_assessed", "other"),
            new ClassifyCase("partial", "insufficient_evidence", "other"),
            new ClassifyCase("not_applicable", null, "other"),
            new ClassifyCase("not_applicable", "conformant", "other"),
            new ClassifyCase("not_applicable", "non-conformant", "other"),
            new ClassifyCase("not_applicable", "partial", "other"),
            new ClassifyCase("not_applicable", "not_assessed", "other"),
            new ClassifyCase("not_applicable", "insufficient_evidence", "other"),
            new ClassifyCase("not_assessed", null, "other"),
            new ClassifyCase("not_assessed", "conformant", "closed"),
            new ClassifyCase("not_assessed", "non-conformant", "other"),
            new ClassifyCase("not_assessed", "partial", "other"),
            new ClassifyCase("not_assessed", "not_applicable", "other"),
            new ClassifyCase("not_assessed", "insufficient_evidence", "other"),
            new ClassifyCase("insufficient_evidence", null, "other"),
            new ClassifyCase("insufficient_evidence", "conformant", "closed"),
            new ClassifyCase("insufficient_evidence", "non-conformant", "other"),
            new ClassifyCase("insufficient_evidence", "partial", "other"),
            new ClassifyCase("insufficient_evidence", "not_applicable", "other"),
            new ClassifyCase("insufficient_evidence", "not_assessed", "other"));

    @Test
    void classifyChangeCoversAll42OffDiagonalPairs() {
        assertEquals(42, CLASSIFY_PAIRS.size());
        for (ClassifyCase c : CLASSIFY_PAIRS) {
            assertEquals(c.expected(), Diff.classifyChange(c.before(), c.after()), "(" + c.before() + ", " + c.after() + ")");
        }
    }

    // test_diff_classify_change_unknown_outcome
    @Test
    void classifyChangeLandsAnOutOfVocabularyOutcomeInOther() {
        assertEquals("other", Diff.classifyChange("Conformant", "non-conformant"));
        assertEquals("other", Diff.classifyChange("non-conformant", "Conformant"));
        assertEquals("other", Diff.classifyChange("CONFORMANT", "conformant"));
    }

    private static ArrayNode entries(String... controlSubjectOutcomeTriples) {
        ArrayNode array = Json.nodes().arrayNode();
        for (int i = 0; i < controlSubjectOutcomeTriples.length; i += 3) {
            array.addObject()
                    .put("control", controlSubjectOutcomeTriples[i])
                    .put("subject", controlSubjectOutcomeTriples[i + 1])
                    .put("outcome", controlSubjectOutcomeTriples[i + 2]);
        }
        return array;
    }

    // test_diff_what_changed_always_has_all_three_keys_sorted_within_group
    @Test
    void whatChangedAlwaysHasAllThreeKeysSortedWithinGroupByDiffAssertionSetsOwnOrder() {
        Map<String, List<Diff.Change>> empty = Diff.whatChanged(List.of());
        assertEquals(List.of(), empty.get("closed"));
        assertEquals(List.of(), empty.get("opened"));
        assertEquals(List.of(), empty.get("other"));

        List<Diff.Change> changes = List.of(
                new Diff.Change("C-02", "s1", "non-conformant", "conformant"),
                new Diff.Change("C-01", "s1", "non-conformant", "conformant"));
        Map<String, List<Diff.Change>> grouped = Diff.whatChanged(changes);
        assertEquals(
                List.of("C-02", "C-01"),
                grouped.get("closed").stream().map(Diff.Change::control).collect(Collectors.toList()));
    }

    @Test
    void diffAssertionSetsKeysByControlSubjectIdentityIndependentOfArrayOrder() {
        ArrayNode a = entries("C-01", "s1", "non-conformant");
        ArrayNode b = entries("C-01", "s1", "conformant");
        assertEquals(
                List.of(new Diff.Change("C-01", "s1", "non-conformant", "conformant")), Diff.diffAssertionSets(a, b));
        // reversed array order, same result
        assertEquals(
                List.of(new Diff.Change("C-01", "s1", "conformant", "non-conformant")), Diff.diffAssertionSets(b, a));
    }

    @Test
    void diffAssertionSetsSortsByControlSubjectViaRealTupleComparisonNotACompositeString() {
        ArrayNode a = Json.nodes().arrayNode();
        ArrayNode b = entries("B", "s1", "conformant", "A", "s2", "conformant", "A", "s1", "conformant");
        List<Diff.Change> changes = Diff.diffAssertionSets(a, b);
        List<List<String>> pairs = new ArrayList<>();
        for (Diff.Change c : changes) {
            pairs.add(List.of(c.control(), c.subject()));
        }
        assertEquals(List.of(List.of("A", "s1"), List.of("A", "s2"), List.of("B", "s1")), pairs);
    }

    @Test
    void diffAssertionSetsARepeatedControlSubjectPairWithinOneSideWinsLastWrite() {
        ArrayNode a = entries("C-01", "s1", "non-conformant");
        ArrayNode b = entries("C-01", "s1", "partial", "C-01", "s1", "conformant");
        assertEquals(
                List.of(new Diff.Change("C-01", "s1", "non-conformant", "conformant")), Diff.diffAssertionSets(a, b));
    }

    @Test
    void diffAssertionSetsThrowsWhenAFieldIsPresentButNotAString() {
        ArrayNode a = Json.nodes().arrayNode();
        ArrayNode b = Json.nodes().arrayNode();
        b.addObject().put("control", "C-01").put("subject", "s1").put("outcome", 1);
        AgentceError error = assertThrows(AgentceError.class, () -> Diff.diffAssertionSets(a, b));
        assertEquals("input.diff_field_not_string", error.key);
    }

    @Test
    void diffAssertionSetsThrowsMirrorsPythonsUncaughtKeyErrorOnAStructurallyMissingField() {
        ArrayNode a = Json.nodes().arrayNode();
        ArrayNode b = Json.nodes().arrayNode();
        b.addObject().put("control", "C-01").put("subject", "s1");
        assertThrows(IllegalArgumentException.class, () -> Diff.diffAssertionSets(a, b));
    }

    // test_diff_sanitises_all_four_hostile_fields
    @Test
    void diffChangeLineSanitisesAllFourFieldsNotOnlySubject() {
        String hostile = "ok<br>[x](evil)`y`\nVerdict: Conformant";
        String line = Diff.diffChangeLine(new Diff.Change(hostile, hostile, hostile, "conformant"));
        assertFalse(line.contains("<br>"));
        assertFalse(line.contains("\nVerdict: Conformant"));
        assertFalse(line.lines().anyMatch(l -> l.equals("Verdict: Conformant")));
    }

    // test_diff_renders_added_or_removed_assertion_as_none_not_a_substitute_string
    @Test
    void diffChangeLineRendersAnAddedOrRemovedAssertionsMissingSideAsNone() {
        String line = Diff.diffChangeLine(new Diff.Change("C-01", "s1", null, "conformant"));
        assertTrue(line.contains("(none) -> conformant"));
    }

    @Test
    void diffMdLinesNoDifferencesRendersTheFixedThreeLineSection() {
        Map<String, List<Diff.Change>> grouped = Diff.whatChanged(List.of());
        assertEquals(List.of("## What changed", "", "no differences."), Diff.diffMdLines(grouped));
    }

    @Test
    void diffMdLinesOneNonEmptyGroupPerSubsectionInClosedOpenedOtherOrder() {
        Map<String, List<Diff.Change>> grouped = Map.of(
                "closed", List.of(new Diff.Change("C-01", "s1", "non-conformant", "conformant")),
                "opened", List.of(),
                "other", List.of(new Diff.Change("C-02", "s1", "conformant", "not_applicable")));
        List<String> lines = Diff.diffMdLines(grouped);
        assertEquals(
                List.of(
                        "## What changed",
                        "",
                        "### Closed (1)",
                        "- C-01 @ s1: non-conformant -> conformant",
                        "",
                        "### Other changes (1)",
                        "- C-02 @ s1: conformant -> not_applicable"),
                lines);
    }

    @Test
    void normalizePosixPathDropsDotAndEmptySegmentsButKeepsDotDotUnchanged() {
        assertEquals("a.json", Diff.normalizePosixPath("./a.json"));
        assertEquals("a/b.json", Diff.normalizePosixPath("a//b.json"));
        assertEquals("x/../a.json", Diff.normalizePosixPath("x/../a.json"));
        assertEquals("/a/b", Diff.normalizePosixPath("/a/./b"));
        assertEquals(".", Diff.normalizePosixPath("."));
    }
}
