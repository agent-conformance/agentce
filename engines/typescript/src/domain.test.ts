/** Unit tests for the domain binding's task scopes (SPEC §7.7.6), as engines/python/tests/test_domain.py. */

import assert from "node:assert/strict";
import { test } from "node:test";
import { DomainBinding } from "./domain";

test("task scope parsing", () => {
  const binding = DomainBinding.fromDict({
    task_scopes: [
      {
        task_type: "a",
        allowed_tools: ["t", 1],
        allowed_effect_classes: ["read"],
        requires_approval: ["spend", null],
      },
      { task_type: "a", allowed_effect_classes: "read" },
      { allowed_effect_classes: ["read"] },
      "nope",
    ],
  });
  assert.deepEqual([...binding.taskScopes.keys()], ["a"]);
  const [first, second] = binding.taskScopes.get("a") ?? [];
  assert.deepEqual(first.allowedTools, new Set(["t"]));
  assert.deepEqual(first.allowedEffectClasses, new Set(["read"]));
  assert.deepEqual(first.requiresApproval, new Set(["spend"]));
  assert.equal(second.allowedTools, null);
  assert.equal(second.allowedEffectClasses.size, 0);
  assert.ok(first.allows("t", "read") && !first.allows("u", "read"));
  assert.ok(
    !first.allows("t", "write") && !first.allows("t", "spend") && !second.allows("t", "read"),
  );
});

test("task scope not a list declares none", () => {
  assert.equal(DomainBinding.fromDict({ task_scopes: { a: {} } }).taskScopes.size, 0);
  assert.equal(DomainBinding.fromDict({ task_scopes: 5 }).taskScopes.size, 0);
});

test("task scope resolution", () => {
  const one = DomainBinding.fromDict({ task_scopes: [{ task_type: "a" }] });
  const two = DomainBinding.fromDict({ task_scopes: [{ task_type: "a" }, { task_type: "b" }] });
  assert.equal(one.scopesFor("task-1"), one.taskScopes.get("a"));
  assert.equal(two.scopesFor("b"), two.taskScopes.get("b"));
  assert.deepEqual(two.scopesFor("task-1"), []);
  assert.deepEqual(DomainBinding.empty().scopesFor("a"), []);
});

test("an unreadable task scope entry keeps the single type from standing for all", () => {
  for (const bad of [{ "task-type": "2024" }, { task_type: 2024 }, "nope"]) {
    const binding = DomainBinding.fromDict({ task_scopes: [bad, { task_type: "batch" }] });
    assert.ok(binding.taskScopesMalformed);
    assert.deepEqual([...binding.taskScopes.keys()], ["batch"]);
    assert.deepEqual(binding.scopesFor("2024"), []);
    assert.equal(binding.scopesFor("batch"), binding.taskScopes.get("batch"));
  }
  assert.ok(!DomainBinding.fromDict({ task_scopes: [{ task_type: "a" }] }).taskScopesMalformed);
});
