/**
 * `computeBlindSpots`: the one missing record type that would unlock the most checks (18.5, Hill 2).
 *
 * A faithful port of the Python reference's `tests/test_blind_spots.py`: the same grouping,
 * normalization, rung, and sort-order cases, so all three engines are proven against the same rules
 * (RFC 0008 is the operative spec).
 */

import assert from "node:assert/strict";
import { test } from "node:test";
import type { Assertion } from "./assertions";
import { makeAssertion } from "./assertions";
import type { BlindSpot, CheckRef } from "./blindSpots";
import { computeBlindSpots } from "./blindSpots";
import type { Catalog, ControlSpec } from "./catalog";
import type { Profile } from "./profile";
import { profileFromDict } from "./profile";

const SUBJECT = "spiffe://corp/agents/a";

function control(
  id: string,
  minimumEvidence: Array<Record<string, string>>,
  version = "1",
): ControlSpec {
  return {
    id,
    version,
    title: id,
    appliesToRoles: ["both"],
    mode: "automated",
    rung: 2,
    severity: "medium",
    minSourceClass: "any",
    minimumEvidence,
    shapePath: null,
    tolerance: { kind: "count", max: 0 },
    testCases: [],
    raw: {},
  };
}

function catalog(id: string, controls: ControlSpec[], version = "2026.09"): Catalog {
  return { id, version, directory: "", controls, shapes: new Map() };
}

function profileOneSubject(): Profile {
  return profileFromDict({
    catalogs: ["c@2026.09"],
    subjects: [{ id: SUBJECT, role: "both" }],
  });
}

function assertion(
  control_: ControlSpec,
  outcome: string,
  population: [number, number],
  subject = SUBJECT,
): Assertion {
  return makeAssertion({
    control: control_.id,
    controlVersion: control_.version,
    subject,
    outcome,
    rung: 2,
    mode: "automated",
    window: ["1970-01-01T00:00:00Z", "1970-01-01T00:00:00Z"],
    population,
    severity: "medium",
    family: control_.id.split("-", 1)[0] as string,
  });
}

function evt(eventType: string, sourceClass = "self_report"): Record<string, unknown> {
  return {
    id: `e-${eventType}-${sourceClass}`,
    subject: SUBJECT,
    agentcesourceclass: sourceClass,
    data: { "@type": eventType },
  };
}

test("a run with zero insufficient_evidence assertions writes the honest empty answer", () => {
  const c = control("A-01", [{ event: "ModelCall", class: "self_report" }]);
  const cat = catalog("c", [c]);
  const profile = profileOneSubject();
  const a = assertion(c, "conformant", [1, 0]);
  const result = computeBlindSpots([a], profile, [cat], []);
  assert.deepEqual(result, { blind_spots: [], no_population: [] });
});

test("any and self_report requirements normalize into one blind spot, never two", () => {
  const c1 = control("A-01", [{ event: "Decision", class: "any" }]);
  const c2 = control("A-02", [{ event: "Decision", class: "self_report" }]);
  const cat = catalog("c", [c1, c2]);
  const profile = profileOneSubject();
  const a1 = assertion(c1, "insufficient_evidence", [1, 0]);
  const a2 = assertion(c2, "insufficient_evidence", [1, 0]);
  const result = computeBlindSpots([a1, a2], profile, [cat], []);
  assert.equal(result.blind_spots.length, 1);
  assert.equal(result.blind_spots[0]?.class, "self_report");
  assert.equal(result.blind_spots[0]?.checks_unlocked, 2);
});

test("an assertion missing two distinct requirements gets no partial credit", () => {
  const c = control("A-01", [
    { event: "ModelCall", class: "self_report" },
    { event: "ApprovalDecided", class: "enforcement_point" },
  ]);
  const cat = catalog("c", [c]);
  const profile = profileOneSubject();
  const a = assertion(c, "insufficient_evidence", [1, 0]);
  const result = computeBlindSpots([a], profile, [cat], []);
  assert.equal(result.blind_spots.length, 2);
  for (const bs of result.blind_spots) {
    assert.equal(bs.checks_unlocked, 0);
    assert.equal(bs.needed_by, 1);
  }
});

