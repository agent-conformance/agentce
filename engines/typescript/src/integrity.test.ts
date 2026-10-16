/**
 * Integrity verification is byte-identical to the Python reference (SPEC §6.6).
 *
 * `testdata/integrity-{fixture,golden}.json` were emitted by the reference engine's `verify_bundle`
 * over streams exercising every status branch (verified, verified_weak, failed, gap, reordered,
 * time_suspect). The TypeScript port must reproduce the same result objects; the canonical form makes
 * the comparison value-exact and key-order-independent.
 */

import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { canonicalString } from "./canonical";
import {
  GENESIS_PREV,
  integrityCaps,
  integrityResultToJson,
  recomputeHash,
  verifyBundle,
  verifyStreams,
} from "./integrity";
import { parseJson } from "./json";

const TESTDATA = join(__dirname, "..", "testdata");

test("integrity results match the Python reference golden", () => {
  const fixture = JSON.parse(readFileSync(join(TESTDATA, "integrity-fixture.json"), "utf-8"));
  const golden = JSON.parse(readFileSync(join(TESTDATA, "integrity-golden.json"), "utf-8"));
  const results = verifyBundle(fixture.events, fixture.manifest).map(integrityResultToJson);
  assert.equal(canonicalString(results), canonicalString(golden));
});

function signedEvent(sigRef: string | null): Record<string, unknown> {
  const integrity: Record<string, unknown> = {
    hash: "",
    prev: GENESIS_PREV,
    stream: "s-signed",
    strength: "source_signed",
  };
  if (sigRef !== null) {
    integrity.sig_ref = sigRef;
  }
  const event: Record<string, unknown> = {
    id: "sg1",
    source: "src-1",
    subject: "subj-A",
    time: "2026-01-01T00:00:00.000Z",
    data: { "@type": "Decision", integrity },
  };
  integrity.hash = recomputeHash(event);
  return event;
}

test("a source_signed stream is verified with a sig_ref and unsigned without one", () => {
  const withSig = verifyBundle([signedEvent("attestations/sg1.sig")], {}, null);
  assert.equal(withSig[0]?.status, "verified");
  const withoutSig = verifyBundle([signedEvent(null)], {}, null);
  assert.equal(withoutSig[0]?.status, "unsigned");
});

test("a record never breaks another source's chain by claiming its stream", () => {
  // 18.151: chains are keyed on (source, claimed stream), so the agent's bad-hash record naming the gateway's stream
  // gets its own result and leaves the gateway's chain unbroken; with two sources on one id, neither reads verified.
  const head = signedEvent("attestations/sg1.sig");
  const block = (e: Record<string, unknown>) =>
    (e.data as Record<string, unknown>).integrity as Record<string, unknown>;
  const tail = { ...signedEvent("attestations/sg2.sig"), id: "sg2" };
  block(tail).prev = block(head).hash;
  block(tail).hash = recomputeHash(tail);
  const hijack = { ...signedEvent(null), id: "x9", source: "agent-1" };
  block(hijack).hash = "f".repeat(64);
  block(hijack).prev = "e".repeat(64);
  const statuses = (events: Array<Record<string, unknown>>) =>
    verifyBundle(events, {}, null).map((r) => [r.stream, r.status]);
  assert.deepEqual(statuses([head, hijack, tail]), [
    ["s-signed", "failed"],
    ["s-signed", "verified_weak"],
  ]);
  assert.deepEqual(statuses([head, tail]), [["s-signed", "verified"]]);
});

// --- A sig_ref is evidence content: a signature file outside the bundle root never counts,
// --- whether it is reached literally or through a symlink (SPEC §6.6, §8.1).

