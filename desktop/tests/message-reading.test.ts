import assert from "node:assert/strict";
import test from "node:test";

import { messageReadingLayout, canUseReadingOpener } from "../src/shells/unified/message-reading.ts";
const layout = messageReadingLayout;
const opener = canUseReadingOpener;

test("short replies and empty placeholders retain a compact bubble", () => {
  assert.equal(layout("可以，这次只改聊天气泡。"), "bubble");
  assert.equal(layout(""), "bubble");
  assert.equal(layout("文".repeat(259)), "bubble");
});

test("long paragraphs become reading surfaces without requiring headings", () => {
  assert.equal(layout("文".repeat(260)), "reading");
  assert.equal(layout("长段落。".repeat(200)), "reading");
});

test("many paragraphs and tall hard-break output do not grow a giant bubble", () => {
  assert.equal(layout(Array(5).fill("一个段落。").join("\n\n")), "reading");
  assert.equal(layout(Array(8).fill("一行。").join("\n")), "reading");
});

test("fenced code, Markdown tables, and rendered HTML blocks use reading layout", () => {
  assert.equal(layout("```ts\nconst ready = true;\n```"), "reading");
  assert.equal(layout("~~~json\n{}\n~~~"), "reading");
  assert.equal(layout("| 项目 | 状态 |\n| --- | --- |\n| 界面 | 完成 |"), "reading");
  assert.equal(layout("<table><tr><td>示例</td></tr></table>"), "reading");
  assert.equal(layout("<pre>示例</pre>"), "reading");
});

test("ordinary inline code and a short quotation keep their bubble", () => {
  assert.equal(layout("用 `example` 表示即可。"), "bubble");
  assert.equal(layout("> 先把边界讲清楚。\n\n这是一个短回答。"), "bubble");
});

test("the opener must be a complete, short, existing paragraph with a continuation", () => {
  assert.equal(opener("这就是原有首段。", true), true);
  assert.equal(opener("文".repeat(160), true), true);
  assert.equal(opener("文".repeat(161), true), false);
  assert.equal(opener("   ", true), false);
  assert.equal(opener("只有这一段。", false), false);
});

test("streaming text can cross the reading boundary without truncating the input", () => {
  const chunks = ["这是原句。", "文".repeat(259), "文".repeat(260), "文".repeat(500)];
  assert.deepEqual(chunks.map(layout), ["bubble", "bubble", "reading", "reading"]);
  assert.equal(chunks[3].length, 500);
});
