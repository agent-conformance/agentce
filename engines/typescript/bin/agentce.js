#!/usr/bin/env node
const { version } = require("../package.json");
const args = process.argv.slice(2);

// The bare `--version`/`-V` flag stays a plain one-line shortcut that never loads the engine, but only
// as the whole command line: anything else with it goes to `cli.js`'s `main`, which refuses it (18.108).
// The `version` subcommand is a real command (structured `--json` envelope, a no_ml self-report).
if (args.length === 1 && (args[0] === "--version" || args[0] === "-V")) {
  console.log(`agentce ${version}`);
  process.exit(0);
}

// Real commands run the compiled engine; `pnpm build` produces dist/. During development the
// `agentce` package script runs the TypeScript source directly with tsx instead.
// exitCode, not exit(): exit() would end the process before a piped stdout drains (a large
// rendering cut at 64 KiB).
const { main } = require("../dist/cli.js");
process.exitCode = main(args);