/** The stream status for a `sig_ref` resolved against a real temporary bundle root. */
function statusForSigRef(sigRef: string, prepare: (dir: string, root: string) => void): string {
  const dir = mkdtempSync(join(tmpdir(), "agentce-integrity-"));
  try {
    const root = join(dir, "bundle");
    mkdirSync(join(root, "attestations"), { recursive: true });
    prepare(dir, root);
    return verifyBundle([signedEvent(sigRef)], {}, root)[0]?.status ?? "";
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

test("a sig_ref escaping the bundle root with '..' is not counted as signed", () => {
  const status = statusForSigRef("../sig.bin", (dir) => {
    writeFileSync(join(dir, "sig.bin"), "signature bytes\n", "utf-8");
  });
  assert.equal(status, "unsigned");
});

test("a sig_ref whose symlink escapes the bundle root is not counted as signed", () => {
  const status = statusForSigRef("attestations/sg1.sig", (dir, root) => {
    const outside = join(dir, "sig.bin");
    writeFileSync(outside, "signature bytes\n", "utf-8");
    symlinkSync(outside, join(root, "attestations", "sg1.sig"));
  });
  assert.equal(status, "unsigned");
});

test("a sig_ref inside the bundle root is counted as signed", () => {
  const status = statusForSigRef("attestations/sg1.sig", (_dir, root) => {
    writeFileSync(join(root, "attestations", "sg1.sig"), "signature bytes\n", "utf-8");
  });
  assert.equal(status, "verified");
});

test("a sig_ref that is missing from the bundle is not counted as signed", () => {
  const status = statusForSigRef("attestations/absent.sig", () => {});
  assert.equal(status, "unsigned");
});

const CAP_STREAM = "urn:stream:gw";

function capChain(count: number, strength: string): Array<Record<string, unknown>> {
  const events: Array<Record<string, unknown>> = [];
  let prev = "0".repeat(64);
  for (let i = 0; i < count; i += 1) {
    const block: Record<string, unknown> = { prev, stream: CAP_STREAM, strength };
    const event: Record<string, unknown> = {
      id: `e${i}`,
      source: "urn:src:gw",
      subject: "s",
      time: `2026-01-01T00:0${i}:00Z`,
      data: { "@type": "ToolCall", integrity: block },
    };
    block.hash = recomputeHash(event);
    prev = block.hash as string;
    events.push(event);
  }
  return events;
}

function anchoredAt(at: string): Record<string, unknown> {
  return { streams: [{ stream: CAP_STREAM, anchors: [{ kind: "rfc3161", ref: "r", at }] }] };
}

function classed(
  events: Array<Record<string, unknown>>,
  cls: string,
): Array<Record<string, unknown>> {
  return events.map((e) => ({ ...e, agentcesourceclass: cls }));
}

test("time check: an anchor time with no UTC offset, or none, anchors nothing (critic r2 N1)", () => {
  for (const at of ["2026-01-01T09:00:00", ""]) {
    const [result] = verifyBundle(capChain(2, "export_anchored"), anchoredAt(at));
    assert.equal(result?.status, "time_suspect");
    assert.equal(result?.firstBadIndex, null);
    assert.equal(result?.strengthStatus, "verified_weak");
  }
});

test("time check: an anchor of the wrong type anchors nothing, and is written out as read", () => {
  const placeable = "2026-01-01T09:00:00Z";
  for (const anchor of [
    "1.5",
    "true",
    "null",
    `["${placeable}"]`,
    '{"at":1.5}',
    `{"at":["${placeable}"]}`,
  ]) {
    const manifest = parseJson(
      `{"streams":[{"stream":"${CAP_STREAM}","anchors":[${anchor}]}]}`,
    ) as Record<string, unknown>;
    const [result] = verifyBundle(capChain(2, "export_anchored"), manifest);
    assert.ok(result);
    assert.equal(result.status, "time_suspect");
    assert.equal(result.strengthStatus, "verified_weak");
    assert.deepEqual(integrityResultToJson(result).anchors, [JSON.parse(anchor)]);
  }
});

test("time check: a suspect stream keeps its strength reading (critic r1 F1)", () => {
  const [result] = verifyBundle(capChain(2, "source_signed"), anchoredAt("2026-01-01T00:00:30Z"));
  assert.equal(result?.status, "time_suspect");
  assert.equal(result?.firstBadIndex, 1);
  assert.equal(result?.strengthStatus, "unsigned");
});

test("integrity cap: broken streams cap enforcement-point and independent-system records, never self_report", () => {
  const tampered = capChain(2, "source_signed");
  tampered[0] = { ...tampered[0], note: "changed after it was hashed" };
  for (const cls of ["enforcement_point", "independent_system"]) {
    const caps = integrityCaps(tampered, classed(tampered, cls), verifyStreams(tampered, {}));
    assert.deepEqual([...caps.broken].sort(), ["e0", "e1"]);
  }
  const own = integrityCaps(
    tampered,
    classed(tampered, "self_report"),
    verifyStreams(tampered, {}),
  );
  assert.equal(own.broken.size + own.weak.size + own.timeSuspect.size, 0);
  const missing = [{ ...capChain(1, "source_signed")[0], data: { "@type": "ToolCall" } }];
  const caps = integrityCaps(
    missing,
    classed(missing, "enforcement_point"),
    verifyStreams(missing, {}),
  );
  assert.deepEqual([...caps.broken], ["e0"]);
});

test("integrity cap: weak streams cap only enforcement-point records, and a suspect weak stream stays weak", () => {
  const weak = capChain(2, "export_chained");
  const ep = integrityCaps(weak, classed(weak, "enforcement_point"), verifyStreams(weak, {}));
  assert.deepEqual([...ep.weak].sort(), ["e0", "e1"]);
  const is = integrityCaps(weak, classed(weak, "independent_system"), verifyStreams(weak, {}));
  assert.equal(is.weak.size, 0);
  const results = verifyStreams(weak, anchoredAt("2026-01-01T00:00:30Z"));
  const suspect = integrityCaps(weak, classed(weak, "enforcement_point"), results);
  assert.deepEqual([...suspect.timeSuspect].sort(), ["e0", "e1"]);
  assert.deepEqual([...suspect.weak].sort(), ["e0", "e1"]);
});
