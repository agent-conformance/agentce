/**
 * The PyYAML rules js-yaml does not keep, for the domain binding, the applicability profile and the deviation
 * register (18.184p): the scanner's tab, flow `?` and empty flow key refusals, Python's key equality for a repeated
 * key, the Unicode 15.0 digit table, the profile read as Python reads it, and a list `control` named as Python prints
 * it. Java's `UntrustedYamlFormsTest` holds the same cases.
 */

import assert from "node:assert/strict";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { InputError } from "./errors";
import { loadProfile } from "./profile";
import { deviationLint, pyyamlLoad } from "./readiness";
import { YamlProfileError } from "./yamlProfile";

function withFile(text: string, read: (path: string) => void): void {
  const dir = mkdtempSync(join(tmpdir(), "agentce-yaml-"));
  try {
    const path = join(dir, "p.yaml");
    writeFileSync(path, text);
    read(path);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

test("forms PyYAML's scanner refuses are refused", () => {
  for (const text of [
    "a: [a?, b]\n",
    "a: [a ?, b]\n",
    "a: {a?: 1}\n",
    "a: [a ?]\n",
    "a: [a?b]\n",
    'a: [lookup ?,\n "yes"]\n',
    "a: b\t\n",
    "a: 'b'\t\n",
    "a: [b]\t\n",
    "a\t: b\n",
    "a:\tb\n",
    "-\ta\n",
    "a: |\t\n  x\n",
    "a: [b,\tc]\n",
    "a: b\t# c\n",
    "a: {: 1}\n",
    "a: [: 1]\n",
    "a: {a: 1, : 2}\n",
  ]) {
    assert.throws(() => pyyamlLoad(text), Error, text);
  }
});

/** `text` refused by the SPEC 8.7 YAML profile under `rule` at `line` (18.184u). */
function assertOutside(text: string, rule: string, line = 1): void {
  assert.throws(
    () => pyyamlLoad(text),
    (error: unknown) =>
      error instanceof YamlProfileError &&
      error.violation.rule === rule &&
      error.violation.line === line,
    text,
  );
}

test("a repeated key is decided by its text; a key that is not text never reaches the comparison (18.184u)", () => {
  // These were decided by Python's key equality (1 == 1.0 == true); the YAML profile now refuses the key.
  for (const text of [
    "{1: a, 1.0: b}",
    "{1: a, true: b}",
    "{0: a, false: b}",
    "{0x1: a, 1: b}",
    "{null: a, ~: b}",
    "{.nan: a, .nan: b}",
    "{2026-01-01: a, 2026-01-01: b}",
    "{2026-01-01T00:00:00Z: a, 2026-01-01T01:00:00+01:00: b}",
    '{"1": a, 1: b}',
    '{null: a, "null": b}',
    "{2026-01-01: a, 2026-01-02: b}",
    '{2026-01-01: a, "2026-01-01": b}',
    "{1.5: a, 15e-1: b}",
  ]) {
    assertOutside(`${text}\n`, "non_string_key");
  }
  assertOutside("a: 1\nb: 2\na: 3\n", "duplicate_key", 3);
  assertOutside('{"a": 1, a: 2}\n', "duplicate_key");
  assertOutside("{a: &x v, b: &y v}\n", "anchor");
  assert.deepEqual(pyyamlLoad('{"1": a, "01": b}\n'), { "1": "a", "01": "b" });
  assert.deepEqual(pyyamlLoad('{"null": a, "~": b}\n'), { null: "a", "~": "b" });
});

test("a flow `?` or `:` starting a token is outside the YAML profile (18.184u)", () => {
  for (const [text, rule] of [
    ["a: {?b: 1}\n", "flow_scalar_indicator_start"],
    ["a: {? b: 1}\n", "explicit_key"],
    ["a: [?b]\n", "flow_scalar_indicator_start"],
    ["a: [c, ?b: d]\n", "flow_scalar_indicator_start"],
    ["a: {?b}\n", "flow_scalar_indicator_start"],
    ["a: {?: 1}\n", "flow_scalar_indicator_start"],
    ["a: [?]\n", "explicit_key"],
    ["a: [\u{10000}, ?b, \u{10000}, ?c]\n", "flow_scalar_indicator_start"],
    ["a: {?:1}\n", "flow_scalar_indicator_start"],
    ["a: [c, &d :e]\n", "anchor"],
    ["a: {?:, b: 2}\n", "flow_scalar_indicator_start"],
    ["a: {?:1, ?:2}\n", "flow_scalar_indicator_start"],
    ["a: {? :1, ? :2}\n", "explicit_key"],
    ["a: {&b :1, &c :2}\n", "anchor"],
    ["a: {!!str :1, !!str :2}\n", "tag"],
    ["a: {b: 1, ?b: 2}\n", "flow_scalar_indicator_start"],
  ] as const) {
    assertOutside(text, rule);
  }
  for (const [text, value] of [
    ['a: ["?b", c]\n', { a: ["?b", "c"] }],
    ["?b: 1\n", { "?b": 1 }],
    ['a: {"b":1}\n', { a: { b: 1 } }],
    ["a: {b:1}\n", { a: { "b:1": null } }],
  ] as const) {
    assert.deepEqual(pyyamlLoad(text), value, text);
  }
});

test("a long flow list is read, never a stack overflow", () => {
  const items = Array.from({ length: 200000 }, (_, i) => i);
  assert.equal((pyyamlLoad(`a: [${items.join(",")}]\n`) as { a: unknown[] }).a.length, 200000);
});

test("an explicit merge key and a later one are outside the YAML profile (18.184u)", () => {
  assertOutside("? <<\n: {a: 1}\n<<: {a: 2}\n", "explicit_key");
  assertOutside("<<: {a: 2}\n? <<\n: {a: 1, b: 1}\nb: 0\n", "merge_key");
});

test("a tagged digit is outside the YAML profile, whatever its digits (18.184u)", () => {
  for (const text of [
    'a: !!int "١٠"\n',
    'a: !!int "𐵁𐵀"\n',
    'a: !!float "𐵁.5"\n',
    'a: !!int "𖵱"\n',
  ]) {
    assertOutside(text, "tag");
  }
});

test("a profile's merge key is outside the YAML profile, its anchor refused first (18.184u)", () => {
  withFile("b: &b {catalogs: [eu-ai-act]}\n<<: *b\n", (path) => {
    assert.throws(
      () => loadProfile(path),
      (error: unknown) =>
        error instanceof InputError &&
        error.key === "input.profile_invalid" &&
        error.cause.endsWith(" is outside the YAML profile: anchor at line 1 (SPEC 8.7)."),
    );
  });
});

test("two merge keys are outside the YAML profile (18.184u)", () => {
  for (const text of [
    "{<<: {a: 1}, <<: {a: 2}}",
    "{<<: {a: 2, b: 2}, <<: [{a: 1}, {a: 3, c: 3}]}",
    "{a: 0, <<: {a: 1}, <<: {a: 2, b: 2}}",
    "- <<: {control: DOC-01}\n  <<: {control: NOPE-99}\n",
  ]) {
    assertOutside(text, "merge_key");
  }
});

test("a profile with a YAML error is refused as a profile problem", () => {
  for (const text of [
    "x: !!binary aGVsbG8=\n",
    "x: !!set {a: null}\n",
    "<<:\n",
    "x: [a\n",
    "x: a\t\n",
  ]) {
    withFile(text, (path) => {
      assert.throws(
        () => loadProfile(path),
        (error: unknown) => error instanceof InputError && error.key === "input.profile_invalid",
        text,
      );
    });
  }
});

test("an observation window bound that is not an RFC 3339 date is refused (18.87)", () => {
  for (const [text, field] of [
    ['observation_window: {start: "2026-01-01T00:00:00Z", end: "foo"}\n', "end"],
    ['observation_window: {start: "2026-01-01T00:00:00Z", end: 2026-02-30}\n', "end"],
    ['observation_window: {start: "2026-01-01T00:00:00Z", end: null}\n', "end"],
    ['observation_window: {start: "foo", end: "2026-02-01T00:00:00Z"}\n', "start"],
  ]) {
    withFile(text, (path) => {
      assert.throws(
        () => loadProfile(path),
        (error: unknown) =>
          error instanceof InputError &&
          error.key === "input.profile_invalid" &&
          error.cause.endsWith(
            ` has an observation_window ${field} that is not an RFC 3339 date or date-time.`,
          ),
        text,
      );
    });
  }
});

test("an observation window that is not a mapping is refused (18.87)", () => {
  withFile('observation_window: "2026"\n', (path) => {
    assert.throws(
      () => loadProfile(path),
      (error: unknown) =>
        error instanceof InputError &&
        error.key === "input.profile_invalid" &&
        error.cause.endsWith(" has an observation_window that is not a mapping of start and end."),
    );
  });
});

test("an unquoted YAML date bound is accepted as its Python text (18.87)", () => {
  withFile("observation_window: {start: 2026-01-01, end: 2026-02-01T10:00:00Z}\n", (path) => {
    assert.deepEqual(loadProfile(path).observationWindow, {
      start: "2026-01-01",
      end: "2026-02-01 10:00:00+00:00",
    });
  });
});

test("a list or mapping control is named as Python prints it", () => {
  const lint = (control: unknown): string | undefined =>
    deviationLint([{ control }], {
      controlIds: new Set(["DOC-01"]),
      outcomesByControl: new Map(),
      appliedControls: new Set(),
      asOf: null,
    })[0];
  assert.equal(lint(["DOC-01"]), "['DOC-01']: control is not in the catalog");
  assert.equal(lint({ "DOC-01": "x" }), "{'DOC-01': 'x'}: control is not in the catalog");
});
