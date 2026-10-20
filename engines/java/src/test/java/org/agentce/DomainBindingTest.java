package org.agentce;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertSame;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Arrays;
import java.util.List;
import java.util.Set;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/** The domain binding's task scopes (SPEC §7.7.6), as engines/python/tests/test_domain.py. */
class DomainBindingTest {

    @Test
    void taskScopeParsing() {
        DomainBinding binding = DomainBinding.fromDict(Json.parse("{\"task_scopes\":["
                + "{\"task_type\":\"a\",\"allowed_tools\":[\"t\",1],\"allowed_effect_classes\":[\"read\"],"
                + "\"requires_approval\":[\"spend\",null]},"
                + "{\"task_type\":\"a\",\"allowed_effect_classes\":\"read\"},"
                + "{\"allowed_effect_classes\":[\"read\"]},\"nope\"]}"));
        assertEquals(Set.of("a"), binding.taskScopes.keySet());
        DomainBinding.TaskScope first = binding.taskScopes.get("a").get(0);
        DomainBinding.TaskScope second = binding.taskScopes.get("a").get(1);
        assertEquals(Set.of("t"), first.allowedTools());
        assertEquals(Set.of("read"), first.allowedEffectClasses());
        assertEquals(Set.of("spend"), first.requiresApproval());
        assertNull(second.allowedTools());
        assertTrue(second.allowedEffectClasses().isEmpty());
        assertTrue(first.allows("t", "read") && !first.allows("u", "read"));
        assertFalse(first.allows("t", "write") || first.allows("t", "spend") || second.allows("t", "read"));
    }

    @Test
    void taskScopeNotAListDeclaresNone() {
        assertTrue(DomainBinding.fromDict(Json.parse("{\"task_scopes\":{\"a\":{}}}")).taskScopes.isEmpty());
        assertTrue(DomainBinding.fromDict(Json.parse("{\"task_scopes\":5}")).taskScopes.isEmpty());
    }

    @Test
    void taskScopeResolution() {
        DomainBinding one = DomainBinding.fromDict(Json.parse("{\"task_scopes\":[{\"task_type\":\"a\"}]}"));
        DomainBinding two = DomainBinding.fromDict(
                Json.parse("{\"task_scopes\":[{\"task_type\":\"a\"},{\"task_type\":\"b\"}]}"));
        assertSame(one.taskScopes.get("a"), one.scopesFor("task-1"));
        assertSame(two.taskScopes.get("b"), two.scopesFor("b"));
        assertEquals(List.of(), two.scopesFor("task-1"));
        assertEquals(List.of(), DomainBinding.empty().scopesFor("a"));
    }

    @Test
    void unreadableTaskScopeEntryKeepsTheSingleTypeFromStandingForAll() {
        for (String bad : List.of("{\"task-type\":\"2024\"}", "{\"task_type\":2024}", "\"nope\"")) {
            DomainBinding binding = DomainBinding.fromDict(
                    Json.parse("{\"task_scopes\":[" + bad + ",{\"task_type\":\"batch\"}]}"));
            assertTrue(binding.taskScopesMalformed);
            assertEquals(Set.of("batch"), binding.taskScopes.keySet());
            assertEquals(List.of(), binding.scopesFor("2024"));
            assertSame(binding.taskScopes.get("batch"), binding.scopesFor("batch"));
        }
        assertFalse(DomainBinding.fromDict(
                Json.parse("{\"task_scopes\":[{\"task_type\":\"a\"}]}")).taskScopesMalformed);
    }

    @Test
    void budgetsOfTaskScope() {
        DomainBinding binding = DomainBinding.fromDict(Json.parse("{\"task_scopes\":["
                + "{\"task_type\":\"a\",\"budgets\":{\"spend\":0,\"tokens\":10,\"cost\":3,\"compute_seconds\":\"60\"}},"
                + "{\"task_type\":\"b\",\"budgets\":[1]},"
                + "{\"task_type\":\"c\",\"budgets\":{\"tokens\":true,\"spend\":-1}},"
                + "{\"task_type\":\"d\"}]}"));
        assertEquals(
                List.of(
                        new DomainBinding.Budget("tokens", 10.0),
                        new DomainBinding.Budget("compute_seconds", null),
                        new DomainBinding.Budget("spend", 0.0)),
                binding.taskScopes.get("a").get(0).budgets());
        assertEquals(
                List.of(
                        new DomainBinding.Budget("tokens", null),
                        new DomainBinding.Budget("compute_seconds", null),
                        new DomainBinding.Budget("spend", null)),
                binding.taskScopes.get("b").get(0).budgets());
        assertEquals(
                List.of(new DomainBinding.Budget("tokens", null), new DomainBinding.Budget("spend", null)),
                binding.taskScopes.get("c").get(0).budgets());
        assertEquals(List.of(), binding.taskScopes.get("d").get(0).budgets());
    }

