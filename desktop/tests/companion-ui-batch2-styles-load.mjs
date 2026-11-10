import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ts = createRequire(import.meta.url)("typescript");
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../src/shells/unified");

const js = ts.transpileModule(readFileSync(path.join(root, "companion-chat-briefing.ts"), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
const exports = {};
new Function("exports", "require", js)(exports, (id) => {
  if (id.endsWith(".ts")) return {};
  return createRequire(import.meta.url)(id);
});

export const renderWorkRecordsBriefingShell = exports.renderWorkRecordsBriefingShell;
