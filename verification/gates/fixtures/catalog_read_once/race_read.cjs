// Preloaded into the TypeScript CLI (`node -r race_read.cjs dist/cli.js ...`) for VG-CATALOG-READ-ONCE (18.171).
// Counts every readFileSync of RACE_FILE and writes the count to RACE_COUNT at exit; right after the first read,
// RACE_FILE on disk becomes the bytes of RACE_SWAP (unless it is "-"), as a writer racing the run would make it.
const fs = require("node:fs");
const target = fs.realpathSync(process.env.RACE_FILE);
const swap = process.env.RACE_SWAP === "-" ? null : fs.readFileSync(process.env.RACE_SWAP);
const read = fs.readFileSync;
let count = 0;
fs.readFileSync = function readFileSync(path, ...rest) {
  const out = read.call(this, path, ...rest);
  let same = false;
  try {
    same = typeof path !== "number" && fs.realpathSync(path) === target;
  } catch {
    same = false;
  }
  if (same) {
    count += 1;
    if (count === 1 && swap !== null) {
      fs.writeFileSync(target, swap);
    }
  }
  return out;
};
process.on("exit", () => fs.writeFileSync(process.env.RACE_COUNT, String(count)));
