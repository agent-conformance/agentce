/**
 * The test seams (18.108): small computations the cross-engine gates and tools drive directly
 * (`node dist/seams.js <seam> <path>`, `pnpm seams <seam> <path>` in development). They ship with
 * the package so the installed-artifact check can run them, but they are not `agentce` commands.
 */
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import type { Activity } from "./activity";
import { aggregate, assertionFromJson } from "./assertions";
import { computeAuditorView } from "./auditorView";
import type { BuyerView } from "./buyerView";
import { CanonicalizationError, canonicalString } from "./canonical";
import { main as cliMain } from "./cli";
import { InputError } from "./errors";
import { ExitCode } from "./exitCodes";
import { parseFailOn } from "./failOn";
import type { Event } from "./graph";
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
import { renderRemediationMd } from "./remediation";
import {
  AUDIENCE_VIEWS,
  SEAM_EMIT,
  SEAM_FOR,
  appliedDeviationIds,
  catalogProvenanceDigest,
  renderBuyerHtml,
  renderBuyerMd,
  renderCsv,
  renderJunit,
  renderOscalXml,
  renderPdf,
  renderPublicStatement,
  renderSecurityHtml,
  renderSecurityMd,
} from "./report";
import { type SecurityView, computeSecurityView } from "./securityView";
import {
  renderReverifyMd,
  renderSkillFindingMd,
  renderSkillMd,
  skillFindingFilename,
  toolNamesForSubject,
} from "./skill";
import { joinPath } from "./util";

