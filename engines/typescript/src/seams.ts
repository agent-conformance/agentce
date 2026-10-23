/**
 * The test seams (18.108): small computations the cross-engine gates and tools drive directly
 * (`node dist/seams.js <seam> <path>`, `pnpm seams <seam> <path>` in development). They ship with
 * the package so the installed-artifact check can run them, but they are not `agentce` commands.
 */
import { readFileSync } from "node:fs";
import type { Activity } from "./activity";
import { assertionFromJson } from "./assertions";
import { computeAuditorView } from "./auditorView";
import { CanonicalizationError, canonicalString } from "./canonical";
import { InputError } from "./errors";
import { ExitCode } from "./exitCodes";
import { parseFailOn } from "./failOn";
import {
  JsonProfileError,
  type NonCanonicalNumber,
  isJsonInteger,
  jsonEqual,
  readBundled,
  readJson,
  readJsonl,
  renderNumber,
  renderValue,
} from "./json";
import { computeVectorFile } from "./numerics";
import { OtelGenaiAdapterError, adapt as adaptOtelGenai } from "./otelGenai";
import { catalogProvenanceDigest } from "./report";
import { computeSecurityView } from "./securityView";
import { joinPath } from "./util";

export function main(argv: string[]): number {
  const command = argv[0];

  // The numerics verb is a plain computation seam for the two-engine vector check (P3.1): it reads a
  // `numerics-vectors` case file and prints {caseName: result} as plain JSON, not the envelope.
  if (command === "numerics") {
    const casefile = argv[1];
    if (casefile === undefined) {
      console.error("numerics: a case file path is required");
      return ExitCode.INPUT_ERROR;
    }
    const data = readBundled(readFileSync(casefile));
    console.log(JSON.stringify(computeVectorFile(data)));
    return 0;
  }

  // digest-tree is a plain computation seam (the same pattern as `numerics` above), driven from
  // outside the repo's TypeScript sources by `tools/catalog_digest_check.py` against the built
  // `dist/`: it prints one catalog directory's real content digest, nothing else.
  if (command === "digest-tree") {
    const dir = argv[1];
    if (dir === undefined) {
      console.error("digest-tree: a directory path is required");
      return ExitCode.INPUT_ERROR;
    }
    console.log(catalogProvenanceDigest(dir));
    return 0;
  }

  // security-view is a plain computation seam (the same pattern as `numerics`/`digest-tree` above),
  // driven by the security-view build gate's cross-engine diff (18.16, C5): it reads a fixture file
  // with `{activity, assertions}`, runs `computeSecurityView`, and prints the result as JSON -- not
  // part of the public `assess` command surface (no engine here has `--for`/multi-format
  // `write_report` yet, TRADEOFFS row 8).
  if (command === "security-view") {
    const fixturePath = argv[1];
    if (fixturePath === undefined) {
      console.error("security-view: a fixture file path is required");
      return ExitCode.INPUT_ERROR;
    }
    const data = readBundled(readFileSync(fixturePath));
    const activity = data.activity as Activity;
    const assertions = (data.assertions as unknown[]).map(assertionFromJson);
    console.log(JSON.stringify(computeSecurityView(activity, assertions)));
    return 0;
  }

  // auditor-view is the same kind of test-only seam (18.17a, VG-DEVIATIONS-PARITY): it reads a fixture
  // file with `{assertions, deviations}`, runs `computeAuditorView`, and prints canonical JSON, the
  // bytes Python's `auditor.json` holds -- not part of the public command surface.
  if (command === "auditor-view") {
    const fixturePath = argv[1];
    if (fixturePath === undefined) {
      console.error("auditor-view: a fixture file path is required");
      return ExitCode.INPUT_ERROR;
    }
    const data = readBundled(readFileSync(fixturePath));
    const assertions = (data.assertions as unknown[]).map(assertionFromJson);
    console.log(canonicalString(computeAuditorView(assertions, data.deviations ?? null)));
    return 0;
  }

  // fail-on-check is the same kind of test-only seam (18.73): it reads a fixture file with
  // `{assertions, expressions}` and, per expression in order, prints one canonical JSON line -- the
  // refusal's `{error: {key, cause, fix}}`, or `{matched: [...]}` with one boolean per assertion --
  // the objects Python's `parse_fail_on` gives. Not part of the public command surface.
  if (command === "fail-on-check") {
    const fixturePath = argv[1];
    if (fixturePath === undefined) {
      console.error("fail-on-check: a fixture file path is required");
      return ExitCode.INPUT_ERROR;
    }
    const data = readBundled(readFileSync(fixturePath));
    const assertions = (data.assertions as unknown[]).map(assertionFromJson);
    for (const expression of data.expressions as string[]) {
      let line: Record<string, unknown>;
      try {
        line = { matched: assertions.map(parseFailOn(expression)) };
      } catch (exc) {
        if (!(exc instanceof InputError)) {
          throw exc;
        }
        line = { error: { key: exc.key, cause: exc.cause, fix: exc.fix } };
      }
      console.log(canonicalString(line));
    }
    return 0;
  }

  // otel-genai-fixture is a plain computation seam (the same pattern as `security-view` above),
  // driven by the otel-genai adapter's cross-engine parity check (18.29, C3/C4): it reads
  // `<dir>/input.json` and `<dir>/adapt.json` the same way the Python reference's fixtures module
  // does, calls `adapt`, and for each emitted event (in the adapter's own pinned time/id order)
  // attempts `canonicalString`; on success prints `EVENT <result>`. On a `CanonicalizationError`,
  // nothing further goes to stdout -- `ERROR canonical:<reason>` goes to stderr and the process exits
  // 1, so the check can tell "the adapter accepted this but the event cannot be serialised" apart
  // from "the adapter refused the whole document" (an `OtelGenaiAdapterError`, printed the same way
  // without the `canonical:` prefix). On a clean run, one final `REPORT <json>` line follows every
  // `EVENT` line.
  if (command === "otel-genai-fixture") {
    const dir = argv[1];
    if (dir === undefined) {
      console.error("otel-genai-fixture: a directory path is required");
      return ExitCode.INPUT_ERROR;
    }
    const inputBytes = readFileSync(joinPath(dir, "input.json"));
    const adaptArgs = readBundled(readFileSync(joinPath(dir, "adapt.json")));
    try {
      const result = adaptOtelGenai(inputBytes, {
        subject: adaptArgs.subject,
        sourceClass: adaptArgs.source_class,
        source: adaptArgs.source,
      });
      for (const event of result.events) {
        let line: string;
        try {
          line = canonicalString(event);
        } catch (exc) {
          if (exc instanceof CanonicalizationError) {
            console.error(`ERROR canonical:${exc.reason}`);
            return 1;
          }
          throw exc;
        }
        console.log(`EVENT ${line}`);
      }
      console.log(
        `REPORT ${JSON.stringify({
          adapter: result.report.adapter,
          conventions: result.report.conventions,
          spans_seen: result.report.spansSeen,
          events_emitted: result.report.eventsEmitted,
          skipped: result.report.skipped.map((s) => ({
            name: s.name,
            reason: s.reason,
            span_id: s.spanId,
          })),
        })}`,
      );
      return 0;
    } catch (exc) {
      if (exc instanceof OtelGenaiAdapterError) {
        console.error(`ERROR ${exc.reason}`);
        return 1;
      }
      throw exc;
    }
  }

  // json-cases is the JSON profile seam (SPEC 8.7, 18.184x) for tools/json_parity_check.py: every row of
  // the shared case table through this engine's reader, printed as one result per row in table order,
  // the shape Python's `agentce.tools.json_cases` and Java's `json-cases` seam print.
  if (command === "json-cases") {
    const casefile = argv[1];
    if (casefile === undefined) {
      console.error("json-cases: a case file path is required");
      return ExitCode.INPUT_ERROR;
    }
    const cases = (readBundled(readFileSync(casefile)) as { cases: JsonCase[] }).cases;
    console.log(JSON.stringify(cases.map((c) => [c.id, jsonCaseResult(c)])));
    return 0;
  }

  console.error(`seams: unknown seam '${command ?? ""}'`);
  return ExitCode.INPUT_ERROR;
}

