/**
 * Where the engine finds the data it ships (SPEC §13.4 AX-1).
 *
 * The base and overlay catalogs and the quickstart project are vendored under `data/`, resolved
 * relative to this module so an installed package resolves them from wherever it is installed and a
 * source checkout resolves the same bytes. `bundledData.test.ts` holds the vendored tree
 * byte-identical to its `spec/`/`corpus/` original. This is a faithful port of the Python reference
 * (`bundled.py`).
 */

import { join } from "node:path";

function dataRoot(): string {
  return join(__dirname, "..", "data");
}

/** The vendored catalogs, laid out as `base/<catalog>/` and `overlays/<catalog>/`. */
export function catalogsDir(): string {
  return join(dataRoot(), "catalogs");
}

/** The vendored quickstart project: an evidence bundle, an applicability profile, and a domain. */
export function quickstartDir(): string {
  return join(dataRoot(), "corpus", "quickstart");
}
