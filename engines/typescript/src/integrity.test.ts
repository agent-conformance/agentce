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
import { GENESIS_PREV, integrityResultToJson, recomputeHash, verifyBundle } from "./integrity";

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
