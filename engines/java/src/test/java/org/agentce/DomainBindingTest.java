package org.agentce;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertSame;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.util.List;
import java.util.Set;
import org.junit.jupiter.api.Test;

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
}
