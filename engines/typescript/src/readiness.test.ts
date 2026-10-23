/**
 * `readiness.ts` against the Python reference's own fixture data (`engines/python/tests/
 * test_readiness.py`), copied verbatim where the case is portable, plus a set of adversarial
 * regressions this port must not reintroduce: duplicate reasons, non-BMP sort order, missing-field
 * `None` rendering, `pyRepr` backslash escaping, and the quoted-vs-unquoted YAML timestamp
 * distinction.
 */

import assert from "node:assert/strict";
import {
  chmodSync,
  mkdirSync,
  mkdtempSync,
  renameSync,
  symlinkSync,
  unlinkSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import {
  DEFAULT_MAX_DEVIATION_DAYS,
  NOT_READY,
  READY,
  READY_WITH_LIMITATIONS,
  computeReadiness,
  deviationLint,
  loadDeviationRegister,
  normalizeDeviationDates,
  parseDate,
  parseGapsFile,
  pythonizeTimestamp,
} from "./readiness";
import { REPORT_FILE_RULE_CASES, ruleCaseContent } from "./testSupport";
import { byteCompare } from "./util";

const SEVERITIES = new Map([
  ["OVS-03", "high"],
  ["REC-01", "high"],
  ["DAT-01", "medium"],
  ["TRN-01", "medium"],
]);

function report(
  overrides: {
    assertions?: Record<string, unknown>[];
    integrity?: Record<string, unknown>[];
    coverage?: Record<string, unknown>;
    applicability?: Record<string, unknown>[];
  } = {},
): string {
  const dir = mkdtempSync(join(tmpdir(), "agentce-readiness-"));
  writeFileSync(join(dir, "assertions.json"), JSON.stringify(overrides.assertions ?? []));
  writeFileSync(
    join(dir, "integrity.jsonl"),
    (overrides.integrity ?? []).map((r) => `${JSON.stringify(r)}\n`).join(""),
  );
  writeFileSync(join(dir, "coverage.json"), JSON.stringify(overrides.coverage ?? { subjects: {} }));
  writeFileSync(
    join(dir, "applicability.jsonl"),
    (overrides.applicability ?? []).map((s) => `${JSON.stringify(s)}\n`).join(""),
  );
  writeFileSync(join(dir, "quarantine.jsonl"), "");
  return dir;
}

function deviation(over: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    control: "OVS-03",
    rationale: "compensated",
    compensating_control: "manual review",
    owner: "alice",
    approver: "bob",
    granted: "2026-01-01",
    expiry: "2026-03-01",
    ...over,
  };
}

// --- compute_readiness (test_readiness.py) ------------------------------------------------------

test("a clean report is READY", () => {
  const dir = report({
    assertions: [{ control: "OVS-03", outcome: "conformant", subject: "s" }],
    integrity: [{ status: "verified", stream: "a" }],
    coverage: { subjects: { s: { coverage_status: "ok" } } },
  });
  const verdict = computeReadiness(dir, { severities: SEVERITIES });
  assert.equal(verdict.verdict, READY);
  assert.deepEqual(verdict.reasons, []);
});

const REPORT_FILES = [
  "coverage.json",
  "integrity.jsonl",
  "applicability.jsonl",
  "assertions.json",
  "quarantine.jsonl",
];
const asRoot = process.getuid?.() === 0;

/** Makes `dir/name` present but unreadable; returns a folder to reopen after the check. */
function makeUnreadable(dir: string, name: string, shape: string): string | undefined {
  const target = join(dir, name);
  if (shape === "link into a mode-600 folder") {
    const locked = mkdtempSync(join(tmpdir(), "agentce-locked-"));
    renameSync(target, join(locked, name));
    symlinkSync(join(locked, name), target);
    chmodSync(locked, 0o600);
    return locked;
  }
  if (shape === "mode 000") {
    chmodSync(target, 0o000);
    return undefined;
  }
  unlinkSync(target);
  if (shape === "dangling link") {
    symlinkSync(join(dir, "nowhere"), target);
  } else {
    mkdirSync(target);
  }
  return undefined;
}

// An entry that is there but cannot be read blocks; it never reads as absent, which would let a
// recorded gap stop blocking (18.165, the same answer Python gives).
for (const name of REPORT_FILES) {
  for (const shape of ["link into a mode-600 folder", "mode 000", "dangling link", "folder"]) {
    test(`an unreadable ${name} (${shape}) is NOT READY`, { skip: asRoot }, () => {
      const dir = report();
      const locked = makeUnreadable(dir, name, shape);
      try {
        const v = computeReadiness(dir, { severities: SEVERITIES });
        assert.equal(v.verdict, NOT_READY);
        assert.deepEqual(v.reasons, [`cannot read report file ${name}`]);
      } finally {
        if (locked !== undefined) {
          chmodSync(locked, 0o700);
        }
      }
    });
  }
  test(`an absent ${name} is NOT READY and named (18.166)`, () => {
    const dir = report();
    unlinkSync(join(dir, name));
    const v = computeReadiness(dir, { severities: SEVERITIES });
    assert.equal(v.verdict, NOT_READY);
    assert.deepEqual(v.reasons, [`missing report file ${name}`]);
  });
}

