/** Bundle loading, schema validation, and ingest quarantine (cross-checked with the Python engine). */

import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { copyFileSync, mkdirSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { loadBundle } from "./bundle";
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

/** `sources` entries are a source id (declared with no class) or `[id, class]`. */
function writeBundle(
  events: Array<Record<string, unknown>>,
  sources: Array<string | [string, string]> | null,
): string {
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
    manifest.sources = sources.map((s) =>
      typeof s === "string" ? { id: s } : { id: s[0], class: s[1] },
    );
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

test("a manifest number the canonical form refuses is refused at load with input.bundle_manifest_invalid, as Python and Java refuse it", () => {
  for (const [token, detail] of [
    ["2.0", "non_integer_number: 2.0"],
    ["1e2", "non_integer_number: 100.0"],
    ["10000000000000001", "integer_out_of_range: 10000000000000001"],
  ] as const) {
    const dir = mkdtempSync(join(tmpdir(), "agentce-manifest-"));
    mkdirSync(join(dir, "events"));
    copyFileSync(join(BUNDLE, "events", "log.jsonl"), join(dir, "events", "log.jsonl"));
    const manifest = readFileSync(join(BUNDLE, "manifest.json"), "utf-8").replace(
      '"bundle_format": "1.0"',
      `"bundle_format": "1.0", "note": ${token}`,
    );
    writeFileSync(join(dir, "manifest.json"), manifest);
    // The bundle digest is the canonical manifest's (SPEC 8.1), so loading refuses it (bundle.py).
    assert.throws(
      () => loadBundle(dir),
      (err: unknown) =>
        err instanceof InputError &&
        err.key === "input.bundle_manifest_invalid" &&
        err.cause === `manifest.json cannot be put in canonical form: ${detail}.`,
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

test('a manifest class of "", null, or a non-string value is treated as undeclared (SPEC §6.4)', () => {
  // A manifest entry naming a source is not the same as declaring its class: an empty string, `null`,
  // or a non-string JSON value must get the same honest default as a source with no `class` key at
  // all, not be coerced or rejected -- a hostile or malformed manifest gets no special treatment.
  const sources = ["empty", "null", "number"].map(
    (suffix) => `urn:agentce:source:undeclared-${suffix}`,
  );
  const events = sources.map((source, index) => ({
    ...enforcementPointEvent(),
    source,
    id: `evt-undeclared-${index}`,
  }));
  const dir = writeBundle(events, null);
  const manifestPath = join(dir, "manifest.json");
  const manifest = JSON.parse(readFileSync(manifestPath, "utf-8")) as Record<string, unknown>;
  manifest.sources = [
    { id: sources[0], class: "" },
    { id: sources[1], class: null },
    { id: sources[2], class: 42 },
  ];
  writeFileSync(manifestPath, JSON.stringify(manifest));

  const result = ingest(loadBundle(dir));
  assert.equal(result.accepted.length, 3);
  assert.equal(result.quarantined.length, 0);
  for (const event of result.accepted) {
    assert.equal(event.agentcesourceclass, "self_report");
  }
});

test("a lower-class record never displaces a higher-class record with the same id (18.143)", () => {
  const genuine = enforcementPointEvent();
  const shadow = { ...genuine, source: "urn:example:agent", agentcesourceclass: "self_report" };
  const classes: Array<[string, string]> = [
    [String(genuine.source), "enforcement_point"],
    ["urn:example:agent", "self_report"],
  ];
  for (const [events, detail] of [
    [[shadow, genuine], "a record of a higher source class has this event id"],
    [[genuine, shadow], "event id already seen in this bundle"],
  ] as const) {
    const result = ingest(loadBundle(writeBundle([...events], classes)));
    assert.deepEqual(result.accepted, [genuine]);
    assert.deepEqual(
      result.quarantined.map((q) => [q.reason, q.source, q.detail]),
      [["duplicate_id", "urn:example:agent", detail]],
    );
  }
  // The class ingest trusts ranks, not the record's own: an undeclared source asserting
  // independent_system counts as self_report and still loses the id.
  const undeclared = { ...shadow, agentcesourceclass: "independent_system" };
  const result = ingest(
    loadBundle(
      writeBundle(
        [undeclared, genuine],
        ["urn:example:agent", [String(genuine.source), "enforcement_point"]],
      ),
    ),
  );
  assert.deepEqual(result.accepted, [genuine]);
  assert.equal(result.quarantined[0].source, "urn:example:agent");
});

test("a record never joins another source's time order by claiming its stream (18.147)", () => {
  const genuine = enforcementPointEvent();
  const hijack = {
    ...genuine,
    id: "evt-agent",
    source: "urn:example:agent",
    agentcesourceclass: "self_report",
    time: "2099-01-01T00:00:00.000Z",
    data: {
      ...(genuine.data as Record<string, unknown>),
      integrity: {
        hash: GENESIS_PREV,
        prev: GENESIS_PREV,
        stream: String(genuine.source),
        strength: "export_chained",
      },
    },
  };
  const classes: Array<[string, string]> = [
    [String(genuine.source), "enforcement_point"],
    ["urn:example:agent", "self_report"],
  ];
  const result = ingest(loadBundle(writeBundle([hijack, genuine], classes)));
  assert.deepEqual(
    result.accepted.map((e) => e.id),
    ["evt-agent", genuine.id],
  );
  assert.deepEqual(result.quarantined, []);
});

test("time order compares times as instants (18.145)", () => {
  // 00:30 two hours behind UTC is 02:30Z, after 01:00Z; 01:30 two hours ahead is 23:30Z the day before.
  const genuine = enforcementPointEvent();
  const at = (id: string, time: string) => ({ ...genuine, id, time });
  const first = at("evt-first", "2026-01-10T01:00:00Z");
  const later = at("evt-later", "2026-01-10T00:30:00-02:00");
  const earlier = at("evt-earlier", "2026-01-10T01:30:00+02:00");
  const classes: Array<[string, string]> = [[String(genuine.source), "enforcement_point"]];
  const result = ingest(loadBundle(writeBundle([first, later, earlier], classes)));
  assert.deepEqual(
    result.accepted.map((e) => e.id),
    ["evt-first", "evt-later"],
  );
  assert.deepEqual(
    result.quarantined.map((q) => [q.eventId, q.reason]),
    [["evt-earlier", "time_order"]],
  );
});

test("withheld lists the quarantined records that may hold evidence (18.119)", () => {
  // A record refused for where it sits stays readable; one ingest cannot read has no event; an identical redelivery
  // and an unknown type hold nothing new.
  const first = enforcementPointEvent();
  const late = { ...first, id: "evt-late", time: "2020-01-01T00:00:00.000Z" };
  const invalid = { ...first, id: "evt-invalid", time: 7 };
  const other = { ...first, time: "2030-01-01T00:00:00.000Z" };
  const unknown = {
    ...first,
    id: "evt-unknown",
    type: "org.agent-conformance.evidence.Heartbeat.v1",
  };
  const result = ingest(
    loadBundle(
      writeBundle([first, late, invalid, { ...first }, other, unknown], [String(first.source)]),
    ),
  );
  // File order, then each non-identical duplicate once every record is placed.
  assert.deepEqual(
    result.withheld.map((w) => w.record.reason),
    ["time_order", "schema_invalid", "duplicate_id"],
  );
  const [timeOrder, unreadable, duplicate] = result.withheld;
  // Each readable record is the event as readers see it: this undeclared source's class reads self_report.
  assert.deepEqual(timeOrder.event, { ...late, agentcesourceclass: "self_report" });
  assert.equal(timeOrder.subject, first.subject);
  assert.equal(unreadable.event, null);
  assert.equal(unreadable.subject, first.subject);
  assert.deepEqual(duplicate.event, { ...other, agentcesourceclass: "self_report" });
  assert.notEqual(duplicate.digest, null);
  assert.equal(result.quarantined.length, 5);
});

test("an oversize record is withheld with no event (18.119)", () => {
  const result = ingest(loadBundle(writeBundle([enforcementPointEvent()], null)), 10);
  assert.deepEqual(
    result.withheld.map((w) => [w.record.reason, w.event, w.subject]),
    [["oversize", null, null]],
  );
});

test("withheld keeps the raw facts of an unreadable write (18.172)", () => {
  // An unreadable record is a MemoryWrite unless it names another type the engine knows; its record_ref and store
  // are kept when each is a string.
  const evidence = (name: string) => `org.agent-conformance.evidence.${name}.v1`;
  const write = (id: string, type: unknown, data: Record<string, unknown>) => ({
    id,
    source: "urn:src:guard",
    subject: "spiffe://corp/agents/other",
    type,
    time: "2026-01-02T00:00:01Z",
    specversion: "1.0",
    data: { store: "kb", record_ref: "mem:r1", extra: 1, ...data },
  });
  const result = ingest(
    loadBundle(
      writeBundle(
        [
          write("w1", evidence("MemoryWrite"), { "@type": "MemoryWrite" }),
          write("w2", 5, {}),
          write("w3", evidence("MemoryWrit"), { "@type": "MemoryWrit" }),
          write("w4", evidence("MemoryRead"), { "@type": "MemoryRead" }),
          write("w5", 5, { "@type": "MemoryRead" }),
          write("w6", 5, { record_ref: 7, store: null }),
          write("w7", evidence("MemoryRead"), { "@type": "MemoryWrite" }),
        ],
        ["urn:src:guard"],
      ),
    ),
  );
  const kept = { recordRef: "mem:r1", store: "kb" };
  assert.deepEqual(
    result.withheld.map((w) => w.write ?? null),
    [kept, kept, kept, null, null, { recordRef: null, store: null }, kept],
  );
  assert.deepEqual(
    new Set(result.withheld.map((w) => w.subject)),
    new Set(["spiffe://corp/agents/other"]),
  );
});

test("an unknown version of MemoryWrite is withheld as unreadable (18.202)", () => {
  // An unknown_type record whose envelope type names MemoryWrite at any version or none, or whose data @type is
  // MemoryWrite, is withheld as an unreadable write; another unknown type is only quarantined. An empty record_ref
  // or store reads as absent.
  const v2 = (name: string) => `org.agent-conformance.evidence.${name}.v2`;
  const write = (id: string, type: string, data?: Record<string, unknown>) => ({
    id,
    source: "urn:src:guard",
    subject: "spiffe://corp/agents/other",
    type,
    time: "2026-01-02T00:00:01Z",
    specversion: "1.0",
    datacontenttype: "application/ld+json",
    agentcesourceclass: "enforcement_point",
    data: data ?? { "@type": "MemoryWrite", store: "kb", record_ref: "mem:r1" },
  });
  const read = { "@type": "MemoryRead", store: "kb", record_refs: ["mem:r1"] };
  const result = ingest(
    loadBundle(
      writeBundle(
        [
          write("w1", v2("MemoryWrite")),
          write("w2", v2("MemoryWrite"), read),
          write("w3", "com.example.Thing.v1"),
          write("w4", v2("MemoryRead"), read),
          write("w5", v2("MemoryWrite"), { "@type": "MemoryWrite", record_ref: "", store: "" }),
          write("w6", "org.agent-conformance.evidence.MemoryWrite", read),
          write("w7", "org.agent-conformance.evidence.MemoryWriteV2", read),
          write("w8", "org.agent-conformance.evidence.MemoryRead", read),
        ],
        ["urn:src:guard"],
      ),
    ),
  );
  assert.deepEqual(
    result.quarantined.map((q) => q.reason),
    Array(8).fill("unknown_type"),
  );
  assert.deepEqual(
    result.withheld.map((w) => [w.record.eventId, w.write ?? null]),
    [
      ["w1", { recordRef: "mem:r1", store: "kb" }],
      ["w2", { recordRef: null, store: "kb" }],
      ["w3", { recordRef: "mem:r1", store: "kb" }],
      ["w5", { recordRef: null, store: null }],
      ["w6", { recordRef: null, store: "kb" }],
      ["w7", { recordRef: null, store: "kb" }],
    ],
  );
});

/** A bundle of one raw JSON Lines line, for numbers `JSON.stringify` cannot write (9007199254740993). */
function writeRawBundle(line: string): string {
  const dir = mkdtempSync(join(tmpdir(), "agentce-ingest-"));
  mkdirSync(join(dir, "events"));
  const lines = `${line}\n`;
  writeFileSync(join(dir, "events", "stream.jsonl"), lines);
  const hash = createHash("sha256").update(lines).digest("hex");
  writeFileSync(
    join(dir, "manifest.json"),
    JSON.stringify({
      bundle_format: "1.0",
      files: [{ path: "events/stream.jsonl", sha256: `sha256:${hash}` }],
    }),
  );
  return dir;
}

function withContext(context: string): string {
  const event = enforcementPointEvent();
  const data = { ...(event.data as Record<string, unknown>), "@context": "__CTX__" };
  return JSON.stringify({ ...event, data }).replace('"__CTX__"', context);
}

// A record with no canonical form (SPEC 6.7) is quarantined schema_invalid at admission, never admitted for a later
// stage to crash on (census odd-inputs line 110, 18.184r). The detail is byte-identical in all three engines.
for (const [context, refusal] of [
  ["1.5", "non_integer_number: 1.5"],
  ['{"a": 1.5}', "non_integer_number: 1.5"],
  ['["x", 1.5]', "non_integer_number: 1.5"],
  ["9007199254740993", "integer_out_of_range: 9007199254740993"],
]) {
  test(`a record whose @context is ${context} has no canonical form and is quarantined schema_invalid`, () => {
    const result = ingest(loadBundle(writeRawBundle(withContext(context))));
    assert.equal(result.accepted.length, 0);
    assert.deepEqual(result.quarantined, [
      {
        reason: "schema_invalid",
        eventId: "evt-undeclared-source",
        source: "urn:agentce:source:gw:eu-1",
        type: "org.agent-conformance.evidence.ToolCall.v1",
        detail: `the record has no canonical form: ${refusal} (SPEC 6.7)`,
      },
    ]);
  });
}

test("a record whose @context is a safe integer is admitted", () => {
  const result = ingest(loadBundle(writeRawBundle(withContext("2"))));
  assert.equal(result.accepted.length, 1);
  assert.equal(result.quarantined.length, 0);
});
