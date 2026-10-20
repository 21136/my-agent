import assert from "node:assert/strict";
import { test, beforeEach, afterEach } from "node:test";
import * as settings from "../src/settings.ts";

const originalStorage = Object.getOwnPropertyDescriptor(globalThis, "localStorage");
const originalDocument = Object.getOwnPropertyDescriptor(globalThis, "document");
let stored: Map<string, string>;
let applied: Map<string, string>;
beforeEach(() => {
  stored = new Map();
  applied = new Map();
  Object.defineProperty(globalThis, "localStorage", { configurable: true, value: {
    getItem: (key: string) => stored.get(key) ?? null,
    setItem: (key: string, value: string) => stored.set(key, value),
  }});
  Object.defineProperty(globalThis, "document", { configurable: true, value: {
    documentElement: { style: { setProperty: (key: string, value: string) => applied.set(key, value) } },
  }});
});
afterEach(() => {
  if (originalStorage) Object.defineProperty(globalThis, "localStorage", originalStorage);
  else Reflect.deleteProperty(globalThis, "localStorage");
  if (originalDocument) Object.defineProperty(globalThis, "document", originalDocument);
  else Reflect.deleteProperty(globalThis, "document");
});

test("chat font size exports the approved bounds and smaller default", () => {
  assert.equal(settings.CHAT_FONT_SIZE_MIN, 12);
  assert.equal(settings.CHAT_FONT_SIZE_MAX, 20);
  assert.equal(settings.CHAT_FONT_SIZE_DEFAULT, 14);
});
test("normalization accepts px integers and clamps/rounds numeric values", () => {
  for (const [input, expected] of [[12,12], [20,20], [9,12], [30,20], [15.6,16], ["17",17]]) {
    assert.equal(settings.normalizeChatFontSize(input), expected);
  }
});
test("invalid or absent persisted values use 14px", () => {
  assert.equal(settings.readChatFontSize(), 14);
  for (const input of [undefined, null, "", " ", "oops", "16px", NaN, Infinity, {}, true]) {
    assert.equal(settings.normalizeChatFontSize(input), 14);
  }
});
test("writing immediately applies the scoped CSS variable and persists for later reads", () => {
  assert.equal(settings.writeChatFontSize(18), 18);
  assert.equal(settings.readChatFontSize(), 18);
  assert.deepEqual([...applied], [["--ma-chat-font-size", "18px"]]);
});
test("application at boot reads persisted size without changing global font size", () => {
  settings.writeChatFontSize(12);
  applied.clear();
  assert.equal(settings.applyChatFontSize(), 12);
  assert.deepEqual([...applied], [["--ma-chat-font-size", "12px"]]);
});
test("reset immediately restores and saves 14px", () => {
  settings.writeChatFontSize(20);
  assert.equal(settings.resetChatFontSize(), 14);
  assert.equal(settings.readChatFontSize(), 14);
  assert.equal(applied.get("--ma-chat-font-size"), "14px");
});
test("corrupted, too small, and too large stored values stay safe", () => {
  for (const [input, expected] of [["bad",14], ["4",12], ["999",20]]) {
    settings.writeChatFontSize(18);
    for (const key of stored.keys()) stored.set(key, String(input));
    assert.equal(settings.readChatFontSize(), expected);
  }
});
test("storage denial still allows current-session changes and does not crash boot", () => {
  Object.defineProperty(globalThis, "localStorage", { configurable: true, get() { throw new Error("blocked"); }});
  assert.equal(settings.readChatFontSize(), 14);
  assert.equal(settings.writeChatFontSize(16), 16);
  assert.equal(applied.get("--ma-chat-font-size"), "16px");
  assert.equal(settings.resetChatFontSize(), 14);
});