test("an absent report file outweighs a gap recorded as a limitation", () => {
  const dir = report({
    assertions: [{ control: "OVS-03", outcome: "insufficient_evidence", subject: "s" }],
  });
  unlinkSync(join(dir, "coverage.json"));
  const v = computeReadiness(dir, { severities: SEVERITIES, gaps: new Set(["OVS-03"]) });
  assert.equal(v.verdict, NOT_READY);
  assert.ok(v.reasons.includes("missing report file coverage.json"));
});

test("every absent report file is named, sorted", () => {
  const dir = report();
  for (const name of REPORT_FILES) {
    unlinkSync(join(dir, name));
  }
  assert.deepEqual(
    computeReadiness(dir, { severities: SEVERITIES }).reasons,
    [...REPORT_FILES].sort(byteCompare).map((n) => `missing report file ${n}`),
  );
});

test("a report folder that can be listed but not entered is NOT READY", { skip: asRoot }, () => {
  const dir = report();
  chmodSync(dir, 0o600);
  try {
    const v = computeReadiness(dir, { severities: SEVERITIES });
    assert.deepEqual(
      v.reasons,
      [...REPORT_FILES].sort(byteCompare).map((n) => `cannot read report file ${n}`),
    );
  } finally {
    chmodSync(dir, 0o700);
  }
});

test("broken integrity is NOT READY", () => {
  const dir = report({ integrity: [{ status: "failed", stream: "gw" }] });
  assert.equal(computeReadiness(dir, { severities: SEVERITIES }).verdict, NOT_READY);
});

const JSONL_RECORD: Record<string, string> = {
  "integrity.jsonl": '{"stream": "s", "status": "verified"}',
  "applicability.jsonl": '{"drift": []}',
  "quarantine.jsonl": '{"record": "r"}',
};
const NOT_JSONL: Record<string, (r: string) => string> = {
  vt: (r) => `${r}\u000b${r}\n`,
  ff: (r) => `${r}\u000c${r}\n`,
  nel: (r) => `${r}\u0085${r}\n`,
  ls: (r) => `${r} ${r}\n`,
  ps: (r) => `${r} ${r}\n`,
  lone_cr: (r) => `${r}\r${r}\n`,
  lone_cr_no_lf: (r) => `${r}\r${r}`,
  lead_nbsp: (r) => ` ${r}\n`,
  lead_bom: (r) => `﻿${r}\n`,
  trail_nbsp: (r) => `${r} \n`,
  nbsp_line: (r) => ` \n${r}\n`,
};
const JSONL_OK: Record<string, (r: string) => string> = {
  crlf: (r) => `${r}\r\n`,
  space_tab: (r) => `\t ${r} \t\n`,
  blank_lines: (r) => `\n \n\r\n${r}\n\n`,
  cr_between_tokens: (r) => `${r.replace(": ", ":\r")}\n`,
  u2028_in_string: (r) => `${r.slice(0, -1)}, "x": "a b"}\n`,
  empty: () => "",
};

for (const [name, record] of Object.entries(JSONL_RECORD)) {
  for (const [id, lines] of Object.entries(NOT_JSONL)) {
    test(`${name} line outside the JSON Lines rule (${id}) is NOT READY (18.184n)`, () => {
      const dir = report();
      writeFileSync(join(dir, name), lines(record));
      assert.deepEqual(computeReadiness(dir, { severities: SEVERITIES }).reasons, [
        `cannot read report file ${name}`,
      ]);
    });
  }
  test(`${name} with a byte that is not UTF-8 is NOT READY (18.184n)`, () => {
    const dir = report();
    writeFileSync(
      join(dir, name),
      Buffer.concat([
        Buffer.from(`${record.slice(0, -1)}, "x": "`),
        Buffer.from([0xff, 0x22, 0x7d, 0x0a]),
      ]),
    );
    assert.deepEqual(computeReadiness(dir, { severities: SEVERITIES }).reasons, [
      `cannot read report file ${name}`,
    ]);
  });
  for (const [id, lines] of Object.entries(JSONL_OK)) {
    test(`${name} line inside the JSON Lines rule (${id}) stays READY (18.184n)`, () => {
      const dir = report();
      writeFileSync(join(dir, name), lines(record));
      assert.equal(computeReadiness(dir, { severities: SEVERITIES }).verdict, READY);
    });
  }
}

