/**
 * assessSubjects scans the accepted-event list a number of times independent of subject count.
 */

import assert from "node:assert/strict";
import { test } from "node:test";
import { assessSubjects } from "./assess";
import { DomainBinding } from "./domain";
import { profileFromDict } from "./profile";

const EVENT_COUNT = 300;
const MANY_SUBJECTS = 60;

type Event = Record<string, unknown>;

function countingProxy(target: Event[], counter: { n: number }): Event[] {
  return new Proxy(target, {
    get(t, prop, receiver) {
      if (typeof prop === "string" && /^\d+$/.test(prop)) {
        counter.n += 1;
      }
      return Reflect.get(t, prop, receiver);
    },
  });
}

function events(): Event[] {
  return Array.from({ length: EVENT_COUNT }, (_, i) => ({
    id: `e${i}`,
    subject: "ghost",
    time: "2024-01-01T00:00:00Z",
    agentcesourceclass: "operator",
    data: { "@type": "ToolCall" },
  }));
}

function scans(numSubjects: number): number {
  const counter = { n: 0 };
  const profile = profileFromDict({
    subjects: Array.from({ length: numSubjects }, (_, j) => ({ id: `s${j}` })),
  });
  const accepted = countingProxy(events(), counter);
  assessSubjects(accepted, profile, [], DomainBinding.empty());
  return counter.n;
}

test("accepted-event scans do not grow with subject count", () => {
  const one = scans(1);
  const many = scans(MANY_SUBJECTS);
  assert.ok(one > 0);
  assert.equal(many, one);
});
