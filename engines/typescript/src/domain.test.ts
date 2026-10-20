/** Unit tests for the domain binding's task scopes (SPEC §7.7.6), as engines/python/tests/test_domain.py. */

import assert from "node:assert/strict";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { DomainBinding, type TaskScope } from "./domain";
import { InputError } from "./errors";

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

test("task scope budgets", () => {
  const binding = DomainBinding.fromDict({
    task_scopes: [
      { task_type: "a", budgets: { spend: 0, tokens: 10, cost: 3, compute_seconds: "60" } },
      { task_type: "b", budgets: [1] },
      { task_type: "c", budgets: { tokens: true, spend: -1 } },
      { task_type: "d" },
    ],
  });
  const of = (t: string) => binding.taskScopes.get(t)?.[0]?.budgets;
  assert.deepEqual(of("a"), [
    ["tokens", 10],
    ["compute_seconds", null],
    ["spend", 0],
  ]);
  assert.deepEqual(of("b"), [
    ["tokens", null],
    ["compute_seconds", null],
    ["spend", null],
  ]);
  assert.deepEqual(of("c"), [
    ["tokens", null],
    ["spend", null],
  ]);
  assert.deepEqual(of("d"), []);
});

function loadText(text: string): DomainBinding {
  const path = join(mkdtempSync(join(tmpdir(), "domain-")), "domain.yaml");
  writeFileSync(path, text, "utf-8");
  return DomainBinding.load(path);
}

function scopeFromYaml(scope: string): TaskScope {
  return loadText(`task_scopes:\n  - {task_type: a, ${scope}}\n`).taskScopes.get(
    "a",
  )?.[0] as TaskScope;
}

test("a non-finite budget limit is unreadable in every dimension (18.184g)", () => {
  for (const dimension of ["spend", "compute_seconds", "tokens"]) {
    for (const value of [".inf", "-.inf", ".nan"]) {
      assert.deepEqual(scopeFromYaml(`budgets: {${dimension}: ${value}}`).budgets, [
        [dimension, null],
      ]);
    }
  }
});

test("budget limit spellings read as PyYAML types them (18.184g)", () => {
  const cases: [string, number | null][] = [
    ["10", 10],
    ["1_0", 10],
    ["010", 8],
    ["0x10", 16],
    ["1e1", null],
    ['"1e1"', null],
    ["2.5", 2.5],
  ];
  for (const [value, limit] of cases) {
    assert.deepEqual(
      scopeFromYaml(`budgets: {spend: ${value}}`).budgets,
      [["spend", limit]],
      value,
    );
  }
});

test("allowed_tools YAML 1.1 booleans name no tool; quoted they are names (18.184g)", () => {
  assert.deepEqual(scopeFromYaml("allowed_tools: [yes, on, off]").allowedTools, new Set());
  assert.deepEqual(scopeFromYaml('allowed_tools: ["yes"]').allowedTools, new Set(["yes"]));
});

test("a key written twice, at any depth, is refused input.domain_binding_invalid (18.184g)", () => {
  for (const text of [
    'task_scopes:\n  - {task_type: a, allowed_tools: ["x"], allowed_tools: ["y"]}\n',
    "task_scopes:\n  - {task_type: a, budgets: {spend: 1, spend: 2}}\n",
    "task_scopes: []\ntask_scopes: []\n",
  ]) {
    assert.throws(
      () => loadText(text),
      (e: unknown) => e instanceof InputError && e.key === "input.domain_binding_invalid",
    );
  }
});

test("a key a << merge brings in may be written again (18.184g)", () => {
  const binding = loadText(
    "base: &b {task_type: a, allowed_tools: [x]}\ntask_scopes:\n  - {<<: *b, allowed_tools: [y]}\n",
  );
  assert.deepEqual(binding.taskScopes.get("a")?.[0].allowedTools, new Set(["y"]));
});
