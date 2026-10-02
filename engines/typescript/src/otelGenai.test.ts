/** The TypeScript otel-genai port matches the Python reference on every fixture and hostile vector. */

import assert from "node:assert/strict";
import { existsSync, readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { test } from "node:test";
import { CanonicalizationError, canonicalString } from "./canonical";
import { type AdaptResult, OtelGenaiAdapterError, adapt } from "./otelGenai";

const FIXTURES_DIR = join(__dirname, "..", "..", "..", "adapters", "otel-genai", "fixtures");
const HOSTILE_DIR = join(
  __dirname,
  "..",
  "..",
  "..",
  "spec",
  "model",
  "test-vectors",
  "otel-genai-hostile",
);

interface AdaptArgs {
  subject: string;
  source_class?: string;
  source?: string;
}

function loadAdaptArgs(dir: string): AdaptArgs {
  return JSON.parse(readFileSync(join(dir, "adapt.json"), "utf-8"));
}

function runAdapt(dir: string): AdaptResult {
  const bytes = readFileSync(join(dir, "input.json"));
  const args = loadAdaptArgs(dir);
  return adapt(bytes, {
    subject: args.subject,
    sourceClass: args.source_class,
    source: args.source,
  });
}

function readExpectedEvents(dir: string): Record<string, unknown>[] {
  return readFileSync(join(dir, "expected.jsonl"), "utf-8")
    .split("\n")
    .filter((line) => line.trim().length > 0)
    .map((line) => JSON.parse(line));
}

function reportAsJson(result: AdaptResult): Record<string, unknown> {
  return {
    adapter: result.report.adapter,
    conventions: result.report.conventions,
    spans_seen: result.report.spansSeen,
    events_emitted: result.report.eventsEmitted,
    skipped: result.report.skipped.map((s) => ({
      name: s.name,
      reason: s.reason,
      span_id: s.spanId,
    })),
  };
}

const fixtureNames = readdirSync(FIXTURES_DIR).sort();

test("the vendor fixture set is present", () => {
  assert.ok(fixtureNames.length >= 9);
});

for (const name of fixtureNames) {
  test(`vendor fixture ${name}`, () => {
    const dir = join(FIXTURES_DIR, name);
    const result = runAdapt(dir);
    assert.deepStrictEqual(result.events, readExpectedEvents(dir));
  });
}

const hostileNames = readdirSync(HOSTILE_DIR).sort();

test("the hostile vector set is present", () => {
  assert.ok(hostileNames.length >= 18);
});

for (const name of hostileNames) {
  test(`hostile vector ${name}`, () => {
    const dir = join(HOSTILE_DIR, name);
    const errorPath = join(dir, "expected-error.json");
    if (existsSync(errorPath)) {
      const expected = JSON.parse(readFileSync(errorPath, "utf-8")) as { reason: string };
      if (expected.reason.startsWith("canonical:")) {
        const wantReason = expected.reason.slice("canonical:".length);
        const result = runAdapt(dir);
        let threw = false;
        for (const event of result.events) {
          try {
            canonicalString(event);
          } catch (exc) {
            assert.ok(exc instanceof CanonicalizationError);
            assert.equal((exc as CanonicalizationError).reason, wantReason);
            threw = true;
          }
        }
        assert.ok(threw, "expected at least one event to fail canonicalization");
        return;
      }
      assert.throws(
        () => runAdapt(dir),
        (err: unknown) => err instanceof OtelGenaiAdapterError && err.reason === expected.reason,
      );
      return;
    }
    const result = runAdapt(dir);
    assert.deepStrictEqual(result.events, readExpectedEvents(dir));
    const reportPath = join(dir, "expected-report.json");
    if (existsSync(reportPath)) {
      assert.deepStrictEqual(reportAsJson(result), JSON.parse(readFileSync(reportPath, "utf-8")));
    }
  });
}

/** A minimal OTLP/JSON document with one span carrying a single `startTimeUnixNano`. */
function docWithTimestamp(unixNanos: string): Uint8Array {
  const doc = {
    resourceSpans: [
      {
        resource: { attributes: [{ key: "service.name", value: { stringValue: "svc" } }] },
        scopeSpans: [
          {
            scope: { name: "x" },
            schemaUrl: "https://opentelemetry.io/schemas/1.30.0",
            spans: [
              {
                traceId: "t",
                spanId: "s",
                name: "chat x",
                startTimeUnixNano: unixNanos,
                attributes: [{ key: "gen_ai.operation.name", value: { stringValue: "chat" } }],
              },
            ],
          },
        ],
      },
    ],
  };
  return new TextEncoder().encode(JSON.stringify(doc));
}

test("timestamp truncation: year 9999-12-31 is kept, year 10000-01-01 is dropped", () => {
  const kept = adapt(docWithTimestamp("253402300799000000000"), { subject: "s" });
  assert.equal(kept.events.length, 1);
  assert.equal(kept.events[0].time, "9999-12-31T23:59:59.000Z");

  const dropped = adapt(docWithTimestamp("253402300800000000000"), { subject: "s" });
  assert.equal(dropped.events.length, 0);
  assert.equal(dropped.report.skipped.length, 1);
  // The pre-existing `unrecognised_operation`-not-`missing_operation` mislabeling (ported
  // byte-for-byte from the reference, MAINTAINER-INBOX row 48): a recognised operation with no
  // usable timestamp is skipped under the same reason a truly unrecognised operation would be.
  assert.equal(dropped.report.skipped[0].reason, "unrecognised_operation");
});

test("timestamp formatting is clock/locale independent (TZ=Pacific/Kiritimati)", () => {
  const previousTz = process.env.TZ;
  try {
    process.env.TZ = "Pacific/Kiritimati"; // UTC+14, the furthest-ahead civil time zone
    const result = adapt(docWithTimestamp("1734000000123000000"), { subject: "s" });
    assert.equal(result.events.length, 1);
    assert.equal(result.events[0].time, "2024-12-12T10:40:00.123Z");
  } finally {
    if (previousTz === undefined) {
      Reflect.deleteProperty(process.env, "TZ");
    } else {
      process.env.TZ = previousTz;
    }
  }
});