type Json = Record<string, unknown>;

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

  // report-formats is the same kind of test-only seam (18.16c, VG-REPORT-FORMAT-PARITY): for each
  // report directory, it renders report.junit.xml, report.csv, oscal-ar.xml, public-statement.md and
  // report.pdf from that directory's assertions.json, oscal-ar.json and manifest (catalogs,
  // run.started_at, run.report_language), the way Python's write_report does, into
  // <out-root>/<index>/. `assess --emit` reaches them with 18.16b.
  if (command === "report-formats") {
    const [outRoot, ...sources] = argv.slice(1);
    if (outRoot === undefined || sources.length === 0) {
      console.error("report-formats: <out-root> and at least one report directory are required");
      return ExitCode.INPUT_ERROR;
    }
    sources.forEach((source, index) => {
      const read = (name: string) => readBundled(readFileSync(joinPath(source, name)));
      const assertions = (read("assertions.json") as unknown[]).map(assertionFromJson);
      const manifest = read("manifest.json") as {
        inputs: { catalogs: Record<string, string>[] };
        run: { started_at: string; report_language: string };
      };
      const catalogs = manifest.inputs.catalogs.map((c) => `${c.id}@${c.version}`);
      const out = joinPath(outRoot, String(index));
      mkdirSync(out, { recursive: true });
      const rendered: Array<[string, string]> = [
        ["report.junit.xml", renderJunit(assertions)],
        ["report.csv", renderCsv(assertions)],
        ["oscal-ar.xml", renderOscalXml(read("oscal-ar.json") as Record<string, unknown>)],
        [
          "public-statement.md",
          renderPublicStatement(assertions, catalogs, appliedDeviationIds(assertions)),
        ],
      ];
      for (const [name, text] of rendered) {
        writeFileSync(joinPath(out, name), text, "utf-8");
      }
      writeFileSync(
        joinPath(out, "report.pdf"),
        renderPdf(
          assertions,
          aggregate(assertions),
          manifest.run.started_at,
          manifest.run.report_language,
        ),
      );
    });
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

  // assess-emit runs this engine's real `assess` with extra formats written (18.16e): until 18.16b wires
  // `assess --emit` here, report_format_parity_check.py compares the remediation output through it.
  if (command === "assess-emit") {
    const [formats, ...assessArgv] = argv.slice(1);
    const names = (formats ?? "").split(",");
    const presets = names.filter((name) => name.startsWith("for=")).map((name) => name.slice(4));
    const emits = names.filter((name) => !name.startsWith("for="));
    if (
      formats === undefined ||
      emits.some((name) => name !== "remediation" && name !== "skill") ||
      presets.length > 1 ||
      presets.some((preset) => !Object.hasOwn(AUDIENCE_VIEWS, preset))
    ) {
      console.error(
        "assess-emit: the formats are remediation and skill, plus at most one for=<registered view>",
      );
      return ExitCode.INPUT_ERROR;
    }
    for (const name of emits) {
      SEAM_EMIT.add(name);
    }
    SEAM_FOR.preset = presets[0];
    try {
      return cliMain(assessArgv);
    } finally {
      SEAM_EMIT.clear();
      SEAM_FOR.preset = undefined;
    }
  }

  // security-cases renders every case of a case file ({cases: [{name, language, view}]}) with the
  // security view's renderers and writes {"<name>.md": text, "<name>.html": text} as JSON (18.16g).
  if (command === "security-cases") {
    const [casefile, outPath] = argv.slice(1);
    if (casefile === undefined || outPath === undefined) {
      console.error("security-cases: a case file and an output path are required");
      return ExitCode.INPUT_ERROR;
    }
    const cases = (
      readBundled(readFileSync(casefile)) as {
        cases: { name: string; language: string; view: SecurityView }[];
      }
    ).cases;
    const rendered: Record<string, string> = {};
    for (const c of cases) {
      rendered[`${c.name}.md`] = renderSecurityMd(c.view, c.language);
      rendered[`${c.name}.html`] = renderSecurityHtml(c.view, c.language);
    }
    writeFileSync(outPath, JSON.stringify(rendered), "utf-8");
    return 0;
  }

  // buyer-cases renders every case of a case file ({cases: [{name, language, view, packaged, folder,
  // argv, catalogs: [[id, version]]}]}) with the buyer view's renderers and writes {"<name>.md": text,
  // "<name>.html": text} as JSON (18.16h).
  if (command === "buyer-cases") {
    const [casefile, outPath] = argv.slice(1);
    if (casefile === undefined || outPath === undefined) {
      console.error("buyer-cases: a case file and an output path are required");
      return ExitCode.INPUT_ERROR;
    }
    const cases = (
      readBundled(readFileSync(casefile)) as {
        cases: {
          name: string;
          language: string;
          view: BuyerView;
          packaged: boolean;
          folder: string;
          argv: string[];
          catalogs: [string, string][];
        }[];
      }
    ).cases;
    const rendered: Record<string, string> = {};
    for (const c of cases) {
      const options = {
        language: c.language,
        argv: c.argv,
        packaged: c.packaged,
        reportFolder: c.folder,
        catalogs: c.catalogs.map(([id, version]) => `${id}@${version}`),
      };
      rendered[`${c.name}.md`] = renderBuyerMd(c.view, options);
      rendered[`${c.name}.html`] = renderBuyerHtml(c.view, options);
    }
    writeFileSync(outPath, JSON.stringify(rendered), "utf-8");
    return 0;
  }

  // skill-cases renders every case of a case file ({cases: [{name, kind, ...}]}: skill, finding, reverify,
  // tool_names, filename) with the skill folder's functions and writes {name: text} as JSON (18.16f).
  if (command === "skill-cases") {
    const [casefile, outPath] = argv.slice(1);
    if (casefile === undefined || outPath === undefined) {
      console.error("skill-cases: a case file and an output path are required");
      return ExitCode.INPUT_ERROR;
    }
    const cases = (readBundled(readFileSync(casefile)) as { cases: Json[] }).cases;
    const rendered = Object.fromEntries(cases.map((c) => [c.name, skillCase(c)]));
    writeFileSync(outPath, JSON.stringify(rendered), "utf-8");
    return 0;
  }

  // remediation-md renders every package in a case file ({cases: [{name, package}]}) with
  // renderRemediationMd and writes {name: markdown} as JSON to the output path (18.16e).
  if (command === "remediation-md") {
    const [casefile, outPath] = argv.slice(1);
    if (casefile === undefined || outPath === undefined) {
      console.error("remediation-md: a case file and an output path are required");
      return ExitCode.INPUT_ERROR;
    }
    const cases = (
      readBundled(readFileSync(casefile)) as { cases: { name: string; package: Json }[] }
    ).cases;
    const rendered = Object.fromEntries(cases.map((c) => [c.name, renderRemediationMd(c.package)]));
    writeFileSync(outPath, JSON.stringify(rendered), "utf-8");
    return 0;
  }

  console.error(`seams: unknown seam '${command ?? ""}'`);
  return ExitCode.INPUT_ERROR;
}

/** One skill_cases.json case through the skill folder function its kind names. */
export function skillCase(c: Json): string {
  switch (c.kind) {
    case "skill":
      return renderSkillMd(
        c.spec_version as string,
        c.cli_version as string,
        c.catalog_version as string,
        c.assertions_digest as string,
      );
    case "finding":
      return renderSkillFindingMd(c.finding as Json, c.tool_calls as string[]);
    case "reverify":
      return renderReverifyMd(c.argv as string[]);
    case "tool_names":
      return toolNamesForSubject(c.events as Event[]).join("\n");
    case "filename":
      return skillFindingFilename(c.control as string, c.index as number);
    default:
      throw new Error(`skill-cases: unknown kind ${String(c.kind)}`);
  }
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
