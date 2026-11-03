import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
const ts = createRequire(import.meta.url)("typescript");
const cache = new Map();
function load(url) {
  if (cache.has(url.href)) return cache.get(url.href);
  const exports = {}; cache.set(url.href, exports);
  const code = ts.transpileModule(readFileSync(url, "utf8"), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
  new Function("exports", "require", code)(exports, id => {
    assert.ok(id.startsWith(".")); return load(new URL(id.endsWith(".ts") ? id : `${id}.ts`, url));
  });
  return exports;
}
const { renderTopbarV2 } = load(new URL("../src/shells/unified/topbar.ts", import.meta.url));
const state = open => ({ proposals: [], projectLabel: "", contextLabel: "", headerCtas: [], memoryCount: 0, pendingMemoryCount: 0, sessionCount: 0,
  currentWorkOverview: { visible: true, open, label: "当前工作 · 2 组需关注", bodyHtml: "<p>项目与独立事项</p>", canExpand: false, moreLabel: "", loadingMore: false } });
function fakeContainer() {
  const events = new Map();
  return { innerHTML: "", classList: { add() {}, remove() {} }, events,
    querySelector(selector) { return selector === ".unified-topbar-menu" ? null : { addEventListener: (type, fn) => events.set(`${selector}:${type}`, fn) }; },
    querySelectorAll() { return []; } };
}
test("existing real-state work entry has a labelled dismissible nonmodal detail surface", () => {
  const el = fakeContainer(); let toggles = 0;
  renderTopbarV2(el, state(true), { onToggleCurrentWorkOverview: () => toggles++ });
  assert.match(el.innerHTML, /aria-expanded="true"/);
  assert.match(el.innerHTML, /aria-controls="unified-current-work-popover"/);
  assert.match(el.innerHTML, /id="unified-current-work-close"/);
  assert.match(el.innerHTML, /项目与独立事项/);
  assert.doesNotMatch(el.innerHTML, /aria-modal="true"|role="dialog"|unified-confirm-glass/);
  el.events.get("#unified-current-work-close:click")(); assert.equal(toggles, 1);
  renderTopbarV2(el, state(false), { onToggleCurrentWorkOverview: () => toggles++ });
  assert.match(el.innerHTML, /aria-expanded="false"/);
  assert.doesNotMatch(el.innerHTML, /id="unified-current-work-close"/);
});
test("popover sizing reserves the current composer region rather than relying on a blocking overlay", () => {
  const index = readFileSync(new URL("../src/shells/unified/index.ts", import.meta.url), "utf8");
  const css = readFileSync(new URL("../src/shells/unified/current-work-overview.css", import.meta.url), "utf8");
  assert.match(index, /function sizeCurrentWorkPopover\(/);
  assert.match(index, /composerDock\.getBoundingClientRect\(\)\.top/);
  assert.match(index, /currentWork.*ResizeObserver/s);
  assert.match(css, /\.unified-current-work-popover\s*\{[^}]*box-sizing:\s*border-box/s);
});

test("the actual size function tracks composer movement and hides when no safe space remains", () => {
  const src = readFileSync(new URL("../src/shells/unified/index.ts", import.meta.url), "utf8");
  const ast = ts.createSourceFile("index.ts", src, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
  function find(node) { if (ts.isFunctionDeclaration(node) && node.name?.text === "sizeCurrentWorkPopover") return node; return ts.forEachChild(node, find); }
  const fn = find(ast); assert.ok(fn);
  const js = ts.transpileModule(`${fn.getText(ast)}; return sizeCurrentWorkPopover;`, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText;
  const popover = { style: {}, getBoundingClientRect: () => ({ top: 70 }) };
  let composerTop = 300;
  const size = new Function("topbarEl", "composerDock", js)({ querySelector: () => popover }, { getBoundingClientRect: () => ({ top: composerTop }) });
  size(); assert.equal(popover.style.maxHeight, "222px"); assert.equal(popover.style.visibility, "");
  composerTop = 105; size(); assert.equal(popover.style.maxHeight, "27px"); assert.equal(popover.style.visibility, "hidden");
  composerTop = 400; size(); assert.equal(popover.style.maxHeight, "322px"); assert.equal(popover.style.visibility, "");
});