interface JsonCase {
  id: string;
  rule: string;
  mode?: "json" | "jsonl";
  bom?: "ignore" | "refuse";
  text?: string;
  hex?: string;
  input?: string;
  left?: string;
  right?: string;
}

const jsonValue = (text: string): unknown => readJson(Buffer.from(text, "utf8"), "refuse");

function jsonReaderResult(c: JsonCase): Record<string, unknown> {
  const raw = c.hex !== undefined ? Buffer.from(c.hex, "hex") : Buffer.from(c.text ?? "", "utf8");
  const bom = c.bom ?? "refuse";
  try {
    if (c.mode === "json") {
      return { canonical: renderValue(readJson(raw, bom)) };
    }
    const lines = readJsonl(raw, bom);
    const bad = lines.find(([, v]) => v instanceof JsonProfileError);
    if (bad !== undefined) {
      const exc = bad[1] as JsonProfileError;
      return { refuse: [exc.rule, exc.line] };
    }
    return { records: lines.map(([n]) => n) };
  } catch (exc) {
    if (exc instanceof JsonProfileError) {
      return { refuse: [exc.rule, exc.line] };
    }
    throw exc;
  }
}

function jsonCaseResult(c: JsonCase): Record<string, unknown> {
  if (c.mode !== undefined) {
    return jsonReaderResult(c);
  }
  switch (c.rule) {
    case "number_rendering":
      return {
        rendered: renderNumber(jsonValue(c.input as string) as number | NonCanonicalNumber),
      };
    case "compare_by_value":
    case "compare_across_types": {
      const left = jsonValue(c.left as string);
      const right = jsonValue(c.right as string);
      return { equal: [jsonEqual(left, right), jsonEqual(right, left)] };
    }
    case "integer_field_float_form":
      return { integer: isJsonInteger(jsonValue(c.text as string)) };
    case "non_integer_number":
      try {
        canonicalString(jsonValue(c.text as string));
      } catch (exc) {
        if (exc instanceof CanonicalizationError) {
          return { refusal: exc.message };
        }
        throw exc;
      }
      return { refusal: null };
    default:
      return { unknown_rule: c.rule };
  }
}

if (require.main === module) {
  process.exit(main(process.argv.slice(2)));
}
