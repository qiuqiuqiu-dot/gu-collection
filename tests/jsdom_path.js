/* jsdom 依赖定位：优先用 tests/node_modules，没有就退回 .tmp/node（之前装在那儿）。

   在 tests/ 下跑一次 `npm install` 就会装到 tests/node_modules。
   两个地方都没有时给出清楚的提示，而不是一句 "Cannot find module"。 */
const fs = require("fs");
const path = require("path");

const HERE = __dirname;
const CANDIDATES = [
  path.join(HERE, "node_modules", "jsdom"),
  path.join(HERE, "..", ".tmp", "node", "node_modules", "jsdom"),
];

for (const candidate of CANDIDATES) {
  if (fs.existsSync(candidate)) {
    module.exports = candidate;
    return;
  }
}
throw new Error(
  "找不到 jsdom。请在 tests/ 目录下执行一次：npm install\n"
  + "（已找过：" + CANDIDATES.join("、") + "）");
