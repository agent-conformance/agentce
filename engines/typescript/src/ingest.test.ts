/** Bundle loading, schema validation, and ingest quarantine (cross-checked with the Python engine). */

import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { copyFileSync, mkdirSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { loadBundle } from "./bundle";
import { CanonicalizationError } from "./canonical";
import { InputError } from "./errors";
import { ingest } from "./ingest";
import { GENESIS_PREV, IntegrityStatus, recomputeHash, verifyBundle } from "./integrity";
import { countsByReason } from "./quarantine";
import { eventTypes, validateEvent } from "./schema";

const BUNDLE = join(__dirname, "..", "testdata", "ingest-bundle");

/** A schema-valid ToolCall event, self-asserting `agentcesourceclass`, for the undeclared-source
 * trust-class tests below (SPEC §6.4, item 18.31). */
function enforcementPointEvent(): Record<string, unknown> {
  return {
    specversion: "1.0",
    id: "evt-undeclared-source",
    source: "urn:agentce:source:gw:eu-1",
    subject: "spiffe://corp/agents/a",
    time: "2026-05-01T08:00:00.000Z",
    type: "org.agent-conformance.evidence.ToolCall.v1",
    datacontenttype: "application/ld+json",
    agentcesourceclass: "enforcement_point",
    data: {
      "@context": "https://agent-conformance.org/contexts/evidence/v1",
      "@type": "ToolCall",
      tool: { name: "t" },
    },
  };
}

function writeBundle(events: Array<Record<string, unknown>>, sources: string[] | null): string {
  const dir = mkdtempSync(join(tmpdir(), "agentce-ingest-"));
  mkdirSync(join(dir, "events"));
  const lines = `${events.map((e) => JSON.stringify(e)).join("\n")}\n`;
  writeFileSync(join(dir, "events", "stream.jsonl"), lines);
  const hash = createHash("sha256").update(lines).digest("hex");
  const manifest: Record<string, unknown> = {
    bundle_format: "1.0",
    files: [{ path: "events/stream.jsonl", sha256: `sha256:${hash}` }],
  };
  if (sources !== null) {
    manifest.sources = sources.map((id) => ({ id }));
  }
  writeFileSync(join(dir, "manifest.json"), JSON.stringify(manifest));
  return dir;
}

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

test("a manifest number the canonical form refuses is refused on digest, not folded to a whole number, and never blocks loading", () => {
  for (const [token, reason] of [
    ["2.0", "non_integer_number"],
    ["1e2", "non_integer_number"],
    ["10000000000000001", "integer_out_of_range"],
  ] as const) {
    const dir = mkdtempSync(join(tmpdir(), "agentce-manifest-"));
    mkdirSync(join(dir, "events"));
    copyFileSync(join(BUNDLE, "events", "log.jsonl"), join(dir, "events", "log.jsonl"));
    const manifest = readFileSync(join(BUNDLE, "manifest.json"), "utf-8").replace(
      '"bundle_format": "1.0"',
      `"bundle_format": "1.0", "note": ${token}`,
    );
    writeFileSync(join(dir, "manifest.json"), manifest);
    // `digest` is lazy (18.65): loading, and ingest, never touch it, matching the Python
    // reference's `@property`. It still throws the same way once something reads it.
    const bundle = loadBundle(dir);
    assert.equal(ingest(bundle).accepted.length, 1, token);
    assert.throws(
      () => bundle.digest,
      (err: unknown) => err instanceof CanonicalizationError && err.reason === reason,
      token,
    );
  }
});

test("a source declared without a class defaults its event to self_report (SPEC §6.4)", () => {
  const event = enforcementPointEvent();
  const dir = writeBundle([event], [String(event.source)]);
  const result = ingest(loadBundle(dir));
  assert.equal(result.accepted.length, 1);
  assert.equal(result.quarantined.length, 0);
  assert.equal(result.accepted[0]?.agentcesourceclass, "self_report");
});

test("a bundle with no declared classes at all defaults every event to self_report", () => {
  const event = enforcementPointEvent();
  const dir = writeBundle([event], null);
  const result = ingest(loadBundle(dir));
  assert.equal(result.accepted.length, 1);
  assert.equal(result.accepted[0]?.agentcesourceclass, "self_report");
});

test("an undeclared source already self_report is unchanged (accepted and raw are the same object)", () => {
  const event: Record<string, unknown> = {
    ...enforcementPointEvent(),
    agentcesourceclass: "self_report",
  };
  const dir = writeBundle([event], [String(event.source)]);
  const result = ingest(loadBundle(dir));
  assert.equal(result.accepted.length, 1);
  assert.equal(result.accepted[0]?.agentcesourceclass, "self_report");
  assert.equal(result.accepted[0], result.rawAccepted[0]);
});

test("an undeclared source's trust-class correction never reaches rawAccepted, which integrity hashes", () => {
  // SPEC §6.6 hashes the whole CloudEvent, agentcesourceclass included, as the source emitted it:
  // rawAccepted must stay byte-identical so a genuinely unmodified, undeclared-source event still
  // verifies, while the corrected copy (accepted) would wrongly look tampered.
  const event = enforcementPointEvent();
  const stream = "urn:agentce:source:gw:eu-1|spiffe://corp/agents/a";
  const data = event.data as Record<string, unknown>;
  data.integrity = { prev: GENESIS_PREV, stream, strength: "export_chained" };
  data.integrity = { ...(data.integrity as Record<string, unknown>), hash: recomputeHash(event) };
  const dir = writeBundle([event], [String(event.source)]);
  const bundle = loadBundle(dir);
  const result = ingest(bundle);
  assert.equal(result.accepted[0]?.agentcesourceclass, "self_report");
  assert.equal(result.rawAccepted[0]?.agentcesourceclass, "enforcement_point");

  const verified = verifyBundle(result.rawAccepted, bundle.manifest, bundle.root);
  assert.deepEqual(
    verified.map((r) => r.status),
    [IntegrityStatus.VERIFIED_WEAK],
  );

  const tamperedView = verifyBundle(result.accepted, bundle.manifest, bundle.root);
  assert.deepEqual(
    tamperedView.map((r) => r.status),
    [IntegrityStatus.FAILED],
  );
});