for (const [name, record, reason] of [
  ["integrity.jsonl", '{"stream": "s", "status": "failed"}', "integrity failed on stream s"],
  ["applicability.jsonl", '{"drift": [{"kind": "k", "ref": "r"}]}', "applicability drift: k r"],
]) {
  for (const tail of ["", "\r"]) {
    test(`the last ${name} record with no LF after it is read (${JSON.stringify(tail)}, 18.184n)`, () => {
      const dir = report();
      writeFileSync(join(dir, name), `\n${record}${tail}`);
      assert.deepEqual(computeReadiness(dir, { severities: SEVERITIES }).reasons, [reason]);
    });
  }
}

test("a coverage gap is NOT READY", () => {
  const dir = report({ coverage: { subjects: { s: { coverage_status: "gap" } } } });
  const verdict = computeReadiness(dir, { severities: SEVERITIES });
  assert.equal(verdict.verdict, NOT_READY);
  assert.ok(verdict.reasons.some((r) => r.includes("coverage")));
});

test("unknown coverage is NOT READY and names the subject", () => {
  // No independent denominator reconciles the subject's records (SPEC §6.5, §13.3.4).
  const dir = report({ coverage: { subjects: { s: { coverage_status: "unknown" } } } });
  const verdict = computeReadiness(dir, { severities: SEVERITIES });
  assert.equal(verdict.verdict, NOT_READY);
  assert.deepEqual(verdict.reasons, ["coverage unknown for subject s: no independent denominator"]);
});

test("ok coverage adds no coverage reason", () => {
  const dir = report({ coverage: { subjects: { s: { coverage_status: "ok" } } } });
  assert.equal(computeReadiness(dir, { severities: SEVERITIES }).verdict, READY);
});

test("a below-threshold event type is NOT READY even under an unknown subject roll-up", () => {
  const dir = report({
    coverage: {
      subjects: {
        s: { coverage_status: "unknown", event_types: { ToolCall: { status: "below_threshold" } } },
      },
    },
  });
  const verdict = computeReadiness(dir, { severities: SEVERITIES });
  assert.equal(verdict.verdict, NOT_READY);
  assert.ok(verdict.reasons.some((r) => r.includes("shortfall")));
});

test("applicability drift is NOT READY", () => {
  const dir = report({
    applicability: [{ drift: [{ kind: "undeclared_decision_type", ref: "dom:X" }] }],
  });
  assert.equal(computeReadiness(dir, { severities: SEVERITIES }).verdict, NOT_READY);
});

test("a high-severity insufficient_evidence with no gap recorded is NOT READY", () => {
  const dir = report({
    assertions: [{ control: "OVS-03", outcome: "insufficient_evidence", subject: "s" }],
  });
  assert.equal(computeReadiness(dir, { severities: SEVERITIES }).verdict, NOT_READY);
});

test("a high-severity insufficient_evidence recorded in gaps is READY WITH LIMITATIONS", () => {
  const dir = report({
    assertions: [{ control: "OVS-03", outcome: "insufficient_evidence", subject: "s" }],
  });
  const verdict = computeReadiness(dir, { severities: SEVERITIES, gaps: new Set(["OVS-03"]) });
  assert.equal(verdict.verdict, READY_WITH_LIMITATIONS);
  assert.ok(verdict.limitations.length > 0);
});

test("a medium-severity insufficient_evidence is READY outright", () => {
  const dir = report({
    assertions: [{ control: "DAT-01", outcome: "insufficient_evidence", subject: "s" }],
  });
  assert.equal(computeReadiness(dir, { severities: SEVERITIES }).verdict, READY);
});

// --- the round-2 regression scenario: duplicate reasons, non-BMP sort, missing fields ----------

