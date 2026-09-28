#!/usr/bin/env node
const { version } = require("../package.json");
const args = process.argv.slice(2);

// The bare `--version`/`-V` flag stays a plain one-line shortcut that never loads the engine; the
// `version` subcommand is a real command (structured `--json` envelope, a no_ml self-report) and
// must reach `cli.js`'s `main`, not be shadowed here.
if (args[0] === "--version" || args[0] === "-V") {
  console.log(`agentce ${version}`);
  process.exit(0);
}

// Real commands run the compiled engine; `pnpm build` produces dist/. During development the
// `agentce` package script runs the TypeScript source directly with tsx instead.
const { main } = require("../dist/cli.js");
process.exit(main(args));
