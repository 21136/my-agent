import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import test from "node:test";

const ts = createRequire(import.meta.url)("typescript");
const cache = new Map();
function load(url, injected = {}) {
  if (cache.has(url.href)) return cache.get(url.href);
  const exports = {};
  cache.set(url.href, exports);
  const js = ts.transpileModule(readFileSync(url, "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  new Function("exports", "require", "window", "WebSocket", "crypto", js)(
    exports,
    (id) => {
      assert.ok(id.startsWith("."), `unexpected runtime dependency ${id}`);
      return load(new URL(id.endsWith(".ts") ? id : `${id}.ts`, url), injected);
    },
    injected.window ?? { setTimeout: () => 1, clearTimeout() {}, myAgentDesktop: {} },
    injected.WebSocket,
    injected.crypto ?? globalThis.crypto,
  );
  return exports;
}

const SESSION = "session-a";
const OTHER_SESSION = "session-b";
const REQUEST = "11111111-1111-4111-8111-111111111111";
const banner = (session_id = SESSION) => ({
  type: "session.banner", session_id, topics: [], turn_mode: "agent", turn_mode_label: "动手", phase: "S4",
});

class FakeSocket {
  static OPEN = 1;
  static CONNECTING = 0;
  static instances = [];
  readyState = FakeSocket.CONNECTING;
  sent = [];
  listeners = new Map();
  constructor() {
    FakeSocket.instances.push(this);
    queueMicrotask(() => { this.readyState = FakeSocket.OPEN; this.emit("open", {}); });
  }
  addEventListener(type, fn) { this.listeners.set(type, [...(this.listeners.get(type) ?? []), fn]); }
  emit(type, value) { for (const fn of this.listeners.get(type) ?? []) fn(value); }
  message(value) { this.emit("message", { data: JSON.stringify(value) }); }
  send(raw) { this.sent.push(JSON.parse(raw)); }
  close() { this.readyState = 3; this.emit("close", {}); }
}

async function clientHarness() {
  FakeSocket.instances = [];
  let sequence = 0;
  const client = new (load(new URL("../src/api/ws.ts", import.meta.url), {
    WebSocket: FakeSocket,
    crypto: { randomUUID: () => `22222222-2222-4222-8222-${String(++sequence).padStart(12, "0")}` },
  }).AgentWsClient)("127.0.0.1", 1234);
  await client.connect();
  const socket = FakeSocket.instances[0];
  socket.message(banner());
  return { client, socket };
}

function chatHarness() {
  const { createChatSession } = load(new URL("../src/shells/chat-state.ts", import.meta.url));
  const chat = createChatSession({ showProcess: true, confirmInBlocks: false });
  chat.handleEvent(banner());
  return chat;
}

function userBlocks(chat) { return chat.model.blocks.filter((block) => block.kind === "user"); }

const tracked = (chat, overrides = {}) => chat.pushUserMessage("same text", {
  requestId: REQUEST, sessionId: SESSION, receiptEligible: true, ...overrides,
});
const persisted = (session_id = SESSION, request_id = REQUEST) => ({
  type: "user.message.persisted", session_id, request_id,
});

// A user-message receipt is exact, session-bound evidence, not a run lifecycle event.
test("sendMessage creates a request identity, binds the active session, and sends the same identity", async () => {
  const { client, socket } = await clientHarness();
  const dispatch = client.sendMessage("hello");
  assert.match(dispatch.requestId, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
  assert.equal(dispatch.sessionId, SESSION);
  assert.deepEqual(socket.sent.at(-1), {
    type: "user.message", request_id: dispatch.requestId, session_id: SESSION, text: "hello",
  });
  const secondDispatch = client.sendMessage("another");
  assert.notEqual(secondDispatch.requestId, dispatch.requestId);
  assert.equal(secondDispatch.sessionId, SESSION);
  client.destroy();
});

test("WebSocket forwards only exact persisted receipts for the currently bound session", async () => {
  const { client, socket } = await clientHarness();
  const seen = [];
  client.onEvent((event) => seen.push(event));
  socket.message(persisted(OTHER_SESSION));
  socket.message({ ...persisted(), extra: "ignored" });
  socket.message(persisted(SESSION, "not-a-uuid"));
  assert.deepEqual(seen, []);
  socket.message(persisted());
  assert.deepEqual(seen, [persisted()]);
  client.destroy();
});

test("a dispatched user message is never automatically replayed after reconnect", async () => {
  const { client, socket } = await clientHarness();
  const dispatch = client.sendMessage("do not replay");
  socket.close();
  await client.connect();
  const reconnected = FakeSocket.instances[1];
  reconnected.message(banner());
  assert.equal(socket.sent.filter((item) => item.type === "user.message").length, 1);
  assert.equal(reconnected.sent.filter((item) => item.type === "user.message").length, 0);
  assert.equal(dispatch.sessionId, SESSION);
  client.destroy();
});

test("messages sent without a bound session return no session identity", async () => {
  FakeSocket.instances = [];
  const client = new (load(new URL("../src/api/ws.ts", import.meta.url), {
    WebSocket: FakeSocket, crypto: { randomUUID: () => REQUEST },
  }).AgentWsClient)("127.0.0.1", 1234);
  await client.connect();
  const dispatch = client.sendMessage("unbound");
  assert.equal(dispatch.sessionId, null);
  assert.match(dispatch.requestId, /^[0-9a-f-]{36}$/);
  client.destroy();
});

test("only exact current-session persisted receipts confirm the matching local block", () => {
  const chat = chatHarness();
  tracked(chat);
  const block = userBlocks(chat)[0];
  assert.equal(block.persistence?.state, "pending");
  assert.equal(block.persistence?.attachmentRetention, "not_applicable");
  for (const event of [persisted(OTHER_SESSION), persisted(SESSION, "bad-id"), { ...persisted(), extra: true }]) {
    chat.handleEvent(event);
    assert.equal(block.persistence?.state, "pending");
  }
  chat.handleEvent({ type: "turn.start", intent: "chat", intent_label: "聊天", run_id: "run-a" });
  assert.equal(block.persistence?.state, "pending", "turn.start is not persistence evidence");
  chat.handleEvent(persisted());
  assert.equal(block.persistence?.state, "saved");
});

test("turn end and disconnect make unconfirmed sends unknown without retry; a late exact receipt can resolve it", () => {
  const chat = chatHarness();
  tracked(chat);
  const block = userBlocks(chat)[0];
  chat.handleEvent({ type: "turn.end", ok: true, finish_reason: "completed", run_id: "run-a" });
  assert.equal(block.persistence?.state, "unknown");
  chat.handleEvent(persisted());
  assert.equal(block.persistence?.state, "saved");
  tracked(chat, { requestId: "33333333-3333-4333-8333-333333333333", hasAttachments: true });
  const second = userBlocks(chat)[1];
  chat.handleEvent({ type: "client.connection.closed" });
  assert.equal(second.persistence?.state, "unknown");
  assert.equal(second.persistence?.attachmentRetention, "unknown");
});

test("matching attachment receipt confirms only the message record, not attachment retention", () => {
  const chat = chatHarness();
  tracked(chat, { hasAttachments: true });
  const attachmentBlock = userBlocks(chat)[0];
  assert.equal(attachmentBlock.persistence?.state, "pending");
  chat.handleEvent(persisted(OTHER_SESSION));
  chat.handleEvent({ ...persisted(), staged_attachments: ["file-1"] });
  assert.equal(attachmentBlock.persistence?.state, "pending", "cross-session or extra-field receipts are not authoritative");
  chat.handleEvent(persisted());
  assert.equal(attachmentBlock.persistence?.state, "saved");
  assert.equal(attachmentBlock.persistence?.attachmentRetention, "unknown");
});

test("missing or expired attachment error stays unknown and cannot be promoted by a fake receipt", () => {
  const chat = chatHarness();
  tracked(chat, { hasAttachments: true, requestId: "44444444-4444-4444-8444-444444444444" });
  const block = userBlocks(chat)[0];
  chat.handleEvent({ type: "error", message: "unknown or expired attachment id" });
  assert.equal(block.persistence?.state, "unknown");
  chat.handleEvent(persisted(OTHER_SESSION, "44444444-4444-4444-8444-444444444444"));
  assert.equal(block.persistence?.state, "unknown");
  chat.pushUserMessage("offline draft", {
    requestId: "55555555-5555-4555-8555-555555555555", sessionId: null, receiptEligible: true, hasAttachments: true,
  });
  assert.equal(userBlocks(chat)[1].persistence?.state, "unknown");
});

test("switching sessions expires attachment confirmation without carrying it across sessions", () => {
  const chat = chatHarness();
  tracked(chat, { hasAttachments: true });
  const block = userBlocks(chat)[0];
  chat.handleEvent(banner(OTHER_SESSION));
  assert.equal(block.persistence?.state, "unknown");
  assert.equal(chat.model.sessionId, OTHER_SESSION);
});

test("history refresh preserves unresolved message identity rather than matching by text", () => {
  const chat = chatHarness();
  tracked(chat);
  const block = userBlocks(chat)[0];
  chat.handleEvent({
    type: "session.history", session_id: SESSION,
    items: [{ role: "user", text: "same text" }],
  });
  assert.ok(userBlocks(chat).some((item) => item.persistence?.requestId === REQUEST && item.persistence?.state === "pending"));
  assert.equal(block.persistence?.state, "pending");
});

test("renderer displays persistence state inline inside the existing user message", () => {
  const source = readFileSync(new URL("../src/shells/unified/index.ts", import.meta.url), "utf8");
  const ast = ts.createSourceFile("index.ts", source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
  let renderBlockSource;
  function visit(node) {
    if (ts.isFunctionDeclaration(node) && node.name?.text === "renderBlock") renderBlockSource = node.getText(ast);
    ts.forEachChild(node, visit);
  }
  visit(ast);
  assert.ok(renderBlockSource, "unified renderBlock must exist");
  const js = ts.transpileModule(renderBlockSource, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  const renderBlock = new Function(
    "perspective", "messageReadingLayout", "parseUserMessage", "isRecentTurnBlock",
    "recallHighlightTurns", "formatUserMessageHtml",
    "const exports = {}; " + js + "; return renderBlock;",
  )("project", () => null, (text) => ({ body: text }), () => true, new Set(), (text) => text);
  const renderState = (state) => renderBlock({
    kind: "user", text: "hello", turnIndex: 1,
    persistence: { requestId: REQUEST, sessionId: SESSION, state, receiptEligible: true },
  });
  assert.match(renderState("pending"), /待确认/);
  assert.match(renderState("saved"), /已保存/);
  assert.match(renderState("unknown"), /结果未知，请核对历史/);
  assert.match(renderState("unknown"), /class="unified-turn-body"/);
});