test("duplicate reasons are never deduplicated, and sort by byteCompare, not UTF-16 order", () => {
  const dir = report({
    integrity: [
      { status: "failed", stream: "gw" },
      { status: "failed", stream: "gw" },
      // U+FF21 (fullwidth 'A') is a BMP character: its UTF-16 code unit (0xFF21) and its UTF-8
      // first byte (0xEF) both exceed every ASCII byte, so a naive UTF-16 sort and byteCompare agree
      // on it -- it does NOT by itself distinguish the two orders (round-2 verifier finding: an
      // earlier version of this fixture claimed it did). U+1F600 ("grinning face"), an *astral*
      // character represented as a UTF-16 surrogate pair (leading code unit 0xD83D = 55357, below
      // U+FF21's 0xFF21 = 65313) but a 4-byte UTF-8 sequence (leading byte 0xF0 = 240, above U+FF21's
      // leading byte 0xEF = 239), is what actually reverses the two orders relative to each other --
      // confirmed: a naive UTF-16/code-unit sort places 😀 before Ａ, byteCompare places Ａ before 😀.
      { status: "failed", stream: "Ａ" },
      { status: "failed", stream: "😀" },
      // A record with a bad status but no `stream` field at all -- Python's own `record.get("status")
      // in _BAD_INTEGRITY` never enters this branch for a record missing `status` entirely (`None`
      // is not in the bad-status set), so only a present-bad-status/absent-stream combination can
      // ever exercise the `None`-rendering path this fixture pins.
      { status: "gap" },
    ],
  });
  const verdict = computeReadiness(dir, { severities: SEVERITIES });
  const dupe = verdict.reasons.filter((r) => r === "integrity failed on stream gw");
  assert.equal(dupe.length, 2);
  assert.ok(verdict.reasons.includes("integrity gap on stream None"));
  const sorted = verdict.reasons.slice().sort();
  assert.deepEqual(verdict.reasons, sorted.sort(byteCompare));
});

// --- deviation_lint (test_readiness.py) ---------------------------------------------------------

test("a valid deviation lints clean", () => {
  const problems = deviationLint([deviation()], {
    controlIds: new Set(["OVS-03"]),
    outcomesByControl: new Map([["OVS-03", new Set(["non-conformant"])]]),
    appliedControls: new Set(),
    asOf: null,
  });
  assert.deepEqual(problems, []);
});

test("a deviation on an insufficient_evidence control is refused", () => {
  const problems = deviationLint([deviation()], {
    controlIds: new Set(["OVS-03"]),
    outcomesByControl: new Map([["OVS-03", new Set(["insufficient_evidence"])]]),
    appliedControls: new Set(),
    asOf: null,
  });
  assert.ok(problems.some((p) => p.includes("insufficient_evidence")));
});

test("the INT family can never be deviated", () => {
  const problems = deviationLint([deviation({ control: "INT-01" })], {
    controlIds: new Set(["INT-01"]),
    outcomesByControl: new Map([["INT-01", new Set(["non-conformant"])]]),
    appliedControls: new Set(),
    asOf: null,
  });
  assert.ok(problems.some((p) => p.includes("INT family")));
});

test("a same-person owner/approver and an empty-but-present field are both refused", () => {
  const problems = deviationLint([deviation({ approver: "alice", rationale: "" })], {
    controlIds: new Set(["OVS-03"]),
    outcomesByControl: new Map([["OVS-03", new Set(["non-conformant"])]]),
    appliedControls: new Set(),
    asOf: null,
  });
  assert.ok(problems.some((p) => p.includes("distinct")));
  assert.ok(problems.some((p) => p.includes("missing rationale")));
});

test("an empty-array or empty-mapping field value counts as missing too (pyTruthy)", () => {
  const problems = deviationLint([deviation({ rationale: [], compensating_control: {} })], {
    controlIds: new Set(["OVS-03"]),
    outcomesByControl: new Map([["OVS-03", new Set(["non-conformant"])]]),
    appliedControls: new Set(),
    asOf: null,
  });
  assert.ok(problems.some((p) => p.includes("missing rationale")));
  assert.ok(problems.some((p) => p.includes("missing compensating_control")));
});

test("a lifetime over the max is refused", () => {
  const problems = deviationLint([deviation({ expiry: "2027-01-01" })], {
    controlIds: new Set(["OVS-03"]),
    outcomesByControl: new Map([["OVS-03", new Set(["non-conformant"])]]),
    appliedControls: new Set(),
    asOf: null,
  });
  assert.ok(
    problems.some((p) => p.includes(`lifetime exceeds ${DEFAULT_MAX_DEVIATION_DAYS} days`)),
  );
});

test("an unknown control is refused", () => {
  const problems = deviationLint([deviation({ control: "ZZZ-99" })], {
    controlIds: new Set(["OVS-03"]),
    outcomesByControl: new Map(),
    appliedControls: new Set(),
    asOf: null,
  });
  assert.ok(problems.some((p) => p.includes("not in the catalog")));
});

test("multi-subject outcome aggregation is order-independent", () => {
  const a = deviationLint([deviation()], {
    controlIds: new Set(["OVS-03"]),
    outcomesByControl: new Map([["OVS-03", new Set(["non-conformant", "conformant"])]]),
    appliedControls: new Set(),
    asOf: null,
  });
  const b = deviationLint([deviation()], {
    controlIds: new Set(["OVS-03"]),
    outcomesByControl: new Map([["OVS-03", new Set(["conformant", "non-conformant"])]]),
    appliedControls: new Set(),
    asOf: null,
  });
  assert.deepEqual(a, []);
  assert.deepEqual(b, []);
});

