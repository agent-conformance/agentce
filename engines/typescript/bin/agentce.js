#!/usr/bin/env node
const { version } = require("../package.json");
const args = process.argv.slice(2);

if (args[0] === "--version" || args[0] === "-V" || args[0] === "version") {
  console.log(`agentce ${version}`);
  process.exit(0);
}

// Real commands run the compiled engine; `pnpm build` produces dist/. During development the
// `agentce` package script runs the TypeScript source directly with tsx instead.
const { main } = require("../dist/cli.js");
process.exit(main(args));