test("an empty-population assertion with exactly one missing requirement is needed_by, never unlocked", () => {
  const c = control("A-01", [{ event: "ModelCall", class: "self_report" }]);
  const cat = catalog("c", [c]);
  const profile = profileOneSubject();
  const a = assertion(c, "insufficient_evidence", [0, 0]);
  const result = computeBlindSpots([a], profile, [cat], []);
  assert.equal(result.blind_spots.length, 1);
  assert.equal(result.blind_spots[0]?.checks_unlocked, 0);
  assert.equal(result.blind_spots[0]?.needed_by, 1);
});

test("a minimum-evidence assertion with exactly one missing requirement and a non-empty population unlocks it", () => {
  const c = control("A-01", [{ event: "ModelCall", class: "self_report" }]);
  const cat = catalog("c", [c]);
  const profile = profileOneSubject();
  const a = assertion(c, "insufficient_evidence", [1, 0]);
  const result = computeBlindSpots([a], profile, [cat], []);
  assert.equal(result.blind_spots[0]?.checks_unlocked, 1);
  assert.equal(result.blind_spots[0]?.needed_by, 0);
});

test("an insufficient_evidence assertion whose every requirement is already met goes to no_population", () => {
  const c = control("A-01", [{ event: "ModelCall", class: "self_report" }]);
  const cat = catalog("c", [c]);
  const profile = profileOneSubject();
  const a = assertion(c, "insufficient_evidence", [0, 0]);
  const events = [evt("ModelCall", "self_report")];
  const result = computeBlindSpots([a], profile, [cat], events);
  assert.equal(result.blind_spots.length, 0);
  assert.equal(result.no_population.length, 1);
  const entry = result.no_population[0] as CheckRef;
  assert.equal(entry.control, "A-01");
  assert.equal(entry.subject, SUBJECT);
});

test("ladder rung: independent_system is rung 4 regardless of any adapter", () => {
  const c = control("A-01", [{ event: "BundleLoaded", class: "independent_system" }]);
  const cat = catalog("c", [c]);
  const profile = profileOneSubject();
  const a = assertion(c, "insufficient_evidence", [1, 0]);
  const result = computeBlindSpots([a], profile, [cat], []);
  assert.equal(result.blind_spots[0]?.ladder_rung, 4);
  assert.equal(result.blind_spots[0]?.owner_key, "ticketing_or_iam");
});

test("ladder rung: an exact enforcement_point requirement is rung 3", () => {
  const c = control("A-01", [{ event: "ToolCall", class: "enforcement_point" }]);
  const cat = catalog("c", [c]);
  const profile = profileOneSubject();
  const a = assertion(c, "insufficient_evidence", [1, 0]);
  const result = computeBlindSpots([a], profile, [cat], []);
  assert.equal(result.blind_spots[0]?.ladder_rung, 3);
  assert.equal(result.blind_spots[0]?.owner_key, "platform_or_security");
  // otel-genai's ToolCall default is self_report, which cannot satisfy an exact enforcement_point key.
  assert.equal(result.blind_spots[0]?.supplying_adapters.includes("otel-genai"), false);
  assert.ok(result.blind_spots[0]?.supplying_adapters.includes("mcp-gateway"));
});

test("ladder rung: an event only enforcement_point-default adapters supply is rung 3 even for a self_report key", () => {
  const c = control("A-01", [{ event: "AuthzCheck", class: "any" }]);
  const cat = catalog("c", [c]);
  const profile = profileOneSubject();
  const a = assertion(c, "insufficient_evidence", [1, 0]);
  const result = computeBlindSpots([a], profile, [cat], []);
  assert.equal(result.blind_spots[0]?.ladder_rung, 3);
});

test("ladder rung: an OpenTelemetry-shaped self_report event is rung 1", () => {
  const c = control("A-01", [{ event: "ToolCall", class: "self_report" }]);
  const cat = catalog("c", [c]);
  const profile = profileOneSubject();
  const a = assertion(c, "insufficient_evidence", [1, 0]);
  const result = computeBlindSpots([a], profile, [cat], []);
  assert.equal(result.blind_spots[0]?.ladder_rung, 1);
  assert.equal(result.blind_spots[0]?.owner_key, "agent_team");
});

test("ladder rung: a non-OpenTelemetry self_report event is rung 2", () => {
  const c = control("A-01", [{ event: "ApprovalDecided", class: "self_report" }]);
  const cat = catalog("c", [c]);
  const profile = profileOneSubject();
  const a = assertion(c, "insufficient_evidence", [1, 0]);
  const result = computeBlindSpots([a], profile, [cat], []);
  assert.equal(result.blind_spots[0]?.ladder_rung, 2);
});

