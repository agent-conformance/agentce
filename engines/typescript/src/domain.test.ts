/** Unit tests for the domain binding's task scopes (SPEC §7.7.6), as engines/python/tests/test_domain.py. */

import assert from "node:assert/strict";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { CONTEXT_ROOT, DomainBinding, type TaskScope } from "./domain";
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

/** `scope` in a task scope refused as input.domain_binding_invalid, outside the SPEC 8.7 YAML profile under `rule`
 * (18.184u). */
function assertOutside(scope: string, rule: string): void {
  assert.throws(
    () => scopeFromYaml(scope),
    (e: unknown) =>
      e instanceof InputError &&
      e.key === "input.domain_binding_invalid" &&
      e.cause.endsWith(` is outside the YAML profile: ${rule} at line 2 (SPEC 8.7).`),
    scope,
  );
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
    ["0x10", 16],
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
  // Outside the YAML profile (18.184u): refused, never read as PyYAML types them.
  assertOutside("budgets: {spend: 1_0}", "underscore_number");
  assertOutside("budgets: {spend: 010}", "octal");
  assertOutside("budgets: {spend: 1e1}", "exponent_no_dot");
});

test("allowed_tools YAML 1.1 booleans are outside the YAML profile; quoted they are names (18.184g, 18.184u)", () => {
  assertOutside("allowed_tools: [yes, on, off]", "yaml11_bool");
  assert.deepEqual(scopeFromYaml('allowed_tools: ["yes"]').allowedTools, new Set(["yes"]));
});

test("a key written twice, at any depth, or a Python tag is refused input.domain_binding_invalid (18.184g)", () => {
  for (const text of [
    'task_scopes:\n  - {task_type: a, allowed_tools: ["x"], allowed_tools: ["y"]}\n',
    "task_scopes:\n  - {task_type: a, budgets: {spend: 1, spend: 2}}\n",
    "task_scopes: []\ntask_scopes: []\n",
    "task_scopes: !!python/object:os.system {}\n",
  ]) {
    assert.throws(
      () => loadText(text),
      (e: unknown) => e instanceof InputError && e.key === "input.domain_binding_invalid",
    );
  }
});

test("a << merge is outside the YAML profile, its anchor refused first (18.184g, 18.184u)", () => {
  assert.throws(
    () =>
      loadText(
        "base: &b {task_type: a, allowed_tools: [x]}\ntask_scopes:\n  - {<<: *b, allowed_tools: [y]}\n",
      ),
    (e: unknown) =>
      e instanceof InputError &&
      e.cause.endsWith(" is outside the YAML profile: anchor at line 1 (SPEC 8.7)."),
  );
});

test("a YAML date is neither text nor a number, and an id renders as Python's str(date) (18.184g)", () => {
  assert.deepEqual(scopeFromYaml("allowed_tools: [2026-01-01, x]").allowedTools, new Set(["x"]));
  assert.deepEqual(
    scopeFromYaml("budgets: {tokens: 2020-01-01, spend: 2026-01-01T10:00:00}").budgets,
    [
      ["tokens", null],
      ["spend", null],
    ],
  );
  assert.deepEqual(scopeFromYaml("budgets: 2026-01-01").budgets, [
    ["tokens", null],
    ["compute_seconds", null],
    ["spend", null],
  ]);
  const malformed = loadText("task_scopes:\n  - {task_type: 2026-01-01}\n  - 2026-01-01\n");
  assert.equal(malformed.taskScopes.size, 0);
  assert.ok(malformed.taskScopesMalformed);
  const ids = loadText(
    "decision_types:\n  - {id: 2026-01-01, subclass_of: 2026-01-02T03:04:05Z, required_oversight_modality: 2026-01-03}\n" +
      "context_classes:\n  - {id: 2026-01-01 10:00:00}\n",
  );
  assert.deepEqual(
    [...ids.subclasses],
    [
      ["2026-01-01", "2026-01-02 03:04:05+00:00"],
      ["2026-01-01 10:00:00", CONTEXT_ROOT],
    ],
  );
  assert.equal(ids.requiredOversight.size, 0);
});

test("a limit past float range is unreadable (18.184g)", () => {
  const huge = "9".repeat(400);
  assert.deepEqual(scopeFromYaml(`budgets: {spend: ${huge}, compute_seconds: ${huge}}`).budgets, [
    ["compute_seconds", null],
    ["spend", null],
  ]);
});

test("a tag the file writes is outside the YAML profile, quoted or not (18.184g, 18.184u)", () => {
  for (const scope of [
    `allowed_tools: [!!bool "yes", !!bool "true", !!bool 'on', !!int "7", !!str 7, !!str yes, x]`,
    `budgets: {tokens: !!str 1000, spend: !!int "10"}`,
    `budgets: {tokens: !!int "0o10", compute_seconds: !!float "+.5", spend: !!float "inf"}`,
    `budgets: {tokens: !!int " 5", spend: !!float "5"}`,
    "allowed_tools: [!!null x]",
  ]) {
    assertOutside(scope, "tag");
  }
});

test("a date no calendar holds is text; a date key is outside the YAML profile (18.184g, 18.184u)", () => {
  assert.deepEqual(
    scopeFromYaml("allowed_tools: [2021-02-29, 2020-02-29]").allowedTools,
    new Set(["2021-02-29"]),
  );
  assertOutside("budgets: {2020-01-01: 1, 2020-01-02: 2}", "non_string_key");
});

test("a tag the node cannot take is refused (18.184g)", () => {
  for (const scope of [
    "allowed_tools: !!str [x]",
    "task_type_x: !!seq x",
    "allowed_tools: [<<]",
    `budgets: {tokens: !!int "1:"}`,
    `budgets: {spend: !!float "5f"}`,
  ]) {
    assert.throws(
      () => scopeFromYaml(scope),
      (e: unknown) => e instanceof InputError && e.key === "input.domain_binding_invalid",
      scope,
    );
  }
});
