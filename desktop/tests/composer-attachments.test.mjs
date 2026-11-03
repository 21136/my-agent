import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import test from "node:test";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const ts = require("typescript");
const sourcePath = fileURLToPath(new URL("../src/composer-attachments.ts", import.meta.url));
const source = readFileSync(sourcePath, "utf8");
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;

function makeHarness({ text = "draft", attachments = [], sendMessage, beforeSend } = {}) {
  const events = [];
  const input = { value: text, style: { height: "42px" }, disabled: false, addEventListener() {} };
  const sendBtn = { disabled: false };
  let staged = [...attachments];
  const fileDrop = {
    getAttachments: () => staged,
    clearAttachments: () => { events.push("clear-attachments"); staged = []; },
  };
  const messages = [];
  const tracking = [];
  const statuses = [];
  const chat = {
    model: { confirmPending: false },
    isWorking: () => false,
    pushUserMessage: (message, metadata) => { events.push("push-user-message"); messages.push(message); tracking.push(metadata); },
  };
  const client = {
    sendMessage: (...args) => { events.push("send-message"); return sendMessage?.(...args) ?? { requestId: "11111111-1111-4111-8111-111111111111", sessionId: "session-1" }; },
  };
  const module = { exports: {} };
  vm.runInNewContext(compiled, {
    module,
    exports: module.exports,
    require: (name) => {
      assert.equal(name, "./user-message");
      return { userMessagePreview: (value) => value };
    },
  }, { filename: sourcePath });
  const { wireComposerAttachments } = module.exports;
  const wire = wireComposerAttachments({
    input, sendBtn, client, chat, fileDrop,
    onStatus: (status) => statuses.push(status),
    beforeSend: () => { events.push("before-send"); beforeSend?.(); },
  });
  return { input, sendBtn, messages, tracking, statuses, events, wire, getAttachments: () => staged };
}

const attachment = {
  id: "file-1", name: "notes.txt", ref: "staged/notes.txt", size: 2048,
  mime: "text/plain", readable_text: true, copied: true,
};

test("synchronous disconnected send failure preserves text and attachments and reports failure", () => {
  const state = makeHarness({
    text: " keep this draft ", attachments: [attachment],
    sendMessage: () => { throw new Error("WebSocket is not open"); },
  });
  state.wire.sendCurrentMessage();
  assert.equal(state.input.value, " keep this draft ");
  assert.equal(state.input.style.height, "42px");
  assert.deepEqual(state.getAttachments(), [attachment]);
  assert.deepEqual(state.messages, []);
  assert.ok(state.statuses.some((status) => status.includes("发送失败") && status.includes("WebSocket is not open")));
});

test("successful text send dispatches before clearing draft and appending the local message", () => {
  const state = makeHarness({
    text: "hello",
    sendMessage: () => { assert.equal(state.input.value, "hello"); assert.deepEqual(state.messages, []); },
  });
  state.wire.sendCurrentMessage();
  assert.equal(state.input.value, "");
  assert.equal(state.input.style.height, "");
  assert.deepEqual(state.messages, ["hello"]);
  assert.equal(state.tracking[0].sessionId, "session-1");
  assert.equal(state.tracking[0].requestId, "11111111-1111-4111-8111-111111111111");
  assert.equal(state.tracking[0].receiptEligible, true);
  assert.ok(state.events.indexOf("send-message") < state.events.indexOf("push-user-message"));
});

test("successful attachment send clears staged files only after synchronous send returns", () => {
  const state = makeHarness({
    text: "see attached", attachments: [attachment],
    sendMessage: (text, ids) => {
      assert.equal(text, "see attached");
      assert.deepEqual(ids, ["file-1"]);
      assert.deepEqual(state.getAttachments(), [attachment]);
      assert.deepEqual(state.messages, []);
    },
  });
  state.wire.sendCurrentMessage();
  assert.deepEqual(state.getAttachments(), []);
  assert.match(state.messages[0], /notes\.txt/);
  assert.match(state.messages[0], /see attached/);
  assert.equal(state.tracking[0].receiptEligible, true, "the backend receipt covers the resolved attachment message record");
  assert.equal(state.tracking[0].hasAttachments, true, "saved message status must retain the attachment-retention boundary");
  assert.ok(state.events.indexOf("send-message") < state.events.indexOf("clear-attachments"));
  assert.ok(state.events.indexOf("send-message") < state.events.indexOf("push-user-message"));
});

test("beforeSend runs before dispatch but cannot consume the draft on a send failure", () => {
  const state = makeHarness({
    text: "preserve me", sendMessage: () => { throw new Error("disconnected"); },
    beforeSend: () => assert.equal(state.input.value, "preserve me"),
  });
  state.wire.sendCurrentMessage();
  assert.ok(state.events.indexOf("before-send") < state.events.indexOf("send-message"));
  assert.equal(state.input.value, "preserve me");
  assert.deepEqual(state.messages, []);
});