test("ladder rung: an event no adapter declares at all is rung 2, not a vacuous rung 3", () => {
  const c = control("A-01", [{ event: "Decision", class: "self_report" }]);
  const cat = catalog("c", [c]);
  const profile = profileOneSubject();
  const a = assertion(c, "insufficient_evidence", [1, 0]);
  const result = computeBlindSpots([a], profile, [cat], []);
  assert.equal(result.blind_spots[0]?.ladder_rung, 2);
  assert.deepEqual(result.blind_spots[0]?.supplying_adapters, []);
});

test("cross-key non-goal: a rung-3 blind spot's checks_unlocked never absorbs a separate rung-1 blind spot's check", () => {
  const rung1 = control("A-01", [{ event: "ToolCall", class: "any" }]);
  const rung3 = control("A-02", [{ event: "ToolCall", class: "enforcement_point" }]);
  const cat = catalog("c", [rung1, rung3]);
  const profile = profileOneSubject();
  const a1 = assertion(rung1, "insufficient_evidence", [1, 0]);
  const a2 = assertion(rung3, "insufficient_evidence", [1, 0]);
  const result = computeBlindSpots([a1, a2], profile, [cat], []);
  assert.equal(result.blind_spots.length, 2);
  const byRung = new Map(result.blind_spots.map((bs) => [bs.ladder_rung, bs]));
  assert.equal((byRung.get(3) as BlindSpot).checks_unlocked, 1);
  assert.equal((byRung.get(1) as BlindSpot).checks_unlocked, 1);
});

test("two blind spots with equal counts sort by (ladder_rung, event, class), not discovery order", () => {
  const c1 = control("A-01", [{ event: "ResourceAccess", class: "self_report" }]);
  const c2 = control("A-02", [{ event: "ModelCall", class: "self_report" }]);
  const cat = catalog("c", [c1, c2]);
  const profile = profileOneSubject();
  const a1 = assertion(c1, "insufficient_evidence", [1, 0]);
  const a2 = assertion(c2, "insufficient_evidence", [1, 0]);
  const result = computeBlindSpots([a1, a2], profile, [cat], []);
  assert.deepEqual(
    result.blind_spots.map((bs) => bs.event),
    ["ModelCall", "ResourceAccess"],
  );
});

test("two catalogs sharing a control id and version never collide", () => {
  const c = control("SHARED-01", [{ event: "ModelCall", class: "self_report" }]);
  const catA = catalog("cat-a", [c]);
  const catB = catalog("cat-b", [c]);
  const profile = profileOneSubject();
  const aA = assertion(c, "insufficient_evidence", [1, 0]);
  const aB = assertion(c, "conformant", [1, 0]);
  const result = computeBlindSpots([aA, aB], profile, [catA, catB], []);
  assert.equal(result.blind_spots.length, 1);
  assert.equal(result.blind_spots[0]?.checks_unlocked, 1);
  assert.equal(result.blind_spots[0]?.unlocked_checks[0]?.catalog, "cat-a");
});

test("a length mismatch between assertions and the replayed triples is refused loudly", () => {
  const c = control("A-01", [{ event: "ModelCall", class: "self_report" }]);
  const cat = catalog("c", [c]);
  const profile = profileOneSubject();
  assert.throws(() => computeBlindSpots([], profile, [cat], []));
});

test("a positional mismatch between assertions and the replayed sequence is refused loudly", () => {
  const c = control("A-01", [{ event: "ModelCall", class: "self_report" }]);
  const cat = catalog("c", [c]);
  const profile = profileOneSubject();
  const wrong = assertion(c, "insufficient_evidence", [1, 0], "spiffe://corp/agents/other");
  assert.throws(() => computeBlindSpots([wrong], profile, [cat], []));
});

test("repeated calls with the same input are deterministic (no clock or locale dependence)", () => {
  const c = control("A-01", [{ event: "ModelCall", class: "self_report" }]);
  const cat = catalog("c", [c]);
  const profile = profileOneSubject();
  const a = assertion(c, "insufficient_evidence", [1, 0]);
  const first = computeBlindSpots([a], profile, [cat], []);
  const second = computeBlindSpots([a], profile, [cat], []);
  assert.deepEqual(first, second);
});
