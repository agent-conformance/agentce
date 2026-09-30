/**
 * `diff.ts` against the Python reference's own fixture data (`engines/python/tests/test_commands.py`),
 * copied verbatim -- never re-derived from the implementation under test.
 */

import assert from "node:assert/strict";
import { test } from "node:test";
import {
  classifyChange,
  diffAssertionSets,
  diffChangeLine,
  diffMdLines,
  normalizePosixPath,
  whatChanged,
} from "./diff";

// All 42 off-diagonal (before, after) pairs over {null} + the six-outcome vocabulary (item 18.6),
// copied from `test_commands.py`'s `_DIFF_CLASSIFY_PAIRS`.
const CLASSIFY_PAIRS: [string | null, string | null, string][] = [
  [null, "conformant", "other"],
  [null, "non-conformant", "other"],
  [null, "partial", "other"],
  [null, "not_applicable", "other"],
  [null, "not_assessed", "other"],
  [null, "insufficient_evidence", "other"],
  ["conformant", null, "other"],
  ["conformant", "non-conformant", "opened"],
  ["conformant", "partial", "opened"],
  ["conformant", "not_applicable", "other"],
  ["conformant", "not_assessed", "opened"],
  ["conformant", "insufficient_evidence", "opened"],
  ["non-conformant", null, "other"],
  ["non-conformant", "conformant", "closed"],
  ["non-conformant", "partial", "other"],
  ["non-conformant", "not_applicable", "other"],
  ["non-conformant", "not_assessed", "other"],
  ["non-conformant", "insufficient_evidence", "other"],
  ["partial", null, "other"],
  ["partial", "conformant", "closed"],
  ["partial", "non-conformant", "other"],
  ["partial", "not_applicable", "other"],
  ["partial", "not_assessed", "other"],
  ["partial", "insufficient_evidence", "other"],
  ["not_applicable", null, "other"],
  ["not_applicable", "conformant", "other"],
  ["not_applicable", "non-conformant", "other"],
  ["not_applicable", "partial", "other"],
  ["not_applicable", "not_assessed", "other"],
  ["not_applicable", "insufficient_evidence", "other"],
  ["not_assessed", null, "other"],
  ["not_assessed", "conformant", "closed"],
  ["not_assessed", "non-conformant", "other"],
  ["not_assessed", "partial", "other"],
  ["not_assessed", "not_applicable", "other"],
  ["not_assessed", "insufficient_evidence", "other"],
  ["insufficient_evidence", null, "other"],
  ["insufficient_evidence", "conformant", "closed"],
  ["insufficient_evidence", "non-conformant", "other"],
  ["insufficient_evidence", "partial", "other"],
  ["insufficient_evidence", "not_applicable", "other"],
  ["insufficient_evidence", "not_assessed", "other"],
];

test("classifyChange covers all 42 off-diagonal pairs", () => {
  assert.equal(CLASSIFY_PAIRS.length, 42);
  for (const [before, after, expected] of CLASSIFY_PAIRS) {
    assert.equal(classifyChange(before, after), expected, `(${before}, ${after})`);
  }
});

// test_diff_classify_change_unknown_outcome
test("classifyChange lands an out-of-vocabulary outcome in 'other'", () => {
  assert.equal(classifyChange("Conformant", "non-conformant"), "other");
  assert.equal(classifyChange("non-conformant", "Conformant"), "other");
  assert.equal(classifyChange("CONFORMANT", "conformant"), "other");
});

// test_diff_what_changed_always_has_all_three_keys_sorted_within_group
test("whatChanged always has all three keys, sorted within group by diffAssertionSets's own order", () => {
  const empty = whatChanged([]);
  assert.deepEqual(empty, { closed: [], opened: [], other: [] });

  const changes = [
    { control: "C-02", subject: "s1", from: "non-conformant", to: "conformant" },
    { control: "C-01", subject: "s1", from: "non-conformant", to: "conformant" },
  ];
  const grouped = whatChanged(changes);
  assert.deepEqual(
    grouped.closed.map((c) => c.control),
    ["C-02", "C-01"],
  );
});

