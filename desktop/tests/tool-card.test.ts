import assert from "node:assert/strict";
import test from "node:test";

import { formatToolActionLabel, resolveEffectiveToolName } from "../src/copy/tool-display.ts";
import { humanizeToolSubline } from "../src/shells/unified/output-display.ts";

test("run_evolved write with a path summary is a file write, not 扩展工具", () => {
  const summary = "write_text: workspace/scratch-readme/README.md";
  const end = "workspace/scratch-readme/README.md · 写入 1 行：一句项目简介";
  assert.equal(resolveEffectiveToolName("run_evolved", summary), "write_text");
  const label = formatToolActionLabel("run_evolved", summary, end);
  assert.match(label, /写入/);
  assert.match(label, /README\.md/);
  assert.equal(label.includes("扩展工具"), false);
  assert.match(humanizeToolSubline("ok", end), /一句项目简介/);
});

test("bare write_text summary still shows the file from the result line", () => {
  const end = "workspace/scratch-readme/README.md · 写入 1 行：一句项目简介";
  const label = formatToolActionLabel("run_evolved", "write_text", end);
  assert.match(label, /写入/);
  assert.match(label, /README\.md/);
  assert.equal(label.includes("扩展工具"), false);
});

test("a bare ok result does not become a success detail line", () => {
  assert.equal(humanizeToolSubline("ok", "ok"), "");
  assert.equal(humanizeToolSubline("ok", "完成"), "");
});
