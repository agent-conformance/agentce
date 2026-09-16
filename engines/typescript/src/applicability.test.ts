/**
 * Applicability resolution is byte-identical to the Python reference (SPEC §6.5, §7.3).
 *
 * `testdata/applicability-{fixture,golden}.json` were emitted by the reference engine's `resolve` over
 * two subjects (roles `both` and `deployer`), controls in every role class, and events that drift from
 * the declared scope (an undeclared decision type, undeclared components, an undeclared subject).
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { test } from "node:test";
import { type ControlMeta, effectiveRoles, resolve } from "./applicability";
import { canonicalString } from "./canonical";
import { profileFromDict } from "./profile";

const TESTDATA = join(__dirname, "..", "testdata");

test("applicability statements match the Python reference golden", () => {
  const fixture = JSON.parse(readFileSync(join(TESTDATA, "applicability-fixture.json"), "utf-8"));
  const golden = JSON.parse(readFileSync(join(TESTDATA, "applicability-golden.json"), "utf-8"));
  const profile = profileFromDict(fixture.profile);
  const controls: ControlMeta[] = fixture.controls;
  const statements = resolve(profile, fixture.events, controls);
  assert.equal(canonicalString(statements), canonicalString(golden));
});

test("effectiveRoles expands roles per IR-11", () => {
  assert.deepEqual(effectiveRoles("both"), ["deployer", "provider"]);
  assert.deepEqual(effectiveRoles("provider"), ["provider"]);
  assert.deepEqual(effectiveRoles("deployer"), ["deployer"]);
  assert.deepEqual(effectiveRoles(null), ["deployer"]);
  assert.deepEqual(effectiveRoles("unknown"), ["deployer"]);
});