test("an already-applied control skips the outcome re-check", () => {
  const problems = deviationLint([deviation()], {
    controlIds: new Set(["OVS-03"]),
    outcomesByControl: new Map([["OVS-03", new Set(["partial"])]]),
    appliedControls: new Set(["OVS-03"]),
    asOf: null,
  });
  assert.deepEqual(problems, []);
});

test("an applied-and-expired deviation is rejected", () => {
  const problems = deviationLint([deviation({ expiry: "2020-01-01", granted: "2019-08-01" })], {
    controlIds: new Set(["OVS-03"]),
    outcomesByControl: new Map([["OVS-03", new Set(["partial"])]]),
    appliedControls: new Set(["OVS-03"]),
    asOf: "2026-01-01",
  });
  assert.ok(problems.some((p) => p.includes("applied deviation has expired")));
});

test("an unexpired or not-yet-applied entry is never rejected on expiry alone", () => {
  const unexpired = deviationLint([deviation({ expiry: "2027-01-01", granted: "2026-11-01" })], {
    controlIds: new Set(["OVS-03"]),
    outcomesByControl: new Map([["OVS-03", new Set(["partial"])]]),
    appliedControls: new Set(["OVS-03"]),
    asOf: "2026-12-01",
  });
  assert.ok(!unexpired.some((p) => p.includes("expired")));
  const notYetApplied = deviationLint([deviation({ expiry: "2020-01-01" })], {
    controlIds: new Set(["OVS-03"]),
    outcomesByControl: new Map([["OVS-03", new Set(["non-conformant"])]]),
    appliedControls: new Set(),
    asOf: "2026-01-01",
  });
  assert.ok(!notYetApplied.some((p) => p.includes("expired")));
});

test("a duplicate entry for the same control is rejected", () => {
  const problems = deviationLint([deviation(), deviation()], {
    controlIds: new Set(["OVS-03"]),
    outcomesByControl: new Map([["OVS-03", new Set(["non-conformant"])]]),
    appliedControls: new Set(),
    asOf: null,
  });
  assert.ok(problems.some((p) => p.includes("duplicate deviation entry")));
});

test("an unparseable expiry or granted is rejected, not silently skipped", () => {
  const badExpiry = deviationLint([deviation({ expiry: "not-a-date" })], {
    controlIds: new Set(["OVS-03"]),
    outcomesByControl: new Map([["OVS-03", new Set(["non-conformant"])]]),
    appliedControls: new Set(),
    asOf: null,
  });
  assert.ok(badExpiry.some((p) => p.includes("expiry is not a valid RFC 3339 date")));
  const badGranted = deviationLint([deviation({ granted: "not-a-date" })], {
    controlIds: new Set(["OVS-03"]),
    outcomesByControl: new Map([["OVS-03", new Set(["non-conformant"])]]),
    appliedControls: new Set(),
    asOf: null,
  });
  assert.ok(badGranted.some((p) => p.includes("granted is not a valid RFC 3339 date")));
});

test("control: null renders as 'None', an absent control as '' -- the pyGet-vs-|| distinction", () => {
  const nullControl = deviationLint([deviation({ control: null })], {
    controlIds: new Set(["OVS-03"]),
    outcomesByControl: new Map(),
    appliedControls: new Set(),
    asOf: null,
  });
  assert.ok(nullControl.some((p) => p.startsWith("None:")));
  const { control: _drop, ...withoutControl } = deviation();
  const absentControl = deviationLint([withoutControl], {
    controlIds: new Set(["OVS-03"]),
    outcomesByControl: new Map(),
    appliedControls: new Set(),
    asOf: null,
  });
  assert.ok(absentControl.some((p) => p.startsWith("<none>:")));
});

// --- compute_readiness + deviations integration -------------------------------------------------

test("an invalid deviation makes the whole report NOT READY", () => {
  const dir = report({
    assertions: [{ control: "OVS-03", outcome: "non-conformant", subject: "s" }],
  });
  const verdict = computeReadiness(dir, {
    severities: SEVERITIES,
    deviations: [deviation({ expiry: "2027-06-01" })],
  });
  assert.equal(verdict.verdict, NOT_READY);
});

test("a report whose deviation was already applied is accepted without re-checking outcome", () => {
  const dir = report({
    assertions: [
      {
        control: "OVS-03",
        outcome: "partial",
        deviation: "OVS-03",
        subject: "s",
        window: { start: "2026-01-01", end: "2026-02-01" },
      },
    ],
  });
  const verdict = computeReadiness(dir, { severities: SEVERITIES, deviations: [deviation()] });
  assert.notEqual(verdict.verdict, NOT_READY);
});