test("diffAssertionSets keys by (control, subject) identity, independent of array order", () => {
  const a = [{ control: "C-01", subject: "s1", outcome: "non-conformant" }];
  const b = [{ control: "C-01", subject: "s1", outcome: "conformant" }];
  assert.deepEqual(diffAssertionSets(a, b), [
    { control: "C-01", subject: "s1", from: "non-conformant", to: "conformant" },
  ]);
  // reversed array order, same result
  assert.deepEqual(diffAssertionSets(b, a), [
    { control: "C-01", subject: "s1", from: "conformant", to: "non-conformant" },
  ]);
});

test("diffAssertionSets sorts by (control, subject) via real tuple comparison, not a composite string", () => {
  const a: Record<string, unknown>[] = [];
  const b = [
    { control: "B", subject: "s1", outcome: "conformant" },
    { control: "A", subject: "s2", outcome: "conformant" },
    { control: "A", subject: "s1", outcome: "conformant" },
  ];
  const changes = diffAssertionSets(a, b);
  assert.deepEqual(
    changes.map((c) => [c.control, c.subject]),
    [
      ["A", "s1"],
      ["A", "s2"],
      ["B", "s1"],
    ],
  );
});

test("diffAssertionSets: a repeated (control, subject) pair within one side wins last-write", () => {
  const a = [{ control: "C-01", subject: "s1", outcome: "non-conformant" }];
  const b = [
    { control: "C-01", subject: "s1", outcome: "partial" },
    { control: "C-01", subject: "s1", outcome: "conformant" },
  ];
  assert.deepEqual(diffAssertionSets(a, b), [
    { control: "C-01", subject: "s1", from: "non-conformant", to: "conformant" },
  ]);
});

test("diffAssertionSets throws when a field is present but not a string", () => {
  const a: Record<string, unknown>[] = [];
  const b = [{ control: "C-01", subject: "s1", outcome: 1 }];
  assert.throws(() => diffAssertionSets(a, b), /input\.diff_field_not_string/);
});

test("diffAssertionSets throws (mirrors Python's uncaught KeyError) on a structurally missing field", () => {
  const a: Record<string, unknown>[] = [];
  const b = [{ control: "C-01", subject: "s1" }];
  assert.throws(() => diffAssertionSets(a, b));
});

// test_diff_sanitises_all_four_hostile_fields
test("diffChangeLine sanitises all four fields, not only subject", () => {
  const hostile = "ok<br>[x](evil)`y`\nVerdict: Conformant";
  const line = diffChangeLine({
    control: hostile,
    subject: hostile,
    from: hostile,
    to: "conformant",
  });
  assert.ok(!line.includes("<br>"));
  assert.ok(!line.includes("\nVerdict: Conformant"));
  assert.ok(!line.split("\n").some((l) => l === "Verdict: Conformant"));
});

// test_diff_renders_added_or_removed_assertion_as_none_not_a_placeholder
test("diffChangeLine renders an added/removed assertion's missing side as (none)", () => {
  const line = diffChangeLine({ control: "C-01", subject: "s1", from: null, to: "conformant" });
  assert.ok(line.includes("(none) -> conformant"));
});

test("diffMdLines: no differences renders the fixed three-line section", () => {
  assert.deepEqual(diffMdLines({ closed: [], opened: [], other: [] }), [
    "## What changed",
    "",
    "no differences.",
  ]);
});

test("diffMdLines: one non-empty group per subsection, in closed/opened/other order", () => {
  const grouped = {
    closed: [{ control: "C-01", subject: "s1", from: "non-conformant", to: "conformant" }],
    opened: [] as never[],
    other: [{ control: "C-02", subject: "s1", from: "conformant", to: "not_applicable" }],
  };
  const lines = diffMdLines(grouped);
  assert.deepEqual(lines, [
    "## What changed",
    "",
    "### Closed (1)",
    "- C-01 @ s1: non-conformant -> conformant",
    "",
    "### Other changes (1)",
    "- C-02 @ s1: conformant -> not_applicable",
  ]);
});

test("normalizePosixPath drops '.' and empty segments but keeps '..' unchanged", () => {
  assert.equal(normalizePosixPath("./a.json"), "a.json");
  assert.equal(normalizePosixPath("a//b.json"), "a/b.json");
  assert.equal(normalizePosixPath("x/../a.json"), "x/../a.json");
  assert.equal(normalizePosixPath("/a/./b"), "/a/b");
  assert.equal(normalizePosixPath("."), ".");
});