    @TempDir
    Path tempDir;

    private DomainBinding loadText(String text) throws IOException {
        Path path = Files.createTempFile(tempDir, "domain-", ".yaml");
        Files.writeString(path, text);
        return DomainBinding.load(path);
    }

    private DomainBinding.TaskScope scopeFromYaml(String scope) throws IOException {
        return loadText("task_scopes:\n  - {task_type: a, " + scope + "}\n").taskScopes.get("a").get(0);
    }

    @Test
    void nonFiniteBudgetLimitIsUnreadableInEveryDimension() throws IOException {
        for (String dimension : List.of("spend", "compute_seconds", "tokens")) {
            for (String value : List.of(".inf", "-.inf", ".nan")) {
                assertEquals(
                        List.of(new DomainBinding.Budget(dimension, null)),
                        scopeFromYaml("budgets: {" + dimension + ": " + value + "}").budgets(),
                        value);
            }
        }
    }

    @Test
    void budgetLimitSpellingsReadAsPyyamlTypesThem() throws IOException {
        Object[][] cases = {
            {"10", 10.0}, {"1_0", 10.0}, {"010", 8.0}, {"0x10", 16.0}, {"1e1", null}, {"\"1e1\"", null}, {"2.5", 2.5},
        };
        for (Object[] c : cases) {
            assertEquals(
                    List.of(new DomainBinding.Budget("spend", (Double) c[1])),
                    scopeFromYaml("budgets: {spend: " + c[0] + "}").budgets(),
                    Arrays.toString(c));
        }
    }

    @Test
    void allowedToolsYaml11BooleansNameNoTool() throws IOException {
        assertEquals(Set.of(), scopeFromYaml("allowed_tools: [yes, on, off]").allowedTools());
        assertEquals(Set.of("yes"), scopeFromYaml("allowed_tools: [\"yes\"]").allowedTools());
    }

    @Test
    void keyWrittenTwiceOrPythonTagIsRefused() {
        for (String text : List.of(
                "task_scopes:\n  - {task_type: a, allowed_tools: [\"x\"], allowed_tools: [\"y\"]}\n",
                "task_scopes:\n  - {task_type: a, budgets: {spend: 1, spend: 2}}\n",
                "task_scopes: []\ntask_scopes: []\n",
                "task_scopes: !!python/object:os.system {}\n")) {
            InputError e = assertThrows(InputError.class, () -> loadText(text), text);
            assertEquals("input.domain_binding_invalid", e.key);
        }
    }

    @Test
    void keyAMergeBringsInMayBeWrittenAgain() throws IOException {
        DomainBinding binding = loadText(
                "base: &b {task_type: a, allowed_tools: [x]}\ntask_scopes:\n  - {<<: *b, allowed_tools: [y]}\n");
        assertEquals(Set.of("y"), binding.taskScopes.get("a").get(0).allowedTools());
    }

