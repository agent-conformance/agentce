/**
 * The six literals REC-03, DAT-01, TRN-02, INT-04, ROB-01 and ROB-04 read (18.37g), three on a Decision and three on a
 * ToolCall, over
 * `call-record-cases.json`, the table engines/python/tests/test_graph_call_record.py keeps in sync.
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { test } from "node:test";
import { DomainBinding } from "./domain";
import { buildGraph } from "./graph";

const table = JSON.parse(
  readFileSync(join(__dirname, "..", "testdata", "call-record-cases.json"), "utf-8"),
);
const domain = DomainBinding.fromDict(table.domain);

for (const row of table.cases) {
  test(`call record: ${row.name}`, () => {
    const store = buildGraph(row.events, { domain });
    assert.deepEqual(store.literalValues(row.node, `agentce:${row.literal}`), [row.want]);
  });
}
