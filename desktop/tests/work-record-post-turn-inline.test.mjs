import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";

const ts = createRequire(import.meta.url)("typescript");
const cache = new Map();
function load(url) {
  if (cache.has(url.href)) return cache.get(url.href);
  const exports = {};
  cache.set(url.href, exports);
  const js = ts.transpileModule(readFileSync(url, "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  new Function("exports", "require", js)(exports, id => {
    assert.ok(id.startsWith("."));
    return load(new URL(id.endsWith(".ts") ? id : `${id}.ts`, url));
  });
  return exports;
}
const { createChatSession, isConfirmInProgressLabel, escapeHtml } = load(new URL("../src/shells/chat-state.ts", import.meta.url));
const { renderConfirmCardHtml } = load(new URL("../src/shells/unified/confirm-preview.ts", import.meta.url));
const source = readFileSync(new URL("../src/shells/unified/index.ts", import.meta.url), "utf8");
const ast = ts.createSourceFile("index.ts", source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
function findNode(predicate, root = ast) {
  if (predicate(root)) return root;
  return ts.forEachChild(root, child => findNode(predicate, child));
}
function functionNode(name) {
  const node = findNode(n => ts.isFunctionDeclaration(n) && n.name?.text === name);
  assert.ok(node, `missing ${name}`);
  return node;
}
const renderBlock = functionNode("renderBlock");
const confirmBranch = findNode(n => ts.isIfStatement(n) && n.expression.getText(ast) === 'block.kind === "confirm"', renderBlock);
assert.ok(confirmBranch);
const clickRegistration = findNode(n => ts.isCallExpression(n)
  && n.expression.getText(ast) === "chatEl.addEventListener"
  && n.arguments[0]?.getText(ast) === '"click"'
  && n.getText(ast).includes('submitConfirmById(id, choice)'));
assert.ok(clickRegistration);
const code = `
  function renderConfirm(block) { ${confirmBranch.getText(ast)} return ""; }
  ${functionNode("recallDetailsOpen").getText(ast)}
  ${functionNode("submitConfirmById").getText(ast)}
  ${clickRegistration.getText(ast)};
  return { renderConfirm, recallDetailsOpen };
`;
const compiled = ts.transpileModule(code, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText;
const makeUi = new Function("chat", "chatEl", "confirmGlass", "perspective", "client", "setStatus", "renderChat", "renderConfirmGlass", "syncWorkingVisual", "isConfirmChoiceAllowed", "isWorkCardRecallPreview", "isConfirmInProgressLabel", "renderConfirmCardHtml", "escapeHtml", compiled);

function makeChat() {
  const chat = createChatSession({ showProcess: true, confirmInBlocks: false });
  chat.handleEvent({ type: "session.banner", session_id: "session-a", topics: [], turn_mode: "agent", turn_mode_label: "动手", phase: "S4" });
  return chat;
}
function request(chat, id = "record-1", preview = "工作事项确认") {
  return chat.handleEvent({ type: "confirm.request", confirm_kind: "work_record_post_turn", session_id: "session-a", request_id: id, preview, allow_approve_all: false });
}
function fakeContainer() {
  return { cards: [], listeners: {}, querySelectorAll() { return this.cards; }, addEventListener(type, fn) { this.listeners[type] = fn; } };
}
function harness(chat, perspective) {
  const chatEl = fakeContainer(), glass = fakeContainer(), sent = [];
  const ui = makeUi(chat, chatEl, glass, perspective, { sendConfirm: (...args) => sent.push(args) }, () => {}, () => {}, () => {}, () => {},
    (preview, choice, open) => preview !== "recall" || choice === "n" || open,
    preview => preview === "recall", isConfirmInProgressLabel, renderConfirmCardHtml, escapeHtml);
  function click(id, choice) {
    const button = { disabled: false, dataset: { id, confirm: choice } };
    chatEl.listeners.click({ target: { closest: () => button } });
  }
  return { ...ui, chatEl, glass, sent, click };
}

test("post-turn confirmation is rendered inline in both perspectives while ordinary night confirmation remains hidden", () => {
  for (const perspective of ["project", "night"]) {
    const chat = makeChat(); request(chat);
    const block = chat.model.blocks.find(item => item.kind === "confirm");
    const ui = harness(chat, perspective);
    const html = ui.renderConfirm(block);
    assert.match(html, /unified-confirm/);
    assert.match(html, /data-confirm="y"/);
    assert.match(html, /data-confirm="n"/);
    assert.equal(chat.model.confirmOverlay, null);
    assert.equal(chat.model.confirmPending, false);
    if (perspective === "night") assert.equal(ui.renderConfirm({ kind: "confirm", requestId: "ordinary", preview: "普通确认", allowApproveAll: false }), "");
  }
});

test("inline clicks submit once without reviving a turn; switch and rejection remain safe", () => {
  for (const perspective of ["project", "night"]) {
    const chat = makeChat(); request(chat);
    const ui = harness(chat, perspective);
    ui.click("record-1", "n");
    ui.click("record-1", "n");
    assert.deepEqual(ui.sent, [["record-1", "n"]]);
    assert.equal(chat.model.turnActive, false);
    assert.equal(chat.model.turnActivityStartedAt, 0);
    const done = { type: "confirm.done", confirm_kind: "work_record_post_turn", session_id: "session-a", request_id: "record-1", choice: "n" };
    assert.equal(chat.handleEvent(done), true);
    assert.equal(chat.handleEvent(done), false);
    chat.handleEvent({ type: "session.banner", session_id: "session-b", topics: [], turn_mode: "agent", turn_mode_label: "动手", phase: "S4" });
    ui.click("record-1", "y");
    assert.deepEqual(ui.sent, [["record-1", "n"]]);
    assert.equal(chat.handleEvent(done), false);
  }
});

test("night inline recall approval checks the expanded card in the chat, not the glass", () => {
  const chat = makeChat(); request(chat, "record-recall", "recall");
  const ui = harness(chat, "night");
  const card = { querySelectorAll: () => [{ dataset: { id: "record-recall" } }], querySelector: () => ({ open: true }) };
  ui.chatEl.cards.push(card);
  assert.equal(ui.recallDetailsOpen("record-recall"), true);
  ui.click("record-recall", "y");
  assert.deepEqual(ui.sent, [["record-recall", "y"]]);
  assert.equal(chat.model.turnActive, false);
});

test("session switch clearing the model before the banner discards stale post-turn request bindings", () => {
  const chat = makeChat();
  assert.equal(request(chat, "reused-id"), true);
  // The shell clears model fields while navigation is in progress, before session.banner.
  chat.model.blocks = [];
  chat.model.sessionId = "";
  chat.handleEvent({ type: "session.banner", session_id: "session-b", topics: [], turn_mode: "agent", turn_mode_label: "动手", phase: "S4" });
  assert.equal(chat.handleEvent({ type: "confirm.request", confirm_kind: "work_record_post_turn", session_id: "session-b", request_id: "reused-id", preview: "新会话", allow_approve_all: false }), true);
  const ui = harness(chat, "night");
  assert.match(ui.renderConfirm(chat.model.blocks.find(item => item.kind === "confirm")), /data-confirm="y"/);
  ui.click("reused-id", "y");
  assert.deepEqual(ui.sent, [["reused-id", "y"]]);
  assert.equal(chat.model.turnActive, false);
});

test("post-turn request identifiers are safe in inline button attributes without changing the identifier", () => {
  const chat = makeChat();
  const id = 'record" data-injected="yes&x';
  request(chat, id);
  const ui = harness(chat, "night");
  const html = ui.renderConfirm(chat.model.blocks.find(item => item.kind === "confirm"));
  assert.match(html, /data-id="record&quot; data-injected=&quot;yes&amp;x"/);
  ui.click(id, "n");
  assert.deepEqual(ui.sent, [[id, "n"]]);
});

test("approval in either perspective leaves a different running turn unchanged", () => {
  for (const perspective of ["project", "night"]) {
    const chat = makeChat();
    chat.pushUserMessage("正在进行另一回合");
    const before = { key: chat.model.currentTurnKey, started: chat.model.turnActivityStartedAt, active: chat.model.turnActive, finished: chat.model.turnFinished, counter: chat.model.turnCounter };
    request(chat);
    const ui = harness(chat, perspective);
    ui.click("record-1", "y");
    assert.deepEqual(ui.sent, [["record-1", "y"]]);
    assert.equal(chat.handleEvent({ type: "confirm.done", confirm_kind: "work_record_post_turn", session_id: "session-a", request_id: "record-1", choice: "y" }), true);
    assert.deepEqual({ key: chat.model.currentTurnKey, started: chat.model.turnActivityStartedAt, active: chat.model.turnActive, finished: chat.model.turnFinished, counter: chat.model.turnCounter }, before);
  }
});
