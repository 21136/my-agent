import assert from "node:assert/strict";
import test from "node:test";

import { formatToolActionLabel, resolveEffectiveToolName } from "../src/copy/tool-display.ts";
import { humanizeToolSubline, userFacingToolError } from "../src/shells/unified/output-display.ts";

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

test("tool card failures are Chinese, including a repeated patch after revert", () => {
  const hash = "patch_id was already applied but the file now has a different hash";
  assert.equal(humanizeToolSubline("fail", hash).includes("文件"), true);
  assert.equal(/[A-Za-z]{4,}/.test(humanizeToolSubline("fail", hash)), false);
  assert.match(userFacingToolError("find anchor not found"), /没有找到/);
  assert.match(userFacingToolError("base_hash mismatch; file changed since the patch was prepared"), /又变了/);
  assert.equal(userFacingToolError("secret token rejected by the remote service"), "这一步没有完成");
  assert.equal(humanizeToolSubline("ok", "workspace/scratch-readme/README.md · 写入 1 行：一句项目简介").includes("写入"), true);
});
