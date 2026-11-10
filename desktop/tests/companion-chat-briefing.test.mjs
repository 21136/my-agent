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
  new Function("exports", "require", js)(exports, (id) => load(new URL(id.endsWith(".ts") ? id : `${id}.ts`, url)));
  return exports;
}

const briefing = load(new URL("../src/shells/unified/companion-chat-briefing.ts", import.meta.url));
const reading = load(new URL("../src/shells/unified/message-reading.ts", import.meta.url));

const SAMPLE = `当前会话已记录事项概况（工作记录和历史卡片不修改）：
共 4 组（1 个项目组、3 个独立事项组），4 个事项、8 项要求。
- 事项：多项目任务批次；状态概况：有要求结果待确认；状态计数：结果待确认 1
  - 要求：创建 index.html；状态：结果待确认

还有 16 组未展开，包含 12 个事项、22 项要求未展开；以上不是全部记录。

隐私提示：本问答会进入当前对话历史，后续对话模型可能读取。`;

test("detects work-record overview briefing in chat history", () => {
  assert.equal(briefing.isWorkRecordsOverviewBriefing(SAMPLE), true);
  assert.match(briefing.workRecordsBriefingSummary(SAMPLE), /工作记录|事项概况|未展开/);
  assert.equal(reading.messageReadingLayout(SAMPLE, { companionHome: true }), "bubble");
});

test("briefing shell keeps full body in details without rewriting text", () => {
  const shell = briefing.renderWorkRecordsBriefingShell("摘要", "<p>正文</p>");
  assert.match(shell, /<details class="unified-history-briefing">/);
  assert.match(shell, /<p>正文<\/p>/);
});