test("an applied-but-since-expired deviation is NOT READY, expiry rendered via pythonizeTimestamp", () => {
  const dir = report({
    assertions: [
      {
        control: "OVS-03",
        outcome: "partial",
        deviation: "OVS-03",
        subject: "s",
        window: { start: "2026-01-01", end: "2026-02-01" },
      },
    ],
  });
  const regDir = mkdtempSync(join(tmpdir(), "agentce-readiness-yaml-"));
  const regPath = join(regDir, "deviations.yaml");
  writeFileSync(
    regPath,
    [
      "deviations:",
      "  - control: OVS-03",
      "    rationale: compensated",
      "    compensating_control: manual review",
      "    owner: alice",
      "    approver: bob",
      "    granted: 2019-08-01",
      // Unquoted, so PyYAML/js-yaml resolve it as a timestamp and pythonizeTimestamp rewrites it.
      "    expiry: 2020-01-01T10:00:00Z",
      "",
    ].join("\n"),
  );
  const verdict = computeReadiness(dir, {
    severities: SEVERITIES,
    deviations: loadDeviationRegister(regPath),
  });
  assert.equal(verdict.verdict, NOT_READY);
  assert.ok(verdict.reasons.some((r) => r.includes("expiry 2020-01-01 10:00:00+00:00")));
});

// --- pythonizeTimestamp / parseDate: the round-2 regression fixtures ---------------------------

test("pythonizeTimestamp matches Python's str(date|datetime), generated by running real Python", () => {
  assert.equal(pythonizeTimestamp("2021-01-01"), "2021-01-01");
  assert.equal(pythonizeTimestamp("2021-1-5"), "2021-01-05");
  assert.equal(pythonizeTimestamp("2021-01-01T00:00:00Z"), "2021-01-01 00:00:00+00:00");
  assert.equal(pythonizeTimestamp("2021-01-01T00:00:00.5"), "2021-01-01 00:00:00.500000");
  assert.equal(pythonizeTimestamp("2021-01-01T00:00:00.000"), "2021-01-01 00:00:00");
  assert.equal(pythonizeTimestamp("2021-01-01T10:00:00+05:00"), "2021-01-01 10:00:00+05:00");
  assert.equal(pythonizeTimestamp("2021-01-01T10:00:00"), "2021-01-01 10:00:00");
});

test("parseDate rejects an out-of-range hour/minute/second/offset, not only an invalid calendar date", () => {
  assert.equal(parseDate("0000-01-01"), null);
  assert.equal(parseDate("2021-02-30"), null);
  assert.notEqual(parseDate("2020-02-29"), null);
  assert.equal(parseDate("2021-02-29"), null);
  assert.equal(parseDate("2021-01-01T25:00:00Z"), null);
  assert.equal(parseDate("2021-01-01T10:61:00Z"), null);
  assert.equal(parseDate("2021-01-01T23:59:60Z"), null);
  assert.equal(parseDate("2021-01-01T10:00:00+24:00"), null);
});

test("parseDate rejects fromisoformat's extra forms -- RFC 3339 only (2026-09-30 maintainer decision)", () => {
  // TRADEOFFS.md/inbox row 19: one grammar in all three engines. Python's own `parse_date` now
  // rejects these same four forms (cross-engine vector) even though real `datetime.fromisoformat`
  // still accepts each of them.
  assert.equal(parseDate("20211231"), null); // basic format (no separators)
  assert.equal(parseDate("2021-W52-5"), null); // ISO week date
  assert.equal(parseDate("2021-12-31T10"), null); // hour-only precision, no minutes/seconds
  assert.equal(parseDate("2021-12-31T10:30"), null); // minute precision, no seconds
  assert.notEqual(parseDate("2021-12-31T10:30:00"), null); // RFC 3339 still accepted
});

test("parseDate rejects non-ASCII digits and a trailing newline (verifier round 2, F1)", () => {
  // A Unicode-`\d`-and-`re.match`-with-trailing-`$` regex (Python's original F1 bug) accepts these;
  // RFC 3339 requires ASCII digits and the whole string, not a prefix ending just before a newline.
  assert.equal(parseDate("٢٠٢٦-٠١-٠١"), null); // Arabic-Indic digits
  assert.equal(parseDate("２０２６-０１-０１"), null); // fullwidth digits
  assert.equal(parseDate("2026-01-01\n"), null); // trailing newline
  assert.notEqual(parseDate("2026-01-01"), null); // the equivalent ASCII date is still accepted
});

// --- loadDeviationRegister: PyYAML-matching implicit resolution --------------------------------

function withTempFile(contents: string, run: (path: string) => void): void {
  const dir = mkdtempSync(join(tmpdir(), "agentce-readiness-yaml-"));
  const path = join(dir, "deviations.yaml");
  writeFileSync(path, contents);
  run(path);
}

