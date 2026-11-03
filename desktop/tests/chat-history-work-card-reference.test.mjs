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
  new Function("exports", "require", js)(exports, (id) => {
    assert.ok(id.startsWith("."));
    return load(new URL(id.endsWith(".ts") ? id : `${id}.ts`, url));
  });
  return exports;
}
const { historyFromItems } = load(new URL("../src/shells/chat-state.ts", import.meta.url));

const validReference = {
  role: "work_card",
  type: "work_card_reference",
  presentation_id: "7cfe09c0-4f54-4d04-9f14-9815b4374e58",
  card_id: `wc-${"a".repeat(64)}`,
  text: "工作记录关联（详情暂不可用）",
};

test("history keeps a validated work-card reference as an independent block at its source position", () => {
  const loaded = historyFromItems([
    { role: "user", text: "before" },
    validReference,
    { role: "assistant", text: "after" },
  ]);

  assert.deepEqual(loaded.blocks.map((block) => block.kind), ["user", "work-card-reference", "assistant"]);
  assert.deepEqual(loaded.blocks[1], {
    kind: "work-card-reference",
    presentationId: validReference.presentation_id,
    cardId: validReference.card_id,
    text: validReference.text,
  });
  assert.equal(loaded.turnCounter, 1);
  assert.deepEqual(loaded.seeds, [
    { role: "user", turnIndex: 1 },
    { role: "assistant", turnIndex: 1 },
  ]);
});

test("invalid work-card identity remains an explicit placeholder without changing neighboring history", () => {
  const loaded = historyFromItems([
    { role: "user", text: "before" },
    { ...validReference, presentation_id: `${validReference.presentation_id}\n` },
    { ...validReference, card_id: `wc-${"a".repeat(63)}` },
    { ...validReference, type: "work_card" },
    { ...validReference, card_id: `${validReference.card_id}\n` },
    { role: "assistant", text: "after" },
  ]);

  assert.deepEqual(loaded.blocks.map((block) => block.kind), ["user", "work-card-reference", "work-card-reference", "work-card-reference", "work-card-reference", "assistant"]);
  assert.equal(loaded.blocks[0].kind === "user" && loaded.blocks[0].text, "before");
  assert.ok(loaded.blocks.slice(1, 5).every((block) => block.kind === "work-card-reference" && block.text.includes("引用无效")));
  assert.equal(loaded.blocks[5].kind === "assistant" && loaded.blocks[5].text, "after");
  assert.equal(loaded.turnCounter, 1);
  assert.deepEqual(loaded.seeds.map((seed) => seed.role), ["user", "assistant"]);
});


