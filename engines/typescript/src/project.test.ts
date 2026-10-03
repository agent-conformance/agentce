/**
 * `computeProjectView`: the per-agent rollup and undeclared-agents list (18.14, Hill 7).
 *
 * A faithful port of the Python reference's `tests/test_project.py`: same fixtures, same
 * assertions, so both engines are proven against the same cases.
 */

import assert from "node:assert/strict";
import { test } from "node:test";
import { makeAssertion } from "./assertions";
import type { BlindSpot, BlindSpots, CheckRef } from "./blindSpots";
import { blindSpotsBySubject, computeProjectView, noPopulationBySubject } from "./project";

const WINDOW: [string, string] = ["2026-01-01T00:00:00Z", "2026-02-01T00:00:00Z"];

function assertion(
  subject: string,
  control: string,
  outcome = "conformant",
  deviation: string | null = null,
) {
  return makeAssertion({
    control,
    controlVersion: "2026.09",
    subject,
    outcome,
    rung: 2,
    mode: "automated",
    window: WINDOW,
    population: [1, 0],
    severity: "high",
    family: "REC",
    deviation,
  });
}

function checkRef(subject: string, control: string): CheckRef {
  return { subject, catalog: "cat", control, control_version: "2026.09" };
}

function blindSpot(unlocked: CheckRef[], needed: CheckRef[], event = "Decision"): BlindSpot {
  return {
    event,
    class: "any",
    ladder_rung: 2,
    owner_key: "agent_team",
    step_kind: "code_change",
    supplying_adapters: [],
    checks_unlocked: unlocked.length,
    unlocked_checks: unlocked,
    needed_by: needed.length,
    needed_by_checks: needed,
  };
}

function profileWith(subjectIds: string[]) {
  return {
    profileVersion: 1,
    observationWindow: {},
    catalogs: [],
    subjects: subjectIds.map((id) => ({
      id,
      name: null,
      role: null,
      evidenceSources: [],
      coverageDenominators: [],
      declaredDecisionTypes: [],
      declaredOversight: {},
      declaredComponents: [],
      declaredTools: [],
      declaredModels: [],
    })),
  };
}

test("blindSpotsBySubject rescopes counts and refs", () => {
  const entry = blindSpot(
    [checkRef("A", "REC-01"), checkRef("A", "REC-02"), checkRef("B", "REC-03")],
    [],
  );
  const bySubject = blindSpotsBySubject({ blind_spots: [entry], no_population: [] });
  assert.deepEqual(
    (bySubject.get("A") as BlindSpot[])[0]?.unlocked_checks.map((cr) => cr.control),
    ["REC-01", "REC-02"],
  );
  assert.equal((bySubject.get("A") as BlindSpot[])[0]?.checks_unlocked, 2);
  assert.deepEqual(
    (bySubject.get("B") as BlindSpot[])[0]?.unlocked_checks.map((cr) => cr.control),
    ["REC-03"],
  );
  assert.equal((bySubject.get("B") as BlindSpot[])[0]?.checks_unlocked, 1);
  assert.equal(entry.checks_unlocked, 3);
});

test("blindSpotsBySubject drops entries that do not touch a subject", () => {
  const entry = blindSpot([checkRef("A", "REC-01")], []);
  const bySubject = blindSpotsBySubject({ blind_spots: [entry], no_population: [] });
  assert.equal(bySubject.has("B"), false);
});

test("noPopulationBySubject groups by own field", () => {
  const grouped = noPopulationBySubject([
    checkRef("A", "REC-04"),
    checkRef("B", "REC-05"),
    checkRef("A", "REC-06"),
  ]);
  assert.deepEqual(
    (grouped.get("A") as CheckRef[]).map((cr) => cr.control),
    ["REC-04", "REC-06"],
  );
  assert.deepEqual(
    (grouped.get("B") as CheckRef[]).map((cr) => cr.control),
    ["REC-05"],
  );
});