test("a quoted timestamp scalar is never rewritten, byte-identical to the source text", () => {
  withTempFile(
    'deviations:\n  - control: "OVS-03"\n    expiry: "2021-01-01T00:00:00Z"\n',
    (path) => {
      const [entry] = loadDeviationRegister(path);
      assert.equal(entry?.expiry, "2021-01-01T00:00:00Z");
    },
  );
});

test("a << merge key is honoured, an explicit key overrides the merged one (verifier round 2, F3)", () => {
  withTempFile(
    "deviations:\n" +
      "  - <<: &base\n" +
      "      rationale: shared\n" +
      "      owner: alice\n" +
      "    control: OVS-03\n" +
      "    owner: carol\n",
    (path) => {
      const [entry] = loadDeviationRegister(path);
      assert.equal(entry?.rationale, "shared"); // pulled in from the merge source
      assert.equal(entry?.owner, "carol"); // the explicit local key wins over the merged one
      assert.equal(entry?.control, "OVS-03");
    },
  );
});

test("a duplicate mapping key keeps the last value, matching PyYAML (verifier round 2, F4)", () => {
  withTempFile("deviations:\n  - control: OVS-03\n    owner: alice\n    owner: carol\n", (path) => {
    const [entry] = loadDeviationRegister(path);
    assert.equal(entry?.owner, "carol");
  });
});

test("an unquoted timestamp scalar is pythonized, matching PyYAML's str(datetime) rendering", () => {
  withTempFile("deviations:\n  - control: OVS-03\n    expiry: 2021-01-01T00:00:00Z\n", (path) => {
    const [entry] = loadDeviationRegister(path);
    assert.equal(entry?.expiry, "2021-01-01 00:00:00+00:00");
  });
});

test("a colonless-offset scalar is never resolved as a timestamp, stays a plain string", () => {
  withTempFile(
    "deviations:\n  - control: OVS-03\n    expiry: 2021-01-01T10:00:00+0500\n",
    (path) => {
      const [entry] = loadDeviationRegister(path);
      assert.equal(entry?.expiry, "2021-01-01T10:00:00+0500");
    },
  );
});

test("YAML-1.1 booleans resolve unquoted, stay strings quoted", () => {
  withTempFile(
    'deviations:\n  - control: OVS-03\n    rationale: no\n  - control: OVS-04\n    rationale: "no"\n',
    (path) => {
      const [unquoted, quoted] = loadDeviationRegister(path);
      assert.equal(unquoted?.rationale, false);
      assert.equal(quoted?.rationale, "no");
    },
  );
});

test("a Python-falsy top-level value is treated as no deviations, not a shape error", () => {
  withTempFile("false\n", (path) => {
    assert.deepEqual(loadDeviationRegister(path), []);
  });
});

test("deviations: null is a shape error, distinct from an absent deviations key", () => {
  withTempFile("deviations: null\n", (path) => {
    assert.throws(() => loadDeviationRegister(path), /deviations.*key is not a list/);
  });
  withTempFile("other: 1\n", (path) => {
    assert.deepEqual(loadDeviationRegister(path), []);
  });
});

test("a YAML syntax error maps to input.deviation_invalid, never a crash", () => {
  withTempFile("deviations: [\n", (path) => {
    assert.throws(() => loadDeviationRegister(path), /deviation_invalid|carries a YAML construct/);
  });
});

// --- parseGapsFile: Python's Unicode-aware \b -----------------------------------------------

test("parseGapsFile matches Python's Unicode-aware \\b, not JS's ASCII-only default", () => {
  // An accented-letter prefix, a leading em-dash, and a trailing non-ASCII digit: Python's
  // re.findall gives exactly 1 match (AB-12), never 3 -- confirmed against real Python this item.
  const matches = parseGapsFile("éAB-12 —CD-34 EF-56٠");
  assert.deepEqual([...matches].sort(), ["CD-34"]);
});

test("parseGapsFile deduplicates, matching set(re.findall(...))", () => {
  assert.deepEqual(
    [...parseGapsFile("OVS-03 owned by alice on 2026-02-01; also OVS-03")],
    ["OVS-03"],
  );
});

// --- normalizeDeviationDates -----------------------------------------------------------------

test("normalizeDeviationDates passes a plain string, number, or boolean through unchanged", () => {
  const out = normalizeDeviationDates([
    { control: "OVS-03", granted: "already a string", count: 3, ok: true },
  ]);
  assert.deepEqual(out, [{ control: "OVS-03", granted: "already a string", count: 3, ok: true }]);
});

