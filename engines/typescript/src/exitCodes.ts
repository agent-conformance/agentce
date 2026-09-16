/**
 * The common exit-code scheme for every AgentCE command (SPEC §8.5).
 *
 * `0` success with no findings requiring action; `1` findings requiring action; `2` insufficient
 * evidence on any `severity: high` control (assess only); `3` input, version, or
 * signature-verification error. When several apply the highest code is returned and the JSON output
 * carries all of them. Every code has a stable name so automation never scrapes prose.
 */

export enum ExitCode {
  OK = 0,
  FINDINGS = 1,
  INSUFFICIENT_EVIDENCE = 2,
  INPUT_ERROR = 3,
}

const NAMES = new Map<number, string>([
  [ExitCode.OK, "ok"],
  [ExitCode.FINDINGS, "findings"],
  [ExitCode.INSUFFICIENT_EVIDENCE, "insufficient_evidence"],
  [ExitCode.INPUT_ERROR, "input_error"],
]);

/** The stable name of a code, or throw for an unknown code. */
export function nameOf(code: number): string {
  const name = NAMES.get(code);
  if (name === undefined) {
    throw new Error(`unknown exit code ${code}`);
  }
  return name;
}

/** The highest applicable code; an empty set means OK (SPEC §8.5). */
export function combine(codes: Iterable<number>): number {
  let highest = ExitCode.OK;
  for (const code of codes) {
    if (!NAMES.has(code)) {
      throw new Error(`unknown exit code ${code}`);
    }
    highest = Math.max(highest, code);
  }
  return highest;
}

/** The sorted, de-duplicated applicable codes; OK only when nothing else applies. */
export function applicable(codes: Iterable<number>): number[] {
  const unique = new Set<number>();
  for (const code of codes) {
    if (!NAMES.has(code)) {
      throw new Error(`unknown exit code ${code}`);
    }
    unique.add(code);
  }
  const nonOk = [...unique].filter((code) => code !== ExitCode.OK).sort((a, b) => a - b);
  return nonOk.length > 0 ? nonOk : [ExitCode.OK];
}
