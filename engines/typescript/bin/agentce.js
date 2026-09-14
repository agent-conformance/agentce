#!/usr/bin/env node
"use strict";
const { version } = require("../package.json");
const args = process.argv.slice(2);
if (args[0] === "--version" || args[0] === "-V" || args[0] === "version") {
  console.log(`agentce ${version}`);
  process.exit(0);
}
console.log(
  [
    "agentce (Agent Conformance Engine) - placeholder release.",
    "The assessment engine is under development.",
    "Repository: https://github.com/agent-conformance/agentce",
    "Usage: agentce --version",
  ].join("\n")
);
