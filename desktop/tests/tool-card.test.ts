import assert from "node:assert/strict";
import test from "node:test";

import { formatToolActionLabel, resolveEffectiveToolName } from "../src/copy/tool-display.ts";
import { renderActivityTimeline } from "../src/shells/activity-timeline.ts";
import { settledToolStatus } from "../src/shells/tool-outcome.ts";
import {
  humanizeToolSubline,
  lastFailedTool,
  processPillLabel,
  renderToolFailAlert,
  userFacingToolError,
} from "../src/shells/unified/output-display.ts";

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

test("a missing-file read is a muted note, not a failure", () => {
  assert.equal(settledToolStatus(true, "文件还不存在", "absent"), "note");
  assert.equal(settledToolStatus(false, "文件还不存在"), "note");
  assert.equal(settledToolStatus(false, "path does not exist: workspace/scratch-readme/README.md"), "fail");
  assert.equal(humanizeToolSubline("note", "文件还不存在"), "文件还不存在");

  const tools = [
    {
      tool: "read_file",
      summary: "workspace/scratch-readme/README.md",
      status: "note" as const,
      endSummary: "文件还不存在",
      callId: "read-1",
      startedAt: 1,
      endedAt: 2,
    },
    {
      tool: "write_text",
      summary: "workspace/scratch-readme/README.md",
      status: "ok" as const,
      endSummary: "workspace/scratch-readme/README.md · 写入 1 行：一句项目简介",
      callId: "write-1",
      startedAt: 3,
      endedAt: 4,
    },
  ];
  const block = { collapsed: true, tools, turnKey: "t-absent" };
  assert.equal(processPillLabel(block).includes("失败"), false);
  assert.equal(lastFailedTool(tools), undefined);
  assert.equal(renderToolFailAlert(lastFailedTool(tools)), "");

  const html = renderActivityTimeline(
    {
      kind: "process",
      lines: [],
      reasoning: "",
      collapsed: false,
      turnKey: "t-absent",
      entries: [{ kind: "tool", ...tools[0] }],
    },
    { liveTurn: false, toolsFoldOpen: false },
  );
  assert.match(html, /读取 README\.md/);
  assert.match(html, /文件还不存在/);
  assert.match(html, /is-note/);
  assert.equal(html.includes("✗"), false);
  assert.equal(html.includes("✓"), false);
  assert.equal(html.includes("is-fail"), false);
  assert.equal(html.includes("这一步没有完成"), false);
  assert.equal(html.includes("修补未成功"), false);
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
