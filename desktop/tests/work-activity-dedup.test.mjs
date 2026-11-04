import assert from "node:assert/strict";
import test from "node:test";
import { createRequire } from "node:module";
import { pathToFileURL } from "node:url";
import path from "node:path";
const require = createRequire(import.meta.url);
const esbuild = require("esbuild");
async function importBundle(entry) {
  const result = await esbuild.build({ entryPoints: [path.resolve(entry)], bundle: true, write: false, platform: "node", format: "esm", logLevel: "silent" });
  return import(`data:text/javascript;base64,${Buffer.from(result.outputFiles[0].text).toString("base64")}`);
}
const { renderActivityTimeline } = await importBundle("desktop/src/shells/activity-timeline.ts");

const base = {
  kind: "process", lines: [], reasoning: "", collapsed: false, turnKey: "dedupe-test",
  entries: [
    { kind: "tool", callId: "run", tool: "file_read", summary: "读取配置", status: "running", startedAt: 1 },
    { kind: "tool", callId: "ok", tool: "file_write", summary: "写入界面", endSummary: "完成", status: "ok", startedAt: 2, endedAt: 3 },
    { kind: "tool", callId: "fail", tool: "file_write", summary: "保存文件", endSummary: "权限拒绝", status: "fail", logsTail: "permission denied", startedAt: 4, endedAt: 5 },
    { kind: "tool", callId: "note", tool: "file_read", summary: "读取资源", endSummary: "文件暂不存在", status: "note", startedAt: 6, endedAt: 7 },
  ],
};

test("summary mode hides only routine running/success rows and keeps failure/note evidence", () => {
  const html = renderActivityTimeline(base, { liveTurn: false, toolsFoldOpen: false, toolDisplay: "summary" });
  assert.equal(html.includes('data-tool-call="run"'), false);
  assert.equal(html.includes('data-tool-call="ok"'), false);
  assert.equal(html.includes('data-tool-call="fail"'), true);
  assert.equal(html.includes("permission denied"), true);
  assert.equal(html.includes('data-tool-call="note"'), true);
  assert.equal(html.includes("文件暂不存在"), true);
});

test("default tool timeline remains backward-compatible for historical/detail views", () => {
  const html = renderActivityTimeline(base, { liveTurn: false, toolsFoldOpen: false });
  for (const callId of ["run", "ok", "fail", "note"]) assert.equal(html.includes(`data-tool-call="${callId}"`), true);
});
