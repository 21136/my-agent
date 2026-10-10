import assert from "node:assert/strict";
import test from "node:test";

import { summarizeConfirmPreview } from "../src/shells/unified/confirm-preview.ts";

test("run_command confirm shows the real command, not a char count", () => {
  const cmd = "python3 -m unittest -v test_todo.py && rm -f .test-todos.json && " + "x".repeat(200);
  const preview = [
    `运行命令：${cmd.slice(0, 120)}…（共 ${cmd.length} 字符 · 1 行）`,
    "工作目录：workspace/todo",
    "命令类型：other",
    "命令全文：",
    cmd,
  ].join("\n");
  const view = summarizeConfirmPreview(preview);
  assert.match(view.headline, /运行 python3 -m unittest/);
  assert.equal(view.detail, cmd);
  assert.ok(!/^运行命令：\d+ 字符/.test(view.headline));
});

test("danger run_command still labels the risk", () => {
  const preview = ["运行命令：rm -rf /", "命令类型：danger", "命令全文：", "rm -rf /"].join("\n");
  const view = summarizeConfirmPreview(preview);
  assert.match(view.reason, /高风险/);
  assert.equal(view.detail, "rm -rf /");
});
