/** Bundle loading, schema validation, and ingest quarantine (cross-checked with the Python engine). */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { test } from "node:test";
import { loadBundle } from "./bundle";
import { InputError } from "./errors";
import { ingest } from "./ingest";
import { countsByReason } from "./quarantine";
import { eventTypes, validateEvent } from "./schema";

const BUNDLE = join(__dirname, "..", "testdata", "ingest-bundle");

test("bundle digest is the canonical SHA-256 of the manifest (matches the reference)", () => {
  assert.equal(
    loadBundle(BUNDLE).digest,
    "sha256:3b1a171abe9f0d3f1945a353c4b608d1f7e8bbaf526053f7280c9f92d8ee94be",
  );
});

test("ingest accepts valid events and quarantines the rest with reference reasons", () => {
  const result = ingest(loadBundle(BUNDLE));
  assert.equal(result.accepted.length, 1);
  assert.deepEqual(countsByReason(result.quarantined), { duplicate_id: 1, schema_invalid: 1 });
});

test("loadBundle refuses a bundle with no manifest", () => {
  assert.throws(
    () => loadBundle(join(BUNDLE, "events")),
    (err: unknown) => err instanceof InputError && err.key === "input.bundle_manifest_missing",
  );
});

test("the vendored evidence schema is byte-identical to the generated artifact", () => {
  const vendored = readFileSync(join(__dirname, "..", "schema", "agentce-evidence.schema.json"));
  const generated = readFileSync(
    join(
      __dirname,
      "..",
      "..",
      "..",
      "spec",
      "model",
      "generated",
      "json-schema",
      "agentce-evidence.schema.json",
    ),
  );
  assert.ok(vendored.equals(generated), "vendored schema has drifted from spec/model/generated");
});

test("the EventType enum is read from the schema", () => {
  const types = eventTypes();
  assert.ok(types.has("Decision"));
  assert.ok(types.has("ToolCall"));
  assert.ok(!types.has("Nonsense"));
});

test("validateEvent rejects an unknown payload type", () => {
  const errors = validateEvent({
    specversion: "1.0",
    id: "x",
    source: "s",
    type: "org.agent-conformance.evidence.Decision.v1",
    time: "2026-06-01T00:00:00.000Z",
    subject: "sub",
    datacontenttype: "application/ld+json",
    agentcesourceclass: "self_report",
    data: { "@type": "Nonsense" },
  });
  assert.ok(errors.length > 0);
});
