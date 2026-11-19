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

test("WebSocket rejects stale-session history and forwards canonical user request IDs", async () => {
  const { client, socket } = await clientHarness();
  const seen = [];
  client.onEvent((event) => seen.push(event));
  socket.message({
    type: "session.history", session_id: OTHER_SESSION,
    items: [{ role: "user", text: "stale", request_id: REQUEST }],
  });
  socket.message({
    type: "session.history", session_id: SESSION,
    items: [{ role: "user", text: "current", request_id: REQUEST }, { role: "assistant", text: "reply" }],
  });
  assert.equal(seen.length, 1);
  assert.deepEqual(seen[0].items, [
    { role: "user", text: "current", request_id: REQUEST }, { role: "assistant", text: "reply" },
  ]);
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

test("deferred identified history reconciles after receipt and terminal despite shifted ordinals", () => {
  const chat = chatHarness();
  const prior = [
    { role: "user", text: "prior one" }, { role: "assistant", text: "reply one" },
    { role: "user", text: "prior two" }, { role: "assistant", text: "reply two" },
    { role: "user", text: "prior three" }, { role: "assistant", text: "reply three" },
    { role: "user", text: "prior four" }, { role: "assistant", text: "reply four" },
  ];
  chat.handleEvent({ type: "session.history", session_id: SESSION, items: prior });
  const requestId = "33333333-3333-4333-8333-333333333333";
  chat.pushUserMessage("current", {
    requestId, sessionId: SESSION, receiptEligible: true, hasAttachments: true,
  });
  chat.handleEvent({
    type: "session.history", session_id: SESSION, truncated: true, omitted_count: 2,
    items: [...prior.slice(2), { role: "user", text: "current", request_id: requestId }],
  });
  assert.equal(userBlocks(chat).length, 5, "busy history remains deferred");
  chat.handleEvent({ type: "user.message.persisted", session_id: SESSION, request_id: requestId });
  chat.handleEvent({ type: "turn.end", ok: true, finish_reason: "completed" });
  const users = userBlocks(chat);
  assert.deepEqual(users.map((block) => block.text), ["prior two", "prior three", "prior four", "current"]);
  assert.equal(users.at(-1).persistence?.requestId, requestId);
  assert.equal(users.at(-1).persistence?.state, "saved");
  assert.equal(users.at(-1).persistence?.attachmentRetention, "unknown");
  assert.deepEqual(chat.model.blocks.filter((block) => block.kind === "assistant").map((block) => block.text),
    ["reply two", "reply three", "reply four"]);
});

test("exact identified history proves persistence without a receipt", () => {
  const chat = chatHarness();
  chat.handleEvent({
    type: "session.history", session_id: SESSION,
    items: [{ role: "user", text: "prior" }, { role: "assistant", text: "reply" }],
  });
  const requestId = "44444444-4444-4444-8444-444444444444";
  chat.pushUserMessage("same text", { requestId, sessionId: SESSION, receiptEligible: true, hasAttachments: true });
  chat.handleEvent({ type: "turn.end", ok: false, finish_reason: "failed" });
  chat.handleEvent({
    type: "session.history", session_id: SESSION, truncated: true, omitted_count: 8,
    items: [{ role: "user", text: "same text", request_id: requestId }],
  });
  const users = userBlocks(chat);
  assert.equal(users.length, 1);
  assert.equal(users[0].persistence?.requestId, requestId);
  assert.equal(users[0].persistence?.state, "saved", "a unique same-session durable ID is persistence evidence");
  assert.equal(users[0].persistence?.receiptConfirmed, false, "history identity is not a receipt event");
  assert.equal(users[0].persistence?.attachmentRetention, "unknown");
});

test("history identity alone cannot authorize legacy turn/text fallback", () => {
  const chat = chatHarness();
  const requestId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
  chat.pushUserMessage("same text", { requestId, sessionId: SESSION, receiptEligible: true });
  chat.handleEvent({ type: "turn.end", ok: false, finish_reason: "failed" });
  chat.handleEvent({
    type: "session.history", session_id: SESSION,
    items: [{ role: "user", text: "same text", request_id: requestId }],
  });
  assert.equal(userBlocks(chat)[0].persistence?.state, "saved");
  assert.equal(userBlocks(chat)[0].persistence?.receiptConfirmed, false);
  chat.handleEvent({
    type: "session.history", session_id: SESSION,
    items: [{ role: "user", text: "same text" }],
  });
  assert.equal(userBlocks(chat).length, 2, "only an exact persistence receipt enables legacy chronology fallback");
});

test("late exact receipt remains explicit after history already proved persistence", () => {
  const chat = chatHarness();
  const requestId = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
  chat.pushUserMessage("identified", { requestId, sessionId: SESSION, receiptEligible: true });
  chat.handleEvent({ type: "turn.end", ok: false, finish_reason: "failed" });
  chat.handleEvent({
    type: "session.history", session_id: SESSION,
    items: [{ role: "user", text: "identified", request_id: requestId }],
  });
  assert.equal(userBlocks(chat)[0].persistence?.state, "saved");
  assert.equal(userBlocks(chat)[0].persistence?.receiptConfirmed, false);
  chat.handleEvent({ type: "user.message.persisted", session_id: SESSION, request_id: requestId });
  assert.equal(userBlocks(chat)[0].persistence?.receiptConfirmed, true);
});

test("different and malformed history request IDs never use legacy chronology fallback", () => {
  for (const historyRequestId of ["55555555-5555-4555-8555-555555555555", "not-a-uuid", undefined]) {
    const chat = chatHarness();
    const localRequestId = "66666666-6666-4666-8666-666666666666";
    chat.pushUserMessage("same text", {
      requestId: localRequestId, sessionId: SESSION, receiptEligible: true,
    });
    chat.handleEvent({ type: "user.message.persisted", session_id: SESSION, request_id: localRequestId });
    chat.handleEvent({ type: "turn.end", ok: true, finish_reason: "completed" });
    chat.handleEvent({
      type: "session.history", session_id: SESSION, ...(historyRequestId === undefined ? { truncated: true, omitted_count: 1 } : {}),
      items: [{ role: "user", text: "same text", ...(historyRequestId === undefined ? {} : { request_id: historyRequestId }) }],
    });
    assert.equal(userBlocks(chat).length, 2, "identified rows are not legacy turn/text matches");
    assert.ok(userBlocks(chat).some((block) => block.persistence?.requestId === localRequestId));
  }
});

test("duplicate identified history rows are ambiguous and do not resolve unknown persistence", () => {
  const chat = chatHarness();
  const requestId = "99999999-9999-4999-8999-999999999999";
  chat.pushUserMessage("ambiguous", { requestId, sessionId: SESSION, receiptEligible: true, hasAttachments: true });
  chat.handleEvent({ type: "turn.end", ok: false, finish_reason: "failed" });
  chat.handleEvent({
    type: "session.history", session_id: SESSION,
    items: [
      { role: "user", text: "ambiguous", request_id: requestId },
      { role: "assistant", text: "reply" },
      { role: "user", text: "ambiguous", request_id: requestId },
    ],
  });
  const users = userBlocks(chat);
  assert.equal(users.length, 3);
  assert.ok(users.some((user) => user.persistence?.requestId === requestId
    && user.persistence.state === "unknown" && user.persistence.attachmentRetention === "unknown"));
});

test("two same-text local sends reconcile only to their exact history identities", () => {
  const chat = chatHarness();
  const first = "77777777-7777-4777-8777-777777777777";
  const second = "88888888-8888-4888-8888-888888888888";
  chat.pushUserMessage("repeat", { requestId: first, sessionId: SESSION, receiptEligible: true });
  chat.pushUserMessage("repeat", { requestId: second, sessionId: SESSION, receiptEligible: true });
  chat.handleEvent({ type: "turn.end", ok: true, finish_reason: "completed" });
  chat.handleEvent({
    type: "session.history", session_id: SESSION,
    items: [
      { role: "user", text: "repeat", request_id: first },
      { role: "user", text: "repeat", request_id: second },
    ],
  });
  const users = userBlocks(chat);
  assert.equal(users.length, 2);
  assert.deepEqual(users.map((block) => block.persistence?.requestId), [first, second]);
});

test("untracked preview messages reconcile only at the same turn and text", () => {
  const matching = chatHarness();
  matching.handleEvent({
    type: "session.history", session_id: SESSION,
    items: [{ role: "user", text: "prior" }],
  });
  matching.pushUserMessage("current");
  matching.handleEvent({ type: "turn.end", ok: true, finish_reason: "completed" });
  matching.handleEvent({
    type: "session.history", session_id: SESSION,
    items: [{ role: "user", text: "prior" }, { role: "user", text: "current" }],
  });
  assert.deepEqual(userBlocks(matching).map((block) => [block.turnIndex, block.text]), [[1, "prior"], [2, "current"]]);

  const separate = chatHarness();
  separate.handleEvent({
    type: "session.history", session_id: SESSION,
    items: [{ role: "user", text: "repeat" }],
  });
  separate.pushUserMessage("repeat");
  separate.handleEvent({ type: "turn.end", ok: true, finish_reason: "completed" });
  separate.handleEvent({
    type: "session.history", session_id: SESSION,
    items: [{ role: "user", text: "repeat" }],
  });
  assert.deepEqual(userBlocks(separate).map((block) => block.turnIndex), [1, 2],
    "same text in a different turn is not a match");
});

test("history hydration reconciles a receipt-confirmed live message at its turn position", () => {
  const chat = chatHarness();
  tracked(chat);
  const live = userBlocks(chat)[0];
  chat.handleEvent(persisted());
  chat.handleEvent({ type: "turn.end", ok: true, finish_reason: "completed" });
  chat.handleEvent({
    type: "session.history", session_id: SESSION,
    items: [{ role: "user", text: "same text" }],
  });
  const users = userBlocks(chat);
  assert.equal(users.length, 1, "the persisted history row and its live identity render once");
  assert.equal(users[0].turnIndex, live.turnIndex);
  assert.equal(users[0].persistence?.requestId, REQUEST);
  assert.equal(users[0].persistence?.state, "saved");
});

test("legacy history does not guess an unknown local identity from turn and text", () => {
  const chat = chatHarness();
  tracked(chat);
  chat.handleEvent({ type: "turn.end", ok: false, finish_reason: "failed" });
  chat.handleEvent({
    type: "session.history", session_id: SESSION,
    items: [{ role: "user", text: "same text" }],
  });
  const users = userBlocks(chat);
  assert.equal(users.length, 2, "legacy history cannot attribute an unknown send by chronology alone");
  assert.ok(users.some((user) => user.persistence?.requestId === REQUEST && user.persistence.state === "unknown"));
});

test("history hydration does not collapse a separate earlier message with identical text", () => {
  const chat = chatHarness();
  chat.handleEvent({
    type: "session.history", session_id: SESSION,
    items: [{ role: "user", text: "same text" }],
  });
  tracked(chat);
  chat.handleEvent({ type: "turn.end", ok: true, finish_reason: "completed" });
  chat.handleEvent({
    type: "session.history", session_id: SESSION,
    items: [{ role: "user", text: "same text" }],
  });
  assert.deepEqual(userBlocks(chat).map((block) => block.turnIndex), [1, 2]);
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
  const { userMessagePersistenceLabel } = load(new URL("../src/shells/chat-state.ts", import.meta.url));
  const renderBlock = new Function(
    "perspective", "messageReadingLayout", "parseUserMessage", "isRecentTurnBlock",
    "recallHighlightTurns", "formatUserMessageHtml", "userMessagePersistenceLabel",
    "const exports = {}; " + js + "; return renderBlock;",
  )("project", () => null, (text) => ({ body: text }), () => true, new Set(), (text) => text, userMessagePersistenceLabel);
  const renderState = (state) => renderBlock({
    kind: "user", text: "hello", turnIndex: 1,
    persistence: { requestId: REQUEST, sessionId: SESSION, state, receiptEligible: true },
  });
  assert.doesNotMatch(renderState("pending"), /unified-message-persistence/);
  assert.doesNotMatch(renderState("saved"), /已保存/);
  assert.doesNotMatch(renderState("saved"), /unified-message-persistence/);
  assert.match(renderState("unknown"), /结果未知，请核对历史/);
  assert.match(renderState("unknown"), /class="unified-turn-body"/);
});

test("userMessagePersistenceLabel keeps successful saves silent", () => {
  const { userMessagePersistenceLabel } = load(new URL("../src/shells/chat-state.ts", import.meta.url));
  assert.equal(userMessagePersistenceLabel("saved"), "");
  assert.equal(userMessagePersistenceLabel("pending"), "");
  assert.match(userMessagePersistenceLabel("unknown"), /核对历史/);
});