    @Test
    void yamlDatesReadAsPyyamlDatesNeitherTextNorNumber() throws IOException {
        DomainBinding top = loadText("reviewed: 2026-01-01\ntask_scopes:\n  - {task_type: a, allowed_tools: [x]}\n");
        assertEquals(Set.of("x"), top.taskScopes.get("a").get(0).allowedTools());
        assertEquals(Set.of("x"), scopeFromYaml("allowed_tools: [2026-01-01, x]").allowedTools());
        assertEquals(
                List.of(new DomainBinding.Budget("tokens", null), new DomainBinding.Budget("spend", null)),
                scopeFromYaml("budgets: {tokens: 2020-01-01, spend: 2026-01-01T10:00:00}").budgets());
        assertEquals(
                List.of(
                        new DomainBinding.Budget("tokens", null),
                        new DomainBinding.Budget("compute_seconds", null),
                        new DomainBinding.Budget("spend", null)),
                scopeFromYaml("budgets: 2026-01-01").budgets());
        DomainBinding taskType = loadText("task_scopes:\n  - {task_type: 2026-01-01}\n  - 2026-01-01\n");
        assertTrue(taskType.taskScopes.isEmpty() && taskType.taskScopesMalformed);
        assertTrue(loadText("2026-01-01\n").taskScopes.isEmpty());
        DomainBinding ids = loadText("decision_types:\n  - {id: 2026-01-01, subclass_of: 2026-01-02T03:04:05Z,"
                + " required_oversight_modality: 2026-01-03}\ncontext_classes:\n  - {id: 2026-01-01 10:00:00}\n");
        assertEquals("2026-01-02 03:04:05+00:00", ids.subclasses.get("2026-01-01"));
        assertEquals(DomainBinding.CONTEXT_ROOT, ids.subclasses.get("2026-01-01 10:00:00"));
        assertTrue(ids.requiredOversight.isEmpty());
    }

    @Test
    void limitPastFloatRangeIsUnreadable() throws IOException {
        String huge = "9".repeat(400);
        assertEquals(
                List.of(new DomainBinding.Budget("compute_seconds", null), new DomainBinding.Budget("spend", null)),
                scopeFromYaml("budgets: {spend: " + huge + ", compute_seconds: " + huge + "}").budgets());
    }

    @Test
    void explicitTagsConstructAsSafeLoaderDoes() throws IOException {
        assertEquals(
                Set.of("7", "x", "yes"),
                scopeFromYaml("allowed_tools: [!!bool \"yes\", !!bool \"true\", !!bool 'on', !!int \"7\", !!float \"1.5\","
                                + " !!str 7, !!str yes, x]")
                        .allowedTools());
        assertEquals(
                List.of(
                        new DomainBinding.Budget("tokens", null),
                        new DomainBinding.Budget("compute_seconds", 5.0),
                        new DomainBinding.Budget("spend", 10.0)),
                scopeFromYaml("budgets: {tokens: !!str 1000, spend: !!int \"10\", compute_seconds: !!float \"5\"}").budgets());
        assertEquals(
                List.of(new DomainBinding.Budget("tokens", 10.0), new DomainBinding.Budget("spend", null)),
                scopeFromYaml("budgets: {tokens: !!int \"1_0\", spend: !!float \".inf\"}").budgets());
    }

    @Test
    void taggedTextReadsAsPythonsIntAndFloatDo() throws IOException {
        assertEquals(
                List.of(
                        new DomainBinding.Budget("tokens", 8.0),
                        new DomainBinding.Budget("compute_seconds", 0.5),
                        new DomainBinding.Budget("spend", null)),
                scopeFromYaml("budgets: {tokens: !!int \"0o10\", compute_seconds: !!float \"+.5\", spend: !!float \"inf\"}")
                        .budgets());
        assertEquals(
                List.of(new DomainBinding.Budget("tokens", 5.0)),
                scopeFromYaml("budgets: {tokens: !!int \" 5\"}").budgets());
    }

    @Test
    void aDateNoCalendarHoldsIsText() throws IOException {
        assertEquals(Set.of("2021-02-29"), scopeFromYaml("allowed_tools: [2021-02-29, 2020-02-29]").allowedTools());
        assertEquals(List.of(), scopeFromYaml("budgets: {2020-01-01: 1, 2020-01-02: 2}").budgets());
    }

    @Test
    void aTagTheNodeCannotTakeIsRefused() {
        for (String scope : List.of(
                "allowed_tools: !!str [x]",
                "task_type_x: !!seq x",
                "allowed_tools: [<<]",
                "budgets: {tokens: !!int \"1:\"}",
                "budgets: {spend: !!float \"5f\"}")) {
            InputError e = assertThrows(InputError.class, () -> scopeFromYaml(scope), scope);
            assertEquals("input.domain_binding_invalid", e.key);
        }
    }
}