// A report file that is not JSON of its shape reads NOT READY, never a crash and never READY (inbox
// row 393, 18.184h), and so does an inner coverage entry of the wrong shape (watch ruling 2026-10-21).
for (const [name, text] of [
  ["coverage.json", "null"],
  ["coverage.json", "[]"],
  ["coverage.json", '"x"'],
  ["coverage.json", "3"],
  ["coverage.json", ""],
  ["coverage.json", "{"],
  ["coverage.json", '{"subjects": []}'],
  ["coverage.json", '{"subjects": null}'],
  ["coverage.json", '{"subjects": {"s": 3}}'],
  ["coverage.json", '{"subjects": {"s": null}}'],
  ["coverage.json", '{"subjects": {"s": {"event_types": []}}}'],
  ["coverage.json", '{"subjects": {"s": {"event_types": {"t": 3}}}}'],
  ["assertions.json", "null"],
  ["assertions.json", "{}"],
  ["assertions.json", ""],
  ["assertions.json", "[3]"],
  ["assertions.json", "[null]"],
  ["integrity.jsonl", "not json\n"],
  ["integrity.jsonl", "3\n"],
  ["applicability.jsonl", "[]\n"],
  ["applicability.jsonl", '{"drift": {"kind": "k", "ref": "r"}}\n'],
  ["applicability.jsonl", '{"drift": "x"}\n'],
  ["applicability.jsonl", '{"drift": null}\n'],
  ["applicability.jsonl", '{"drift": [3]}\n'],
] as const) {
  test(`${name} reading ${JSON.stringify(text)} is NOT READY, named (18.184h)`, () => {
    const dir = report();
    writeFileSync(join(dir, name), text);
    const verdict = computeReadiness(dir, { severities: SEVERITIES });
    assert.equal(verdict.verdict, NOT_READY);
    assert.deepEqual(verdict.reasons, [`cannot read report file ${name}`]);
  });
}

test("a coverage.json that is not UTF-8 is NOT READY (18.184h)", () => {
  const dir = report();
  writeFileSync(join(dir, "coverage.json"), Buffer.from('{"subjects": {}}\xff', "latin1"));
  assert.deepEqual(computeReadiness(dir, { severities: SEVERITIES }).reasons, [
    "cannot read report file coverage.json",
  ]);
});

test("an empty coverage.json object stays READY (ruling 393)", () => {
  assert.equal(
    computeReadiness(report({ coverage: {} }), { severities: SEVERITIES }).verdict,
    READY,
  );
});

// A field the verdict is decided on that is not a JSON string (null included) makes its file
// unreadable, with or without a deviation register, never read loosely (18.184m, as Python).
for (const [name, text] of [
  ["integrity.jsonl", '{"status": ["broken"], "stream": "s"}\n'],
  ["integrity.jsonl", '{"status": null, "stream": "s"}\n'],
  ["assertions.json", '[{"control": ["DOC-01"], "outcome": "insufficient_evidence"}]'],
  ["assertions.json", '[{"control": "DOC-01", "outcome": 3}]'],
  ["assertions.json", '[{"control": "DOC-01", "window": "x"}]'],
  ["assertions.json", '[{"control": "DOC-01", "window": {"end": 3}}]'],
  ["assertions.json", '[{"control": "DOC-01", "deviation": ["D-1"]}]'],
  ["coverage.json", '{"subjects": {"s": {"coverage_status": ["gap"]}}}'],
  ["coverage.json", '{"subjects": {"s": {"event_types": {"t": {"status": true}}}}}'],
] as const) {
  for (const deviations of [undefined, [{ control: "DOC-01" }]]) {
    test(`${name} reading ${text.trim()} is NOT READY${deviations ? " under a register" : ""} (18.184m)`, () => {
      const dir = report();
      writeFileSync(join(dir, name), text);
      const verdict = computeReadiness(dir, {
        severities: SEVERITIES,
        deviations,
      });
      assert.equal(verdict.verdict, NOT_READY);
      assert.ok(
        verdict.reasons.includes(`cannot read report file ${name}`),
        verdict.reasons.join("; "),
      );
    });
  }
}

test("deviation: null stays readable (18.184m)", () => {
  const dir = report();
  writeFileSync(
    join(dir, "assertions.json"),
    '[{"control": "DOC-01", "outcome": "conformant", "deviation": null}]',
  );
  assert.equal(computeReadiness(dir, { severities: SEVERITIES }).verdict, READY);
});

for (const ruleCase of REPORT_FILE_RULE_CASES) {
  test(`one report-file rule (18.184q): ${ruleCase.id}`, () => {
    const dir = report();
    const content = ruleCaseContent(ruleCase);
    if (content === null) {
      unlinkSync(join(dir, ruleCase.file));
    } else {
      writeFileSync(join(dir, ruleCase.file), content);
    }
    assert.deepEqual(computeReadiness(dir, { severities: SEVERITIES }).reasons, ruleCase.readiness);
  });
}
