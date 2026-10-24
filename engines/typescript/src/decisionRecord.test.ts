/**
 * The eight decision-record literals DOC-02, TRN-04, OVS-05, RSK-01, REC-05, TRN-01 and TRN-05 read (18.37f), over
 * `decision-record-cases.json`, the table engines/python/tests/test_graph_decision_record.py keeps in sync.
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { test } from "node:test";
import { DomainBinding } from "./domain";
import { buildGraph } from "./graph";

const table = JSON.parse(
  readFileSync(join(__dirname, "..", "testdata", "decision-record-cases.json"), "utf-8"),
);
const domain = DomainBinding.fromDict(table.domain);

for (const row of table.cases) {
  test(`decision record: ${row.name}`, () => {
    const store = buildGraph(row.events, { domain });
    assert.deepEqual(store.literalValues(table.decision, `agentce:${row.literal}`), [row.want]);
  });
}
