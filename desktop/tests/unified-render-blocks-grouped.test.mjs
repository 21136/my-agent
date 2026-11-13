import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const desktopRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const ts = createRequire(path.join(desktopRoot, "package.json"))("typescript");
const source = readFileSync(path.join(desktopRoot, "src/shells/unified/index.ts"), "utf8");
const ast = ts.createSourceFile("index.ts", source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
let renderBlocksGroupedSource;
function visit(node) {
  if (ts.isFunctionDeclaration(node) && node.name?.text === "renderBlocksGrouped") {
    renderBlocksGroupedSource = node.getText(ast);
  }
  ts.forEachChild(node, visit);
}
visit(ast);
assert.ok(renderBlocksGroupedSource, "renderBlocksGrouped must remain directly testable");
const executable = ts.transpileModule(renderBlocksGroupedSource, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText;

function createRenderer() {
  const seen = [];
  const scope = {
    chat: { isWorking: () => false },
    useTurnCardLayout: () => true,
    segmentChatBlocks: () => ["first", "last"],
    renderChatSegment: (segment, isLast) => { seen.push({ segment, isLast }); return segment; },
    appendPendingConfirmFallbackHtml: html => html,
  };
  const render = new Function("scope", `with (scope) { ${executable}; return renderBlocksGrouped; }`)(scope);
  return { render, seen };
}

test("grouped chat rendering marks only the final segment as last", () => {
  const { render, seen } = createRenderer();
  assert.equal(render([{"kind":"assistant","text":"hello"}]), "firstlast");
  assert.deepEqual(seen, [{ segment: "first", isLast: false }, { segment: "last", isLast: true }]);
});
