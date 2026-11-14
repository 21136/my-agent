import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";

const ts = createRequire(import.meta.url)("typescript");
const source = readFileSync(new URL("../src/shells/unified/doc-reading.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
const moduleExports = {};
new Function("exports", "require", compiled)(moduleExports, () => ({
  escapeHtml: (value) => String(value).replaceAll("&", "&amp;").replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;").replaceAll('"', "&quot;"),
}));

const docs = [{ path: "GUIDE.md", name: "GUIDE.md", size: 4, is_standard: false }];

test("independent reader keeps save and reload but hides unsupported actions and overwrite", () => {
  const { html } = moduleExports.renderDocumentReaderHtml({
    docs, currentPath: "GUIDE.md", currentContent: "# Guide", renderedHtml: "<h1>Guide</h1>",
    newDocName: "", includeCatalog: true, independentMode: true, view: "edit", draft: "# changed",
    dirty: true, conflictPrompt: true,
  });
  assert.match(html, /data-action="document-save"/);
  assert.match(html, /data-action="document-conflict-reload"/);
  assert.doesNotMatch(html, /data-action="create-doc"|data-doc-more=|doc-selection-to-ai|doc-ai-draft/);
  assert.doesNotMatch(html, /document-conflict-overwrite|仍要覆盖/);
});

test("project discovery candidates are labeled unverified, never treated as authorization", () => {
  const html = moduleExports.renderIndependentDocProjectPickerHtml([
    { id: "alpha", root: "C:/work/alpha", session_id: null, is_current: false },
  ], "alpha");
  assert.match(html, /data-independent-doc-project-select/);
  assert.match(html, /C:\/work\/alpha/);
  assert.match(html, /选择项目/);
  assert.match(html, /确认访问后可查看和编辑文档，不会改变当前会话/);
  assert.doesNotMatch(html, /后端确认|发现列表|候选项目/);
  assert.doesNotMatch(html, /project\.open|绑定会话/);
});

test("independent project document commands carry explicit request and access context", async () => {
  const source = readFileSync(new URL("../src/api/ws.ts", import.meta.url), "utf8");
  const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
  class Socket { static OPEN = 1; readyState = 0; listeners = new Map(); sent = []; constructor() { queueMicrotask(() => { this.readyState = 1; this.emit("open"); }); } addEventListener(type, fn) { this.listeners.set(type, [...(this.listeners.get(type) ?? []), fn]); } emit(type, event) { for (const fn of this.listeners.get(type) ?? []) fn(event); } send(text) { this.sent.push(JSON.parse(text)); } close() { this.readyState = 3; this.emit("close", {}); } }
  const apiExports = {}; new Function("exports", "require", "window", "WebSocket", compiled)(apiExports, () => ({ normalizeEvolutionHostRequest: (request) => request }), { myAgentDesktop: {} }, Socket);
  const client = new apiExports.AgentWsClient("127.0.0.1", 8765); await client.connect(); client.ws.sent.length = 0;
  client.authorizeProjectDocView("a1", "alpha"); client.listProjectDocView("a2", "alpha", "view-1"); client.readProjectDocView("a3", "alpha", "view-1", "GUIDE.md"); client.writeProjectDocView("a4", "alpha", "view-1", "GUIDE.md", "# edit", 12, "hash-1"); client.closeProjectDocView("a5", "alpha", "view-1");
  assert.deepEqual(client.ws.sent, [
    { type: "project.doc.view.authorize", request_id: "a1", project_id: "alpha" },
    { type: "project.doc.view.list", request_id: "a2", project_id: "alpha", view_id: "view-1" },
    { type: "project.doc.view.read", request_id: "a3", project_id: "alpha", view_id: "view-1", path: "GUIDE.md" },
    { type: "project.doc.view.write", request_id: "a4", project_id: "alpha", view_id: "view-1", path: "GUIDE.md", content: "# edit", base_mtime: 12, base_hash: "hash-1" },
    { type: "project.doc.view.close", request_id: "a5", project_id: "alpha", view_id: "view-1" },
  ]); client.destroy();
});

test("independent document view wires authorization, correlated replies, and saves without session binding", () => {
  const source = readFileSync(new URL("../src/shells/unified/index.ts", import.meta.url), "utf8");
  for (const event of ["project.doc.view.authorized", "project.doc.view.list.done", "project.doc.view.read.done", "project.doc.view.write.done", "project.doc.view.close.done", "project.doc.view.error"]) {
    assert.match(source, new RegExp(`case \\"${event}\\"`), `missing event handler ${event}`);
  }
  assert.match(source, /data-independent-doc-project-select/);
  assert.match(source, /authorizeProjectDocView/);
  assert.match(source, /writeProjectDocView/);
  assert.match(source, /baseHash/);
  assert.match(source, /independentDocView\.requests/);
});






test("independent view disables discard and blocks navigation while a save is in flight", () => {
  const { html } = moduleExports.renderDocumentReaderHtml({
    docs, currentPath: "GUIDE.md", currentContent: "# Guide", renderedHtml: "",
    newDocName: "", includeCatalog: true, independentMode: true, view: "edit", draft: "# changed",
    dirty: true, saving: true,
  });
  assert.match(html, /data-action="document-discard" disabled/);
  assert.equal(moduleExports.independentDocNavigationBlocked(false), false);
  assert.equal(moduleExports.independentDocNavigationBlocked(true), true);
  const source = readFileSync(new URL("../src/shells/unified/index.ts", import.meta.url), "utf8");
  assert.match(source, /independentDocNavigationBlocked\(state\.saving\)[^\n]*不能切换项目/);
  assert.match(source, /independentDocNavigationBlocked\(independentDocView\.saving\)[^\n]*不能切换文档/);
});

test("independent document surface handles shared confirmation actions", () => {
  const source = readFileSync(new URL("../src/shells/unified/index.ts", import.meta.url), "utf8");
  assert.match(source, /documentEl\.addEventListener\(["\x27]click["\x27],\s*handleConfirmButtonClick\)/);
});


test("document save shortcut routes to the active document mode", () => {
  const indexSource = readFileSync(new URL("../src/shells/unified/index.ts", import.meta.url), "utf8");
  const syntax = ts.createSourceFile("index.ts", indexSource, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
  let callback;
  function visit(node) {
    if (ts.isCallExpression(node) && node.expression.getText(syntax) === "documentEl.addEventListener"
      && node.arguments[0]?.getText(syntax) === '"keydown"') callback = node.arguments[1];
    ts.forEachChild(node, visit);
  }
  visit(syntax);
  assert.ok(callback, "document keydown listener must exist");
  const code = ts.transpileModule(`const handler = ${callback.getText(syntax)};`, {
    compilerOptions: { target: ts.ScriptTarget.ES2022 },
  }).outputText;
  function run({ projectId, docView, authorization, view, key = "s", ctrlKey = true, metaKey = false }) {
    const projectState = { projectId, docView, mainFocus: "document" };
    const independentDocView = { authorization, view };
    const calls = { independent: 0, bound: 0, prevented: false };
    const handler = new Function("projectState", "independentDocView", "saveIndependentDocument", "saveCurrentDoc", code + "\nreturn handler;")(
      projectState, independentDocView, () => calls.independent++, () => calls.bound++,
    );
    handler({ key, ctrlKey, metaKey, target: { closest: () => null }, preventDefault: () => { calls.prevented = true; } });
    return calls;
  }
  assert.deepEqual(run({ projectId: "", docView: "edit", authorization: "authorized", view: "edit" }),
    { independent: 1, bound: 0, prevented: true });
  assert.deepEqual(run({ projectId: "", docView: "preview", authorization: "authorized", view: "edit", ctrlKey: false, metaKey: true, key: "S" }),
    { independent: 1, bound: 0, prevented: true });
  assert.deepEqual(run({ projectId: "bound", docView: "edit", authorization: "authorized", view: "edit" }),
    { independent: 0, bound: 1, prevented: true });
  assert.deepEqual(run({ projectId: "", docView: "edit", authorization: "denied", view: "edit" }),
    { independent: 0, bound: 0, prevented: false });
  assert.deepEqual(run({ projectId: "", docView: "edit", authorization: "authorized", view: "preview" }),
    { independent: 0, bound: 0, prevented: false });
  assert.deepEqual(run({ projectId: "", docView: "edit", authorization: "authorized", view: "edit", ctrlKey: false }),
    { independent: 0, bound: 0, prevented: false });
});
