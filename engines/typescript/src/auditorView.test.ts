/**
 * The auditor view (18.17a) is a port of `agentce/auditor_view.py`: these tests pin the selection's
 * order, the `by_clause` index, the manual note and the deviation detail. Byte-identity with Python's
 * own output over a real run is VG-DEVIATIONS-PARITY's job (the `auditor-view` seam).
 */

import assert from "node:assert/strict";
import { test } from "node:test";
import { type Assertion, makeAssertion } from "./assertions";
import { computeAuditorView } from "./auditorView";
import { catalogue } from "./messages";

function assertion(control: string, subject: string, fields: Partial<Assertion> = {}): Assertion {
  return makeAssertion({
    control,
    controlVersion: "2026.09",
    subject,
    outcome: "conformant",
    rung: 2,
    mode: "automated",
    window: ["2026-01-01T00:00:00Z", "2026-02-01T00:00:00Z"],
    population: [1, 0],
    severity: "high",
    family: control.slice(0, 3),
    ...fields,
  });
}

const REGISTER = [
  {
    control: "AUV-01",
    rationale: "Accepted pending remediation. <script>alert(1)</script>",
    compensating_control: "Manual quarterly review.",
    owner: "user:ops-lead@example.com",
    approver: "user:ciso@example.com",
    granted: "2026-01-01T00:00:00.000Z",
    expiry: "2026-06-01T00:00:00.000Z",
    evidence_refs: ["reference/plan.md"],
  },
];

test("computeAuditorView sorts clauses by (control, subject) whatever the input order", () => {
  const view = computeAuditorView([
    assertion("AUV-02", "spiffe://b"),
    assertion("AUV-01", "spiffe://b"),
    assertion("AUV-02", "spiffe://a"),
    assertion("AUV-01", "spiffe://a"),
  ]);
  const order = (view.clauses as Record<string, unknown>[]).map((c) => `${c.control} ${c.subject}`);
  assert.deepEqual(order, [
    "AUV-01 spiffe://a",
    "AUV-01 spiffe://b",
    "AUV-02 spiffe://a",
    "AUV-02 spiffe://b",
  ]);
});

test("computeAuditorView indexes by_clause by (framework, clause) with deduplicated, sorted control ids", () => {
  const xw = (framework: string, clause: string) => ({ framework, clause });
  const view = computeAuditorView([
    assertion("AUV-02", "spiffe://a", { crosswalk: [xw("iso", "9.1"), xw("eu", "Art.12")] }),
    assertion("AUV-01", "spiffe://a", { crosswalk: [xw("eu", "Art.12")] }),
    assertion("AUV-01", "spiffe://b", { crosswalk: [xw("eu", "Art.12"), xw("eu", "Art.10")] }),
  ]);
  assert.deepEqual(view.by_clause, {
    eu: { "Art.10": ["AUV-01"], "Art.12": ["AUV-01", "AUV-02"] },
    iso: { "9.1": ["AUV-02"] },
  });
  assert.deepEqual(Object.keys(view.by_clause as object), ["eu", "iso"]);
  assert.deepEqual(Object.keys((view.by_clause as Record<string, object>).eu as object), [
    "Art.10",
    "Art.12",
  ]);
});

test("computeAuditorView gives a not-assessed manual control the disclosed note, and no other", () => {
  const view = computeAuditorView([
    assertion("AUV-01", "spiffe://a", { mode: "manual", outcome: "not_assessed" }),
    assertion("AUV-02", "spiffe://a", { mode: "semi-automated", outcome: "not_assessed" }),
    assertion("AUV-03", "spiffe://a", { mode: "manual", outcome: "conformant" }),
    assertion("AUV-04", "spiffe://a", { mode: "automated", outcome: "not_assessed" }),
  ]);
  const notes = (view.clauses as Record<string, unknown>[]).map((c) => c.manual_checklist_note);
  const note = catalogue()["report.manual_checklist_not_yet_evaluated"];
  assert.deepEqual(notes, [note, note, undefined, undefined]);
});

test("computeAuditorView copies the register entry verbatim for a deviated clause", () => {
  const view = computeAuditorView(
    [assertion("AUV-01", "spiffe://a", { outcome: "partial", deviation: "AUV-01" })],
    REGISTER,
  );
  const [clause] = view.clauses as Record<string, unknown>[];
  const { control: _control, ...detail } = REGISTER[0] as Record<string, unknown>;
  assert.deepEqual(clause?.deviation, detail);
});

test("computeAuditorView without a register names only the control in the deviation", () => {
  const view = computeAuditorView([
    assertion("AUV-01", "spiffe://a", { outcome: "partial", deviation: "AUV-01" }),
  ]);
  const [clause] = view.clauses as Record<string, unknown>[];
  assert.deepEqual(clause?.deviation, { control: "AUV-01" });
});

test("computeAuditorView counts every outcome unless the caller passes its own counts", () => {
  const assertions = [
    assertion("AUV-01", "spiffe://a"),
    assertion("AUV-02", "spiffe://a", { outcome: "non-conformant" }),
  ];
  const own = computeAuditorView(assertions).counts as Record<string, number>;
  assert.equal(own.conformant, 1);
  assert.equal(own["non-conformant"], 1);
  assert.deepEqual(computeAuditorView(assertions, null, { conformant: 7 }).counts, {
    conformant: 7,
  });
});
