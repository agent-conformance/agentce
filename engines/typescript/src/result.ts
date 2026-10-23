/**
 * The result of running one command, and its deterministic `--json` envelope.
 *
 * A command handler returns a {@link CommandResult}: the exit codes that apply, a machine-readable
 * `data` payload, and human-readable lines. The CLI renders it as the `--json` envelope (the
 * command's `data` at the top level, the reserved exit-code keys layered on top) or as text, and
 * derives the process exit code from it. This mirrors the Python engine's envelope byte for byte.
 */

import { ExitCode, applicable, combine, nameOf } from "./exitCodes";

const VALID_CODES = new Set<number>([
  ExitCode.OK,
  ExitCode.FINDINGS,
  ExitCode.INSUFFICIENT_EVIDENCE,
  ExitCode.INPUT_ERROR,
]);

export class CommandResult {
  readonly command: string;
  readonly codes = new Set<number>();
  data: Record<string, unknown> = {};
  readonly humanLines: string[] = [];

  constructor(command: string) {
    this.command = command;
  }

  addCode(code: number): void {
    if (!VALID_CODES.has(code)) {
      throw new Error(`unknown exit code ${code}`);
    }
    this.codes.add(code);
  }

  note(line: string): void {
    this.humanLines.push(line);
  }

  get exitCode(): number {
    return combine(this.codes);
  }

  get applicableCodes(): number[] {
    return applicable(this.codes);
  }

  envelope(): Record<string, unknown> {
    return {
      ...this.data,
      command: this.command,
      exit_code: this.exitCode,
      exit_codes: this.applicableCodes,
      exit_status: this.applicableCodes.map(nameOf),
    };
  }
}