test("three-subject rollup: declared, undeclared, and top gaps", () => {
  const profile = profileWith(["A", "B"]);
  const assertions = [
    assertion("A", "REC-01", "conformant"),
    assertion("C", "REC-01", "insufficient_evidence"),
  ];
  const declaredSubjectIds = new Set(["A", "B"]);
  const activityBySubject = new Map<string, { agents: string[] }>([
    ["A", { agents: ["A"] }],
    ["B", { agents: [] }],
    ["C", { agents: ["C"] }],
  ]);
  const sharedGap = blindSpot([checkRef("A", "REC-01")], [checkRef("C", "REC-01")], "ModelCall");
  const otherGap = blindSpot([checkRef("B", "REC-02")], [], "ToolCall");
  const blindSpots: BlindSpots = { blind_spots: [sharedGap, otherGap], no_population: [] };

  const view = computeProjectView(
    assertions,
    profile,
    declaredSubjectIds,
    activityBySubject,
    blindSpots,
  );

  const rows = new Map(view.agents.map((row) => [row.id, row]));
  assert.deepEqual([...rows.keys()].sort(), ["A", "B", "C"]);
  assert.equal(rows.get("A")?.declared, true);
  assert.equal(rows.get("B")?.declared, true);
  assert.equal(rows.get("C")?.declared, false);
  assert.equal(rows.get("C")?.blind_spots_count, 1);
  assert.deepEqual(view.undeclared_agents, ["C"]);

  assert.equal(view.top_gaps.length, 2);
  assert.deepEqual(view.top_gaps[0]?.agents, ["A", "C"]);
  assert.deepEqual(view.top_gaps[1]?.agents, ["B"]);
  assert.equal(view.top_gaps[0]?.checks_unlocked, sharedGap.checks_unlocked);
});

test("single-subject profile still returns a valid one-row view", () => {
  const profile = profileWith(["A"]);
  const assertions = [assertion("A", "REC-01")];
  const view = computeProjectView(
    assertions,
    profile,
    new Set(["A"]),
    new Map([["A", { agents: ["A"] }]]),
    { blind_spots: [], no_population: [] },
  );
  assert.equal(view.agents.length, 1);
  assert.equal(view.agents[0]?.id, "A");
  assert.equal(view.agents[0]?.declared, true);
  assert.equal(view.agents[0]?.blind_spots_count, 0);
  assert.deepEqual(view.agents[0]?.agents_observed, ["A"]);
  assert.deepEqual(view.agents[0]?.deviations, []);
  assert.deepEqual(view.undeclared_agents, []);
  assert.deepEqual(view.top_gaps, []);
});

test("deviation surfaces per subject without leaking", () => {
  const profile = profileWith(["A", "B"]);
  const assertions = [
    assertion("A", "REC-01", "non-conformant", "REC-01"),
    assertion("B", "REC-02", "conformant"),
  ];
  const view = computeProjectView(
    assertions,
    profile,
    new Set(["A", "B"]),
    new Map([
      ["A", { agents: ["A"] }],
      ["B", { agents: ["B"] }],
    ]),
    { blind_spots: [], no_population: [] },
    [{ control: "REC-01", expiry: "2026-06-01T00:00:00.000Z" }],
  );
  const rows = new Map(view.agents.map((row) => [row.id, row]));
  assert.deepEqual(rows.get("A")?.deviations, [
    { control: "REC-01", expiry: "2026-06-01T00:00:00.000Z" },
  ]);
  assert.deepEqual(rows.get("B")?.deviations, []);
});

test("deviation omits unknown expiry", () => {
  const profile = profileWith(["A"]);
  const assertions = [assertion("A", "REC-01", "non-conformant", "REC-01")];
  const view = computeProjectView(
    assertions,
    profile,
    new Set(["A"]),
    new Map([["A", { agents: ["A"] }]]),
    { blind_spots: [], no_population: [] },
  );
  assert.deepEqual(view.agents[0]?.deviations, [{ control: "REC-01" }]);
});
